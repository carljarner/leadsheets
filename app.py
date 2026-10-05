import hashlib
import json
import os
import secrets
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

import sheets
import user_store


def _secrets(name: str) -> list[str]:
    """A comma-separated env var as a list, e.g. ADMIN_PASSWORD="a,b" lets
    either password in."""
    return [p.strip() for p in os.environ.get(name, "").split(",") if p.strip()]


ADMIN_PASSWORDS = _secrets("ADMIN_PASSWORD")
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


def require_admin(request: Request) -> None:
    if not is_admin(request):
        raise HTTPException(status_code=403, detail="Only the admin can change the archive.")


def _archive(request: Request) -> list[dict]:
    """The archive's sheets, or none: only the admin may see the archive."""
    return sheets.list_leadsheets() if is_admin(request) else []


def _archive_sheet(request: Request, leadsheet_id: str) -> dict:
    # A 404 for everyone else, so they can't tell which sheets exist.
    if not is_admin(request):
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
    # archive is the admin's alone.
    archive = _archive(request)
    return templates.TemplateResponse(
        request,
        "sheets.html",
        {"sheets": archive, "archive_ids_json": _json([s["id"] for s in archive])},
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


@app.get("/sheets/{leadsheet_id}", response_class=HTMLResponse)
async def sheet_page(request: Request, leadsheet_id: str):
    sheet = _archive_sheet(request, leadsheet_id)
    config = {
        "canEdit": True,
        "canCopy": True,
        # The Saved in box.
        "canMove": True,
        "storage": "server",
        "saveUrl": f"/sheets/{sheet['id']}",
        "deleteUrl": f"/sheets/{sheet['id']}/delete",
        "afterDeleteUrl": "/sheets",
    }
    return templates.TemplateResponse(
        request,
        "sheet.html",
        {"sheet": sheet, "sheet_json": _json(sheet), "config_json": _json(config)},
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
    return RedirectResponse("/sheets", status_code=303)


@app.get("/setlist", response_class=HTMLResponse)
async def setlist_page(request: Request):
    # The setlists live in the browser (localStorage), and so do the user's
    # own sheets; the page gets the archive to pick from and fetches each
    # archive sheet when making the PDF.
    catalog = [
        {"id": s["id"], "title": s["title"], "artist": s.get("artist", ""), "key": s.get("key", "")}
        for s in _archive(request)
    ]
    sheet = {"id": "", "title": "", "artist": "", "key": "", "elements": []}
    return templates.TemplateResponse(
        request,
        "setlist.html",
        {"sheets_json": _json(catalog), "sheet_json": _json(sheet)},
    )


@app.get("/new")
async def new_page():
    # Create New used to be a page of its own; it now opens the editor.
    return RedirectResponse("/draft?mode=edit#new")


@app.get("/draft", response_class=HTMLResponse)
async def draft_page(request: Request):
    # The sheet itself lives in the browser (localStorage); the page loads it
    # from the id in the URL's #fragment.
    # The admin may move it into the archive (a version replaces its original).
    config = {"canEdit": True, "canMove": is_admin(request), "storage": "local", "afterDeleteUrl": "/sheets"}
    # The archive's ids, so versions whose original was deleted from it (or,
    # for everyone but the admin, all versions of archive sheets) turn into
    # sheets of their own.
    archive_ids = [s["id"] for s in _archive(request)]
    return templates.TemplateResponse(
        request, "draft.html", {"config_json": _json(config), "archive_ids_json": _json(archive_ids)}
    )


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
