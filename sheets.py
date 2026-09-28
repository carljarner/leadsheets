"""The lead sheet archive: each song's chart is a single A4 page of
freely-positioned elements (title boxes, bar rows, text fields, repeat
marks, arrows, rhythm notation) rather than a scanned image.

Each sheet is its own file under DATA_DIR/leadsheets, named "<id>.json".
Ids are the same as in the James Band intern app the archive was copied
from, so the band can map its repertoire songs onto them.
"""

import json
import re
from datetime import datetime, timezone
from pathlib import Path

import chords
import data_store

LEADSHEETS_DIR = "leadsheets"
MAX_TEXT = 200
ID_RE = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*")


def _dir() -> Path:
    return data_store.DATA_DIR / LEADSHEETS_DIR


def _path(leadsheet_id: str) -> Path:
    # Ids are slugs; anything else can't name a sheet (and can't escape the folder).
    if not ID_RE.fullmatch(leadsheet_id or ""):
        raise KeyError(leadsheet_id)
    return _dir() / f"{leadsheet_id}.json"


def _existing_ids() -> set[str]:
    if not _dir().exists():
        return set()
    return {p.stem for p in _dir().glob("*.json")}


def list_leadsheets() -> list[dict]:
    if not _dir().exists():
        return []
    sheets = [json.loads(p.read_text()) for p in _dir().glob("*.json")]
    return sorted(sheets, key=lambda sheet: sheet["title"].casefold())


def get_leadsheet(leadsheet_id: str) -> dict:
    path = _path(leadsheet_id)
    if not path.exists():
        raise KeyError(leadsheet_id)
    return json.loads(path.read_text())


def _save(sheet: dict) -> None:
    data_store.save_json(_path(sheet["id"]), sheet)


def _unique_id(title: str, existing_ids: set[str]) -> str:
    try:
        base = chords.slugify(title)
    except chords.ChordError:
        base = "sheet"
    if base not in existing_ids:
        return base
    n = 2
    while f"{base}-{n}" in existing_ids:
        n += 1
    return f"{base}-{n}"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _text(value) -> str:
    return str(value or "").strip()[:MAX_TEXT]


def _clean_doc(leadsheet_id: str, doc: dict) -> dict:
    """The sheet as stored: only the fields the editor owns."""
    title = _text(doc.get("title"))
    if not title:
        raise ValueError("Sheet title can't be empty.")
    elements = doc.get("elements", [])
    if not isinstance(elements, list):
        raise ValueError("Malformed lead sheet document.")
    cleaned = {
        "id": leadsheet_id,
        "title": title,
        "artist": _text(doc.get("artist")),
        "key": _text(doc.get("key")),
        "updated_at": _now(),
        "elements": elements,
    }
    for field in LINK_FIELDS:
        url = _clean_url(doc.get(field))
        if url:
            cleaned[field] = url
    return cleaned


# The practice links shown in the sheet's Practice box.
LINK_FIELDS = ("lyrics_url", "youtube_url", "spotify_url")


def _clean_url(value) -> str:
    """A link as stored: trimmed, with https:// added when it has no scheme.
    Only http(s) links are kept."""
    url = str(value or "").strip()
    if url and "://" not in url:
        url = "https://" + url
    if not url.lower().startswith(("http://", "https://")):
        return ""
    return url


# Kept free so an old /sheets/new link never opens a sheet.
RESERVED_IDS = {"new"}


def add_leadsheet(doc: dict) -> dict:
    """Store a new sheet (the editor's first save of it) under an id made
    from its title."""
    title = _text(doc.get("title"))
    if not title:
        raise ValueError("Sheet title can't be empty.")
    leadsheet_id = _unique_id(title, _existing_ids() | RESERVED_IDS)
    sheet = _clean_doc(leadsheet_id, doc)
    _save(sheet)
    return sheet


def update_leadsheet(leadsheet_id: str, doc: dict) -> dict:
    if not _path(leadsheet_id).exists():
        raise KeyError(leadsheet_id)
    cleaned = _clean_doc(leadsheet_id, doc)
    _save(cleaned)
    return cleaned


def delete_leadsheet(leadsheet_id: str) -> None:
    path = _path(leadsheet_id)
    if not path.exists():
        raise KeyError(leadsheet_id)
    path.unlink()
