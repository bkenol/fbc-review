"""Reading a set for what it already states.

The declaration questionnaire asks for a dozen facts the engine can mostly read
off the drawings, so the form opens with those answers in it rather than blank.
Two properties matter and the rest is detail:

* a suggestion must never become a declaration by itself — `POST /api/prefill`
  starts nothing, stores nothing and changes nothing, and only what the
  applicant sends to `POST /api/review` is declared; and
* a value that cannot be offered honestly must be dropped rather than coerced,
  because a wrong number in front of an applicant who is about to assert it is
  worse than an empty field.
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from conftest import make_blank_pdf, make_pdf
from fbcreview import declaration_schema as schema
from webapp import prefill
from webapp.models import PrefillResponse


def post(client, pdf, name="set.pdf"):
    return client.post(
        "/api/prefill",
        files={"file": (name, io.BytesIO(pdf), "application/pdf")},
    )


def field(key: str) -> schema.Field:
    return schema.BY_KEY[key]


# ── rendering a value into a form control ─────────────────────────────────
def test_a_bool_becomes_the_string_the_select_holds():
    mixed = field("mixed_occupancy")
    assert prefill.control_value(mixed, True) == "true"
    assert prefill.control_value(mixed, False) == "false"


def test_an_integer_is_rounded_rather_than_shown_as_a_float():
    assert prefill.control_value(field("stories"), 2.0) == "2"


def test_a_whole_number_loses_its_decimal_point():
    # 4180.0 is a building area, not a measurement to four decimal places.
    assert prefill.control_value(field("building_area_sf"), 4180.0) == "4180"


def test_a_fractional_number_keeps_its_value():
    assert prefill.control_value(field("building_area_sf"), 4180.5) == "4180.5"


def test_an_enum_the_form_cannot_hold_is_dropped_not_coerced():
    occupancy = field("occupancy_group")
    assert occupancy.choices, "this test needs a field with a served choice list"
    assert prefill.control_value(occupancy, occupancy.choices[0]) == occupancy.choices[0]
    # Offering a value the select has no option for would put a number in front
    # of the applicant that they cannot see the provenance of, and cannot clear
    # by choosing it again.
    assert prefill.control_value(occupancy, "NOT-A-GROUP") is None


def test_none_is_never_offered():
    for key in ("occupancy_group", "stories", "mixed_occupancy", "building_area_sf"):
        assert prefill.control_value(field(key), None) is None


def test_a_number_that_will_not_convert_is_dropped():
    assert prefill.control_value(field("building_area_sf"), "not a number") is None


# ── the endpoint ──────────────────────────────────────────────────────────
def test_prefill_reads_a_set_without_starting_anything(client):
    response = post(client, make_pdf(pages=2))
    assert response.status_code == 200, response.text

    body = PrefillResponse.model_validate(response.json())
    assert body.pages == 2
    assert body.filename == "set.pdf"
    assert body.bytes > 0

    # The whole point: no job, no upload, nothing to poll.
    assert client.fake_store.docs == {}
    assert client.fake_files.blobs == {}


def test_every_suggestion_names_a_served_field_and_where_it_was_read(client):
    body = PrefillResponse.model_validate(post(client, make_pdf()).json())
    served = {f.key for f in schema.FIELDS}
    for suggestion in body.fields:
        assert suggestion.key in served
        assert suggestion.value != ""
        # A suggestion without provenance is a guess, and the form has no way to
        # tell the applicant where it came from.
        assert suggestion.source


def test_a_readable_set_that_states_nothing_yields_no_suggestions(client):
    # The case the declaration exists to cover. A set the engine can open but
    # which states nothing about itself must come back empty and successful —
    # the questionnaire simply opens blank, as it always did.
    body = PrefillResponse.model_validate(post(client, make_pdf(text="")).json())
    assert body.fields == []


def test_an_unreadable_file_is_refused_the_way_a_review_refuses_it(client):
    # Consistency, not politeness: a file the review will reject should be
    # rejected here too, with the same code. Quietly returning no suggestions
    # would let someone fill in a whole questionnaire before finding out.
    response = post(client, make_blank_pdf())
    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "corrupt_pdf"


def test_a_scanned_set_is_read_rather_than_refused(client):
    from conftest import make_raster_pdf

    # POST /api/review refuses a raster set unless conversion is asked for.
    # Prefill has no such stake: it simply states very little, and saying so is
    # more useful than declining to look.
    response = post(client, make_raster_pdf())
    assert response.status_code == 200, response.text


def test_prefill_needs_the_caller_to_be_signed_in(anon_client):
    response = post(anon_client, make_pdf())
    assert response.status_code in (401, 403), response.text
