/* Browser drafts: sheets made on Create New by someone who can't add to the
 * archive. They live only in this browser's localStorage -- one entry per
 * draft ("leadsheets:draft:<id>") plus an index for the drafts list
 * ("leadsheets:drafts"). Storage can be blocked or full, so every access
 * is wrapped; callers show a message when a write throws.
 */
(function () {
  const INDEX_KEY = 'leadsheets:drafts';
  const draftKey = id => `leadsheets:draft:${id}`;

  function read(key) {
    try {
      const raw = localStorage.getItem(key);
      return raw ? JSON.parse(raw) : null;
    } catch (err) {
      return null;
    }
  }

  function list() {
    const index = read(INDEX_KEY);
    return Array.isArray(index) ? index : [];
  }

  function writeIndex(index) {
    localStorage.setItem(INDEX_KEY, JSON.stringify(index));
  }

  function get(id) {
    return read(draftKey(id));
  }

  // Throws when storage is unavailable or full.
  function save(id, sheet) {
    const doc = Object.assign({}, sheet, { id, updated_at: new Date().toISOString() });
    localStorage.setItem(draftKey(id), JSON.stringify(doc));
    const entry = { id, title: doc.title || 'Untitled', artist: doc.artist || '', updated_at: doc.updated_at };
    writeIndex([entry].concat(list().filter(d => d.id !== id)));
    return doc;
  }

  function create(title, artist) {
    const id = `draft-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 6)}`;
    return save(id, { title, artist, key: '', elements: [] });
  }

  function remove(id) {
    try {
      localStorage.removeItem(draftKey(id));
      writeIndex(list().filter(d => d.id !== id));
    } catch (err) { /* nothing to remove */ }
  }

  window.leadsheetDrafts = { list, get, save, create, remove };
})();
