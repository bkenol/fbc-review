"""The review history.

One property matters more than everything else here: the list is scoped to the
caller's own uid, at the query. A history that fetches everything and filters
afterwards is one refactor away from being a data leak, and permit sets are
client documents.

The rest is shape. Rows carry counts and no signed URLs — minting a download
link costs a round trip each, and a list of thirty rows nobody has opened does
not need thirty of them.
"""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from conftest import make_pdf, upload_form
from webapp import jobs as jobs_mod
from webapp.models import HistoryResponse


def seed(store, *, job_id, uid, filename="set.pdf", state=jobs_mod.DONE,
         created_at=None, summary=None, options=None, declaration=None):
    """Put a finished review straight into the store, as the worker would."""
    record = store.create(
        job_id=job_id,
        uid=uid,
        email=f"{uid}@example.com",
        filename=filename,
        size_bytes=1234,
        pages=24,
        options=options or {"project_name": "Sculpted Hot Pilates", "edition": "fbc2023"},
        upload_blob=f"uploads/{job_id}/{filename}",
        stages=["Reading the PDF", "Delivering"],
        declaration=declaration,
    )
    fields = {"state": state}
    if summary is not None:
        fields["summary"] = summary
    if created_at is not None:
        fields["created_at"] = created_at
    store.update(job_id, **fields)
    return record


# A whole Summary, because `GET /api/jobs/{id}` validates one. The history rows
# only read `counts` and `open` out of it, which is the point of the test below
# that they carry no download links.
SUMMARY = {
    "sheets": 24, "pages": 28, "cad_layers": 200, "annotations": 34, "marked": 13,
    "counts": {"CRITICAL": 1, "HIGH": 2, "VERIFIED": 5},
    "open": 3, "verified": 5, "conflicts": 0, "abstentions": [],
    "rules_run": 30, "scale_pages": 13, "pdf_bytes": 17_800_000,
    "pdf_name": "set-marked.pdf", "findings_count": 8,
}


# ── scoping ───────────────────────────────────────────────────────────────
def test_the_history_is_only_ever_the_callers_own(client, user):
    store = client.fake_store
    seed(store, job_id="mine", uid=user.uid)
    seed(store, job_id="theirs", uid="uid-someone-else")

    body = HistoryResponse.model_validate(client.get("/api/jobs").json())
    assert [e.id for e in body.entries] == ["mine"]


def test_an_empty_history_is_an_empty_list_not_an_error(client):
    response = client.get("/api/jobs")
    assert response.status_code == 200, response.text
    assert HistoryResponse.model_validate(response.json()).entries == []


def test_history_needs_the_caller_to_be_signed_in(anon_client):
    response = anon_client.get("/api/jobs")
    assert response.status_code in (401, 403), response.text


# ── ordering and shape ────────────────────────────────────────────────────
def test_newest_first(client, user):
    store = client.fake_store
    now = jobs_mod.utcnow()
    seed(store, job_id="older", uid=user.uid, created_at=now - dt.timedelta(hours=2))
    seed(store, job_id="newest", uid=user.uid, created_at=now)
    seed(store, job_id="middle", uid=user.uid, created_at=now - dt.timedelta(hours=1))

    body = HistoryResponse.model_validate(client.get("/api/jobs").json())
    assert [e.id for e in body.entries] == ["newest", "middle", "older"]


def test_a_finished_row_carries_its_counts_and_open_total(client, user):
    seed(client.fake_store, job_id="done-1", uid=user.uid, summary=SUMMARY)

    entry = HistoryResponse.model_validate(client.get("/api/jobs").json()).entries[0]
    assert entry.counts == {"CRITICAL": 1, "HIGH": 2, "VERIFIED": 5}
    assert entry.open_findings == 3
    assert entry.project_name == "Sculpted Hot Pilates"
    assert entry.pages == 24


def test_a_row_carries_no_signed_urls(client, user):
    # Rows are a list, not thirty jobs. Links are minted per row on
    # GET /api/jobs/{id}, when a row is actually opened.
    seed(client.fake_store, job_id="done-1", uid=user.uid, summary=SUMMARY)
    raw = client.get("/api/jobs").json()["entries"][0]
    assert "downloads" not in raw


def test_an_unfinished_review_is_listed_in_the_state_it_ended_in(client, user):
    store = client.fake_store
    seed(store, job_id="broken", uid=user.uid, state=jobs_mod.ERROR)
    store.update("broken", error="This review was interrupted by a server restart.")

    entry = HistoryResponse.model_validate(client.get("/api/jobs").json()).entries[0]
    assert entry.state == "error"
    assert entry.error
    # Nothing to show, and nothing pretending there is.
    assert entry.counts == {}
    assert entry.open_findings == 0


def test_the_declared_count_is_answers_given_not_questions_asked(client, user):
    seed(
        client.fake_store,
        job_id="declared",
        uid=user.uid,
        summary=SUMMARY,
        declaration={"occupancy_group": "A-3", "stories": 1, "wind_speed_mph": None},
    )
    entry = HistoryResponse.model_validate(client.get("/api/jobs").json()).entries[0]
    assert entry.declared_fields == 2


def test_the_limit_is_clamped_rather_than_trusted(client, user):
    store = client.fake_store
    now = jobs_mod.utcnow()
    for n in range(5):
        seed(store, job_id=f"job-{n}", uid=user.uid,
             created_at=now - dt.timedelta(minutes=n))

    assert len(HistoryResponse.model_validate(
        client.get("/api/jobs", params={"limit": 2}).json()).entries) == 2
    # A limit of zero or a negative one is a request for nothing, which is
    # never what anyone means. One row is the floor.
    assert len(HistoryResponse.model_validate(
        client.get("/api/jobs", params={"limit": 0}).json()).entries) == 1


# ── re-opening ────────────────────────────────────────────────────────────
def test_a_listed_review_can_still_be_opened(client, user):
    seed(client.fake_store, job_id="done-1", uid=user.uid, summary=SUMMARY)

    listed = HistoryResponse.model_validate(client.get("/api/jobs").json()).entries[0]
    reopened = client.get(f"/api/jobs/{listed.id}")
    assert reopened.status_code == 200, reopened.text
    assert reopened.json()["id"] == listed.id


def test_someone_elses_review_is_absent_from_the_list_and_from_the_id(client):
    seed(client.fake_store, job_id="theirs", uid="uid-someone-else", summary=SUMMARY)

    assert HistoryResponse.model_validate(client.get("/api/jobs").json()).entries == []
    # Reported as absent rather than forbidden, so ids cannot be probed.
    assert client.get("/api/jobs/theirs").status_code == 404
