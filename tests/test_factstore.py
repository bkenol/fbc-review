"""The field catalog, the deterministic reader and the fact store.

Each case is a failure the old extractors had on a real set, written down as the
input that produced it (`docs/ENGINE-TEARDOWN.md` §3):

* `FIRE SPRINKLERS:` / `SPRINKLERED` and `BASIC WIND SPEED:` / `ULTIMATE: 170 MPH`
  on G-0 were reported as "neither the drawings nor the declaration state this".
* `OCCUPANCY: ASSEMBLY (A-3)` read as `A` — the subgroup lost.
* `OCCUPANT LOAD: 35` under `EXIT DISCHARGE 1` is one exit's share, not the building's.
* `SITE AREA` beside `BUILDING AREA` must never be read as the building.
* `2023 FBC-MECHANICAL 8TH EDITION` is an edition statement
  (`docs/FEATURE-PROMPT-inference-ladder.md` §1.2).
* With nothing stated, the egress rules assumed Group A-3, sprinklered.
"""
from __future__ import annotations

import pymupdf
import pytest

from fbcreview.factstore import AI, PAIR, Claim, FactStore
from fbcreview.facts import CodeDatum, ProjectFacts, Sheet
from fbcreview.layout import page_layout
from fbcreview.read import read_layouts
from fbcreview.read import parse as P
from fbcreview.rules import run_all


def _store_for(page_writer, width=1224, height=792):
    doc = pymupdf.open()
    page = doc.new_page(width=width, height=height)
    page_writer(page)
    layouts = {0: page_layout(page)}
    store = FactStore()
    store.extend(read_layouts(layouts, {0: "G-0"}))
    return store


def _lines(*rows, x=40, y=100, step=15, size=9):
    def write(page):
        for i, row in enumerate(rows):
            page.insert_text((x, y + i * step), row, fontsize=size)
    return write


# ══ parsers ════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("text,expected", [
    ("ASSEMBLY (A-3)", "A-3"), ("GROUP A", "A"), ("BUSINESS / OFFICE, PROFESSIONAL", "B"),
    ("A-3", "A-3"), ("70", None), ("TO EXISTING MECHANICAL", None),
])
def test_occupancy(text, expected):
    assert P.occupancy(text) == expected


@pytest.mark.parametrize("text,expected", [
    ("SPRINKLERED", "YES"), ("YES", "YES"), ("FULLY SPRINKLERED", "YES"),
    ("NFPA 13", "NFPA13"), ("YES, PER NFPA 13R", "NFPA13R"),
    ("NON-SPRINKLERED", "NONE"), ("NO", "NONE"), ("NONE", "NONE"), ("SEE M-1", None),
])
def test_sprinkler(text, expected):
    assert P.sprinkler(text) == expected


@pytest.mark.parametrize("text,expected", [
    ("ULTIMATE: 170 MPH NOMINAL: 132 MPH", 170.0), ("170 MPH", 170.0),
    ("VULT = 155 MPH", 155.0), ("NOMINAL: 132 MPH", None), ("12 MPH", None),
])
def test_wind(text, expected):
    assert P.wind(text) == expected


@pytest.mark.parametrize("text,expected", [
    ("III-B", "III-B"), ("TYPE II-B NON-COMBUSTIBLE NON-RATED", "II-B"),
    ("TYPE 2B", "II-B"), ("V-B", "V-B"), ("SEE NOTES", None),
])
def test_construction(text, expected):
    assert P.construction(text) == expected


@pytest.mark.parametrize("text,expected", [
    ("2023 FLORIDA BUILDING CODE 8TH EDITION - BUILDING", "fbc2023"),
    ("VENTILATION REQUIREMENTS PER 2023 FBC-MECHANICAL 8TH EDITION", "fbc2023"),
    ("FLORIDA BUILDING CODE, 7TH EDITION (2020)", "fbc2020"),
    ("2020 NATIONAL ELECTRIC CODE - NFPA 70", None),
    ("FBC 2902.2 #5: SEPARATE FACILITIES", None),
])
def test_edition(text, expected):
    assert P.edition(text) == expected


# ══ reading ════════════════════════════════════════════════════════════════
def test_values_the_old_sweep_called_absent_are_read():
    store = _store_for(_lines("FIRE SPRINKLERS: SPRINKLERED",
                              "BASIC WIND SPEED: ULTIMATE: 170 MPH",
                              "OCCUPANCY: ASSEMBLY (A-3)"))
    assert store.value("sprinkler_system") == "YES"
    assert store.value("wind_speed_mph") == 170.0
    assert store.value("occupancy_group") == "A-3"


def test_the_site_area_is_never_the_building_area():
    """Every one of these is printed in square feet on real permit sets, and
    only one of them is the building."""
    store = _store_for(_lines("SITE AREA: 96,267 SF", "LOT AREA: 2.21 AC",
                              "PARKING AREA: 31,500 SF", "IMPERVIOUS AREA: 61,000 SF",
                              "BUILDING AREA: 15,376 SF"))
    assert store.value("building_area_sf") == 15376.0
    assert not store.resolve("building_area_sf").conflict


