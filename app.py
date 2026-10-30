import hashlib
import json
import os
import secrets
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

import arrangements
import sheets
import user_store


def _secrets(name: str) -> list[str]:
    """A comma-separated env var as a list, e.g. ADMIN_PASSWORD="a,b" lets
    either password in."""
    return [p.strip() for p in os.environ.get(name, "").split(",") if p.strip()]


ADMIN_PASSWORDS = _secrets("ADMIN_PASSWORD")
# Optional: sees what the admin sees (the archive, the admin's drafts and
# their Arrangements), read-only.
VIEWER_PASSWORDS = _secrets("VIEWER_PASSWORD")
# One token per site that reads the archive (e.g. the James Band intern app).
API_TOKENS = _secrets("API_TOKEN")
SESSION_SECRET = os.environ["SESSION_SECRET"]
SECURE_COOKIES = os.environ.get("SECURE_COOKIES") == "1"
if not ADMIN_PASSWORDS:
    raise RuntimeError("ADMIN_PASSWORD must be set.")

MAX_BODY = 1_000_000


def _static_version() -> str:
    """Short hash of everything under static/, so URLs like
    /static/style.css?v=<hash> change whenever a deploy changes a file."""
    digest = hashlib.sha1()
    for path in sorted(Path("static").rglob("*")):
        if path.is_file():
            digest.update(str(path).encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()[:10]


class VersionedStaticFiles(StaticFiles):
    """Versioned requests (?v=...) are cached for good; the URL changes on
    the next deploy. Unversioned ones keep the default revalidation."""

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        if b"v=" in scope.get("query_string", b""):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response


app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", VersionedStaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")
templates.env.globals["static_version"] = _static_version()


def _matches(value: str, candidates: list[str]) -> bool:
    return any(secrets.compare_digest(value.encode(), c.encode()) for c in candidates)


def role(request: Request) -> str | None:
    return request.session.get("role")


def is_admin(request: Request) -> bool:
    return role(request) == "admin"


def can_view(request: Request) -> bool:
    """The admin, or a viewer: may see the archive, the admin's drafts and
    their Arrangements. Only the admin may change them."""
    return role(request) in ("admin", "viewer")


def require_admin(request: Request) -> None:
    if not is_admin(request):
        raise HTTPException(status_code=403, detail="Only the admin can change the archive.")


def _archive(request: Request) -> list[dict]:
    """The archive's sheets, or none: only the admin and viewers may see the archive."""
    return sheets.list_leadsheets() if can_view(request) else []


def _archive_sheet(request: Request, leadsheet_id: str) -> dict:
    # A 404 for everyone else, so they can't tell which sheets exist.
    if not can_view(request):
        raise HTTPException(status_code=404)
    try:
        return sheets.get_leadsheet(leadsheet_id)
    except KeyError:
        raise HTTPException(status_code=404)


def _json(data) -> str:
    """JSON safe to put inside a <script> tag."""
    return json.dumps(data).replace("</", "<\\/")


@app.middleware("http")
async def hide_login(request: Request, call_next):
    # The site is public; only the admin's login page stays out of search.
    response = await call_next(request)
    if request.url.path == "/login":
        response.headers["X-Robots-Tag"] = "noindex, nofollow"
    return response


app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET,
    https_only=SECURE_COOKIES,
    max_age=60 * 60 * 24 * 90,
)


@app.get("/robots.txt", response_class=PlainTextResponse)
async def robots():
    return "User-agent: *\nDisallow: /login\n"


@app.get("/login", response_class=HTMLResponse)
async def login_form(request: Request):
    return templates.TemplateResponse(request, "login.html", {"error": None})


@app.post("/login")
async def login(request: Request, password: str = Form(...)):
    if _matches(password, ADMIN_PASSWORDS):
        request.session["role"] = "admin"
    elif _matches(password, VIEWER_PASSWORDS):
        request.session["role"] = "viewer"
    else:
        return templates.TemplateResponse(
            request, "login.html", {"error": "Wrong password"}, status_code=401
        )
    return RedirectResponse("/sheets", status_code=303)


@app.post("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/sheets", status_code=303)


@app.get("/")
async def home():
    return RedirectResponse("/sheets")


@app.get("/sheets", response_class=HTMLResponse)
async def sheets_page(request: Request):
    # Everyone's own sheets are drawn by the page (static/user-store.js); the
    # archive is for the admin and viewers, and viewers see the admin's drafts.
    archive = _archive(request)
    # Versions live under their song (its Versions box), not as cards.
    shared_drafts = [d for d in _drafts() if not d.get("based_on")] if role(request) == "viewer" else []
    return templates.TemplateResponse(
        request,
        "sheets.html",
        {
            "sheets": archive,
            "shared_drafts": shared_drafts,
            "archive_ids_json": _json([s["id"] for s in archive]),
        },
    )


