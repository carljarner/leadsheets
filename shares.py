"""Share links for the admin's setlists: DATA_DIR/shares.json maps a secret
token to the setlist it opens, {token: {"setlist_id", "created_at"}}.

Anyone with the link (/gig/<token>) sees that setlist read-only, with the
charts and Arrangements of its songs. A setlist has at most one token; a new
one replaces it, so the old link stops working. Kept apart from the setlist
itself, which the editor rewrites whole on every save.
"""

import json
import re
import secrets
import threading
from datetime import datetime, timezone

import data_store

TOKEN_RE = re.compile(r"[A-Za-z0-9_-]{16,64}")

_lock = threading.Lock()


def _path():
    return data_store.DATA_DIR / "shares.json"


def _read() -> dict:
    path = _path()
    return json.loads(path.read_text()) if path.exists() else {}


def resolve(token: str) -> str:
    """The setlist id a token opens; KeyError if it opens none."""
    if not TOKEN_RE.fullmatch(token or ""):
        raise KeyError(token)
    entry = _read().get(token)
    if not entry:
        raise KeyError(token)
    return entry["setlist_id"]


def token_for(setlist_id: str) -> str | None:
    return next((t for t, e in _read().items() if e["setlist_id"] == setlist_id), None)


def shared_ids() -> list[str]:
    return [e["setlist_id"] for e in _read().values()]


def create(setlist_id: str) -> str:
    """A new token for the setlist, replacing its old one."""
    with _lock:
        shares = {t: e for t, e in _read().items() if e["setlist_id"] != setlist_id}
        token = secrets.token_urlsafe(16)
        shares[token] = {"setlist_id": setlist_id, "created_at": datetime.now(timezone.utc).isoformat()}
        data_store.save_json(_path(), shares)
    return token


def revoke(setlist_id: str) -> None:
    with _lock:
        shares = _read()
        kept = {t: e for t, e in shares.items() if e["setlist_id"] != setlist_id}
        if len(kept) != len(shares):
            data_store.save_json(_path(), kept)
