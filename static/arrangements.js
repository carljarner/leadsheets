/* Arrangements (arrangements.html): sheet music for each archive sheet,
   ported from MatRevy's Bandet -> Arkiv tab.

   One card per lead sheet of the admin's (drafts, then the archive), never
   made here: a new card comes with a new sheet. An open card first links
   to the lead sheet and its versions (they open in Practice mode), then lists its folders ("MatRevy 26 - 0.5 op", ...), each
   collapsible with its own files. Files go only in folders.

   The page gets the archive and every folder as JSON; each write answers
   with what changed, so nothing is fetched again. Built with
   createElement/textContent only, never innerHTML.

   The setlist's gig page (setlist_view.html) shows the same cards,
   read-only, one per song: it sets window.ARRANGEMENTS without sheets and
   builds them with window.ArrangementCards.card(entry). Its entries also
   carry `uid` (the card, as a song can be in a setlist twice), `foldersOf`
   (whose arrangements: a version shows its original's), `charts` ([title,
   href] pairs, in place of the sheet and its versions), `position` and
   `keyLabel` (the key it's played in). */

(function () {
  'use strict';

  // A viewer (canEdit false) only opens and downloads.
  const { sheets, folders: foldersBySheet, maxMb, canEdit } = window.ARRANGEMENTS;
  const ALLOWED_EXTS = ['pdf', 'mscz', 'mscx'];
  const OPEN_KEY = 'arrangements-open';

  const lists = {
    drafts: document.getElementById('arr-drafts'),
    archive: document.getElementById('arr-archive'),
  };
  const search = document.getElementById('arr-search');
  // The Arrangements page itself, rather than the gig page's cards.
  const onPage = !!lists.drafts;

  // Open folders are remembered in this browser; open cards only until
  // the page is left, so every card starts closed.
  const open = loadOpen();
  function loadOpen() {
    try {
      const saved = JSON.parse(localStorage.getItem(OPEN_KEY) || '{}');
      return { cards: new Set(), folders: new Set(saved.folders || []) };
    } catch (e) {
      return { cards: new Set(), folders: new Set() };
    }
  }
  function saveOpen() {
    try {
      localStorage.setItem(OPEN_KEY, JSON.stringify({ folders: [...open.folders] }));
    } catch (e) { /* private window: just not remembered */ }
  }

  // "Uploading 2 of 3…" per folder id, while its uploads run.
  const uploadStatus = new Map();

  function mk(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  }

  function iconBtn(text, title, extraClass) {
    const b = mk('button', 'arr-icon-btn' + (extraClass ? ' ' + extraClass : ''), text);
    b.type = 'button';
    b.title = title;
    b.setAttribute('aria-label', title);
    return b;
  }

  // A card's own key, and whose arrangements it shows.
  function cardKey(sheet) { return sheet.uid || sheet.id; }
  function ownerId(sheet) { return sheet.foldersOf || sheet.id; }

  function folders(sheetId) {
    if (!foldersBySheet[sheetId]) foldersBySheet[sheetId] = [];
    return foldersBySheet[sheetId];
  }

  function filesLabel(n) {
    return n === 1 ? '1 file' : `${n} files`;
  }

  function ext(name) {
    return name.includes('.') ? name.split('.').pop().toLowerCase() : '';
  }

  // PDFs first, MuseScore files after; upload order within each.
  function sortedFiles(files) {
    return files.filter(f => f.ext === 'pdf').concat(files.filter(f => f.ext !== 'pdf'));
  }

  function base(sheetId) {
    return `/arrangements/${encodeURIComponent(sheetId)}`;
  }

  async function api(url, body) {
    let resp;
    try {
      resp = await fetch(url, {
        method: 'POST',
        headers: body instanceof Blob ? {} : { 'Content-Type': 'application/json' },
        body: body instanceof Blob ? body : JSON.stringify(body || {}),
      });
    } catch (e) {
      return { ok: false, message: 'No connection to the server.' };
    }
    if (!resp.ok) {
      const text = await resp.text();
      const message = resp.status === 404 ? 'It no longer exists. Reload the page.'
        : resp.status === 403 ? 'You are no longer logged in.'
        : text || `Error ${resp.status}`;
      return { ok: false, status: resp.status, message };
    }
    return { ok: true, data: resp.status === 204 ? null : await resp.json() };
  }

  /* ---------- modal ---------- */
  // One dialog for every question: a confirm (no fields) or a form of
  // text fields. `onOk(values)` returns an error to show, or nothing to close.
  const modal = document.getElementById('arr-modal');
  const modalOk = document.getElementById('arr-modal-ok');
  const modalError = document.getElementById('arr-modal-error');
  let modalSubmit = null;

  function openModal({ title, detail = '', okLabel = 'OK', danger = false, fields = [], onOk }) {
    document.getElementById('arr-modal-title').textContent = title;
    const detailEl = document.getElementById('arr-modal-detail');
    detailEl.textContent = detail;
    detailEl.hidden = !detail;
    const box = modal.querySelector('.modal-box');
    box.classList.toggle('modal-box--confirm', !fields.length);
    box.classList.toggle('arr-modal--form', fields.length > 0);
    const holder = document.getElementById('arr-modal-fields');
    holder.replaceChildren();
    const inputs = fields.map((field, i) => {
      const wrap = mk('label', 'field arr-modal-field');
      if (field.head) wrap.appendChild(field.head);
      const input = mk('input');
      input.type = 'text';
      input.maxLength = 120;
      input.value = field.value || '';
      input.placeholder = field.placeholder || '';
      input.setAttribute('aria-label', field.label || title);
      input.addEventListener('keydown', e => {
        if (e.key !== 'Enter') return;
        e.preventDefault();
        if (i + 1 < fields.length) inputs[i + 1].focus();
        else submit();
      });
      wrap.appendChild(input);
      holder.appendChild(wrap);
      return input;
    });
    modalOk.textContent = okLabel;
    modalOk.classList.toggle('danger', danger);
    modalOk.disabled = false;
    modalError.hidden = true;
    modal.hidden = false;

    async function submit() {
      const values = inputs.map(input => input.value.trim());
      const empty = inputs.find(input => !input.value.trim());
      if (empty) { showModalError('Give it a name.'); empty.focus(); return; }
      modalOk.disabled = true;
      const error = await onOk(values);
      modalOk.disabled = false;
      if (error) { showModalError(error); return; }
      closeModal();
    }
    modalSubmit = submit;
    if (inputs.length) { inputs[0].focus(); inputs[0].select(); } else modalOk.focus();
  }

  function showModalError(message) {
    modalError.textContent = message;
    modalError.hidden = false;
  }

  function closeModal() {
    modal.hidden = true;
    modalSubmit = null;
  }

  // Only the admin's Arrangements page has the dialog.
  if (modal) {
    modalOk.addEventListener('click', () => { if (modalSubmit) modalSubmit(); });
    document.getElementById('arr-modal-cancel').addEventListener('click', closeModal);
    modal.addEventListener('click', e => { if (e.target === modal) closeModal(); });
    document.addEventListener('keydown', e => { if (e.key === 'Escape' && !modal.hidden) closeModal(); });
  }

  /* ---------- cards ---------- */
  function render() {
    for (const [section, ul] of Object.entries(lists)) {
      const own = sheets.filter(s => !!s.draft === (section === 'drafts'));
      ul.replaceChildren(...own.map(buildCard));
      const noun = section === 'drafts' ? 'sheet' : 'song';
      document.getElementById(`arr-${section}-count`).textContent = own.length ? `${own.length} ${noun}${own.length === 1 ? '' : 's'}` : '';
      document.getElementById(`arr-${section}-empty`).hidden = own.length > 0;
    }
    search.hidden = !sheets.length;
    filter();
  }

  function rerenderCard(sheet) {
    const node = document.querySelector(`.arr-card[data-id="${CSS.escape(cardKey(sheet))}"]`);
    if (node) node.replaceWith(buildCard(sheet));
  }

  // Hides cards in place rather than rebuilding them, so typing keeps focus.
  function filter() {
    const terms = search.value.toLowerCase().split(/\s+/).filter(Boolean);
    let shown = 0;
    for (const card of document.querySelectorAll('.arr-card')) {
      card.hidden = !terms.every(t => card.dataset.search.includes(t));
      if (!card.hidden) shown++;
    }
    document.getElementById('arr-search-empty').hidden = !sheets.length || shown > 0;
  }
  if (onPage) search.addEventListener('input', filter);

  function buildCard(sheet) {
    const key = cardKey(sheet);
    const isOpen = open.cards.has(key);
    const li = mk('li', 'setlist-card arr-card' + (isOpen ? ' arr-card--open' : ''));
    li.dataset.id = key;
    li.dataset.search = `${sheet.title} ${sheet.artist || ''} ${(sheet.versions || []).map(v => v.title).join(' ')}`.toLowerCase();

    const head = mk('button', 'arr-card__head');
    head.type = 'button';
    head.setAttribute('aria-expanded', String(isOpen));
    head.appendChild(mk('span', 'arr-chevron', isOpen ? '▾' : '▸'));
    if (sheet.position) head.appendChild(mk('span', 'arr-position', `${sheet.position}.`));
    // One line each, cut short with "…"; the full text shows on hover.
    head.title = sheet.artist ? `${sheet.title} – ${sheet.artist}` : sheet.title;
    const titles = mk('span', 'arr-card__titles');
    const header = mk('span', 'setlist-card__header');
    header.appendChild(mk('span', 'arr-ellipsis', sheet.title));
    titles.appendChild(header);
    if (sheet.artist) titles.appendChild(mk('span', 'leadsheet-card__artist arr-ellipsis', sheet.artist));
    head.appendChild(titles);
    if (sheet.keyLabel) head.appendChild(mk('span', 'arr-key', sheet.keyLabel));
    head.addEventListener('click', () => {
      if (open.cards.has(key)) open.cards.delete(key);
      else open.cards.add(key);
      saveOpen();
      rerenderCard(sheet);
    });
    li.appendChild(head);
    if (!isOpen) return li;

    const body = mk('div', 'arr-card__body');
    // The lead sheet itself, first, then its versions ("Song (MatRevy 26)",
    // drafts all): each opens in Practice mode. A viewer sees the admin's
    // drafts read-only at /drafts/<id>.
    const draftHref = id => canEdit ? `/draft#${encodeURIComponent(id)}` : `/drafts/${encodeURIComponent(id)}`;
    const charts = sheet.charts || [[sheet.title, sheet.draft ? draftHref(sheet.id) : `/sheets/${encodeURIComponent(sheet.id)}`]]
      .concat(sheet.versions.map(v => [v.title, draftHref(v.id)]));
    for (const [title, href] of charts) {
      const sheetRow = mk('div', 'arr-sheet-row');
      sheetRow.appendChild(mk('span', 'arr-kind arr-kind--sheet', 'Chart'));
      const link = mk('a', 'arr-sheet-link', title);
      link.href = href;
      link.title = 'Open the lead sheet in Practice mode';
      sheetRow.appendChild(link);
      body.appendChild(sheetRow);
    }

    for (const folder of folders(ownerId(sheet))) body.appendChild(buildFolder(sheet, folder));
    if (!canEdit) {
      if (!folders(ownerId(sheet)).length) body.appendChild(mk('p', 'muted arr-folder__empty', 'No arrangements yet.'));
      li.appendChild(body);
      return li;
    }

    const newBtn = mk('button', 'add-row-btn arr-add', '+');
    newBtn.type = 'button';
    newBtn.title = 'New folder';
    newBtn.setAttribute('aria-label', 'New folder');
    newBtn.addEventListener('click', () => newFolder(sheet));
    body.appendChild(newBtn);
    li.appendChild(body);
    return li;
  }

  /* ---------- folders ---------- */
  function buildFolder(sheet, folder) {
    const isOpen = open.folders.has(folder.id);
    const node = mk('div', 'arr-folder' + (isOpen ? ' arr-folder--open' : ''));

    const headRow = mk('div', 'arr-folder__head');
    const toggle = mk('button', 'arr-folder__toggle');
    toggle.type = 'button';
    toggle.setAttribute('aria-expanded', String(isOpen));
    toggle.appendChild(mk('span', 'arr-chevron', isOpen ? '▾' : '▸'));
    toggle.appendChild(mk('span', 'arr-folder__name', folder.name));
    toggle.appendChild(mk('span', 'arr-count', filesLabel(folder.files.length)));
    toggle.addEventListener('click', () => {
      if (open.folders.has(folder.id)) open.folders.delete(folder.id);
      else open.folders.add(folder.id);
      saveOpen();
      rerenderCard(sheet);
    });
    headRow.appendChild(toggle);
    node.appendChild(headRow);
    if (canEdit) wireFolderEditing(sheet, folder, node, headRow);

    if (!isOpen) return node;

    const body = mk('div', 'arr-folder__body');
    if (!folder.files.length) body.appendChild(mk('p', 'muted arr-folder__empty', 'No files yet.'));
    for (const file of sortedFiles(folder.files)) body.appendChild(buildFile(sheet, folder, file));
    node.appendChild(body);
    if (!canEdit) return node;

    const bar = mk('div', 'arr-upload-bar');
    const input = mk('input');
    input.type = 'file';
    input.multiple = true;
    input.accept = ALLOWED_EXTS.map(x => '.' + x).join(',');
    input.hidden = true;
    input.addEventListener('change', () => {
      const picked = Array.from(input.files || []);
      input.value = '';
      if (picked.length) askUpload(sheet, folder, picked);
    });
    const uploadBtn = mk('button', 'add-row-btn arr-add arr-add--small', '+');
    uploadBtn.type = 'button';
    uploadBtn.title = 'Upload files (or drop them on the folder)';
    uploadBtn.setAttribute('aria-label', 'Upload files');
    uploadBtn.disabled = uploadStatus.has(folder.id);
    uploadBtn.addEventListener('click', () => input.click());
    bar.appendChild(uploadBtn);
    bar.appendChild(input);
    if (uploadStatus.has(folder.id)) bar.appendChild(mk('span', 'muted', uploadStatus.get(folder.id)));
    body.appendChild(bar);
    return node;
  }

  // The admin's controls on a folder: rename, delete, and dropping files on it.
  function wireFolderEditing(sheet, folder, node, headRow) {
    const actions = mk('div', 'arr-actions');
    const renameBtn = iconBtn('✎', 'Rename folder');
    renameBtn.addEventListener('click', () => renameFolder(sheet, folder));
    actions.appendChild(renameBtn);
    const deleteBtn = iconBtn('×', 'Delete folder', 'arr-icon-btn--danger');
    deleteBtn.addEventListener('click', () => deleteFolder(sheet, folder));
    actions.appendChild(deleteBtn);
    headRow.appendChild(actions);

    // Drop files from the desktop onto a folder, open or closed, to upload.
    node.addEventListener('dragover', e => {
      if (!e.dataTransfer || !Array.from(e.dataTransfer.types || []).includes('Files')) return;
      e.preventDefault();
      node.classList.add('arr-dragover');
    });
    node.addEventListener('dragleave', e => {
      if (!node.contains(e.relatedTarget)) node.classList.remove('arr-dragover');
    });
    node.addEventListener('drop', e => {
      if (!e.dataTransfer || !e.dataTransfer.files.length) return;
      e.preventDefault();
      node.classList.remove('arr-dragover');
      open.folders.add(folder.id);
      saveOpen();
      askUpload(sheet, folder, Array.from(e.dataTransfer.files));
    });
  }

  function newFolder(sheet) {
    openModal({
      title: `New folder for "${sheet.title}"`,
      okLabel: 'Create',
      fields: [{ placeholder: 'e.g. MatRevy 26 - 0.5 op', label: 'Folder name' }],
      onOk: async ([name]) => {
        const result = await api(`${base(sheet.id)}/folders`, { name });
        if (!result.ok) return result.message;
        folders(sheet.id).push(result.data);
        open.cards.add(cardKey(sheet));
        open.folders.add(result.data.id);
        saveOpen();
        rerenderCard(sheet);
      },
    });
  }

  function renameFolder(sheet, folder) {
    openModal({
      title: 'Rename folder',
      okLabel: 'Save',
      fields: [{ value: folder.name, label: 'Folder name' }],
      onOk: async ([name]) => {
        if (name === folder.name) return;
        const result = await api(`${base(sheet.id)}/folders/${folder.id}`, { name });
        if (!result.ok) return result.message;
        folder.name = result.data.name;
        rerenderCard(sheet);
      },
    });
  }

  function deleteFolder(sheet, folder) {
    const n = folder.files.length;
    openModal({
      title: `Delete "${folder.name}"?`,
      detail: n ? `Its ${filesLabel(n)} will be deleted too. This can't be undone.` : '',
      okLabel: 'Delete',
      danger: true,
      onOk: async () => {
        const result = await api(`${base(sheet.id)}/folders/${folder.id}/delete`);
        if (!result.ok) return result.message;
        foldersBySheet[sheet.id] = folders(sheet.id).filter(f => f !== folder);
        open.folders.delete(folder.id);
        saveOpen();
        rerenderCard(sheet);
      },
    });
  }

  /* ---------- files ---------- */
  function fileUrl(sheet, file, download) {
    return `${base(ownerId(sheet))}/files/${file.id}${download ? '?download=1' : ''}`;
  }

  function kindBadge(fileExt) {
    return mk('span', 'arr-kind arr-kind--' + (fileExt === 'pdf' ? 'pdf' : 'msc'), fileExt === 'pdf' ? 'PDF' : 'MSC');
  }

  function buildFile(sheet, folder, file) {
    const row = mk('div', 'arr-file');
    row.appendChild(kindBadge(file.ext));
    // A PDF opens in a new tab; a MuseScore file can only be downloaded.
    if (file.ext === 'pdf') {
      const a = mk('a', 'arr-file__name arr-file__open', file.name);
      a.href = fileUrl(sheet, file);
      a.target = '_blank';
      a.rel = 'noopener';
      a.title = 'Open PDF';
      row.appendChild(a);
    } else {
      row.appendChild(mk('span', 'arr-file__name', file.name));
    }

    const actions = mk('div', 'arr-actions');
    const download = mk('a', 'arr-icon-btn', '↓');
    download.href = fileUrl(sheet, file, true);
    download.title = 'Download';
    download.setAttribute('aria-label', 'Download');
    actions.appendChild(download);
    if (!canEdit) {
      row.appendChild(actions);
      return row;
    }
    const renameBtn = iconBtn('✎', 'Rename file');
    renameBtn.addEventListener('click', () => renameFile(sheet, file));
    actions.appendChild(renameBtn);
    const deleteBtn = iconBtn('×', 'Delete file', 'arr-icon-btn--danger');
    deleteBtn.addEventListener('click', () => deleteFile(sheet, folder, file));
    actions.appendChild(deleteBtn);
    row.appendChild(actions);
    return row;
  }

  function renameFile(sheet, file) {
    openModal({
      title: 'Rename file',
      okLabel: 'Save',
      fields: [{ value: file.name, label: 'File name' }],
      onOk: async ([name]) => {
        if (name === file.name) return;
        const result = await api(`${base(sheet.id)}/files/${file.id}`, { name });
        if (!result.ok) return result.message;
        file.name = result.data.name;
        rerenderCard(sheet);
      },
    });
  }

  function deleteFile(sheet, folder, file) {
    openModal({
      title: `Delete "${file.name}"?`,
      detail: 'This can\'t be undone.',
      okLabel: 'Delete',
      danger: true,
      onOk: async () => {
        const result = await api(`${base(sheet.id)}/files/${file.id}/delete`);
        if (!result.ok) return result.message;
        folder.files = folder.files.filter(f => f !== file);
        rerenderCard(sheet);
      },
    });
  }

  /* ---------- upload ---------- */
  // Picked or dropped files: skip (saying so) any of the wrong type or too
  // large, then ask what each one is: "Trumpet", "Score", ... is the name
  // shown on the page.
  function askUpload(sheet, folder, files) {
    if (uploadStatus.has(folder.id)) { alert('Wait for the current upload to finish.'); return; }
    const skipped = [];
    const valid = files.filter(file => {
      if (!ALLOWED_EXTS.includes(ext(file.name))) { skipped.push(`"${file.name}" (only .pdf, .mscz and .mscx)`); return false; }
      if (file.size > maxMb * 1024 * 1024) { skipped.push(`"${file.name}" (over ${maxMb} MB)`); return false; }
      return true;
    });
    if (skipped.length) alert(`Skipped ${skipped.join(', ')}.`);
    if (!valid.length) return;

    openModal({
      title: valid.length === 1 ? `Upload to "${folder.name}"` : `Upload ${valid.length} files to "${folder.name}"`,
      detail: 'Name each file, e.g. Trumpet or Score.',
      okLabel: 'Upload',
      fields: valid.map(file => {
        const head = mk('span', 'arr-upload-item');
        head.appendChild(kindBadge(ext(file.name)));
        head.appendChild(mk('span', 'arr-upload-item__file', file.name));
        return {
          head,
          value: file.name.replace(/\.[^.]+$/, ''),
          label: `Name for ${file.name}`,
        };
      }),
      onOk: names => {
        upload(sheet, folder, valid.map((file, i) => ({ file, name: `${names[i]}.${ext(file.name)}` })));
      },
    });
  }

  // One at a time; a failed file is reported and the rest still go.
  async function upload(sheet, folder, items) {
    const failed = [];
    let done = 0;
    for (const { file, name } of items) {
      uploadStatus.set(folder.id, items.length > 1 ? `Uploading ${done + failed.length + 1} of ${items.length}…` : 'Uploading…');
      rerenderCard(sheet);
      const url = `${base(sheet.id)}/folders/${folder.id}/files?name=${encodeURIComponent(name)}`;
      const result = await api(url, file);
      if (!result.ok) {
        failed.push(`"${file.name}": ${result.message}`);
        if (result.status === 404) break;
        continue;
      }
      folder.files.push(result.data);
      done++;
    }
    uploadStatus.delete(folder.id);
    rerenderCard(sheet);
    if (failed.length) alert(`Couldn't upload ${failed.join('\n')}`);
  }

  window.addEventListener('beforeunload', e => {
    if (uploadStatus.size) { e.preventDefault(); e.returnValue = ''; }
  });

  window.ArrangementCards = { card: buildCard };
  if (!onPage) return;

  // Back/forward can restore the page as it was left; close the cards then too.
  window.addEventListener('pageshow', e => {
    if (e.persisted && open.cards.size) {
      open.cards.clear();
      render();
    }
  });

  render();
})();
