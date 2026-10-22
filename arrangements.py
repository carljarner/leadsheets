"""Arrangements: sheet music (PDF / MuseScore) kept with each archive sheet,
in named folders, so one song can hold several versions ("MatRevy 26 -
0.5 op", "Big band", ...). The admin's alone, like the archive.

There is no card to create: every archive sheet has one, and its files go
when the sheet is deleted. Per sheet:

    DATA_DIR/arrangements/<sheet_id>/index.json   the folders and their files
    DATA_DIR/arrangements/<sheet_id>/<file_id>.<ext>
"""

import json
import re
import secrets
import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path

import data_store
from sheets import ID_RE

ARRANGEMENTS_DIR = "arrangements"
ALLOWED_EXTS = ("pdf", "mscz", "mscx")
MAX_FILE_BYTES = 15 * 1024 * 1024
MAX_NAME = 120
HEX_ID_RE = re.compile(r"[0-9a-f]{16}")

# One read-modify-write of an index at a time, so two uploads can't drop
# each other's file.
_lock = threading.Lock()


def _root() -> Path:
    return data_store.DATA_DIR / ARRANGEMENTS_DIR


def _dir(sheet_id: str) -> Path:
    # Ids are slugs; anything else can't name a sheet (and can't escape the folder).
    if not ID_RE.fullmatch(sheet_id or ""):
        raise KeyError(sheet_id)
    return _root() / sheet_id


def _check_id(value: str) -> str:
    if not HEX_ID_RE.fullmatch(value or ""):
        raise KeyError(value)
    return value


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _name(value) -> str:
    name = " ".join(str(value or "").split())[:MAX_NAME]
    if not name:
        raise ValueError("Give it a name.")
    return name


def _read(sheet_id: str) -> dict:
    path = _dir(sheet_id) / "index.json"
    return json.loads(path.read_text()) if path.exists() else {"folders": []}


def _write(sheet_id: str, index: dict) -> None:
    data_store.save_json(_dir(sheet_id) / "index.json", index)


def _folder(index: dict, folder_id: str) -> dict:
    for folder in index["folders"]:
        if folder["id"] == folder_id:
            return folder
    raise KeyError(folder_id)


def _file(index: dict, file_id: str) -> tuple[dict, dict]:
    for folder in index["folders"]:
        for f in folder["files"]:
            if f["id"] == file_id:
                return folder, f
    raise KeyError(file_id)


def list_all() -> dict[str, list[dict]]:
    """Every sheet's folders, by sheet id."""
    if not _root().exists():
        return {}
    return {
        p.parent.name: json.loads(p.read_text())["folders"]
        for p in _root().glob("*/index.json")
    }


def file_count(sheet_id: str) -> int:
    return sum(len(folder["files"]) for folder in _read(sheet_id)["folders"])


def add_folder(sheet_id: str, name) -> dict:
    name = _name(name)
    with _lock:
        index = _read(sheet_id)
        if any(f["name"].casefold() == name.casefold() for f in index["folders"]):
            raise ValueError(f'There is already a folder called "{name}".')
        folder = {"id": secrets.token_hex(8), "name": name, "created_at": _now(), "files": []}
        index["folders"].append(folder)
        _write(sheet_id, index)
    return folder


def rename_folder(sheet_id: str, folder_id: str, name) -> dict:
    name = _name(name)
    with _lock:
        index = _read(sheet_id)
        folder = _folder(index, _check_id(folder_id))
        if any(f is not folder and f["name"].casefold() == name.casefold() for f in index["folders"]):
            raise ValueError(f'There is already a folder called "{name}".')
        folder["name"] = name
        _write(sheet_id, index)
    return folder


def delete_folder(sheet_id: str, folder_id: str) -> None:
    with _lock:
        index = _read(sheet_id)
        folder = _folder(index, _check_id(folder_id))
        for f in folder["files"]:
            (_dir(sheet_id) / f"{f['id']}.{f['ext']}").unlink(missing_ok=True)
        index["folders"].remove(folder)
        _write(sheet_id, index)


def split_name(filename: str) -> tuple[str, str]:
    """"Trumpet.pdf" -> ("Trumpet", "pdf"), refusing other types."""
    stem, _, ext = str(filename or "").rpartition(".")
    ext = ext.lower()
    if not stem or ext not in ALLOWED_EXTS:
        raise ValueError("Only .pdf, .mscz and .mscx files can be uploaded.")
    return _name(stem), ext


def add_file(sheet_id: str, folder_id: str, filename: str, data: bytes) -> dict:
    name, ext = split_name(filename)
    if len(data) > MAX_FILE_BYTES:
        raise ValueError(f"The file is too large (max {MAX_FILE_BYTES // (1024 * 1024)} MB).")
    if not data:
        raise ValueError("The file is empty.")
    with _lock:
        index = _read(sheet_id)
        folder = _folder(index, _check_id(folder_id))
        record = {"id": secrets.token_hex(8), "name": name, "ext": ext, "size": len(data), "uploaded_at": _now()}
        data_store.write_bytes(_dir(sheet_id) / f"{record['id']}.{ext}", data)
        folder["files"].append(record)
        _write(sheet_id, index)
    return record


def get_file(sheet_id: str, file_id: str) -> tuple[dict, Path]:
    """The file's record and where it is on disk."""
    _, record = _file(_read(sheet_id), _check_id(file_id))
    path = _dir(sheet_id) / f"{record['id']}.{record['ext']}"
    if not path.exists():
        raise KeyError(file_id)
    return record, path


def rename_file(sheet_id: str, file_id: str, name) -> dict:
    # Only the shown name changes; the type stays.
    name = _name(name)
    with _lock:
        index = _read(sheet_id)
        _, record = _file(index, _check_id(file_id))
        record["name"] = name
        _write(sheet_id, index)
    return record


def delete_file(sheet_id: str, file_id: str) -> None:
    with _lock:
        index = _read(sheet_id)
        folder, record = _file(index, _check_id(file_id))
        (_dir(sheet_id) / f"{record['id']}.{record['ext']}").unlink(missing_ok=True)
        folder["files"].remove(record)
        _write(sheet_id, index)


def delete_sheet(sheet_id: str) -> None:
    """All of a sheet's arrangements, when the sheet itself is deleted."""
    with _lock:
        shutil.rmtree(_dir(sheet_id), ignore_errors=True)
