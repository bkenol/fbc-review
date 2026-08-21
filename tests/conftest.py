"""Test fixtures.

The service is exercised against in-memory doubles for Firestore and Cloud
Storage, wired in through FastAPI's dependency_overrides. That keeps the
production modules free of "if testing" branches and means the whole hardening
surface — upload admission, the error envelope, auth, ownership, rate limits —
is testable with no cloud credentials and no network.
"""
from __future__ import annotations

import datetime as dt
import io
import os
import sys
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Set before anything imports webapp.config, whose settings() is lru_cached.
os.environ.setdefault("FBC_BUCKET", "test-bucket")
os.environ.setdefault("FBC_PROJECT_ID", "test-project")
os.environ.setdefault("FBC_ALLOWED_EMAILS", "allowed@example.com")
os.environ.pop("K_SERVICE", None)
os.environ.pop("FBC_DEV_UNSAFE_AUTH", None)

import pymupdf  # noqa: E402

from webapp import jobs as jobs_mod  # noqa: E402
from webapp.auth import User  # noqa: E402
from webapp.config import settings  # noqa: E402


# ── doubles ───────────────────────────────────────────────────────────────
class FakeJobStore:
    """Mirrors webapp.jobs.JobStore, backed by a dict."""

    def __init__(self) -> None:
        self.docs: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()
        self.done = threading.Event()

    def create(self, *, job_id, uid, email, filename, size_bytes, pages, options, upload_blob, source=None):
        record = {
            "id": job_id, "uid": uid, "email": email, "filename": filename,
            "bytes": size_bytes, "pages": pages, "state": jobs_mod.QUEUED, "stage": 0,
            "options": options, "upload_blob": upload_blob, "source": source, "summary": None,
            "error": None, "error_code": None,
            "created_at": jobs_mod.utcnow(), "started_at": None, "finished_at": None,
        }
        with self._lock:
            self.docs[job_id] = record
        return record

    def update(self, job_id, **fields):
        with self._lock:
            self.docs[job_id].update(fields)

    def mark_running(self, job_id):
        self.update(job_id, state=jobs_mod.RUNNING, stage=0, started_at=jobs_mod.utcnow())

    def mark_stage(self, job_id, stage):
        self.update(job_id, stage=stage)

    def mark_done(self, job_id, summary):
        self.update(job_id, state=jobs_mod.DONE, stage=4, summary=summary,
                    finished_at=jobs_mod.utcnow())
        self.done.set()

    def mark_error(self, job_id, code, message):
        self.update(job_id, state=jobs_mod.ERROR, error=message, error_code=code,
                    finished_at=jobs_mod.utcnow())
        self.done.set()

    def get(self, job_id):
        with self._lock:
            record = self.docs.get(job_id)
            return dict(record) if record else None

    # Derived exactly as the real store derives them, so the limit logic is
    # what is under test rather than a counter.
    def active_count(self, uid):
        return sum(1 for d in self.docs.values()
                   if d["uid"] == uid and d["state"] in jobs_mod.ACTIVE)

    def recent_count(self, uid, since):
        return sum(1 for d in self.docs.values()
                   if d["uid"] == uid and d["created_at"] > since)

    enforce_limits = jobs_mod.JobStore.enforce_limits

    def fail_stale_running(self):
        return 0


class FakeStorage:
    def __init__(self) -> None:
        self.blobs: Dict[str, bytes] = {}
        self.deleted: List[str] = []

    def upload_file(self, local_path, blob_path, content_type):
        data = Path(local_path).read_bytes()
        self.blobs[blob_path] = data
        return len(data)

    def download_to(self, blob_path, local_path):
        if blob_path not in self.blobs:
            raise FileNotFoundError(blob_path)
        Path(local_path).write_bytes(self.blobs[blob_path])

    def delete(self, blob_path):
        self.deleted.append(blob_path)
        self.blobs.pop(blob_path, None)

    def signed_url(self, blob_path, *, download_as=None, ttl_seconds=None):
        suffix = f"&filename={download_as}" if download_as else ""
        return f"https://storage.example/{blob_path}?X-Goog-Signature=fake{suffix}"

    def expires_at(self, ttl_seconds=None):
        return dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=ttl_seconds or 3600)