async def _read_json(request: Request) -> dict:
    body = await request.body()
    if len(body) > MAX_BODY:
        raise ValueError("The sheet is too large to save.")
    doc = json.loads(body)
    if not isinstance(doc, dict):
        raise ValueError("Malformed lead sheet document.")
    return doc


@app.post("/sheets")
async def sheets_create(request: Request):
    require_admin(request)
    try:
        return sheets.add_leadsheet(await _read_json(request))
    except (ValueError, TypeError) as exc:
        return Response(content=str(exc), status_code=400)


# Where a sheet page's back link goes: the Lead Sheets tab, or -- for a
# chart opened from a setlist's gig page -- that gig.
_SHEETS_BACK = {"href": "/sheets", "label": "Lead Sheets"}


def _setlist_back(request: Request) -> dict:
    setlist_id = request.query_params.get("from", "")
    href = f"/setlist/view#{setlist_id}" if sheets.ID_RE.fullmatch(setlist_id) else "/setlist"
    return {"href": href, "label": "Setlist"}


@app.get("/sheets/{leadsheet_id}", response_class=HTMLResponse)
async def sheet_page(request: Request, leadsheet_id: str):
    return _sheet_page(request, leadsheet_id, _SHEETS_BACK)


def _sheet_page(request: Request, leadsheet_id: str, back: dict) -> Response:
    sheet = _archive_sheet(request, leadsheet_id)
    admin = is_admin(request)
    # A viewer gets Practice mode only, without Customize.
    config = {
        "canEdit": admin,
        "canCopy": admin,
        # The Saved in box.
        "canMove": admin,
        "storage": "server",
        "saveUrl": f"/sheets/{sheet['id']}",
        "deleteUrl": f"/sheets/{sheet['id']}/delete",
        "afterDeleteUrl": "/sheets",
    }
    if admin:
        # Its Arrangements go with it, so the delete question mentions them.
        config["arrangementFiles"] = arrangements.file_count(sheet["id"])
    return templates.TemplateResponse(
        request,
        "sheet.html",
        {"sheet": sheet, "sheet_json": _json(sheet), "config_json": _json(config), "back": back},
    )


@app.get("/sheets/{leadsheet_id}/json")
async def sheet_json(request: Request, leadsheet_id: str):
    # The whole sheet, for the setlist page to draw into its PDF.
    sheet = _archive_sheet(request, leadsheet_id)
    return JSONResponse(sheet, headers={"Cache-Control": "no-store"})


@app.post("/sheets/{leadsheet_id}")
async def sheet_save(leadsheet_id: str, request: Request):
    require_admin(request)
    try:
        sheets.update_leadsheet(leadsheet_id, await _read_json(request))
    except KeyError:
        raise HTTPException(status_code=404)
    except (ValueError, TypeError) as exc:
        return Response(content=str(exc), status_code=400)
    return Response(status_code=204)


@app.post("/sheets/{leadsheet_id}/delete")
async def sheet_delete(leadsheet_id: str, request: Request):
    require_admin(request)
    try:
        sheets.delete_leadsheet(leadsheet_id)
    except KeyError:
        raise HTTPException(status_code=404)
    arrangements.delete_sheet(leadsheet_id)
    return RedirectResponse("/sheets", status_code=303)


# The setlists are the user's own (static/user-store.js: in the browser, or
# on the server for the admin), so these pages draw them themselves: the
# setlist's id is in the URL's #fragment.

def _catalog(request: Request) -> list[dict]:
    """The archive, as the setlist pages pick from and look up songs."""
    return [
        {"id": s["id"], "title": s["title"], "artist": s.get("artist", ""), "key": s.get("key", "")}
        for s in _archive(request)
    ]


# What the editor draws off screen before the first sheet of a PDF.
_BLANK_SHEET = {"id": "", "title": "", "artist": "", "key": "", "elements": []}


@app.get("/setlist", response_class=HTMLResponse)
async def setlists_page(request: Request):
    # The setlists as gig cards, upcoming and past.
    return templates.TemplateResponse(request, "setlists.html", {})


