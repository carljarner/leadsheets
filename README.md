# leadsheets.dk

A lead sheet archive and editor, split out of the James Band intern app.
The whole site is behind a login for now (`noindex`, `robots.txt` blocks
everything).

- **Setlist** (`/setlist`): pick sheets from the archive, put them in
  order (with pauses between sets), choose each one's key, and download
  one PDF: a setlist page, then every chart in that order and key. The
  setlist is kept in this browser only (localStorage).
- **Lead Sheets** (`/sheets`): the archive. Anyone logged in can open a
  sheet, transpose it, follow its links and print it or save it as a PDF.
- **Create New** (`/new`): the editor.
  - **Admin** creates sheets in the archive and can switch any sheet into
    Edit mode to save or delete it.
  - **Viewer** gets the full editor, but their sheets are saved only in
    their own browser (localStorage, see `static/drafts.js`). The server
    refuses every write from a viewer with a 403.
- **API** (`/api/sheets`, `/api/sheets/{id}`): read-only JSON for other
  sites, such as the James Band intern app. Send
  `Authorization: Bearer <token>`. It is meant to be called server to
  server, so it has no CORS.

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
4. **Coolify:** in the project, choose **+ New → Private Repository (GitHub App)**,
   repo `leadsheets`, branch `main`.
   - Build Pack: Dockerfile. Ports Exposes: `10000`.
   - Persistent Storage: directory mount from `/srv/leadsheets/data` to `/data`.
   - Environment variables: the ones in the table above, with
     `DATA_DIR=/data` and `SECURE_COOKIES=1`.
   - Domains: `https://leadsheets.dk,https://www.leadsheets.dk`.
5. **Backups:** add `/srv/leadsheets` to the restic backup paths, and add an
   UptimeRobot check for `https://leadsheets.dk/login`.
