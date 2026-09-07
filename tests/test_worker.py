"""The worker's round trip, and the logging guarantees.

The engine itself is covered by tests/test_regression.py against a real permit
set. What is checked here is the shell around it: fetch from storage, run on
local paths, publish both artefacts, and leave no local copy behind whatever
happens.
"""
from __future__ import annotations

import io
import json
import logging
import time
from pathlib import Path

import pytest

from conftest import FakeJobStore, FakeStorage, make_pdf
from webapp import storage as storage_mod
from webapp.logging_config import JsonFormatter
from webapp.models import FindingsDocument
from webapp.worker import STAGES, run_review, stages_for


def wait_for(store: FakeJobStore, job_id: str, timeout: float = 60.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        record = store.get(job_id)
        if record and record["state"] in ("done", "error"):
            return record
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} never reached a terminal state")


def run_one(store, files, job_id="job-1", filename="Test Set.pdf", pages=2):
    from fbcreview.options import ReviewOptions

    blob = storage_mod.upload_path(job_id, filename)
    files.blobs[blob] = make_pdf(pages=pages)
    store.create(job_id=job_id, uid="uid-alice", email="allowed@example.com",
                 filename=filename, size_bytes=len(files.blobs[blob]), pages=pages,
                 options={}, upload_blob=blob, stages=stages_for(False))
    run_review(
        job_id=job_id, uid="uid-alice", email="allowed@example.com",
        filename=filename, upload_blob=blob, options=ReviewOptions(),
        store=store, store_files=files,
    )
    return store.get(job_id)


# ── the round trip ────────────────────────────────────────────────────────
def test_worker_publishes_both_artefacts():
    store, files = FakeJobStore(), FakeStorage()
    record = run_one(store, files)

    assert record["state"] == "done", record.get("error")
    assert f"outputs/job-1/{storage_mod.MARKUP}" in files.blobs
    assert f"outputs/job-1/{storage_mod.FINDINGS}" in files.blobs
    assert files.blobs[f"outputs/job-1/{storage_mod.MARKUP}"].startswith(b"%PDF-")


def test_findings_json_matches_the_published_type():
    """findings.json is fetched from the signed URL and parsed by the client
    against the generated FindingsDocument. If the worker's output stops
    validating, the client breaks at runtime — so validate it here."""
    store, files = FakeJobStore(), FakeStorage()
    run_one(store, files)

    raw = files.blobs[f"outputs/job-1/{storage_mod.FINDINGS}"]
    document = FindingsDocument.model_validate_json(raw)

    assert document.job_id == "job-1"
    assert document.summary.findings_count == len(document.findings)
    # Not a frozen count: the number of rules moves whenever the corpus grows.
    # What must hold is that the published figure is the registry's own size.
    from fbcreview.rules import registered

    assert document.summary.rules_run == len(registered())


def test_summary_stays_well_under_the_firestore_document_limit():
    """The findings themselves live in the bucket precisely because a
    Firestore document is capped at 1 MiB."""
    store, files = FakeJobStore(), FakeStorage()
    record = run_one(store, files)

    encoded = json.dumps(record["summary"], default=str).encode("utf-8")
    assert "findings" not in record["summary"]
    assert len(encoded) < 100 * 1024


def test_summary_carries_a_sheet_for_every_page():
    """The viewer's navigator labels sheets from this, so it has to cover the
    whole set: a page with no entry is a page the navigator cannot name."""
    store, files = FakeJobStore(), FakeStorage()
    record = run_one(store, files, pages=3)

    index = record["summary"]["sheet_index"]
    assert [s["page"] for s in index] == [1, 2, 3]
    assert all(s["code"] for s in index)


def test_an_unreadable_sheet_number_says_so_rather_than_guessing():
    """`fbcreview.extract.document` gives a sheet it could not identify the code
    `p{n}`. That has to survive to the client as "not read" rather than as a
    label that looks like the drawing's own name — a navigator captioned p7 with
    no further comment is claiming the title block says p7."""
    from fbcreview.facts import Sheet
    from webapp.worker import sheet_index

    index = sheet_index([
        Sheet(index=0, code="M.001", title="MECHANICAL NOTES", discipline="M"),
        Sheet(index=1, code="p2", title="", discipline=""),
    ])

    assert [s["code"] for s in index] == ["M.001", "p2"]
    assert [s["read"] for s in index] == [True, False]
    assert [s["page"] for s in index] == [1, 2]