@app.get("/setlist/edit", response_class=HTMLResponse)
async def setlist_edit_page(request: Request):
    # Gets the archive to pick from and fetches each archive sheet when
    # making the PDF.
    return templates.TemplateResponse(
        request,
        "setlist.html",
        {"sheets_json": _json(_catalog(request)), "sheet_json": _json(_BLANK_SHEET)},
    )


@app.get("/setlist/view", response_class=HTMLResponse)
async def setlist_view_page(request: Request):
    # The setlist as a gig, read-only: a card per song with its chart (in
    # the setlist's key) and, for the admin and viewers, its Arrangements.
    return templates.TemplateResponse(
        request,
        "setlist_view.html",
        {
            "sheets_json": _json(_catalog(request)),
            "sheet_json": _json(_BLANK_SHEET),
            "folders_json": _json(arrangements.list_all() if can_view(request) else {}),
        },
    )


# A chart opened from a gig page: the sheet page as ever, but in the Setlist
# tab and with a link back to the gig (?from=<setlist id>).
@app.get("/setlist/sheets/{leadsheet_id}", response_class=HTMLResponse)
async def setlist_sheet_page(request: Request, leadsheet_id: str):
    return _sheet_page(request, leadsheet_id, _setlist_back(request))


@app.get("/setlist/draft", response_class=HTMLResponse)
async def setlist_draft_page(request: Request):
    return _draft_page(request, _setlist_back(request))


@app.get("/new")
async def new_page():
    # Create New used to be a page of its own; it now opens the editor.
    return RedirectResponse("/draft?mode=edit#new")


@app.get("/draft", response_class=HTMLResponse)
async def draft_page(request: Request):
    return _draft_page(request, _SHEETS_BACK)


def _draft_page(request: Request, back: dict) -> Response:
    # The sheet itself lives in the browser (localStorage); the page loads it
    # from the id in the URL's #fragment.
    # The admin may move it into the archive (a version replaces its original).
    config = {"canEdit": True, "canMove": is_admin(request), "storage": "local", "afterDeleteUrl": "/sheets"}
    if is_admin(request):
        # The admin's drafts have Arrangements, deleted with them: files per draft id.
        config["arrangementFiles"] = {
            sheet_id: sum(len(f["files"]) for f in folders)
            for sheet_id, folders in arrangements.list_all().items()
        }
    # The archive's ids, so versions whose original was deleted from it (or,
    # for everyone but the admin, all versions of archive sheets) turn into
    # sheets of their own.
    archive_ids = [s["id"] for s in _archive(request)]
    return templates.TemplateResponse(
        request,
        "draft.html",
        {"config_json": _json(config), "archive_ids_json": _json(archive_ids), "back": back},
    )


@app.get("/drafts/{draft_id}", response_class=HTMLResponse)
async def shared_draft_page(request: Request, draft_id: str):
    # One of the admin's drafts, for a viewer: Practice mode only. (The admin
    # edits it at /draft#<id>.)
    if not can_view(request):
        raise HTTPException(status_code=404)
    try:
        sheet = user_store.get_doc(ADMIN_USER, "sheets", draft_id)
    except KeyError:
        raise HTTPException(status_code=404)
    sheet = dict(sheet, title=sheet.get("title") or "Untitled")
    config = {"canEdit": False, "canCopy": False, "canMove": False, "storage": "server", "afterDeleteUrl": "/sheets"}
    return templates.TemplateResponse(
        request,
        "sheet.html",
        {"sheet": sheet, "sheet_json": _json(sheet), "config_json": _json(config), "back": _SHEETS_BACK},
    )


# ── Arrangements: sheet music for each of the admin's sheets ──────────
# The admin's and viewers' (read-only), like the archive: a 404 for everyone
# else. Every archive sheet and draft has a card; its files sit in named
# folders (arrangements.py).

def _drafts() -> list[dict]:
    """The admin's drafts, by title."""
    drafts = user_store.list_docs(ADMIN_USER, "sheets")
    return sorted(drafts, key=lambda d: (d.get("title") or "Untitled").casefold())


def _arrangement_sheet(request: Request, sheet_id: str, write: bool = False) -> None:
    """Only the admin's sheets, archive or draft, have arrangements. Viewers
    may open and download them; only the admin may change them."""
    if not can_view(request):
        raise HTTPException(status_code=404)
    if write:
        require_admin(request)
    try:
        sheets.get_leadsheet(sheet_id)
        return
    except KeyError:
        pass
    try:
        user_store.get_doc(ADMIN_USER, "sheets", sheet_id)
    except KeyError:
        raise HTTPException(status_code=404)


