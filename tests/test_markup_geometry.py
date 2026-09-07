"""A markup has to reach the person who has to act on it, still pointing.

Written from training feedback `6f65009d1762` on **SUB1- JSP_Naples,FL_MEP.pdf**
— a coverage report (`gap`: "it abstained, but the data is right here") that
arrived at the owner's queue reading:

    - **box** on page 2 at no geometry (PDF user space, origin top-left)

The reviewer drew a box around the thing the review had missed. By the time the
report reached the person who had to fix it, the box no longer pointed at
anything, and the report could not be acted on.

Two separate defects put it there, and both are in `webapp/`:

* `webapp/notify.py` rendered the markup by reading `markup["rect"]` and
  `markup["points"]`. A stored markup has neither — it carries `geometry`, a
  dict of `x0/y0/x1/y1/points`. Both lookups missed, every time, so the
  expression fell through to the literal string "no geometry" for **every**
  markup this feature has ever escalated.
* `MarkupGeometry` defaults all four coordinates to `0`, so a `box` submitted
  with no rectangle at all validates and stores as a degenerate one. Only a
  `note` is meant to carry no geometry. A coverage report is required to point
  at where on the sheet the miss was — a zero-area box satisfies that check
  while pointing nowhere.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from webapp import notify

from test_training import draw, seed_done_job  # noqa: E402


# ══════════════════════════════════════════════════════════════════════════
# 1. The prompt has to carry the coordinates the reviewer drew
# ══════════════════════════════════════════════════════════════════════════
def test_a_boxed_region_reaches_the_prompt_as_a_rectangle():
    """The failing input, written down: the record shape the store actually
    holds, through the renderer that had to describe it."""
    record = {
        "id": "fb-1",
        "subject": "coverage",
        "disposition": "needs_component",
        "answers": {"gap": "missed_violation"},
        "markup": {
            "kind": "box", "page": 2, "sheet": "M.101",
            "geometry": {"x0": 120.5, "y0": 300.0, "x1": 470.25, "y1": 356.0,
                         "points": []},
        },
    }
    markdown = notify.feature_prompt(record)

    assert "no geometry" not in markdown
    for corner in ("120.5", "300", "470.25", "356"):
        assert corner in markdown, f"{corner} missing from the prompt"


def test_a_freehand_path_reaches_the_prompt_as_its_points():
    record = {
        "id": "fb-2",
        "subject": "coverage",
        "disposition": "needs_component",
        "answers": {"gap": "missed_violation"},
        "markup": {
            "kind": "freehand", "page": 3, "sheet": "E.101",
            "geometry": {"x0": 0, "y0": 0, "x1": 0, "y1": 0,
                         "points": [[10.0, 20.0], [30.0, 40.0], [50.0, 60.0]]},
        },
    }
    markdown = notify.feature_prompt(record)
    assert "no geometry" not in markdown
    assert "3 points" in markdown or "[10.0, 20.0]" in markdown


def test_a_note_is_still_allowed_to_carry_no_geometry():
    """`note` is defined as a pin with no geometry. It must not be dressed up
    as a rectangle at the origin, which is a place on the sheet."""
    record = {
        "id": "fb-3",
        "subject": "coverage",
        "disposition": "needs_component",
        "answers": {"gap": "missed_violation"},
        "markup": {"kind": "note", "page": 1, "sheet": "M.001",
                   "geometry": {"x0": 0, "y0": 0, "x1": 0, "y1": 0, "points": []}},
    }
    markdown = notify.feature_prompt(record)
    assert "no geometry" in markdown


def test_the_prompt_still_renders_when_a_markup_carries_nothing():
    """Records predating the geometry fix are in the store. The renderer must
    describe them rather than raise."""
    record = {
        "id": "fb-4", "subject": "coverage", "disposition": "needs_component",
        "answers": {"gap": "missed_violation"},
        "markup": {"kind": "box", "page": 2},
    }
    assert "no geometry" in notify.feature_prompt(record)


# ══════════════════════════════════════════════════════════════════════════
# 2. A shape that needs geometry may not be stored without any
# ══════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("kind", ["box", "highlight", "cloud", "strikeout"])
def test_a_region_shape_needs_a_region(training_client, kind):
    seed_done_job(training_client)
    r = training_client.post("/api/jobs/job-1/markups", json={
        "page": 2, "kind": kind,
        "geometry": {"x0": 0, "y0": 0, "x1": 0, "y1": 0, "points": []},
    })
    assert r.status_code == 400, r.text
    assert "region" in r.json()["error"]["message"].lower()


@pytest.mark.parametrize("kind", ["arrow", "freehand"])
def test_a_path_shape_needs_a_path(training_client, kind):
    seed_done_job(training_client)
    r = training_client.post("/api/jobs/job-1/markups", json={
        "page": 2, "kind": kind,
        "geometry": {"x0": 0, "y0": 0, "x1": 0, "y1": 0, "points": []},
    })
    assert r.status_code == 400, r.text
    assert "path" in r.json()["error"]["message"].lower()


def test_a_note_needs_neither(training_client):
    seed_done_job(training_client)
    r = training_client.post("/api/jobs/job-1/markups", json={
        "page": 2, "kind": "note", "geometry": {}, "comment": "about this sheet",
    })
    assert r.status_code == 200, r.text


def test_a_drawn_box_is_still_accepted(training_client):
    """The guard must not cost the ordinary case."""
    seed_done_job(training_client)
    created = draw(training_client, page=2, kind="box",
                   geometry={"x0": 120.5, "y0": 300.0, "x1": 470.25, "y1": 356.0,
                             "points": []})
    assert created["geometry"]["x1"] == 470.25


def test_a_coverage_report_cannot_point_at_a_zero_area_box(training_client):
    """`/feedback` already refuses a coverage report with no markup at all. A
    box with no region is the same report with an extra step."""
    seed_done_job(training_client)
    r = training_client.post("/api/jobs/job-1/markups", json={
        "page": 2, "kind": "box",
        "geometry": {"x0": 40, "y0": 40, "x1": 40, "y1": 40, "points": []},
    })
    assert r.status_code == 400, r.text
