/* What the setlist editor (/setlist/edit) and the gig page (/setlist/view)
 * share: reading keys, and the PDF of a setlist -- a setlist page split into
 * sets at the pauses, then each sheet in order, moved to its chosen key.
 * The pages include _setlist_pdf.html (the Share/PDF menus' items and the
 * progress modal) and the editor's sheet page off screen, which draws each
 * sheet in turn (leadsheet-editor.js, window.buildLeadSheetBundle).
 */
(function () {
  /* ---------- Keys ---------- */
  // The same fixed spellings as chords.py: one per pitch class (C = 0).
  const MAJOR_KEYS = ['C', 'Db', 'D', 'Eb', 'E', 'F', 'F#', 'G', 'Ab', 'A', 'Bb', 'B'];
  const MINOR_KEYS = ['Cm', 'C#m', 'Dm', 'Ebm', 'Em', 'Fm', 'F#m', 'Gm', 'G#m', 'Am', 'Bbm', 'Bm'];
  const LETTER_PC = { C: 0, D: 2, E: 4, F: 5, G: 7, A: 9, B: 11, H: 11 };
  const FLAT_MAJOR_ROOTS = [1, 3, 5, 8, 10]; // Db Eb F Ab Bb

  // { pc, minor } of a sheet's free-text key ("F# minor", "Hm"), or null.
  // Mirrors chords.read_key.
  function readKey(text) {
    const m = /^\s*([A-Ha-h])([#b♯♭]?)\s*(m(?!aj)|min|-)?/i.exec(String(text || '').split('/')[0]);
    if (!m) return null;
    const acc = { '#': 1, '♯': 1, b: -1, '♭': -1 }[m[2]] || 0;
    return { pc: (LETTER_PC[m[1].toUpperCase()] + acc + 12) % 12, minor: !!m[3] };
  }
  function keyList(minor) { return minor ? MINOR_KEYS : MAJOR_KEYS; }
  // Which spelling a key is written in, by its relative major (Ebm counts
  // as flat although its major is Gb) -- like the editor's semitonePrefersFlats.
  function prefersFlats(pc, minor) {
    const major = minor ? (pc + 3) % 12 : pc;
    return FLAT_MAJOR_ROOTS.includes(major) || (minor && major === 6);
  }
  // How far to move a sheet from its own key to `key`: the shorter way round (-5..+6).
  function semitonesTo(sheetKey, key) {
    const diff = (keyList(sheetKey.minor).indexOf(key) - sheetKey.pc + 12) % 12;
    return diff > 6 ? diff - 12 : diff;
  }
  function originalKey(sheet) {
    const k = readKey(sheet.key);
    return k ? keyList(k.minor)[k.pc] : '';
  }
  // How far it's moved, in whole tones like the key menu ("1.5 ↑"); empty when not.
  function amountLabel(semitones) {
    return semitones ? `${Math.abs(semitones) / 2} ${semitones > 0 ? '↑' : '↓'}` : '';
  }
  // The key a row is played in, as the gig page and the PDF's setlist show
  // it: "E (1 ↑)", the sheet's own key text when it can't be read, or ''.
  function rowKeyLabel(sheet, key) {
    const sheetKey = readKey(sheet.key);
    if (!sheetKey) return String(sheet.key || '').trim();
    const amount = amountLabel(semitonesTo(sheetKey, key));
    return amount ? `${key} (${amount})` : key;
  }

  // "2026-09-05" as the James Band intern app writes it: "5. sep. 2026".
  const DA_MONTHS_ABBR = ['jan.', 'feb.', 'mar.', 'apr.', 'maj', 'jun.',
    'jul.', 'aug.', 'sep.', 'okt.', 'nov.', 'dec.'];
  function danishDate(value) {
    const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value || '');
    return m ? `${Number(m[3])}. ${DA_MONTHS_ABBR[Number(m[2]) - 1]} ${m[1]}` : '';
  }
  // ... and as the setlist's date picker shows it: "5 September 2026".
  const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July',
    'August', 'September', 'October', 'November', 'December'];
  function longDate(value) {
    const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value || '');
    return m ? `${Number(m[3])} ${MONTHS[Number(m[2]) - 1]} ${m[1]}` : '';
  }

  /* ---------- PDF ---------- */
  // A row's whole sheet: one of the user's own (source 'local', from the
  // store) or the archive's, fetched. `title` names it if that fails.
  async function loadSheet(store, row, title) {
    if (row.source === 'local') {
      const sheet = await store.get('sheets', row.id);
      if (!sheet) throw new Error(`"${title}" isn't saved any more`);
      return sheet;
    }
    const resp = await fetch(`/sheets/${encodeURIComponent(row.id)}/json`);
    if (!resp.ok) throw new Error(`couldn't load "${title}"`);
    return resp.json();
  }

  // Wires a page's Share and PDF buttons. `current()` gives the setlist as
  // it is now, { name, date, rows } (rows as stored, without the page's own
  // fields); `loadSheet(row)` resolves to the row's whole sheet, or throws
  // with what went wrong.
  function wirePdf({ current, loadSheet, displayTitle }) {
    const status = document.getElementById('bundle-status');
    const downloadBtn = document.getElementById('bundle-download');
    const shareBtn = document.getElementById('bundle-share');
    // The last PDF made, and what it was made from (see snapshot()).
    let file = null;
    let fileSnapshot = null;
    // Set after "Tap to share": which PDF the next tap on Share shares, skipping the menu.
    let pendingShare = null;

    // The same progress modal as the intern app's gig page.
    const bundleModal = document.getElementById('bundle-modal');
    const bundleText = document.getElementById('bundle-modal-text');
    const bundleBar = document.getElementById('bundle-modal-bar');
    function showProgress(done, total) {
      bundleText.textContent = total ? `Preparing lead sheets… ${done}/${total}` : 'Preparing lead sheets…';
      bundleBar.style.width = `${total ? (done / total) * 100 : 0}%`;
      bundleModal.hidden = false;
    }

    function setStatus(text, error) {
      status.textContent = text;
      status.classList.toggle('save-status--error', !!error);
    }

    async function buildPlan(setlist, withSetlist) {
      const songRows = setlist.rows.filter(r => r.type === 'song');
      const fetched = await Promise.all(songRows.map(loadSheet));
      const sheetOf = new Map(songRows.map((row, i) => [row, fetched[i]]));

      const sets = [[]];
      const pages = [];
      setlist.rows.forEach(row => {
        if (row.type === 'break') { sets.push([]); return; }
        const sheet = sheetOf.get(row);
        const sheetKey = readKey(sheet.key);
        const key = sheetKey ? row.key : '';
        const semitones = sheetKey ? semitonesTo(sheetKey, row.key) : 0;
        sets[sets.length - 1].push({ title: displayTitle(sheet), key: key || String(sheet.key || '').trim(), amount: amountLabel(semitones) });
        const flats = sheetKey ? prefersFlats(keyList(sheetKey.minor).indexOf(row.key), sheetKey.minor) : false;
        pages.push({ sheet, semitones, flats });
      });

      const name = String(setlist.name || '').trim();
      const base = name.replace(/[\\/:*?"<>|]+/g, '').trim();
      return {
        pages,
        // No sets, no setlist page.
        sets: withSetlist ? sets.filter(set => set.length) : [],
        // Under "Setlist", like the James Band intern app's gig PDF:
        // "Bryllup · 29. aug. 2026" (there it's the venue and the date).
        subtitle: [name, danishDate(setlist.date)].filter(Boolean).join(' · '),
        filename: base ? `${base} – lead sheets.pdf` : 'Setlist.pdf',
      };
    }

    function snapshot(setlist, withSetlist) {
      return JSON.stringify([withSetlist, setlist.name, setlist.date, setlist.rows]);
    }

    function download() {
      const url = URL.createObjectURL(file);
      const a = document.createElement('a');
      a.href = url;
      a.download = file.name;
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 60000);
    }

    // The PDF of the setlist as it is now: made once and reused until the
    // setlist changes. Null when there's nothing to make or it failed.
    async function currentFile(withSetlist) {
      const setlist = current();
      if (!setlist.rows.some(r => r.type === 'song')) {
        setStatus('Add some lead sheets first.', true);
        return null;
      }
      const snap = snapshot(setlist, withSetlist);
      if (file && fileSnapshot === snap) return file;
      downloadBtn.disabled = shareBtn.disabled = true;
      shareBtn.textContent = 'Share';
      pendingShare = null;
      setStatus('');
      showProgress(0, 0);
      try {
        const plan = await buildPlan(setlist, withSetlist);
        // Called as sheet i of n starts drawing, so i - 1 are done.
        file = await window.buildLeadSheetBundle(plan, (i, n) => showProgress(i - 1, n));
        showProgress(1, 1);
        fileSnapshot = snap;
        return file;
      } catch (err) {
        file = null;
        setStatus(`Couldn't build the PDF: ${err && err.message ? err.message : err}`, true);
        return null;
      } finally {
        bundleModal.hidden = true;
        downloadBtn.disabled = shareBtn.disabled = false;
      }
    }

    async function downloadPdf(withSetlist) {
      if (await currentFile(withSetlist)) download();
    }

    // Where the browser can't share files (most desktops), Share downloads
    // the PDF instead.
    async function sharePdf(withSetlist) {
      const built = !(file && fileSnapshot === snapshot(current(), withSetlist));
      if (!await currentFile(withSetlist)) return;
      let canShare = false;
      try { canShare = !!(navigator.canShare && navigator.canShare({ files: [file] })); } catch (err) { /* no sharing */ }
      if (!canShare) { download(); return; }
      shareBtn.textContent = 'Share';
      try {
        await navigator.share({ files: [file], title: file.name });
      } catch (err) {
        // Making the PDF took longer than the browser lets a tap count as
        // "the user asked to share". It's ready now, so the next tap shares
        // straight away. (AbortError is just the share sheet being dismissed.)
        if (built && err.name === 'NotAllowedError') {
          shareBtn.textContent = 'Tap to share';
          pendingShare = withSetlist;
        }
      }
    }
    // Share and PDF each open a small menu: the sheets alone, or with the
    // setlist page first.
    function bundleMenu(btn, run) {
      const list = btn.parentElement.querySelector('.bundle-menu__list');
      const close = () => { list.hidden = true; btn.setAttribute('aria-expanded', 'false'); };
      btn.addEventListener('click', () => {
        if (btn === shareBtn && pendingShare !== null) {
          const withSetlist = pendingShare;
          pendingShare = null;
          sharePdf(withSetlist);
          return;
        }
        const open = list.hidden;
        list.hidden = !open;
        btn.setAttribute('aria-expanded', String(open));
      });
      list.querySelectorAll('button').forEach(item => item.addEventListener('click', () => {
        close();
        run(item.dataset.withSetlist === '1');
      }));
      document.addEventListener('click', e => {
        if (!list.hidden && !btn.parentElement.contains(e.target)) close();
      });
      document.addEventListener('keydown', e => {
        if (e.key === 'Escape' && !list.hidden) { close(); btn.focus(); }
      });
    }
    bundleMenu(downloadBtn, downloadPdf);
    bundleMenu(shareBtn, sharePdf);
  }

  window.SetlistPdf = {
    readKey, keyList, prefersFlats, semitonesTo, originalKey, amountLabel, rowKeyLabel,
    danishDate, longDate, loadSheet, wirePdf,
  };
})();