@app.get("/arrangements", response_class=HTMLResponse)
async def arrangements_page(request: Request):
    if not can_view(request):
        raise HTTPException(status_code=404)

    # Versions live under their song: no card of their own, but a link on
    # the song's card, below its chart.
    drafts = _drafts()
    versions: dict[tuple[str, str], list[dict]] = {}
    for d in drafts:
        b = d.get("based_on")
        if b:
            title = d.get("title") or "Untitled"
            name = str(d.get("version_name") or "").strip()
            versions.setdefault((b.get("source") or "archive", b.get("id")), []).append(
                {"id": d["id"], "title": f"{title} ({name})" if name else title}
            )

    def entry(sheet: dict, draft: bool) -> dict:
        return {
            "id": sheet["id"],
            "title": sheet.get("title") or "Untitled",
            "artist": sheet.get("artist", ""),
            "draft": draft,
            "versions": versions.get(("local" if draft else "archive", sheet["id"]), []),
        }

    catalog = [entry(d, True) for d in drafts if not d.get("based_on")] + [entry(s, False) for s in _archive(request)]
    return templates.TemplateResponse(
        request,
        "arrangements.html",
        {
            "sheets_json": _json(catalog),
            "folders_json": _json(arrangements.list_all()),
            "max_mb": arrangements.MAX_FILE_BYTES // (1024 * 1024),
            "can_edit": is_admin(request),
        },
    )


def _arrangement_error(exc: Exception) -> Response:
    return Response(content=str(exc), status_code=400)


@app.post("/arrangements/{sheet_id}/move")
async def arrangement_move(sheet_id: str, request: Request):
    # A draft moved into the archive (as {"to": <archive id>}) takes its
    # arrangements along; called before the draft itself is deleted.
    require_admin(request)
    try:
        user_store.get_doc(ADMIN_USER, "sheets", sheet_id)
        target = sheets.get_leadsheet(str((await _read_json(request)).get("to", "")))["id"]
    except KeyError:
        raise HTTPException(status_code=404)
    except (ValueError, TypeError) as exc:
        return _arrangement_error(exc)
    arrangements.move(sheet_id, target)
    return Response(status_code=204)


@app.post("/arrangements/{sheet_id}/folders")
async def arrangement_folder_add(sheet_id: str, request: Request):
    _arrangement_sheet(request, sheet_id, write=True)
    try:
        return arrangements.add_folder(sheet_id, (await _read_json(request)).get("name"))
    except (ValueError, TypeError) as exc:
        return _arrangement_error(exc)


@app.post("/arrangements/{sheet_id}/folders/{folder_id}")
async def arrangement_folder_rename(sheet_id: str, folder_id: str, request: Request):
    _arrangement_sheet(request, sheet_id, write=True)
    try:
        return arrangements.rename_folder(sheet_id, folder_id, (await _read_json(request)).get("name"))
    except KeyError:
        raise HTTPException(status_code=404)
    except (ValueError, TypeError) as exc:
        return _arrangement_error(exc)


@app.post("/arrangements/{sheet_id}/folders/{folder_id}/delete")
async def arrangement_folder_delete(sheet_id: str, folder_id: str, request: Request):
    _arrangement_sheet(request, sheet_id, write=True)
    try:
        arrangements.delete_folder(sheet_id, folder_id)
    except KeyError:
        raise HTTPException(status_code=404)
    return Response(status_code=204)


@app.post("/arrangements/{sheet_id}/folders/{folder_id}/files")
async def arrangement_file_add(sheet_id: str, folder_id: str, request: Request):
    # The file is the raw body; its name (with the extension) is ?name=.
    _arrangement_sheet(request, sheet_id, write=True)
    too_large = f"The file is too large (max {arrangements.MAX_FILE_BYTES // (1024 * 1024)} MB)."
    try:
        declared = int(request.headers.get("content-length", "0"))
    except ValueError:
        declared = 0
    if declared > arrangements.MAX_FILE_BYTES:
        return Response(content=too_large, status_code=413)
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > arrangements.MAX_FILE_BYTES:
            return Response(content=too_large, status_code=413)
    try:
        return arrangements.add_file(sheet_id, folder_id, request.query_params.get("name", ""), bytes(data))
    except KeyError:
        raise HTTPException(status_code=404)
    except ValueError as exc:
        return _arrangement_error(exc)