def test_stage_progression_is_reported():
    store, files = FakeJobStore(), FakeStorage()
    record = run_one(store, files)
    assert record["stage"] == len(STAGES) - 1
    assert record["started_at"] is not None
    assert record["finished_at"] is not None


# ── failure handling ──────────────────────────────────────────────────────
def test_missing_upload_fails_the_job_instead_of_raising():
    from fbcreview.options import ReviewOptions

    store, files = FakeJobStore(), FakeStorage()
    store.create(job_id="job-x", uid="u", email="e@example.com", filename="gone.pdf",
                 size_bytes=1, pages=1, options={}, upload_blob="uploads/job-x/gone.pdf",
                 stages=stages_for(False))

    # Must not propagate: nothing is awaiting this call.
    run_review(job_id="job-x", uid="u", email="e@example.com", filename="gone.pdf",
               upload_blob="uploads/job-x/gone.pdf", options=ReviewOptions(),
               store=store, store_files=files)

    record = store.get("job-x")
    assert record["state"] == "error"
    assert record["error_code"] == "review_failed"
    assert "Traceback" not in (record["error"] or "")


def test_local_scratch_is_removed_even_on_failure(tmp_path, monkeypatch):
    from fbcreview.options import ReviewOptions

    store, files = FakeJobStore(), FakeStorage()
    created = []

    real_mkdtemp = __import__("tempfile").mkdtemp

    def tracking_mkdtemp(*args, **kwargs):
        path = real_mkdtemp(*args, **kwargs)
        created.append(Path(path))
        return path

    monkeypatch.setattr("webapp.worker.tempfile.mkdtemp", tracking_mkdtemp)

    store.create(job_id="job-y", uid="u", email="e@example.com", filename="gone.pdf",
                 size_bytes=1, pages=1, options={}, upload_blob="missing",
                 stages=stages_for(False))
    run_review(job_id="job-y", uid="u", email="e@example.com", filename="gone.pdf",
               upload_blob="missing", options=ReviewOptions(),
               store=store, store_files=files)

    assert created, "worker did not create a scratch directory"
    for path in created:
        assert not path.exists(), f"{path} survived the run"


# ── logging ───────────────────────────────────────────────────────────────
def test_log_lines_are_single_line_json_with_cloud_logging_fields():
    formatter = JsonFormatter()
    record = logging.LogRecord("fbc.worker", logging.INFO, __file__, 10,
                               "review complete", (), None)
    record.job_id = "abc123"
    record.pages = 24

    line = formatter.format(record)
    assert "\n" not in line
    payload = json.loads(line)
    assert payload["severity"] == "INFO"
    assert payload["message"] == "review complete"
    assert payload["job_id"] == "abc123"
    assert payload["pages"] == 24
    assert payload["time"].endswith("+00:00")


def test_worker_logs_never_carry_the_filename_or_pdf_bytes(caplog):
    """A client's permit set filename usually is the project name. The job id
    is the join key; the filename is not logged."""
    store, files = FakeJobStore(), FakeStorage()
    secret = "CONFIDENTIAL CLIENT PROJECT.pdf"

    with caplog.at_level(logging.DEBUG):
        run_one(store, files, job_id="job-log", filename=secret)

    blob = "".join(
        f"{r.getMessage()} {json.dumps(getattr(r, '__dict__', {}), default=str)}"
        for r in caplog.records
    )
    assert "CONFIDENTIAL" not in blob
    assert "%PDF-" not in blob
    assert "job-log" in blob


def test_exception_details_are_logged_but_not_returned():
    store, files = FakeJobStore(), FakeStorage()
    from fbcreview.options import ReviewOptions

    store.create(job_id="job-z", uid="u", email="e@example.com", filename="x.pdf",
                 size_bytes=1, pages=1, options={}, upload_blob="nope",
                 stages=stages_for(False))
    run_review(job_id="job-z", uid="u", email="e@example.com", filename="x.pdf",
               upload_blob="nope", options=ReviewOptions(), store=store, store_files=files)

    message = store.get("job-z")["error"]
    assert "FileNotFoundError" not in message
    assert "webapp" not in message
