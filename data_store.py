"""Flat-file JSON storage on the server's persistent disk.

DATA_DIR holds everything the app writes: the archive (leadsheets/<id>.json)
and the admin's own sheets and setlists (users/admin/<kind>/<id>.json).

Production: DATA_DIR=/data, bind-mounted from /srv/leadsheets/data on the
server, so it survives redeploys and is covered by the nightly backup.
Local dev: defaults to ./data next to this file (gitignored).
"""

import json
import os
import tempfile
import threading
from pathlib import Path

HERE = Path(__file__).parent
DATA_DIR = Path(os.environ.get("DATA_DIR", HERE / "data")).resolve()

_write_lock = threading.Lock()


def _atomic_write(path: Path, data: bytes) -> None:
    """Write to a temp file in the same folder, then rename over the target,
    so a crash mid-write never leaves a half-written file behind."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with _write_lock:
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise


def save_json(path: Path, data) -> None:
    _atomic_write(path, json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8"))
