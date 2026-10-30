# leadsheets.dk

A lead sheet editor and setlist tool, split out of the James Band intern
app. The tool is public and needs no login. The archive of sheets is
private: only the admin sees it. The admin logs in at `/login`, which
nothing links to.

A **viewer** logs in there too, with the viewer password, and sees what the
admin sees, read-only: the archive and the admin's drafts in Practice mode
(no Customize, drafts at `/drafts/{id}`) and the Arrangements tab, where
files can be opened and downloaded. Lead Sheets lists the admin's Drafts,
the Archive, then the viewer's own sheets (in their browser, as for any
visitor). Every write is the admin's alone.

- **Setlist** (`/setlist`): your setlists as gig cards (name, date, number
  of songs), upcoming and past. **+** opens the editor (`/setlist/edit`):
  pick from your own sheets (and, for the admin, the archive), put them in
  order (with pauses between sets) and choose each one's key. A card opens
  the setlist as a gig (`/setlist/view`), read-only: a card per song with
  its chart, opened in Practice mode in the setlist's key (`?key=`), and
  its Arrangements files. Both download one PDF: a setlist page, then every
  chart in that order and key.
  - **Share links** (admin): **Link** on a gig page makes a secret link,
    `/gig/<token>`, for the band. Anyone with it, logged in or not, sees
    that gig read-only, without the tabs: the charts in the setlist's key,
    the Arrangements files of its songs and the PDF. Nothing outside the
    setlist is served, and an unknown token gets a 404. **New link**
    replaces the old link and **Turn off** removes it; either way the old
    link stops working, and so does deleting the setlist. Tokens live in
    `DATA_DIR/shares.json` (`shares.py`). `/gig/` pages send `noindex` and
    `no-referrer`.
- **Lead Sheets** (`/sheets`): your own sheets, with **Export backup** and
  **Import**. Open one to transpose it, follow its links and print it or
  save it as a PDF. **Customize** makes your own version of a sheet to
  change as you like. If the original is deleted, its versions become
  sheets of their own. The admin also sees the **Archive** here. To
  everyone else, `/sheets/{id}` and `/sheets/{id}/json` return 404.
- **Arrangements** (`/arrangements`, admin; read-only for a viewer): sheet music for each of
  the admin's sheets, drafts and archive. Every sheet has a card (there is
  no other way to make one): it links to the lead sheet, then holds named,
  collapsible folders (e.g. "MatRevy 26 - 0.5 op") of PDF and MuseScore
  files, up to 15 MB each. A draft's arrangements follow it into the
  archive (a version's are added to its original's), and deleting a sheet
  deletes its arrangements.
- **Create New** (`/draft?mode=edit#new`): the editor, straight away.
  (`/new` redirects there.)
  - **Admin** can switch any archive sheet into Edit mode to save or
    delete it. New sheets start as drafts; the **Saved in** box (Archive /
    Draft) at the top of Edit mode moves a draft into the archive, one way
    only. A version of an archive sheet replaces its original there.
  - **Everyone else** gets the full editor, but their sheets are saved only
    in their own browser. The server refuses every write to the archive
    with a 403.

- **API** (`/api/sheets`, `/api/sheets/{id}`): read-only JSON for other
  sites, such as the James Band intern app. Send
  `Authorization: Bearer <token>`. It is meant to be called server to
  server, so it has no CORS.

### Your own sheets and setlists

Everyone's own sheets (drafts and versions made with Customize) and setlists
go through `static/user-store.js`, which has two backends with the same
methods:

- **Admin:** on the server, as `DATA_DIR/users/admin/{sheets,setlists}/<id>.json`
  (`user_store.py`, served at `/me/<kind>/<id>`), so they're the same on
  every device. The first visit after this change copies up whatever the
  admin's browser held.
- **Everyone else:** in their browser's localStorage, since they have no
  accounts. These don't sync between devices, and
  clearing site data deletes them. Safari also deletes them after 7 days of
  use without a visit. **Export backup** and **Import** (on Lead Sheets and
  Setlist) move them between browsers or restore them. Import merges by id,
  and of two copies the newer one wins.

Every record has a random id and `updated_at`, so giving visitors accounts
later means a folder per user and the same merge to upload their browsers.

Sheets are stored as `DATA_DIR/leadsheets/<id>.json`.
Their arrangements are in `DATA_DIR/arrangements/<id>/`: an `index.json` of
folders and files, and the files themselves. The ids match the
James Band intern app's.

## Environment

| Variable | |
|---|---|
| `ADMIN_PASSWORD` | Admin login (comma-separated for several) |
| `VIEWER_PASSWORD` | Optional read-only viewer login (comma-separated for several) |
| `SESSION_SECRET` | A long random string: `python3 -c "import secrets; print(secrets.token_hex(32))"` |
| `API_TOKEN` | Tokens for the read API, comma-separated, one per consumer |
| `DATA_DIR` | `/data` in production. Defaults to `./data` |
| `SECURE_COOKIES` | `1` in production (HTTPS-only session cookie) |

## Local development

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
ADMIN_PASSWORD=a VIEWER_PASSWORD=v SESSION_SECRET=s API_TOKEN=t \
  .venv/bin/uvicorn app:app --reload --port 10001
```

## Going live (one-time)

1. **GitHub:** create `carljarner/leadsheets` and push this folder.
2. **DNS (Simply.com):** add A records for `leadsheets.dk` and `www` pointing
   to `web-1`'s IPv4 address. Add AAAA records too if you want IPv6.
3. **Server:** create the data folder and copy the intern sheets across:
   ```bash
   ssh web-1 'mkdir -p /srv/leadsheets/data/leadsheets && cp -a /srv/jamesband/data/leadsheets/. /srv/leadsheets/data/leadsheets/'
   ```
4. **Deploy key:** in Coolify, **Keys & Tokens → Private Keys → + Add**, generate
   a key named `leadsheets-deploy` and copy its public key. On GitHub, add it
   under the repo's **Settings → Deploy keys**, read-only (write access off).
5. **Coolify:** in the project, choose **+ New → Private Repository (with Deploy Key)**,
   key `leadsheets-deploy`, repo `git@github.com:carljarner/leadsheets.git`,
   branch `main`.
   - Build Pack: Dockerfile. Ports Exposes: `10000`.
   - Persistent Storage: directory mount from `/srv/leadsheets/data` to `/data`.
   - Environment variables: the ones in the table above, with
     `DATA_DIR=/data` and `SECURE_COOKIES=1`.
   - Domains: `https://leadsheets.dk,https://www.leadsheets.dk`.
6. **Auto-deploy:** a deploy key gets no push webhook by itself. Copy the URL
   and secret from the resource's **Webhooks** tab into the repo's
   **Settings → Webhooks** on GitHub (content type `application/json`).
7. **Backups:** the nightly restic job (`/usr/local/bin/backup-srv`) backs up
   all of `/srv`, so `/srv/leadsheets` is included. Run it once and check with
   `restic ls latest /srv/leadsheets`. Add an UptimeRobot check for
   `https://leadsheets.dk/sheets`.
