"""The declaration on the wire.

Two things matter here and the rest is detail: submitting no declaration must
reproduce exactly what the service produced before the field existed, and a
value the schema does not recognise must be refused by name rather than
silently dropped. Silently dropping it would mean reviewing the set against
something the user did not choose.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from conftest import make_pdf, upload_form
from webapp import storage as storage_mod
from webapp.models import ConfigResponse, FindingsDocument


def post(client, pdf, options=None, declaration=None):
    form = upload_form(pdf, options=options, declaration=declaration)
    return client.post("/api/review", files=form["files"], data=form["data"])


def finish(client, response):
    assert response.status_code == 202, response.text
    job_id = response.json()["id"]
    client.fake_store.done.wait(timeout=60)
    client.fake_store.done.clear()
    raw = client.fake_files.blobs[f"outputs/{job_id}/{storage_mod.FINDINGS}"]
    return job_id, FindingsDocument.model_validate_json(raw)


# ── the config contract ───────────────────────────────────────────────────
def test_config_serves_the_whole_questionnaire(client):
    body = ConfigResponse.model_validate(client.get("/api/config").json())

    from fbcreview.declaration import FIELD_NAMES

    assert {f.key for f in body.declaration_fields} == set(FIELD_NAMES)
    assert {g.key for g in body.declaration_groups} == {
        f.group for f in body.declaration_fields
    }
    # Both vocabularies, served together. The client must be able to switch
    # between them without carrying a copy of either.
    for field in body.declaration_fields:
        assert field.pro_label and field.simple_label
        assert field.pro_help and field.simple_help
        if field.kind == "enum":
            assert field.choices and field.choice_labels

    # The tolerances are published too, so the form can explain what counts as
    # a disagreement rather than leaving the user to guess.
    area = next(f for f in body.declaration_fields if f.key == "building_area_sf")
    assert area.tolerance and area.tolerance.rel == 0.02
    assert body.declaration_unlockable


def test_the_superseded_edition_is_listed_and_disabled(client):
    body = client.get("/api/config").json()
    editions = {e["id"]: e for e in body["editions"]}
    # A set can be *drawn* to the 7th Edition — that is what CODE.EDITION_CURRENT
    # exists to say — but this build carries no corpus to review against it.
    assert editions["fbc2020"]["available"] is False
    assert editions["fbc2023"]["available"] is True


# ── omitting the declaration changes nothing ──────────────────────────────
def test_omitting_the_declaration_reproduces_todays_output(client):
    pdf = make_pdf(pages=2)
    _id, without = finish(client, post(client, pdf))
    _id, empty = finish(client, post(client, pdf, declaration="{}"))

    assert [f.model_dump() for f in without.findings] == [
        f.model_dump() for f in empty.findings
    ]
    assert without.declaration is None and empty.declaration is None
    assert all(f.scenario == "both" and f.basis == "drawings" for f in without.findings)


def test_a_submitted_declaration_is_reported_back(client):
    body = json.dumps({"occupancy_group": "B", "construction_type": "II-B", "stories": 1})
    job_id, document = finish(client, post(client, make_pdf(pages=2), declaration=body))

    assert document.declaration is not None
    assert document.declaration.answered == 3
    assert document.declaration.declaration.occupancy_group == "B"
    states = {f.field: f.state for f in document.declaration.fields}
    assert states["occupancy_group"] in ("DECLARED_ONLY", "CORROBORATED", "CONFLICT")
    # Everything nobody answered and the drawings do not state stays UNKNOWN.
    assert states["zoning"] == "UNKNOWN"

    fetched = client.get(f"/api/jobs/{job_id}/declaration")
    assert fetched.status_code == 200
    assert fetched.json()["construction_type"] == "II-B"
    assert fetched.json()["zoning"] is None


def test_the_job_record_carries_the_declaration(client):
    body = json.dumps({"risk_category": "II"})
    job_id, _doc = finish(client, post(client, make_pdf(pages=2), declaration=body))

    record = client.fake_store.get(job_id)
    assert record["declaration"]["risk_category"] == "II"
    assert client.get(f"/api/jobs/{job_id}").json()["declaration"]["risk_category"] == "II"


# ── validation ────────────────────────────────────────────────────────────
@pytest.mark.parametrize("body,needle", [
    ('{"occupancy_group": "Z-9"}', "occupancy_group"),
    ('{"construction_type": "VI-C"}', "construction_type"),
    ('{"sprinkler_system": "nfpa9000"}', "sprinkler_system"),
    ('{"exposure_category": "Z"}', "exposure_category"),
    ('{"code_edition": "fbc1999"}', "code_edition"),
    ('{"stories": 1.5}', "stories"),
    ('{"height_ft": "tall"}', "height_ft"),
    ('{"not_a_field": 1}', "not_a_field"),
])
def test_an_unrecognised_value_is_refused_by_name(client, body, needle):
    response = post(client, make_pdf(), declaration=body)
    assert response.status_code == 400
    detail = response.json()["error"]
    assert detail["code"] == "invalid_request"
    assert needle in detail["message"], detail["message"]


def test_a_declaration_that_is_not_an_object_is_refused(client):
    for body in ("[]", "not json", '"B"'):
        response = post(client, make_pdf(), declaration=body)
        assert response.status_code == 400


def test_a_blank_answer_is_not_an_answer(client):
    """An empty string from an untouched form field must read as unanswered, not
    as an enum value the schema would then reject."""
    body = json.dumps({"occupancy_group": "", "zoning": "", "stories": None})
    _id, document = finish(client, post(client, make_pdf(pages=2), declaration=body))
    assert document.declaration is None


# ── the deprecated bridge off ReviewOptions ───────────────────────────────
def test_occupancy_sent_in_options_seeds_the_declaration(client):
    """`occupancy_group` and `sprinklered` were always building facts in the
    wrong place. A client that still sends them keeps working."""
    options = json.dumps({"occupancy_group": "B", "sprinklered": False})
    _id, document = finish(client, post(client, make_pdf(pages=2), options=options))

    assert document.declaration is not None
    assert document.declaration.declaration.occupancy_group == "B"
    assert document.declaration.declaration.sprinkler_system == "none"


def test_the_declaration_wins_over_the_deprecated_option(client):
    options = json.dumps({"occupancy_group": "B"})
    body = json.dumps({"occupancy_group": "M"})
    _id, document = finish(client, post(client, make_pdf(pages=2),
                                        options=options, declaration=body))
    assert document.declaration.declaration.occupancy_group == "M"


def test_a_defaulted_option_is_not_treated_as_an_answer(client):
    """The form's untouched default must not become a declared fact — that is
    the difference between "the user said A-3" and "nobody said anything"."""
    _id, document = finish(client, post(client, make_pdf(pages=2), options="{}"))
    assert document.declaration is None


# ── ownership ─────────────────────────────────────────────────────────────
def test_someone_elses_declaration_is_absent_not_forbidden(client):
    job_id, _doc = finish(client, post(client, make_pdf(pages=2),
                                       declaration='{"risk_category": "II"}'))
    client.fake_store.docs[job_id]["uid"] = "uid-someone-else"
    assert client.get(f"/api/jobs/{job_id}/declaration").status_code == 404


def test_the_published_schema_carries_the_new_types(client):
    schemas = client.get("/openapi.json").json()["components"]["schemas"]
    for name in ("ProjectDeclaration", "DeclarationField", "DeclarationGroup",
                 "DeclarationReport", "ReconciledField", "DeclarationTolerance"):
        assert name in schemas, f"{name} missing from the generated schema"
    finding = schemas["Finding"]["properties"]
    assert "scenario" in finding and "basis" in finding
