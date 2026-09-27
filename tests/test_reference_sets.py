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
MIN_OPEN_EXACT = 6
MIN_VERIFIED_EXACT = 5


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
