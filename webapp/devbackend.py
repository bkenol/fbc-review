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
import os
import shutil
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from webapp.config import settings
from webapp.jobs import (ACTIVE, DONE, ERROR, QUEUED, RUNNING, check_limits, is_stale,
                         utcnow)

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
        """Write the whole record or none of it.

        `write_text` truncates and then fills, so there is a window in which the
        file on disk is empty or half a record — and a reader in that window
        raises `JSONDecodeError`, which the API reports as a 500. The window is
        small and the browser polls a running job every second, so it is not
        theoretical: it was hit on the first re-run driven through the UI, where
        a job is created and polled immediately.

        `os.replace` is atomic on POSIX and on Windows, so a reader sees either
        the previous record or the new one. The lock this is called under
        serialises writers within one process; it does nothing for a reader in
        another thread, which is exactly who was affected.
        """
        path = self._path(record["id"])
        tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_text(json.dumps(record, default=_encode, indent=2), encoding="utf-8")
        os.replace(tmp, path)

    def create(
        self, *, job_id, uid, email, filename, size_bytes, pages, options, upload_blob,
        stages: List[str], source=None, declaration=None, rerun_of=None,
    ) -> Dict[str, Any]:
        record = {
            "id": job_id, "uid": uid, "email": email, "filename": filename,
            "bytes": size_bytes, "pages": pages, "state": QUEUED, "stage": 0,
            "stages": list(stages), "options": options, "declaration": declaration,
            "upload_blob": upload_blob, "rerun_of": rerun_of,
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
        cfg = settings()
        cutoff = utcnow() - dt.timedelta(minutes=cfg.stale_running_minutes)
        drawing_cutoff = utcnow() - dt.timedelta(minutes=cfg.stale_drawing_minutes)
        failed = 0
        for record in self._all():
            if is_stale(record, cutoff, drawing_cutoff):
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
        # The same contract Cloud Storage's V4 signing offers: a link that
        # carries its own authorisation and expires. See webapp/storage_urls.py
        # for why a download cannot simply reuse the bearer token.
        from webapp import storage_urls

        return storage_urls.build(
            blob_path, download_as=download_as, ttl_seconds=ttl_seconds
        )

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


class LocalFeedbackStore:
    """Feedback, markups and calibration profiles as JSON files.

    Mirrors `webapp.feedback_store.FeedbackStore` exactly, for the same reason
    the classes above mirror the job store and Cloud Storage: without it the
    training-mode client cannot be run on a laptop at all, and a front end that
    can only be reviewed by reading it does not get reviewed.
    """

    def __init__(self) -> None:
        root = dev_root()
        self.feedback_dir = root / "feedback"
        self.markup_dir = root / "markups"
        self.profile_dir = root / "calibration"
        for directory in (self.feedback_dir, self.markup_dir, self.profile_dir):
            directory.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    # ── plumbing ──────────────────────────────────────────────────────────
    def _read(self, directory: Path, key: str) -> Optional[Dict[str, Any]]:
        path = directory / f"{key}.json"
        if not path.exists():
            return None
        try:
            return _decode_feedback(json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            return None

    def _write(self, directory: Path, key: str, record: Dict[str, Any]) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"{key}.json").write_text(
            json.dumps(record, default=_encode, indent=2), encoding="utf-8"
        )

    def _all(self, directory: Path) -> List[Dict[str, Any]]:
        directory.mkdir(parents=True, exist_ok=True)
        out = []
        for path in directory.glob("*.json"):
            try:
                out.append(_decode_feedback(json.loads(path.read_text(encoding="utf-8"))))
            except Exception:
                continue
        return out

    # ── feedback ──────────────────────────────────────────────────────────
    def add_feedback(self, record):
        from webapp import feedback_store as fs

        record = dict(record)
        record.setdefault("id", fs.new_id())
        record.setdefault("state", fs.NEW)
        record.setdefault("created_at", fs.utcnow())
        for key in ("decided_at",):
            record.setdefault(key, None)
        for key in ("decided_by", "decision_note", "issue_url"):
            record.setdefault(key, "")
        record.setdefault("notified", False)
        with self._lock:
            self._write(self.feedback_dir, record["id"], record)
        return record

    def get_feedback(self, feedback_id):
        return self._read(self.feedback_dir, feedback_id)

    def update_feedback(self, feedback_id, **fields):
        with self._lock:
            record = self.get_feedback(feedback_id)
            if record is None:
                return
            record.update(fields)
            self._write(self.feedback_dir, feedback_id, record)

    def list_feedback_for_job(self, job_id, uid, limit=200):
        mine = [
            r for r in self._all(self.feedback_dir)
            if r.get("job_id") == job_id and r.get("uid") == uid
        ]
        mine.sort(key=_created)
        return mine[:limit]

    def list_feedback(self, *, state=None, disposition=None, limit=100):
        rows = self._all(self.feedback_dir)
        if state:
            rows = [r for r in rows if r.get("state") == state]
        if disposition:
            rows = [r for r in rows if r.get("disposition") == disposition]
        rows.sort(key=_created, reverse=True)
        return rows[:limit]

    def count_open(self):
        from webapp import feedback_store as fs

        return sum(1 for r in self._all(self.feedback_dir) if r.get("state") == fs.NEW)

    # ── markups ───────────────────────────────────────────────────────────
    def add_markup(self, record):
        from webapp import feedback_store as fs

        record = dict(record)
        record.setdefault("id", fs.new_id())
        record.setdefault("created_at", fs.utcnow())
        with self._lock:
            self._write(self.markup_dir, record["id"], record)
        return record

    def get_markup(self, markup_id):
        return self._read(self.markup_dir, markup_id)

    def update_markup(self, markup_id, **fields):
        with self._lock:
            record = self.get_markup(markup_id)
            if record is None:
                return
            record.update(fields)
            self._write(self.markup_dir, markup_id, record)

    def delete_markup(self, markup_id):
        (self.markup_dir / f"{markup_id}.json").unlink(missing_ok=True)

    def list_markups(self, job_id, uid, limit=500):
        mine = [
            r for r in self._all(self.markup_dir)
            if r.get("job_id") == job_id and r.get("uid") == uid
        ]
        mine.sort(key=_created)
        return mine[:limit]

    # ── calibration ───────────────────────────────────────────────────────
    def active_profile(self):
        from webapp.calibration import ACTIVE_ID, CalibrationProfile

        return CalibrationProfile.from_dict(self._read(self.profile_dir, ACTIVE_ID))

    def candidate_profile(self, uid):
        from webapp import feedback_store as fs
        from webapp.calibration import CANDIDATE, CalibrationProfile

        stored = self._read(self.profile_dir, fs.candidate_key(uid))
        if stored:
            return CalibrationProfile.from_dict(stored)
        base = self.active_profile()
        seeded = CalibrationProfile.from_dict(base.to_dict())
        seeded.profile_id = fs.candidate_key(uid)
        seeded.scope = CANDIDATE
        seeded.owner_uid = uid
        seeded.label = "Training candidate"
        seeded.derived_from = base.version
        return seeded

    def save_profile(self, profile):
        with self._lock:
            self._write(self.profile_dir, profile.profile_id, profile.to_dict())
        return profile

    def promote(self, profile, *, by, label="", note=""):
        from webapp import feedback_store as fs
        from webapp.calibration import ACTIVE_ID, GLOBAL, CalibrationProfile

        promoted = CalibrationProfile.from_dict(profile.to_dict())
        promoted.profile_id = ACTIVE_ID
        promoted.scope = GLOBAL
        promoted.owner_uid = ""
        promoted.created_by = by
        promoted.created_at = fs.utcnow()
        if label:
            promoted.label = label
        if note:
            promoted.note = note

        archived = CalibrationProfile.from_dict(promoted.to_dict())
        archived.profile_id = fs.version_key(promoted.version)
        with self._lock:
            self._write(self.profile_dir, archived.profile_id, archived.to_dict())
            self._write(self.profile_dir, ACTIVE_ID, promoted.to_dict())
        return promoted

    def profile_versions(self, limit=25):
        from webapp.calibration import ACTIVE_ID, GLOBAL

        rows = [
            r for r in self._all(self.profile_dir)
            if r.get("scope") == GLOBAL and r.get("profile_id") != ACTIVE_ID
        ]
        rows.sort(key=lambda r: int(r.get("version") or 0), reverse=True)
        return rows[:limit]

    def profile_version(self, version):
        from webapp import feedback_store as fs
        from webapp.calibration import CalibrationProfile

        stored = self._read(self.profile_dir, fs.version_key(version))
        return CalibrationProfile.from_dict(stored) if stored else None


def _created(record: Dict[str, Any]) -> dt.datetime:
    stamp = record.get("created_at")
    if isinstance(stamp, dt.datetime):
        return stamp
    return dt.datetime.min.replace(tzinfo=dt.timezone.utc)


_FEEDBACK_STAMPS = ("created_at", "decided_at")


def _decode_feedback(record: Dict[str, Any]) -> Dict[str, Any]:
    for key in _FEEDBACK_STAMPS:
        raw = record.get(key)
        if isinstance(raw, str):
            try:
                record[key] = dt.datetime.fromisoformat(raw)
            except ValueError:
                record[key] = None
    return record
