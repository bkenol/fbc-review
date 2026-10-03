"""`STRUCT.WIND_STANDARD` across the 9th Edition effective date.

The defect, reproduced on 2026-10-03 with the sets built below:

* `fbcreview/codes/editions.py` records `ASCE7["fbc2026"] = None` — the 9th
  Edition's ASCE 7 adoption is not in the corpus, deliberately.
* The rule decided "superseded" from effective dates alone. From 2026-12-31
  every 8th Edition set (ASCE 7-22) got H-WIND, titled *Wind design is to
  ASCE 7-22, which 9th Edition replaced*, whose result said the 9th Edition
  "adopts None" and whose action said to re-read Vult "from None".

A set is superseded *for wind* only when the edition in force adopts a
different ASCE 7. When the corpus does not record what the edition in force
adopts, the rule cannot say either way, and abstains.

The full suite was also run with every rule's `today()` moved to 2027-01-15:
it passed, defect included, so nothing committed would have caught this.
"""
from __future__ import annotations

import datetime as dt

import pymupdf
import pytest

from fbcreview.codes import editions as E
from fbcreview.pipeline import build_facts
from fbcreview.rules import run_all

RULE = "STRUCT.WIND_STANDARD"
#: After the 9th Edition takes effect (2026-12-31).
AS_OF = dt.date(2027, 1, 15)
#: Before it — the date this defect was reproduced.
BEFORE = dt.date(2026, 10, 3)


def _set(tmp_path, ordinal: str) -> str:
    doc = pymupdf.open()
    page = doc.new_page(width=1224, height=792)
    for i, row in enumerate([f"FLORIDA BUILDING CODE {ordinal} EDITION",
                             "BASIC WIND SPEED: ULTIMATE: 170 MPH",
                             "RISK CATEGORY: II"]):
        page.insert_text((40, 100 + i * 15), row, fontsize=9)
    page.insert_text((1100, 760), "G-1", fontsize=15)
    path = tmp_path / f"{ordinal}.pdf"
    doc.save(str(path))
    return str(path)


def _wind(path: str, on: dt.date):
    facts = build_facts(path)
    facts.meta["as_of"] = on
    res = run_all(facts)
    return ([f for f in res.findings if f.rule_id == RULE],
            [a for a in res.abstentions if a.rule_id == RULE])


@pytest.fixture(scope="module")
def sets(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("wind")
    return {"7th": _set(tmp, "7TH"), "8th": _set(tmp, "8TH")}


def test_the_fixtures_state_what_the_rule_needs(sets):
    """Guard the guard: an abstention for "not stated" would pass the tests
    below for the wrong reason."""
    for key, path in (("fbc2020", sets["7th"]), ("fbc2023", sets["8th"])):
        facts = build_facts(path)
        assert facts.store.value("code_edition") == key
        assert facts.store.value("wind_speed_mph") == 170.0


# ══ the corpus as it is: 9th Edition ASCE 7 not recorded ══════════════════
@pytest.mark.parametrize("which", ["7th", "8th"])
def test_with_no_adoption_recorded_the_rule_abstains_for_every_set(sets, which):
    assert E.edition("fbc2026").asce7 is None, \
        "the corpus now records the 9th Edition's ASCE 7 — see the patched tests below"
    findings, abstentions = _wind(sets[which], AS_OF)
    assert findings == []
    [a] = abstentions
    assert a.reason == "the edition in force has no ASCE 7 adoption recorded"
    assert a.detail == "fbc2026"


@pytest.mark.parametrize("which", ["7th", "8th"])
def test_with_no_adoption_recorded_no_finding_prints_a_missing_standard(sets, which):
    """Every finding in the review, not only this rule's: an unrecorded ASCE 7
    must never be printed as the word "None"."""
    facts = build_facts(sets[which])
    facts.meta["as_of"] = AS_OF
    for f in run_all(facts).findings:
        for text in (f.title, f.result, f.code, f.action):
            assert "adopts None" not in text and "from None" not in text, (f.rule_id, text)
            assert "→ None" not in text, (f.rule_id, text)


# ══ with the adoption recorded — patched here, never in the corpus ════════
@pytest.fixture
def ninth_adopts_7_22(monkeypatch):
    """What the owner's DECIDE D2 would record, applied to this test only."""
    monkeypatch.setitem(E.ASCE7, "fbc2026", "ASCE 7-22")
    assert E.edition("fbc2026").asce7 == "ASCE 7-22"


def test_a_7th_edition_set_is_told_the_standard_moved_to_7_22(sets, ninth_adopts_7_22):
    findings, abstentions = _wind(sets["7th"], AS_OF)
    assert abstentions == []
    [f] = findings
    assert (f.fid, f.status, f.severity) == ("H-WIND", "OPEN", "HIGH")
    assert f.title == "Wind design is to ASCE 7-16, which 9th Edition replaced"
    assert "adopts ASCE 7-22" in f.result
    assert f.code == "FBC-B 1609 · 1609.3 · ASCE 7-16 → ASCE 7-22"
    assert f.action == "Re-read Vult for this parcel from ASCE 7-22 and re-run the wind analysis."


def test_an_8th_edition_set_keeps_its_wind_basis(sets, ninth_adopts_7_22):
    """Same ASCE 7 in the cited and the in-force edition: the maps did not move."""
    findings, abstentions = _wind(sets["8th"], AS_OF)
    assert abstentions == []
    [f] = findings
    assert (f.fid, f.status, f.severity) == ("V-WIND", "PASS", "VERIFIED")
    assert f.title == "Wind design basis is the current standard, ASCE 7-22"


# ══ before 2026-12-31 nothing changes ═════════════════════════════════════
def test_before_the_9th_edition_a_7th_edition_set_still_gets_h_wind(sets):
    findings, abstentions = _wind(sets["7th"], BEFORE)
    assert abstentions == []
    [f] = findings
    assert f.fid == "H-WIND"
    assert f.title == "Wind design is to ASCE 7-16, which 8th Edition replaced"
    assert f.code == "FBC-B 1609 · 1609.3 · ASCE 7-16 → ASCE 7-22"


def test_before_the_9th_edition_an_8th_edition_set_still_verifies(sets):
    findings, abstentions = _wind(sets["8th"], BEFORE)
    assert abstentions == []
    [f] = findings
    assert f.fid == "V-WIND"


@pytest.mark.parametrize("on,expect", [
    (dt.date(2026, 12, 30), "V-WIND"),       # last day of the 8th Edition
    (dt.date(2026, 12, 31), None),           # the 9th takes effect; abstain
])
def test_the_boundary_is_the_effective_date(sets, on, expect):
    findings, abstentions = _wind(sets["8th"], on)
    if expect is None:
        assert findings == [] and len(abstentions) == 1
    else:
        assert [f.fid for f in findings] == [expect] and abstentions == []
