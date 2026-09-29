"""The regression gate, on the real permit sets, under pytest.

`tests/test_regression.py` has held the Sculpted expectations since the start,
but it defines `main()` and no test function, so `pytest tests/` has always
collected it and run nothing. Two of its expectations were lost on 26 August
(commit 55e1a67 — the occupant load stopped being read on G-1) and nothing went
red. This module is the same gate, collected.

Client PDFs are never committed (`samples/` is git-ignored), so each test skips
unless the path is supplied:

    FBC_TEST_PDF=samples/SCULPTED….pdf   FBC_ITEC_PDF=samples/ITEC….pdf  pytest tests/

The scorecard floor is a ratchet: raise it when a refinement moves the number,
never lower it to make a change pass.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fbcreview.pipeline import build_facts  # noqa: E402
from fbcreview.rules import registered, run_all  # noqa: E402

SCULPTED = os.environ.get("FBC_TEST_PDF", "")
needs_sculpted = pytest.mark.skipif(not SCULPTED or not os.path.exists(SCULPTED),
                                    reason="set FBC_TEST_PDF to the Sculpted permit set")

#: Scorecard floor on Sculpted — how much of the hand-built review must be
#: reproduced. See `scripts/scorecard.py`.
MIN_OPEN_EXACT = 10
MIN_VERIFIED_EXACT = 10


@pytest.fixture(scope="module")
def sculpted():
    facts = build_facts(SCULPTED)
    return facts, run_all(facts)


@needs_sculpted
def test_the_documented_findings_are_all_produced(sculpted):
    from test_regression import EXPECT_OPEN, EXPECT_PASS
    _facts, res = sculpted
    got_open = {}
    for f in res.findings:
        if f.status == "OPEN":
            got_open.setdefault(f.rule_id, set()).add(f.severity)
    for rid, sev in EXPECT_OPEN.items():
        assert sev in got_open.get(rid, set()), f"{rid}: expected {sev}"
    got_pass = {f.rule_id for f in res.findings if f.status == "PASS"}
    assert EXPECT_PASS <= got_pass, sorted(EXPECT_PASS - got_pass)


@needs_sculpted
def test_every_rule_fires_or_abstains(sculpted):
    _facts, res = sculpted
    spoke = {f.rule_id for f in res.findings} | {a.rule_id for a in res.abstentions}
    assert not set(registered()) - spoke


@needs_sculpted
@pytest.mark.parametrize("field,expected", [
    ("occupancy_group", "A-3"), ("construction_type", "III-B"),
    ("sprinkler_system", "YES"), ("occupant_load", 70.0),
    ("egress_width_factor", 0.15), ("risk_category", "III"),
    ("exposure_category", "C"), ("wind_speed_mph", 170.0),
    ("code_edition", "fbc2023"), ("building_area_sf", 1436.0),
    ("stories", 1.0),
])
def test_what_the_code_blocks_state_is_read(sculpted, field, expected):
    facts, _res = sculpted
    assert facts.store.value(field) == expected


@needs_sculpted
def test_the_two_general_sheets_disagreeing_about_the_common_path_is_seen(sculpted):
    """G-0 states 50 LF; G-1 states 75. Hand finding H-02 names both."""
    facts, _res = sculpted
    r = facts.store.resolve("egress.common_path", "required")
    assert r.conflict
    assert {r.value, *(g[0].value for g in r.rivals)} == {50.0, 75.0}


@needs_sculpted
def test_nothing_printed_on_the_sheets_is_called_absent(sculpted):
    """The old register said sprinklers and wind speed were stated nowhere."""
    _facts, res = sculpted
    absent = {a.rule_id for a in res.abstentions
              if "neither the drawings nor the declaration state this" in a.reason}
    assert not absent & {"DECL.SPRINKLER", "DECL.WIND_SPEED", "DECL.OCCUPANCY",
                         "DECL.CONSTRUCTION_TYPE", "DECL.RISK_CATEGORY"}


@needs_sculpted
def test_the_scorecard_does_not_go_backwards(sculpted):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    from scorecard import grade, summarise
    _facts, res = sculpted
    s = summarise(grade([f.to_dict() for f in res.findings],
                        [a.__dict__ for a in res.abstentions]))
    assert s["open_exact"] >= MIN_OPEN_EXACT, s
    assert s["verified_exact"] >= MIN_VERIFIED_EXACT, s


@needs_sculpted
@pytest.mark.parametrize("rule_id,fid,status,severity,sheet", [
    ("OCC.CLASSIFICATION_CONSISTENCY", "H-01", "OPEN", "HIGH", "G-1"),
    ("OCC.CLASSIFICATION_CONSISTENCY", "L-01", "OPEN", "LOW", "G-1"),
    ("EGRESS.FACTOR_CONSISTENCY", "M-02", "OPEN", "MEDIUM", "G-1"),
    ("EGRESS.OCCUPANT_LOAD_POSTING", "M-05", "OPEN", "MEDIUM", "G-1"),
    ("EGRESS.EXIT_COUNT", "V-04", "PASS", "VERIFIED", "G-0"),
    ("DOORS.CLEAR_WIDTH_REQUIREMENT", "V-05", "PASS", "VERIFIED", "G-0"),
    ("PLUMB.FIXTURE_COUNT", "V-06", "PASS", "VERIFIED", "G-1"),
    ("EGRESS.CEILING_HEIGHT", "V-17", "PASS", "VERIFIED", "A-3"),
    ("MECH.OUTDOOR_AIR_ARITHMETIC", "V-35", "PASS", "VERIFIED", "M-1"),
])
def test_the_checks_added_for_the_register_reproduce_it(sculpted, rule_id, fid, status,
                                                         severity, sheet):
    _facts, res = sculpted
    got = [(f.fid, f.status, f.severity, f.sheet) for f in res.findings if f.rule_id == rule_id]
    assert (fid, status, severity, sheet) in got, got


@needs_sculpted
def test_the_common_path_finding_names_both_sheets(sculpted):
    """Hand finding H-02: 'understated on G-0 and contradicts G-1'."""
    _facts, res = sculpted
    [f] = [f for f in res.findings if f.fid == "H-02"]
    assert f.title == "Common path requirement understated on G-0 and contradicts G-1"


@needs_sculpted
def test_the_values_the_register_checks_are_read_off_the_sheets(sculpted):
    facts, _res = sculpted
    assert {d.name: (d.width_provided_in, d.capacity) for d in facts.discharges} == {
        "EXIT DISCHARGE 1": (72.0, 360.0), "EXIT DISCHARGE 2": (36.0, 180.0)}
    assert min(t.height_ft for t in facts.ceilings) == 10.0
    assert {v.room: v.total_cfm for v in facts.ventilation if v.total_cfm} == {
        "RECEPTION": 20.0, "MAT STUDIO": 860.0, "STORAGE": 2.0}
    assert facts.plumbing.ratios["wc"] == (125.0, 65.0)
    assert facts.plumbing.provided == {"wc": 2, "lav": 2, "drinking_fountain": 1,
                                       "service_sink": 1}


@needs_sculpted
def test_the_two_ways_g0_writes_the_classification_of_work_are_one_answer(sculpted):
    """`CLASSIFICATION OF WORK: ALTERATION - LEVEL II` and `ALTERATION - LEVEL: II`."""
    facts, _res = sculpted
    r = facts.store.resolve("classification_of_work")
    assert not r.conflict and r.value == "ALTERATION - LEVEL II"