def test_an_occupant_load_factor_is_not_the_occupant_load():
    store = _store_for(_lines("OCCUPANT LOAD FACTOR: 150", "TOTAL OCCUPANT LOAD: 152"))
    assert store.value("occupant_load") == 152.0


def test_one_exits_share_is_not_the_buildings_occupant_load():
    def write(page):
        page.insert_text((40, 100), "EXIT DISCHARGE 1:", fontsize=9)
        page.insert_text((40, 115), "OCCUPANT LOAD: 35", fontsize=9)
        page.insert_text((400, 100), "BUILDING CODE ANALYSIS", fontsize=9)
        page.insert_text((400, 115), "OCCUPANT LOAD: 70", fontsize=9)
    store = _store_for(write)
    assert store.value("occupant_load") == 70.0
    assert all(c.value == 70.0 for c in store.claims("occupant_load"))


def test_a_stated_requirement_and_what_is_provided_are_separate_facts():
    store = _store_for(_lines("MAX. COMMON PATH: 75 LF", "COMMON PATH: 8'-1\""))
    assert store.value("egress.common_path", "required") == 75.0
    assert round(store.value("egress.common_path", "provided"), 2) == 8.08


# ══ resolution ═════════════════════════════════════════════════════════════
def _claim(field, value, sheet="G-0", page=0, method=PAIR, score=1.0, role=""):
    return Claim(field, value, f"{field} {value}", page, sheet, (0, 0, 1, 1), method,
                 score=score, role=role)


def test_the_most_specific_agreeing_value_wins():
    store = FactStore()
    store.extend([_claim("occupancy_group", "A", "G-1", 1),
                  _claim("occupancy_group", "A-3", "G-0", 0)])
    r = store.resolve("occupancy_group")
    assert r.value == "A-3" and not r.conflict


def test_two_sheets_agreeing_is_high_confidence():
    store = FactStore()
    store.extend([_claim("risk_category", "III", "G-0", 0),
                  _claim("risk_category", "III", "S-1", 6)])
    assert store.resolve("risk_category").confidence == "high"
    single = FactStore()
    single.add(_claim("risk_category", "III"))
    assert single.resolve("risk_category").confidence == "medium"


def test_two_readers_agreeing_is_high_confidence():
    store = FactStore()
    store.extend([_claim("occupant_load", 70.0), _claim("occupant_load", 70.0, method=AI)])
    r = store.resolve("occupant_load")
    assert r.confidence == "high" and r.methods == ["ai", "pair"]


def test_a_disagreement_is_kept_not_resolved_away():
    store = FactStore()
    store.extend([_claim("egress.common_path", 50.0, "G-0", 0, role="required"),
                  _claim("egress.common_path", 75.0, "G-1", 1, role="required")])
    r = store.resolve("egress.common_path", "required")
    assert r.conflict
    assert {g[0].value for g in r.rivals} | {r.value} == {50.0, 75.0}


def test_nothing_stated_resolves_to_nothing():
    assert FactStore().resolve("occupancy_group") is None
    assert FactStore().value("sprinkler_system") is None


# ══ no hidden defaults ═════════════════════════════════════════════════════
def _facts_with_common_path(occupancy=None, sprinkler=None):
    facts = ProjectFacts(source_path="x.pdf")
    facts.sheets = [Sheet(0, "G-0", "COVER", "G")]
    facts.code_data = [CodeDatum("1006.2.1", "COMMON PATH OF TRAVEL", "50 LF", "8'-1\"",
                                 required=50.0, provided=8.08, unit="ft", sheet="G-0")]
    store = FactStore()
    if occupancy:
        store.add(_claim("occupancy_group", occupancy))
    if sprinkler:
        store.add(_claim("sprinkler_system", sprinkler))
    facts.store = store
    return facts


def test_an_egress_rule_abstains_rather_than_assume_an_occupancy():
    res = run_all(_facts_with_common_path())
    assert not any(f.rule_id == "EGRESS.COMMON_PATH" for f in res.findings)
    stood = [a for a in res.abstentions if a.rule_id == "EGRESS.COMMON_PATH"]
    assert stood and "not stated on the drawings" in stood[0].reason
    assert "Occupancy" in stood[0].detail and "Sprinkler" in stood[0].detail


def test_an_egress_rule_uses_the_occupancy_the_sheet_states():
    res = run_all(_facts_with_common_path("B", "YES"))
    f = next(f for f in res.findings if f.rule_id == "EGRESS.COMMON_PATH")
    # Group B, sprinklered: Table 1006.2.1 is 100 ft. Under the old A-3 default
    # this read 75 ft and said the set understated it.
    assert "100 feet" in f.result and "Group B with a sprinkler system" in f.result


def test_a_non_sprinklered_building_is_not_described_as_sprinklered():
    res = run_all(_facts_with_common_path("A-3", "NONE"))
    f = next(f for f in res.findings if f.rule_id == "EGRESS.COMMON_PATH")
    assert "without a sprinkler system" in f.result
