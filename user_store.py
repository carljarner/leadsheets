"""A user's own sheets (drafts and versions made with Customize) and
setlists, one JSON file each: DATA_DIR/users/<user>/<kind>/<id>.json.

For now the only user with a folder is the admin; viewers keep theirs in
their browser. static/user-store.js has the same methods over either, so
logins for everyone would only mean more folders here.

The documents are the editor's and the setlist page's own, stored as they
come apart from their id and timestamps, which are set here.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

import data_store
from sheets import ID_RE

KINDS = ("sheets", "setlists")


def _dir(user: str, kind: str) -> Path:
    if kind not in KINDS:
        raise KeyError(kind)
    return data_store.DATA_DIR / "users" / user / kind


def _path(user: str, kind: str, doc_id: str) -> Path:
    # Ids are slugs; anything else can't name a document (or escape the folder).
    if not ID_RE.fullmatch(doc_id or ""):
        raise KeyError(doc_id)
    return _dir(user, kind) / f"{doc_id}.json"


def list_docs(user: str, kind: str) -> list[dict]:
    """Every document of a kind, newest first."""
    folder = _dir(user, kind)
    if not folder.exists():
        return []
    docs = [json.loads(p.read_text()) for p in folder.glob("*.json")]
    return sorted(docs, key=lambda d: d.get("updated_at", ""), reverse=True)


def get_doc(user: str, kind: str, doc_id: str) -> dict:
    path = _path(user, kind, doc_id)
    if not path.exists():
        raise KeyError(doc_id)
    return json.loads(path.read_text())


def save_doc(user: str, kind: str, doc_id: str, doc: dict, keep_times: bool = False) -> dict:
    """Stores `doc`, stamping updated_at (and created_at the first time).
    With keep_times the document's own timestamps are kept: for copying
    records across (e.g. from the browser) rather than editing them."""
    path = _path(user, kind, doc_id)
    now = datetime.now(timezone.utc).isoformat()
    existing = json.loads(path.read_text()) if path.exists() else {}
    stored = dict(doc, id=doc_id)
    if keep_times:
        stored["updated_at"] = doc.get("updated_at") or now
        stored["created_at"] = doc.get("created_at") or existing.get("created_at") or now
    else:
        stored["updated_at"] = now
        stored["created_at"] = existing.get("created_at") or doc.get("created_at") or now
    data_store.save_json(path, stored)
    return stored


def delete_doc(user: str, kind: str, doc_id: str) -> None:
    path = _path(user, kind, doc_id)
    if not path.exists():
        raise KeyError(doc_id)
    path.unlink()