@app.get("/arrangements/{sheet_id}/files/{file_id}")
async def arrangement_file_get(sheet_id: str, file_id: str, request: Request):
    _arrangement_sheet(request, sheet_id)
    try:
        record, path = arrangements.get_file(sheet_id, file_id)
    except KeyError:
        raise HTTPException(status_code=404)
    filename = f"{record['name']}.{record['ext']}"
    # PDFs open in the browser; MuseScore files (and ?download=1) are saved.
    inline = record["ext"] == "pdf" and request.query_params.get("download") != "1"
    fallback = filename.encode("ascii", "replace").decode().replace('"', "'").replace("?", "_")
    disposition = f"{'inline' if inline else 'attachment'}; filename=\"{fallback}\"; filename*=UTF-8''{quote(filename)}"
    return FileResponse(
        path,
        media_type="application/pdf" if record["ext"] == "pdf" else "application/octet-stream",
        headers={"Content-Disposition": disposition, "Cache-Control": "no-store"},
    )


@app.post("/arrangements/{sheet_id}/files/{file_id}")
async def arrangement_file_rename(sheet_id: str, file_id: str, request: Request):
    _arrangement_sheet(request, sheet_id, write=True)
    try:
        return arrangements.rename_file(sheet_id, file_id, (await _read_json(request)).get("name"))
    except KeyError:
        raise HTTPException(status_code=404)
    except (ValueError, TypeError) as exc:
        return _arrangement_error(exc)


@app.post("/arrangements/{sheet_id}/files/{file_id}/delete")
async def arrangement_file_delete(sheet_id: str, file_id: str, request: Request):
    _arrangement_sheet(request, sheet_id, write=True)
    try:
        arrangements.delete_file(sheet_id, file_id)
    except KeyError:
        raise HTTPException(status_code=404)
    return Response(status_code=204)


# ── The admin's own sheets and setlists ───────────────────────────────
# Everyone else keeps theirs in the browser; the admin's are kept here, so they're
# the same on every device. static/user-store.js calls these.
ADMIN_USER = "admin"


@app.get("/me/{kind}")
async def me_list(kind: str, request: Request):
    require_admin(request)
    try:
        docs = user_store.list_docs(ADMIN_USER, kind)
    except KeyError:
        raise HTTPException(status_code=404)
    return JSONResponse(docs, headers={"Cache-Control": "no-store"})


@app.get("/me/{kind}/{doc_id}")
async def me_get(kind: str, doc_id: str, request: Request):
    require_admin(request)
    try:
        doc = user_store.get_doc(ADMIN_USER, kind, doc_id)
    except KeyError:
        raise HTTPException(status_code=404)
    return JSONResponse(doc, headers={"Cache-Control": "no-store"})


@app.put("/me/{kind}/{doc_id}")
async def me_save(kind: str, doc_id: str, request: Request):
    require_admin(request)
    try:
        doc = await _read_json(request)
        return user_store.save_doc(
            ADMIN_USER, kind, doc_id, doc, keep_times=request.query_params.get("keep_times") == "1"
        )
    except KeyError:
        raise HTTPException(status_code=404)
    except (ValueError, TypeError) as exc:
        return Response(content=str(exc), status_code=400)


@app.delete("/me/{kind}/{doc_id}")
async def me_delete(kind: str, doc_id: str, request: Request):
    require_admin(request)
    try:
        user_store.delete_doc(ADMIN_USER, kind, doc_id)
    except KeyError:
        raise HTTPException(status_code=404)
    if kind == "sheets":
        arrangements.delete_sheet(doc_id)
    return Response(status_code=204)


# ── Read API for other sites (e.g. the James Band intern app) ─────────
# Server-to-server only: a bearer token from API_TOKEN, no cookies, no CORS.

def _require_token(request: Request) -> None:
    header = request.headers.get("authorization", "")
    token = header[7:].strip() if header.lower().startswith("bearer ") else ""
    if not token or not _matches(token, API_TOKENS):
        raise HTTPException(status_code=401, detail="Missing or wrong API token.")


@app.get("/api/sheets")
async def api_sheets(request: Request):
    _require_token(request)
    return JSONResponse(
        [
            {
                "id": s["id"],
                "title": s["title"],
                "artist": s.get("artist", ""),
                "key": s.get("key", ""),
                "updated_at": s.get("updated_at", ""),
            }
            for s in sheets.list_leadsheets()
        ],
        headers={"Cache-Control": "no-store"},
    )


@app.get("/api/sheets/{leadsheet_id}")
async def api_sheet(request: Request, leadsheet_id: str):
    _require_token(request)
    try:
        sheet = sheets.get_leadsheet(leadsheet_id)
    except KeyError:
        raise HTTPException(status_code=404)
    return JSONResponse(sheet, headers={"Cache-Control": "no-store"})
