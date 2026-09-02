"""Reviewing the same set again with more of the declaration answered.

The abstention this exists for is the commonest one on a real submittal:

    DECL.BUILDING_AREA — neither the drawings nor the declaration state this

The rule was right. It is waiting on one number. Before `POST /api/jobs/{id}/rerun`
the only way to give it that number was to upload the permit set a second time
and re-answer every other question along with it, so the remedy cost more than
the finding was worth and the abstention stayed there.

What is tested here is the part that would bite: that the second review is a
*separate* review rather than an edit of the first, that an answer already given
survives a request that does not mention it, and that the set is never re-sent —
because the whole reason this is cheap is that the PDF is already in the bucket.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from conftest import make_pdf, upload_form
from webapp import abstentions
from webapp.models import Job


def start(client, declaration=None, options=None):
    """A finished review, and its id."""
    form = upload_form(
        make_pdf(),
        options=None if options is None else json.dumps(options),
        declaration=None if declaration is None else json.dumps(declaration),
    )
    response = client.post("/api/review", files=form["files"], data=form["data"])
    assert response.status_code == 202, response.text
    job_id = response.json()["id"]
    client.fake_store.done.wait(timeout=60)
    client.fake_store.done.clear()
    return job_id


def rerun(client, job_id, **body):
    return client.post(f"/api/jobs/{job_id}/rerun", json=body)


# ── the mapping that makes the offer possible ─────────────────────────────
def test_a_rule_names_the_questions_that_would_let_it_run(client):
    """Inverted from the schema's own `unlocks`, so the two cannot disagree.

    This is what the workspace shows beside an abstention. Getting it from the
    schema rather than from a second hand-written table is the point: a field
    that gains a rule gains the offer with nothing else to update.
    """
    assert abstentions.unlocked_by("DECL.BUILDING_AREA") == [
        "building_area_sf", "total_area_sf",
    ]
    assert abstentions.unlocked_by("CODE.EDITION_CURRENT") == ["code_edition"]

    # Empty is the honest answer where no question would help. A geometric rule
    # that could not find its linework is not waiting on a questionnaire, and an
    # offer there would be a dead end dressed as a remedy.
    assert abstentions.unlocked_by("EGRESS.CORRIDOR_WIDTH") == []
    assert abstentions.unlocked_by("") == []


def test_the_classification_carries_it_to_the_client(client):
    rows = abstentions.classify_all([
        {"rule": "DECL.BUILDING_AREA",
         "reason": "neither the drawings nor the declaration state this"},
    ])
    assert rows[0]["unlocked_by"] == ["building_area_sf", "total_area_sf"]
    # The engine's own reason is never rewritten by any of this.
    assert rows[0]["reason"] == "neither the drawings nor the declaration state this"


# ── the re-run ────────────────────────────────────────────────────────────
def test_it_starts_a_second_review_of_the_same_set(client):
    first = start(client)
    response = rerun(client, first, declaration={"building_area_sf": 4200})
    assert response.status_code == 202, response.text
    second = response.json()["id"]
    assert second != first

    client.fake_store.done.wait(timeout=60)
    body = Job.model_validate(client.get(f"/api/jobs/{second}").json())
    assert body.state == "done"
    assert body.rerun_of == first
    assert body.declaration and body.declaration.building_area_sf == 4200


def test_the_first_review_is_not_touched(client):
    """A review is a dated statement about a set under stated assertions. The
    second one is allowed to say something different; rewriting the first would
    change what somebody was already told."""
    first = start(client, declaration={"stories": 1})
    before = client.get(f"/api/jobs/{first}").json()

    rerun(client, first, declaration={"building_area_sf": 4200})
    client.fake_store.done.wait(timeout=60)

    after = client.get(f"/api/jobs/{first}").json()
    assert after["declaration"] == before["declaration"]
    assert after["summary"] == before["summary"]
    assert after["rerun_of"] is None


def test_an_answer_already_given_survives_a_request_that_omits_it(client):
    """The browser sends the questions it asked about. A field it left out is
    unanswered-in-this-request, not withdrawn — losing an answer silently, on
    the one request whose purpose is to add one, would be the wrong default."""
    first = start(client, declaration={"stories": 2, "height_ft": 24})
    response = rerun(client, first, declaration={"building_area_sf": 4200})
    assert response.status_code == 202

    second = response.json()["id"]
    client.fake_store.done.wait(timeout=60)
    declared = client.get(f"/api/jobs/{second}").json()["declaration"]
    assert declared["stories"] == 2
    assert declared["height_ft"] == 24
    assert declared["building_area_sf"] == 4200


def test_the_set_is_never_re_uploaded(client):
    """The reason this is cheap. The PDF is already in the bucket and the
    admission profile is already on the record, so a re-run costs one pass of
    the engine and no bandwidth at all."""
    first = start(client)
    blobs_before = set(client.fake_files.blobs)

    response = rerun(client, first, declaration={"building_area_sf": 4200})
    second = response.json()["id"]
    client.fake_store.done.wait(timeout=60)

    uploads_after = {b for b in client.fake_files.blobs if b.startswith("uploads/")}
    assert uploads_after == {b for b in blobs_before if b.startswith("uploads/")}
    # It reads the first review's upload rather than a copy of its own.
    assert client.fake_store.docs[second]["upload_blob"] == \
        client.fake_store.docs[first]["upload_blob"]


def test_the_options_are_replayed_rather_than_defaulted(client):
    first = start(client, options={"edition": "fbc2023", "min_severity": "HIGH"})
    second = rerun(client, first, declaration={"stories": 1}).json()["id"]
    client.fake_store.done.wait(timeout=60)

    assert client.fake_store.docs[second]["options"]["min_severity"] == "HIGH"


# ── refusals ──────────────────────────────────────────────────────────────
def test_an_unknown_job_is_absent_rather_than_forbidden(client):
    """A job id must not be probeable for existence, here as everywhere else."""
    assert rerun(client, "nosuchjob", declaration={}).status_code == 404


def test_somebody_elses_review_cannot_be_re_run(client):
    first = start(client)
    client.fake_store.docs[first]["uid"] = "uid-someone-else"
    assert rerun(client, first, declaration={}).status_code == 404


def test_a_value_the_schema_does_not_know_is_refused_by_name(client):
    """The same refusal `POST /api/review` makes. Accepting it quietly would
    mean reviewing the set against something nobody chose."""
    first = start(client)
    response = rerun(client, first, declaration={"construction_type": "V-Z"})
    assert response.status_code == 400
    assert "construction_type" in response.text


def test_a_field_the_declaration_does_not_have_is_refused(client):
    first = start(client)
    response = rerun(client, first, declaration={"favourite_colour": "blue"})
    assert response.status_code == 422