# ── pdf builders ──────────────────────────────────────────────────────────
def make_pdf(pages: int = 1, text: str = "SHEET G-0") -> bytes:
    """A plausible plotted sheet: vector linework plus live text.

    Deliberately not a near-blank page — webapp.pdfkind rejects those, and a
    fixture that would be turned away at the door tests nothing.
    """
    doc = pymupdf.open()
    for i in range(pages):
        page = doc.new_page(width=1224, height=792)
        for n in range(200):
            page.draw_line((20 + n * 6, 40), (20 + n * 6, 720))
        page.insert_text(
            (40, 750),
            f"{text} {i + 1}  LIFE SAFETY PLAN  1/4\" = 1'-0\"  "
            "COMMON PATH OF EGRESS TRAVEL 75 FT (1006.2.1)  "
            "OCCUPANT LOAD 70  TRAVEL DISTANCE 250 FT (1017.2)",
            fontsize=8,
        )
    buf = doc.tobytes()
    doc.close()
    return buf


def make_raster_pdf(pages: int = 1) -> bytes:
    """A scanned set: a full-bleed image, no live text, no vector geometry."""
    doc = pymupdf.open()
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 1200, 780))
    pix.set_rect(pix.irect, (255, 255, 255))
    for y in range(60, 720, 31):
        pix.set_rect(pymupdf.IRect(40, y, 1160, y + 2), (0, 0, 0))
    for i in range(pages):
        page = doc.new_page(width=1224, height=792)
        page.insert_image(page.rect, pixmap=pix)
    buf = doc.tobytes()
    doc.close()
    return buf


def make_blank_pdf(pages: int = 1) -> bytes:
    doc = pymupdf.open()
    for _ in range(pages):
        doc.new_page()
    buf = doc.tobytes()
    doc.close()
    return buf


def make_encrypted_pdf() -> bytes:
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "locked")
    buf = doc.tobytes(
        encryption=pymupdf.PDF_ENCRYPT_AES_256,
        owner_pw="owner-secret",
        user_pw="user-secret",
    )
    doc.close()
    return buf


# ── fixtures ──────────────────────────────────────────────────────────────
@pytest.fixture
def store() -> FakeJobStore:
    return FakeJobStore()


@pytest.fixture
def files() -> FakeStorage:
    return FakeStorage()


@pytest.fixture
def user() -> User:
    return User(uid="uid-alice", email="allowed@example.com")


@pytest.fixture
def client(monkeypatch, store, files, user):
    """A TestClient with cloud dependencies replaced and auth satisfied."""
    from fastapi.testclient import TestClient

    from webapp import server

    settings.cache_clear()
    monkeypatch.setattr(server, "get_job_store", lambda: store)

    server.app.dependency_overrides[server.job_store] = lambda: store
    server.app.dependency_overrides[server.file_store] = lambda: files
    server.app.dependency_overrides[server.current_user] = lambda: user

    with TestClient(server.app) as c:
        c.fake_store = store       # type: ignore[attr-defined]
        c.fake_files = files       # type: ignore[attr-defined]
        yield c

    server.app.dependency_overrides.clear()
    settings.cache_clear()


@pytest.fixture
def anon_client(monkeypatch, store, files):
    """A TestClient with the real auth dependency left in place."""
    from fastapi.testclient import TestClient

    from webapp import server

    settings.cache_clear()
    monkeypatch.setattr(server, "get_job_store", lambda: store)
    server.app.dependency_overrides[server.job_store] = lambda: store
    server.app.dependency_overrides[server.file_store] = lambda: files

    with TestClient(server.app) as c:
        yield c

    server.app.dependency_overrides.clear()
    settings.cache_clear()


def upload_form(pdf: bytes, options: Optional[str] = None, name: str = "set.pdf"):
    data = {"review_options": options if options is not None else "{}"}
    return {"files": {"file": (name, io.BytesIO(pdf), "application/pdf")}, "data": data}
