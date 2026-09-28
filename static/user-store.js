/* The user's own things:
 *   sheets   -- drafts made on Create New, and versions made with Customize
 *               (those carry `based_on: { id, title, source }`, source being
 *               'archive' -- the default -- or 'local' for one of these sheets)
 *   setlists -- named setlists: { name, rows }
 *
 * Viewers keep them in this browser's localStorage. The admin keeps them on
 * the server (/me/<kind>/<id>, see user_store.py), so they're the same on
 * every device; base.html sets window.LEADSHEETS_SERVER_STORE for the admin.
 * Both backends have the same methods and every method returns a Promise,
 * so the pages don't care which one they get. Records carry a random id and
 * updated_at, which is all that merging (Import, or the admin's first visit
 * uploading what this browser holds) needs.
 *
 * In the browser, each record is one entry ("leadsheets:draft:<id>",
 * "leadsheets:setlist:<id>") plus an index per kind for the lists
 * ("leadsheets:drafts", "leadsheets:setlists"). Sheets keep their old draft
 * keys, so drafts saved before setlists and versions existed still load.
 * Storage can be blocked or full: reads fall back to nothing, writes reject.
 */
(function () {
  const KIND_NAMES = ['sheets', 'setlists'];

  // What a list shows without loading every record.
  function summary(kind, doc) {
    if (kind === 'setlists') {
      return { id: doc.id, name: doc.name || '', date: doc.date || '', updated_at: doc.updated_at };
    }
    const entry = { id: doc.id, title: doc.title || 'Untitled', artist: doc.artist || '', key: doc.key || '', updated_at: doc.updated_at };
    if (doc.based_on) entry.based_on = doc.based_on;
    return entry;
  }

  function newId(kind) {
    const tag = kind === 'setlists' ? 'setlist' : 'draft';
    return `${tag}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
  }

  /* ---------- In this browser ---------- */
  const browser = (function () {
    const KINDS = {
      sheets: { index: 'leadsheets:drafts', prefix: 'leadsheets:draft:' },
      setlists: { index: 'leadsheets:setlists', prefix: 'leadsheets:setlist:' },
    };
    const VERSION_KEY = 'leadsheets:store-version';
    const VERSION = 2;

    function read(key) {
      try {
        const raw = localStorage.getItem(key);
        return raw ? JSON.parse(raw) : null;
      } catch (err) {
        return null;
      }
    }
    function write(key, value) {
      localStorage.setItem(key, JSON.stringify(value));
    }
    function kindOf(kind) {
      const k = KINDS[kind];
      if (!k) throw new Error(`Unknown kind "${kind}".`);
      return k;
    }
    function listSync(kind) {
      const index = read(kindOf(kind).index);
      return Array.isArray(index) ? index : [];
    }

    // Stores `doc` as it is (updated_at included). The index is kept newest
    // first, which also puts an imported backup's records in their places.
    function put(kind, doc) {
      const k = kindOf(kind);
      write(k.prefix + doc.id, doc);
      const index = [summary(kind, doc)].concat(listSync(kind).filter(d => d.id !== doc.id));
      index.sort((a, b) => String(b.updated_at || '').localeCompare(String(a.updated_at || '')));
      write(k.index, index);
      askToPersist();
      return doc;
    }

    // Ask the browser not to clear this site's storage when space runs low
    // (and, in Safari, after a week without a visit). It may say no; asked
    // once per page.
    let persistAsked = false;
    function askToPersist() {
      if (persistAsked) return;
      persistAsked = true;
      try {
        if (navigator.storage && navigator.storage.persist) navigator.storage.persist().catch(() => {});
      } catch (err) { /* not supported */ }
    }

    // Version 1 kept drafts with a thinner index (no key, no based_on) and a
    // single setlist under "leadsheets:setlist".
    function migrate() {
      if (read(VERSION_KEY) >= VERSION) return;
      try {
        const sheets = KINDS.sheets;
        write(sheets.index, listSync('sheets').map(entry => {
          const doc = read(sheets.prefix + entry.id);
          return doc ? summary('sheets', doc) : entry;
        }));
        const old = read('leadsheets:setlist');
        if (old && Array.isArray(old.rows) && (old.rows.length || old.name)) {
          const now = new Date().toISOString();
          put('setlists', { id: newId('setlists'), name: old.name || '', rows: old.rows, created_at: now, updated_at: now });
        }
        localStorage.removeItem('leadsheets:setlist');
        write(VERSION_KEY, VERSION);
      } catch (err) { /* storage blocked: try again next time */ }
    }
    migrate();

    return {
      async list(kind) { return listSync(kind); },
      async get(kind, id) { return read(kindOf(kind).prefix + id); },
      async save(kind, id, doc) {
        const now = new Date().toISOString();
        const existing = read(kindOf(kind).prefix + id);
        const created = (existing && existing.created_at) || doc.created_at || now;
        return put(kind, Object.assign({}, doc, { id, created_at: created, updated_at: now }));
      },
      async putAsIs(kind, doc) { return put(kind, doc); },
      async remove(kind, id) {
        const k = kindOf(kind);
        localStorage.removeItem(k.prefix + id);
        write(k.index, listSync(kind).filter(d => d.id !== id));
      },
    };
  })();

  /* ---------- On the server (the admin) ---------- */
  const server = (function () {
    const url = (kind, id, query = '') => `/me/${kind}${id ? '/' + encodeURIComponent(id) : ''}${query}`;
    async function check(resp) {
      if (!resp.ok) throw new Error((await resp.text()) || `Server error ${resp.status}`);
      return resp;
    }
    const putJson = (kind, doc, query) => fetch(url(kind, doc.id, query), {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(doc),
    }).then(check).then(r => r.json());

    return {
      async list(kind) {
        const docs = await (await check(await fetch(url(kind)))).json();
        return docs.map(doc => summary(kind, doc));
      },
      async get(kind, id) {
        const resp = await fetch(url(kind, id));
        if (resp.status === 404) return null;
        return (await check(resp)).json();
      },
      async save(kind, id, doc) { return putJson(kind, Object.assign({}, doc, { id })); },
      async putAsIs(kind, doc) { return putJson(kind, doc, '?keep_times=1'); },
      async remove(kind, id) {
        const resp = await fetch(url(kind, id), { method: 'DELETE' });
        if (resp.status !== 404) await check(resp);
      },
    };
  })();

  /* ---------- The store ---------- */
  const onServer = !!window.LEADSHEETS_SERVER_STORE;
  const backend = onServer ? server : browser;

  // Merges records in: matched by id, and of two copies the newer
  // updated_at wins. Returns how many were added or updated.
  async function mergeInto(target, data) {
    let changed = 0;
    for (const kind of KIND_NAMES) {
      const have = new Map((await target.list(kind)).map(entry => [entry.id, entry]));
      for (const doc of Array.isArray(data[kind]) ? data[kind] : []) {
        if (!doc || typeof doc.id !== 'string' || !doc.id) continue;
        const existing = have.get(doc.id);
        if (existing && String(existing.updated_at || '') >= String(doc.updated_at || '')) continue;
        await target.putAsIs(kind, doc);
        changed++;
      }
    }
    return changed;
  }

  async function exportFrom(source) {
    const out = { app: 'leadsheets.dk', version: 2, exported_at: new Date().toISOString() };
    for (const kind of KIND_NAMES) {
      const docs = await Promise.all((await source.list(kind)).map(entry => source.get(kind, entry.id)));
      out[kind] = docs.filter(Boolean);
    }
    return out;
  }

  // The admin's first visit after their things moved to the server copies
  // up whatever this browser holds, once. The browser's copies are left be.
  const UPLOADED_KEY = 'leadsheets:uploaded-to-server';
  async function uploadBrowserRecords() {
    try {
      if (localStorage.getItem(UPLOADED_KEY)) return;
      await mergeInto(server, await exportFrom(browser));
      localStorage.setItem(UPLOADED_KEY, new Date().toISOString());
    } catch (err) { /* storage blocked or the server failed: try again next time */ }
  }
  const ready = onServer ? uploadBrowserRecords() : Promise.resolve();

  // Versions whose original is gone -- deleted from the archive, or one of
  // the user's own sheets deleted -- become sheets of their own. Stored with
  // their timestamps: it isn't an edit. Without archiveIds only the user's
  // own originals are checked.
  async function forgetLostOriginals(archiveIds) {
    const inArchive = new Set(archiveIds || []);
    const sheets = await backend.list('sheets');
    const own = new Set(sheets.map(s => s.id));
    for (const entry of sheets) {
      const b = entry.based_on;
      if (!b) continue;
      const found = b.source === 'local' ? own.has(b.id) : archiveIds ? inArchive.has(b.id) : true;
      if (found) continue;
      const doc = await backend.get('sheets', entry.id);
      if (!doc) continue;
      delete doc.based_on;
      await backend.putAsIs('sheets', doc);
    }
  }

  const store = {
    newId,
    // True when the user's things are on the server, not in this browser.
    onServer,

    async list(kind) { await ready; return backend.list(kind); },
    async get(kind, id) { await ready; return backend.get(kind, id); },

    // Saves `doc` under `id`, stamping updated_at (and created_at the first
    // time). Rejects when it can't be stored.
    async save(kind, id, doc) { await ready; return backend.save(kind, id, doc); },

    async remove(kind, id) {
      await ready;
      await backend.remove(kind, id);
      if (kind === 'sheets') await forgetLostOriginals(null);
    },

    // Call with the archive's sheet ids, on a page that has them.
    async forgetLostOriginals(archiveIds) { await ready; return forgetLostOriginals(archiveIds); },

    // Everything, as one object for a backup file.
    async exportAll() { await ready; return exportFrom(backend); },

    async importAll(data) {
      if (!data || typeof data !== 'object') throw new Error('Not a leadsheets.dk backup.');
      await ready;
      return mergeInto(backend, data);
    },
  };

  /* ---------- Backup buttons ---------- */
  // Wires an Export and an Import button (Import opens a file picker).
  // `onImported` runs after a successful import, e.g. to redraw a list.
  store.wireBackupButtons = function (exportBtn, importBtn, onImported) {
    exportBtn.addEventListener('click', async () => {
      const data = await store.exportAll();
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `leadsheets-backup-${new Date().toISOString().slice(0, 10)}.json`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 60000);
    });

    const picker = document.createElement('input');
    picker.type = 'file';
    picker.accept = 'application/json,.json';
    picker.hidden = true;
    document.body.appendChild(picker);
    importBtn.addEventListener('click', () => picker.click());
    picker.addEventListener('change', async () => {
      const file = picker.files[0];
      picker.value = '';
      if (!file) return;
      try {
        const changed = await store.importAll(JSON.parse(await file.text()));
        alert(changed ? `Imported ${changed} item${changed === 1 ? '' : 's'}.` : 'Everything in that backup is already here.');
        if (onImported) onImported();
      } catch (err) {
        alert(`Couldn't import that file: ${err && err.message ? err.message : err}`);
      }
    });
  };

  window.leadsheetStore = store;
})();
