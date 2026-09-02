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

    def create(self, *, job_id, uid, email, filename, size_bytes, pages, options, upload_blob, stages, source=None, declaration=None, rerun_of=None):
        record = {
            "id": job_id, "uid": uid, "email": email, "filename": filename,
            "bytes": size_bytes, "pages": pages, "state": jobs_mod.QUEUED, "stage": 0,
            "options": options, "declaration": declaration, "rerun_of": rerun_of,
            "upload_blob": upload_blob, "source": source, "summary": None, "conversion": None,
            "stages": list(stages),
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
        record = self.get(job_id) or {}
        final = max(len(record.get("stages") or []) - 1, 0)
        self.update(job_id, state=jobs_mod.DONE, stage=final, summary=summary,
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

    def enforce_limits(self, uid):
        window_start = jobs_mod.utcnow() - dt.timedelta(hours=1)
        jobs_mod.check_limits(self.active_count(uid), self.recent_count(uid, window_start))

    def list_for(self, uid, limit=50):
        mine = [dict(d) for d in self.docs.values() if d["uid"] == uid]
        mine.sort(key=lambda d: d["created_at"], reverse=True)
        return mine[:limit]

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


class FakeFeedbackStore:
    """Mirrors webapp.feedback_store.FeedbackStore, backed by dicts.

    Same reasoning as FakeJobStore: the code under test is the real handler, and
    the store is the only thing swapped out. Profile promotion in particular is
    exercised for real here — it is the one operation that changes what every
    future review reports.
    """

    def __init__(self) -> None:
        self.feedback: Dict[str, Dict[str, Any]] = {}
        self.markups: Dict[str, Dict[str, Any]] = {}
        self.profiles: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()

    # -- feedback --
    def add_feedback(self, record):
        from webapp import feedback_store as fs

        record = dict(record)
        record.setdefault("id", fs.new_id())
        record.setdefault("state", fs.NEW)
        record.setdefault("created_at", fs.utcnow())
        record.setdefault("decided_at", None)
        for key in ("decided_by", "decision_note", "issue_url"):
            record.setdefault(key, "")
        record.setdefault("notified", False)
        with self._lock:
            self.feedback[record["id"]] = record
        return dict(record)

    def get_feedback(self, feedback_id):
        record = self.feedback.get(feedback_id)
        return dict(record) if record else None

    def update_feedback(self, feedback_id, **fields):
        with self._lock:
            if feedback_id in self.feedback:
                self.feedback[feedback_id].update(fields)

    def list_feedback_for_job(self, job_id, uid, limit=200):
        rows = [dict(r) for r in self.feedback.values()
                if r.get("job_id") == job_id and r.get("uid") == uid]
        rows.sort(key=lambda r: r["created_at"])
        return rows[:limit]

    def list_feedback(self, *, state=None, disposition=None, limit=100):
        rows = [dict(r) for r in self.feedback.values()]
        if state:
            rows = [r for r in rows if r.get("state") == state]
        if disposition:
            rows = [r for r in rows if r.get("disposition") == disposition]
        rows.sort(key=lambda r: r["created_at"], reverse=True)
        return rows[:limit]

    def count_open(self):
        from webapp import feedback_store as fs

        return sum(1 for r in self.feedback.values() if r.get("state") == fs.NEW)

    # -- markups --
    def add_markup(self, record):
        from webapp import feedback_store as fs

        record = dict(record)
        record.setdefault("id", fs.new_id())
        record.setdefault("created_at", fs.utcnow())
        with self._lock:
            self.markups[record["id"]] = record
        return dict(record)

    def get_markup(self, markup_id):
        record = self.markups.get(markup_id)
        return dict(record) if record else None

    def update_markup(self, markup_id, **fields):
        with self._lock:
            if markup_id in self.markups:
                self.markups[markup_id].update(fields)

    def delete_markup(self, markup_id):
        self.markups.pop(markup_id, None)

    def list_markups(self, job_id, uid, limit=500):
        rows = [dict(r) for r in self.markups.values()
                if r.get("job_id") == job_id and r.get("uid") == uid]
        rows.sort(key=lambda r: r["created_at"])
        return rows[:limit]

    # -- calibration --
    def active_profile(self):
        from webapp.calibration import ACTIVE_ID, CalibrationProfile

        return CalibrationProfile.from_dict(self.profiles.get(ACTIVE_ID))

    def candidate_profile(self, uid):
        from webapp import feedback_store as fs
        from webapp.calibration import CANDIDATE, CalibrationProfile

        stored = self.profiles.get(fs.candidate_key(uid))
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
            self.profiles[profile.profile_id] = profile.to_dict()
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
            self.profiles[archived.profile_id] = archived.to_dict()
            self.profiles[ACTIVE_ID] = promoted.to_dict()
        return promoted

    def profile_versions(self, limit=25):
        from webapp.calibration import ACTIVE_ID, GLOBAL

        rows = [dict(r) for r in self.profiles.values()
                if r.get("scope") == GLOBAL and r.get("profile_id") != ACTIVE_ID]
        rows.sort(key=lambda r: int(r.get("version") or 0), reverse=True)
        return rows[:limit]

    def profile_version(self, version):
        from webapp import feedback_store as fs
        from webapp.calibration import CalibrationProfile

        stored = self.profiles.get(fs.version_key(version))
        return CalibrationProfile.from_dict(stored) if stored else None


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


def make_pdf_with_pasted_table(pages: int = 1) -> bytes:
    """A properly plotted sheet that pastes its code table in as a picture.

    This is the ITEC case, and it is the common one: the sheet is genuinely
    vector with live text, so a whole-sheet check calls it readable, while the
    rows the rules actually need are pixels.
    """
    # Render the "table" to an image first, so its text is not live.
    table = pymupdf.open()
    tp = table.new_page(width=760, height=340)
    rows = [
        "USE AND OCCUPANCY CLASSIFICATION",
        "OCCUPANCY: BUSINESS",
        "MIXED OCCUPANCY? NO",
        "OCCUPANCY SEPARATION RATING PROVIDED:",
        "MULTIPLE - SEPARATED PER TABLE 508.4",
        "CONSTRUCTION TYPE: II-B",
    ]
    y = 46
    for row in rows:
        tp.insert_text((30, y), row, fontsize=21)
        y += 48
    picture = tp.get_pixmap(dpi=200)
    table.close()

    doc = pymupdf.open()
    for i in range(pages):
        page = doc.new_page(width=1224, height=792)
        for n in range(200):
            page.draw_line((20 + n * 6, 40), (20 + n * 6, 560))
        page.insert_text((40, 600), f"SHEET G-{i} GENERAL NOTES " * 6, fontsize=8)
        page.insert_image(pymupdf.Rect(430, 620, 1190, 780), pixmap=picture)
    buf = doc.tobytes()
    doc.close()
    return buf


def make_pdf_with_sliced_table(pages: int = 1, bands: int = 6) -> bytes:
    """The same pasted table, sliced into bands the way a plot driver emits it.

    AutoCAD's PDF driver cuts one plotted raster into horizontal strips and
    writes each as its own image. Measured on the ITEC set, the G-002 code table
    arrives as three of them. The strips abut exactly and share the full width,
    which is what makes them recognisable as one region — and what makes two
    genuinely separate tables, which do neither, stay separate.

    Deliberately sliced finely enough that no single band clears the size
    floors: before coalescing this sheet reports *no* regions at all, which is
    the quiet version of the bug — the pasted table is not merely OCR'd badly,
    it is never noticed.
    """
    table = pymupdf.open()
    tp = table.new_page(width=760, height=340)
    rows = [
        "USE AND OCCUPANCY CLASSIFICATION",
        "OCCUPANCY: BUSINESS",
        "MIXED OCCUPANCY? NO",
        "OCCUPANCY SEPARATION RATING PROVIDED:",
        "MULTIPLE - SEPARATED PER TABLE 508.4",
        "CONSTRUCTION TYPE: II-B",
    ]
    y = 46
    for row in rows:
        tp.insert_text((30, y), row, fontsize=21)
        y += 48

    # One pixmap per band, clipped out of the same rendered table.
    step = 340 / bands
    slices = [
        tp.get_pixmap(dpi=200, clip=pymupdf.Rect(0, i * step, 760, (i + 1) * step))
        for i in range(bands)
    ]
    table.close()

    doc = pymupdf.open()
    placed_step = 160 / bands
    for i in range(pages):
        page = doc.new_page(width=1224, height=792)
        for n in range(200):
            page.draw_line((20 + n * 6, 40), (20 + n * 6, 560))
        page.insert_text((40, 600), f"SHEET G-{i} GENERAL NOTES " * 6, fontsize=8)
        for k, band in enumerate(slices):
            page.insert_image(
                pymupdf.Rect(430, 620 + k * placed_step, 1190, 620 + (k + 1) * placed_step),
                pixmap=band,
            )
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
def feedback() -> FakeFeedbackStore:
    return FakeFeedbackStore()


@pytest.fixture
def user() -> User:
    return User(uid="uid-alice", email="allowed@example.com")


@pytest.fixture
def client(monkeypatch, store, files, feedback, user):
    """A TestClient with cloud dependencies replaced and auth satisfied."""
    from fastapi.testclient import TestClient

    from webapp import server

    settings.cache_clear()
    monkeypatch.setattr(server, "get_job_store", lambda: store)

    server.app.dependency_overrides[server.job_store] = lambda: store
    server.app.dependency_overrides[server.file_store] = lambda: files
    server.app.dependency_overrides[server.feedback_store] = lambda: feedback
    server.app.dependency_overrides[server.current_user] = lambda: user

    with TestClient(server.app) as c:
        c.fake_store = store          # type: ignore[attr-defined]
        c.fake_files = files          # type: ignore[attr-defined]
        c.fake_feedback = feedback    # type: ignore[attr-defined]
        yield c

    server.app.dependency_overrides.clear()
    settings.cache_clear()


@pytest.fixture
def training_client(monkeypatch, store, files, feedback, user):
    """The same client, on a deployment that has opted into training mode.

    Separate from `client` rather than a flag on it: training mode is off by
    default and every existing test asserts the behaviour of a deployment that
    has not enabled it. That distinction is the point — the feature has to be
    inert until somebody turns it on.
    """
    from fastapi.testclient import TestClient

    from webapp import server

    settings.cache_clear()
    monkeypatch.setenv("FBC_TRAINING_MODE", "1")
    monkeypatch.setenv("FBC_OWNER_EMAILS", "owner@example.com")
    settings.cache_clear()

    monkeypatch.setattr(server, "get_job_store", lambda: store)
    server.app.dependency_overrides[server.job_store] = lambda: store
    server.app.dependency_overrides[server.file_store] = lambda: files
    server.app.dependency_overrides[server.feedback_store] = lambda: feedback
    server.app.dependency_overrides[server.current_user] = lambda: user

    with TestClient(server.app) as c:
        c.fake_store = store          # type: ignore[attr-defined]
        c.fake_files = files          # type: ignore[attr-defined]
        c.fake_feedback = feedback    # type: ignore[attr-defined]
        yield c

    server.app.dependency_overrides.clear()
    settings.cache_clear()


@pytest.fixture
def anon_client(monkeypatch, store, files, feedback):
    """A TestClient with the real auth dependency left in place."""
    from fastapi.testclient import TestClient

    from webapp import server

    settings.cache_clear()
    monkeypatch.setattr(server, "get_job_store", lambda: store)
    server.app.dependency_overrides[server.job_store] = lambda: store
    server.app.dependency_overrides[server.file_store] = lambda: files
    server.app.dependency_overrides[server.feedback_store] = lambda: feedback

    with TestClient(server.app) as c:
        yield c

    server.app.dependency_overrides.clear()
    settings.cache_clear()


def upload_form(pdf: bytes, options: Optional[str] = None, name: str = "set.pdf",
                declaration: Optional[str] = None):
    data = {"review_options": options if options is not None else "{}"}
    if declaration is not None:
        data["declaration"] = declaration
    return {"files": {"file": (name, io.BytesIO(pdf), "application/pdf")}, "data": data}
