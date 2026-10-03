"""The real drawing: EVERGREEN_BLDG_1.dwg, through LibreDWG, ingest and the engine.

The owner made a real DWG the reference for the CAD adapter (2026-10-03): an
AutoCAD 2018 precast hollowcore set, 23 MB, eight layouts, its title block an
external reference that was not sent with it. Every number below was measured
on it, and this test holds them.

It runs only where the drawing and the converter both are — the drawing is a
client's and is never committed (`samples/` is git-ignored), and LibreDWG is
not installed in CI — and only when asked, because it takes about three minutes
and a gigabyte:

    FBC_CAD_REFERENCE=1 FBC_DWG2DXF=/path/to/dwg2dxf pytest tests/test_cad_reference.py

What the drawing does NOT state is asserted as firmly as what it does: it is a
shop-drawing set with no code-analysis block, so the review must abstain on
occupancy, construction type and area rather than find them somewhere.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SAMPLE = ROOT / "samples" / "EVERGREEN_BLDG_1.dwg"

ezdxf = pytest.importorskip("ezdxf")

from fbcreview.cad import convert  # noqa: E402

def _why_not() -> str:
    """Why the gate cannot run here, or "" when it can. Asked in this order so
    the default suite never starts the converter just to find out."""
    if os.environ.get("FBC_CAD_REFERENCE") != "1":
        return "the real-drawing gate runs only with FBC_CAD_REFERENCE=1 (about three minutes)"
    if not SAMPLE.is_file():
        return "samples/EVERGREEN_BLDG_1.dwg is not here"
    if not convert.available():
        return "no dwg2dxf converter is installed (set FBC_DWG2DXF)"
    return ""


_SKIP = _why_not()
pytestmark = [pytest.mark.slow, pytest.mark.skipif(bool(_SKIP), reason=_SKIP or "runs")]

LAYOUTS = ["FLOORPLAN_VIEW1", "FLOORPLAN_VIEW2", "FLOORPLAN_BLANK", "SCHEDULES",
           "STAIR_DETAILS", "DETAILS_ALT_6VIEW", "DETAILS_6VIEW", "DETAILS_ALT_9VIEW"]
#: Read off the title-block ATTDEF prompted "SHEET No. (1)", which displays its
#: tag. FLOORPLAN_BLANK has none: it stays unidentified, never its tab name.
NUMBERS = ["6C", "2A", "p3", "SZ-2", "SZ-3", "SZ-1", "SZ-1", "SZ-1"]


@pytest.fixture(scope="module")
def evergreen(tmp_path_factory):
    import pymupdf

    from fbcreview import cad
    from fbcreview.pipeline import build_facts
    from fbcreview.rules import run_all

    work = tmp_path_factory.mktemp("evergreen")
    cs = cad.ingest(str(SAMPLE), str(work), name=SAMPLE.name)
    facts = build_facts(cs.pdf_path, cad=cs.data)
    result = run_all(facts)
    pdf = pymupdf.open(cs.pdf_path)
    yield cs, facts, result, pdf
    pdf.close()


def test_eight_sheets_one_per_layout_in_tab_order(evergreen):
    cs, _facts, _result, pdf = evergreen
    assert pdf.page_count == 8
    assert [p["layout"] for p in cs.pages] == LAYOUTS
    assert all(tuple(p.rect) == (0, 0, 2592, 1728) for p in pdf)      # ARCH D
    d = cs.data["drawings"][0]
    assert d["release"] == "AutoCAD 2018" and d["dxfversion"] == "AC1032"
    assert d["units"]["name"] == "inches" and d["units"]["basis"] == "stated"
    assert d["viewports_repaired"] > 0                                 # LibreDWG's status 0


def test_sheet_numbers_come_from_the_title_block_attdef(evergreen):
    cs, facts, _result, _pdf = evergreen
    assert [s.code for s in facts.sheets] == NUMBERS
    for p in cs.pages:
        if p["number"]:
            assert p["number_source"] == "title-block attdef “SHEET No. (1)”"
    assert cs.pages[2]["layout"] == "FLOORPLAN_BLANK" and cs.pages[2]["number"] == ""
    assert not set(LAYOUTS) & {s.code for s in facts.sheets}


def test_the_drafters_layers_survive(evergreen):
    cs, facts, _result, pdf = evergreen
    names = {v["name"] for v in pdf.get_ocgs().values()}
    assert len(names) >= 19
    assert "Doors & Windows" in names
    assert "Doors & Windows" in facts.meta["cad_layers"]
    assert len(cs.data["layers"]) == 71


def test_the_2a_viewport_is_exactly_one_eighth_inch_and_agrees_with_its_label(evergreen):
    _cs, facts, _result, _pdf = evergreen
    views = {v.scale.source.rsplit("viewport ", 1)[-1]: v for v in facts.geometry[1].views}
    vp = views["249E7B"]
    assert vp.scale.value == 9.0                                        # 1:96 = 9 pt per foot
    assert "1:96" in vp.scale.note
    assert "agrees with the printed label 1/8\" = 1'-0\"" in vp.scale.note
    assert not facts.geometry[1].scale_pt_per_ft            # several scales: none page-wide


def test_the_missing_title_block_xref_is_one_warning_naming_it(evergreen):
    cs, _facts, _result, _pdf = evergreen
    assert len(cs.warnings) == 1
    assert "Title Block GTB.dwg" in cs.warnings[0]
    assert cs.data["drawings"][0]["xrefs"] == {"Title Block GTB": "not uploaded"}


def test_the_code_edition_is_found_and_judged(evergreen):
    _cs, _facts, result, _pdf = evergreen
    assert any(f.rule_id == "CODE.EDITION_CURRENT" for f in result.findings)


def test_what_a_shop_drawing_set_does_not_state_is_abstained_on(evergreen):
    """No code-analysis block: no occupancy, no construction type, no area —
    and no measured area laundered into one."""
    cs, facts, _result, _pdf = evergreen
    store = facts.store
    for key in ("occupancy_group", "construction_type", "building_area_sf", "total_area_sf"):
        assert store.resolve(key) is None, key
    assert not [r for r in cs.data["claims"] if r["type"] == "area"]


def test_every_page_has_a_text_layer(evergreen):
    _cs, _facts, _result, pdf = evergreen
    assert all(p.get_text().strip() for p in pdf)
