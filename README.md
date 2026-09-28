# leadsheets.dk

A lead sheet archive and editor, split out of the James Band intern app.
The whole site is behind a login for now (`noindex`, `robots.txt` blocks
everything).

- **Setlist** (`/setlist`): pick sheets from the archive (and your own
  sheets), put them in order (with pauses between sets), choose each one's
  key, and download one PDF: a setlist page, then every chart in that order
  and key. Keep as many named setlists as you like.
- **Lead Sheets** (`/sheets`): the archive. Anyone logged in can open a
  sheet, transpose it, follow its links and print it or save it as a PDF.
  **Customize** makes your own version of a sheet (an archive sheet or one
  of your own) to change as you like. If the original is deleted, its
  versions become sheets of their own.
- **Create New** (`/new`): the editor, and **Your Sheets**.
  - **Admin** can switch any archive sheet into Edit mode to save or
    delete it. New sheets start as drafts; the **Saved in** box (Archive /
    Draft) at the top of Edit mode moves a draft into the archive, one way
    only. A version of an archive sheet replaces its original there.
  - **Viewer** gets the full editor, but their sheets are saved only in
    their own browser. The server refuses every write from a viewer with a
    403.

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
- **Viewers:** in their browser's localStorage, since viewers share one
  password and have no accounts. These don't sync between devices, and
  clearing site data deletes them. Safari also deletes them after 7 days of
  use without a visit. **Export backup** and **Import** (on Your Sheets and
  Setlist) move them between browsers or restore them. Import merges by id,
  and of two copies the newer one wins.

Every record has a random id and `updated_at`, so giving viewers accounts
later means a folder per user and the same merge to upload their browsers.

Sheets are stored as `DATA_DIR/leadsheets/<id>.json`. The ids match the
James Band intern app's.

## Environment

| Variable | |
|---|---|
| `ADMIN_PASSWORD` | Admin login (comma-separated for several) |
| `VIEWER_PASSWORD` | Viewer login (comma-separated for several) |
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
   `https://leadsheets.dk/login`.
