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


def _secrets(name: str) -> list[str]:
    """A comma-separated env var as a list, e.g. VIEWER_PASSWORD="a,b" lets
    either password in."""
    return [p.strip() for p in os.environ.get(name, "").split(",") if p.strip()]


ADMIN_PASSWORDS = _secrets("ADMIN_PASSWORD")
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


def require_admin(request: Request) -> None:
    if not is_admin(request):
        raise HTTPException(status_code=403, detail="Only the admin can change the archive.")


def _json(data) -> str:
    """JSON safe to put inside a <script> tag."""
    return json.dumps(data).replace("</", "<\\/")


@app.middleware("http")
async def require_login(request: Request, call_next):
    path = request.url.path
    open_path = (
        path == "/login"
        or path == "/robots.txt"
        or path.startswith("/static/")
        or path.startswith("/api/")
    )
    if not open_path and not role(request):
        response = RedirectResponse("/login")
    else:
        response = await call_next(request)
    # Private until publishing is allowed: keep search engines out.
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    return response


# Added after the decorator-based middleware above so it ends up outermost in
# the stack (Starlette wraps in reverse add-order) — request.session must be
# populated before require_login() reads it.
app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET,
    https_only=SECURE_COOKIES,
    max_age=60 * 60 * 24 * 90,
)


@app.get("/robots.txt", response_class=PlainTextResponse)
async def robots():
    return "User-agent: *\nDisallow: /\n"


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
    return RedirectResponse("/login", status_code=303)


@app.get("/")
async def home():
    return RedirectResponse("/sheets")


@app.get("/sheets", response_class=HTMLResponse)
async def sheets_page(request: Request):
    return templates.TemplateResponse(
        request, "sheets.html", {"sheets": sheets.list_leadsheets()}
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


@app.get("/sheets/new", response_class=HTMLResponse)
async def sheet_new_page(request: Request):
    # A new sheet opens in the editor unsaved; its first Save POSTs to
    # /sheets, which creates it in the archive.
    require_admin(request)
    sheet = {"id": "", "title": "Title", "artist": "Artist", "key": "", "elements": []}
    config = {
        "canEdit": True,
        "storage": "server",
        "isNew": True,
        "saveUrl": "/sheets",
        "afterDeleteUrl": "/new",
    }
    return templates.TemplateResponse(
        request,
        "sheet.html",
        {"sheet": sheet, "sheet_json": _json(sheet), "config_json": _json(config)},
    )


@app.get("/sheets/{leadsheet_id}", response_class=HTMLResponse)
async def sheet_page(request: Request, leadsheet_id: str):
    try:
        sheet = sheets.get_leadsheet(leadsheet_id)
    except KeyError:
        raise HTTPException(status_code=404)
    admin = is_admin(request)
    config = {
        "canEdit": admin,
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
async def sheet_json(leadsheet_id: str):
    # The whole sheet, for the setlist page to draw into its PDF.
    try:
        sheet = sheets.get_leadsheet(leadsheet_id)
    except KeyError:
        raise HTTPException(status_code=404)
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
    # The setlist itself lives in the browser (localStorage); the page gets
    # the archive to pick from and fetches each sheet when making the PDF.
    catalog = [
        {"id": s["id"], "title": s["title"], "artist": s.get("artist", ""), "key": s.get("key", "")}
        for s in sheets.list_leadsheets()
    ]
    sheet = {"id": "", "title": "", "artist": "", "key": "", "elements": []}
    return templates.TemplateResponse(
        request,
        "setlist.html",
        {"sheets_json": _json(catalog), "sheet_json": _json(sheet)},
    )


@app.get("/new", response_class=HTMLResponse)
async def new_page(request: Request):
    return templates.TemplateResponse(request, "new.html", {"admin": is_admin(request)})


@app.get("/draft", response_class=HTMLResponse)
async def draft_page(request: Request):
    # The draft itself lives in the browser (localStorage); the page loads it
    # from the id in the URL's #fragment.
    config = {"canEdit": True, "storage": "local", "afterDeleteUrl": "/new"}
    return templates.TemplateResponse(request, "draft.html", {"config_json": _json(config)})


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
