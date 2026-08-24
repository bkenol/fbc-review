"""Filesystem stand-ins for Firestore and Cloud Storage, for local development.

Without these the client cannot be run at all on a laptop: Firestore needs a
project and Cloud Storage has no emulator, so `ng serve` would have nothing to
talk to and the whole front end could only be reviewed by reading it.

**This module is unreachable in production.** It is only ever constructed when
`settings().dev_unsafe_auth` is true, and `config.py` refuses to set that flag
when `K_SERVICE` is present — which Cloud Run always sets. There is no
environment variable that switches this on in a deployed service.

The interfaces mirror `webapp.jobs.JobStore` and `webapp.storage.Storage`
exactly, so the code under test is the real code.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import shutil
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from webapp.config import settings
from webapp.jobs import ACTIVE, DONE, ERROR, QUEUED, RUNNING, check_limits, utcnow

log = logging.getLogger("fbc.dev")


def dev_root() -> Path:
    root = Path(settings().bucket or "fbc-dev-data")
    if not root.is_absolute():
        root = Path.cwd() / ".devdata" / root.name
    root.mkdir(parents=True, exist_ok=True)
    return root


class LocalJobStore:
    """Job records as one JSON file per job."""

    def __init__(self) -> None:
        self.dir = dev_root() / "jobs"
        self.dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _path(self, job_id: str) -> Path:
        # Recreated on each use: the directory is disposable scratch and gets
        # cleared out between sessions, but this object may be cached across it.
        self.dir.mkdir(parents=True, exist_ok=True)
        return self.dir / f"{job_id}.json"

    def _write(self, record: Dict[str, Any]) -> None:
        self._path(record["id"]).write_text(
            json.dumps(record, default=_encode, indent=2), encoding="utf-8"
        )

    def create(
        self, *, job_id, uid, email, filename, size_bytes, pages, options, upload_blob,
        stages: List[str], source=None, declaration=None,
    ) -> Dict[str, Any]:
        record = {
            "id": job_id, "uid": uid, "email": email, "filename": filename,
            "bytes": size_bytes, "pages": pages, "state": QUEUED, "stage": 0,
            "stages": list(stages), "options": options, "declaration": declaration,
            "upload_blob": upload_blob,
            "source": source, "summary": None, "conversion": None,
            "error": None, "error_code": None,
            "created_at": utcnow(), "started_at": None, "finished_at": None,
        }
        with self._lock:
            self._write(record)
        return record

    def get(self, job_id: str) -> Optional[Dict[str, Any]]:
        path = self._path(job_id)
        if not path.exists():
            return None
        return _decode(json.loads(path.read_text(encoding="utf-8")))

    def update(self, job_id: str, **fields: Any) -> None:
        with self._lock:
            record = self.get(job_id)
            if record is None:
                return
            record.update(fields)
            self._write(record)

    def mark_running(self, job_id: str) -> None:
        self.update(job_id, state=RUNNING, stage=0, started_at=utcnow())

    def mark_stage(self, job_id: str, stage: int) -> None:
        self.update(job_id, stage=stage)

    def mark_done(self, job_id: str, summary: Dict[str, Any]) -> None:
        record = self.get(job_id) or {}
        final = max(len(record.get("stages") or []) - 1, 0)
        self.update(job_id, state=DONE, stage=final, summary=summary, finished_at=utcnow())

    def mark_error(self, job_id: str, code: str, message: str) -> None:
        self.update(job_id, state=ERROR, error=message, error_code=code, finished_at=utcnow())

    def _all(self) -> List[Dict[str, Any]]:
        self.dir.mkdir(parents=True, exist_ok=True)
        out = []
        for path in self.dir.glob("*.json"):
            try:
                out.append(_decode(json.loads(path.read_text(encoding="utf-8"))))
            except Exception:
                continue
        return out

    def list_for(self, uid: str, limit: int = 50) -> List[Dict[str, Any]]:
        mine = [r for r in self._all() if r.get("uid") == uid]
        mine.sort(key=lambda r: r.get("created_at") or dt.datetime.min, reverse=True)
        return mine[:limit]

    def active_count(self, uid: str) -> int:
        return sum(1 for r in self._all() if r.get("uid") == uid and r.get("state") in ACTIVE)

    def recent_count(self, uid: str, since: dt.datetime) -> int:
        return sum(
            1 for r in self._all()
            if r.get("uid") == uid and (r.get("created_at") or utcnow()) > since
        )

    def enforce_limits(self, uid: str) -> None:
        window_start = utcnow() - dt.timedelta(hours=1)
        check_limits(self.active_count(uid), self.recent_count(uid, window_start))

    def fail_stale_running(self) -> int:
        cutoff = utcnow() - dt.timedelta(minutes=settings().stale_running_minutes)
        failed = 0
        for record in self._all():
            started = record.get("started_at")
            if record.get("state") == RUNNING and started and started < cutoff:
                self.mark_error(
                    record["id"], "interrupted",
                    "This review was interrupted by a server restart. Run it again.",
                )
                failed += 1
        return failed


class LocalStorage:
    """Blobs as files. `signed_url` points back at the dev-only download route."""

    def __init__(self) -> None:
        self.dir = dev_root() / "blobs"
        self.dir.mkdir(parents=True, exist_ok=True)

    def _path(self, blob_path: str) -> Path:
        path = self.dir / blob_path
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def upload_file(self, local_path: str, blob_path: str, content_type: str) -> int:
        target = self._path(blob_path)
        shutil.copyfile(local_path, target)
        return target.stat().st_size

    def download_to(self, blob_path: str, local_path: str) -> None:
        shutil.copyfile(self._path(blob_path), local_path)

    def delete(self, blob_path: str) -> None:
        self._path(blob_path).unlink(missing_ok=True)

    def signed_url(self, blob_path: str, *, download_as=None, ttl_seconds=None) -> str:
        suffix = f"&filename={download_as}" if download_as else ""
        return f"/_dev/blob/{blob_path}?dev=1{suffix}"

    def expires_at(self, ttl_seconds: Optional[int] = None) -> dt.datetime:
        return utcnow() + dt.timedelta(seconds=ttl_seconds or settings().signed_url_ttl_seconds)


# ── json round-tripping for datetimes ─────────────────────────────────────
_STAMPS = ("created_at", "started_at", "finished_at")


def _encode(value: Any) -> Any:
    if isinstance(value, dt.datetime):
        return value.isoformat()
    return str(value)


def _decode(record: Dict[str, Any]) -> Dict[str, Any]:
    for key in _STAMPS:
        raw = record.get(key)
        if isinstance(raw, str):
            try:
                record[key] = dt.datetime.fromisoformat(raw)
            except ValueError:
                record[key] = None
    return record
