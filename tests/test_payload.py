"""`findings.json`'s findings: unique keys, a place to draw each one, and what it read.

The viewer used to place a finding by searching the text layer for `anchor` on
`page`, compared against its own 1-based page numbers — every finding one sheet
late, the cover sheet never, and an anchor recovered only by OCR not at all
(`docs/ENGINE-TEARDOWN.md` §8). The engine now says where, in the viewer's own
coordinate space, and keys every finding uniquely.
"""
from __future__ import annotations

import json

import pymupdf
import pytest

from fbcreview.ai.readings import Readings
from fbcreview.ai.schema import FieldReading, SheetReading
from fbcreview.payload import findings_payload
from fbcreview.pipeline import build_facts
from fbcreview.rules import Finding, run_all
from webapp import models


def _set(tmp_path, rotate=0, rows=("OCCUPANCY: ASSEMBLY (A-3)", "RISK CATEGORY: III",
                                   "OCCUPANT LOAD: 70")):
    doc = pymupdf.open()
    page = doc.new_page(width=1224, height=792)
    for i, row in enumerate(rows):
        page.insert_text((40, 100 + i * 15), row, fontsize=9)
    page.insert_text((1100, 760), "G-0", fontsize=15)
    if rotate:
        page.set_rotation(rotate)
    path = tmp_path / "set.pdf"
    doc.save(str(path))
    return str(path)


def _finding(fid, **over):
    base = dict(fid=fid, rule_id="X.Y", status="OPEN", severity="HIGH", discipline="d",
                page=0, sheet="G-0", anchor="RISK CATEGORY", title="t", checked="c",
                result="r", code="k")
    base.update(over)
    return Finding(**base)


def test_every_finding_gets_a_key_no_other_finding_has():
    findings = [_finding("H-03"), _finding("H-03"), _finding("M-04", scenario="as_drawn"),
                _finding("M-04", scenario="as_declared")]
    keys = [d["key"] for d in findings_payload(None, None, findings)]
    assert keys == ["H-03", "H-03~2", "M-04", "M-04@as_declared"]


def test_the_box_is_a_placement_hint_and_never_part_of_the_record():
    f = _finding("H-01", box=(1.0, 2.0, 3.0, 4.0))
    assert "box" not in f.to_dict()
    assert f.same_content(_finding("H-01", box=(9.0, 9.0, 9.0, 9.0)))


@pytest.mark.parametrize("rotate", [0, 90, 270])
def test_a_rect_is_where_the_viewer_draws_on_a_rotated_sheet_too(tmp_path, rotate):
    path = _set(tmp_path, rotate)
    facts = build_facts(path)
    [f] = [f for f in run_all(facts).findings if f.rule_id == "XSHEET.RISK_CATEGORY"]
    [d] = [d for d in findings_payload(path, facts, [f])]
    # Where the risk category is printed — label and value, the claim's box —
    # in the space pdf.js draws in at scale 1 with the page's rotation applied.
    doc = pymupdf.open(path)
    page = doc[0]
    printed = pymupdf.Rect(facts.store.resolve("risk_category").best.box) * page.rotation_matrix
    printed.normalize()
    assert d["rect"] == pytest.approx([printed.x0, printed.y0, printed.x1, printed.y1], abs=0.2)
    assert pymupdf.Rect(d["rect"]).contains(
        pymupdf.Rect(page.search_for("RISK CATEGORY")[0] * page.rotation_matrix).normalize())
    w, h = (page.rect.width, page.rect.height)          # displayed size
    assert 0 <= d["rect"][0] < d["rect"][2] <= w and 0 <= d["rect"][1] < d["rect"][3] <= h


def test_the_rules_own_box_wins_over_the_first_match_of_its_anchor(tmp_path):
    path = _set(tmp_path, rows=("RISK CATEGORY: III", "", "", "RISK CATEGORY: III"))
    facts = build_facts(path)
    second = pymupdf.open(path)[0].search_for("RISK CATEGORY")[1]
    f = _finding("M-03", box=tuple(second))
    [d] = findings_payload(path, facts, [f])
    assert d["rect"] == pytest.approx([second.x0, second.y0, second.x1, second.y1], abs=0.2)


def test_a_finding_only_the_declaration_produced_is_not_drawn(tmp_path):
    path = _set(tmp_path)
    [d] = findings_payload(path, build_facts(path), [_finding("M-03", scenario="as_declared")])
    assert d["rect"] is None


def test_evidence_says_where_each_input_was_read(tmp_path):
    path = _set(tmp_path)
    facts = build_facts(path)
    [f] = [f for f in run_all(facts).findings if f.rule_id == "XSHEET.RISK_CATEGORY"]
    [d] = findings_payload(path, facts, [f])
    ev = {e["field"]: e for e in d["evidence"]}
    assert ev["risk_category"]["value"] == "III" and ev["risk_category"]["sheet"] == "G-0"
    assert "RISK CATEGORY" in ev["risk_category"]["quote"]
    assert ev["occupant_load"]["value"] == "70"          # 70, not 70.0
    assert ev["risk_category"]["note"] == ""


def test_a_value_only_the_ai_reader_found_says_so_on_the_card(tmp_path):
    path = _set(tmp_path, rows=("RISK CATEGORY: III",
                                "DESIGNED FOR AN OCCUPANT LOAD OF 48 PERSONS."))
    readings = Readings("sha", "claude-opus-5", "test", sheets={0: SheetReading(
        sheet_number="G-0", fields=[FieldReading(field="occupant_load", value="48",
                                                 quote="OCCUPANT LOAD OF 48 PERSONS")])})
    facts = build_facts(path, readings=readings)
    [f] = [f for f in run_all(facts).findings if f.rule_id == "XSHEET.RISK_CATEGORY"]
    [d] = findings_payload(path, facts, [f])
    load = next(e for e in d["evidence"] if e["field"] == "occupant_load")
    assert load["method"] == "ai" and load["note"] == "read by AI and verified on G-0"


def test_what_the_worker_writes_is_what_the_client_is_typed_for(tmp_path):
    path = _set(tmp_path)
    facts = build_facts(path)
    for d in findings_payload(path, facts, run_all(facts).findings):
        models.Finding.model_validate(json.loads(json.dumps(d)))
