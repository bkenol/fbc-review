"""Extraction must not depend on how an office numbers its sheets.

The engine used to look up its code block and its schedules by literal sheet
number — G-0, G-1, A-2, M-1, E-3. None of the three real sets it has been
measured against contains a single one of those: they number G-001/A-101,
AG.001/AA.101 and M.101/E.201. The lookups returned nothing, so a set that
carried every table the rules wanted still reviewed to an empty result.

These tests pin the replacement: find the block by what it is called, and carry
the sheet number as provenance rather than using it as a key.
"""
from __future__ import annotations

import pymupdf
import pytest

from fbcreview.pipeline import build_facts, _series
from fbcreview.extract.blocks import code_data_block


# ── sheet numbering ───────────────────────────────────────────────────────
@pytest.mark.parametrize("code,series", [
    ("G-0", "G"), ("G-001", "G"), ("AG.001", "AG"), ("AA.101", "AA"),
    ("M-1", "M"), ("M.101", "M"), ("E-3", "E"), ("E.201", "E"), ("p1", "P"),
])
def test_series_reads_the_discipline_off_any_numbering(code, series):
    assert _series(code) == series


def _sheet(doc, code, title, rows, y=560):
    """One sheet with a title block and a run of code-analysis rows."""
    page = doc.new_page(width=1224, height=792)
    for n in range(120):
        page.draw_line((26 + n * 8, 40), (26 + n * 8, 300))
    y0 = y
    for row in rows:
        page.insert_text((40, y0), row, fontsize=9)
        y0 += 15
    r = page.rect
    page.insert_text((r.x0 + 0.84 * r.width, r.y0 + 0.9 * r.height), code, fontsize=15)
    page.insert_text((r.x0 + 0.84 * r.width, r.y0 + 0.86 * r.height), title, fontsize=8)
    return page


ROWS = [
    "EGRESS",
    "COMMON PATH OF EGRESS TRAVEL (1006.2.1): 75 LF 38'-2\"",
    "MAX TRAVEL DISTANCE (1017.2): 250 LF 69'-4\"",
]


def _build(tmp_path, code):
    doc = pymupdf.open()
    _sheet(doc, code, "LIFE SAFETY PLAN", ROWS)
    path = tmp_path / f"{code.replace('.', '_')}.pdf"
    doc.save(str(path))
    doc.close()
    return build_facts(str(path))


@pytest.mark.parametrize("code", ["G-0", "G-001", "AG.001", "G-101"])
def test_the_code_block_is_found_whatever_the_sheet_is_numbered(tmp_path, code):
    """The block is the same block on every one of these; only the number moves."""
    facts = _build(tmp_path, code)
    sections = {d.section for d in facts.code_data}
    assert {"1006.2.1", "1017.2"} <= sections, f"{code}: got {sections}"
    # The number is still recorded, as provenance.
    assert all(d.sheet == code for d in facts.code_data)


def test_a_row_is_not_duplicated_when_two_headers_reach_it(tmp_path):
    doc = pymupdf.open()
    _sheet(doc, "G-001", "LIFE SAFETY PLAN", ["BUILDING CODE ANALYSIS"] + ROWS)
    path = tmp_path / "two-headers.pdf"
    doc.save(str(path))
    doc.close()
    facts = build_facts(str(path))
    keys = [(d.section, d.label) for d in facts.code_data]
    assert len(keys) == len(set(keys)), keys


# ── prose is not data ─────────────────────────────────────────────────────
def test_a_cited_sentence_does_not_become_a_measurement(tmp_path):
    """The guard that keeps an accessibility note out of the record.

    "...clearances required by SECTION 604 for the..." cites a section and is
    still prose. Read at face value it yields required="for", provided="the" —
    a value a rule could then compare against the code. A row with nothing
    parseable on either side is dropped and the rule abstains instead.
    """
    doc = pymupdf.open()
    _sheet(doc, "G-004", "ACCESSIBILITY NOTES", [
        "EGRESS",
        "Provide the clearances required by SECTION 604 for the fixture and",
        "grab bars shall comply with SECTION 606 . Such installations",
    ])
    path = tmp_path / "prose.pdf"
    doc.save(str(path))
    doc.close()
    facts = build_facts(str(path))
    assert facts.code_data == [], [
        (d.section, d.label, d.required_raw, d.provided_raw) for d in facts.code_data
    ]


def test_a_spelled_out_citation_is_read_when_it_carries_a_value(tmp_path):
    """ITEC's block cites "SECTION 302", not "(302)" — same row, other spelling."""
    doc = pymupdf.open()
    _sheet(doc, "G-002", "CODE COMPLIANCE", [
        "EGRESS",
        "MAX TRAVEL DISTANCE SECTION 1017.2 : 250 LF 69'-4\"",
    ])
    path = tmp_path / "spelled.pdf"
    doc.save(str(path))
    doc.close()
    facts = build_facts(str(path))
    assert [d.section for d in facts.code_data] == ["1017.2"]


# ── search window ─────────────────────────────────────────────────────────
def test_the_search_window_grows_with_the_sheet(tmp_path):
    """A 36" x 24" sheet plots at 2592 x 1728. A window fixed at 620 x 260
    points cropped the block to a corner there and never saw the rows below."""
    doc = pymupdf.open()
    page = doc.new_page(width=2592, height=1728)
    # A header that does not recur in the rows, so the anchor is unambiguous,
    # and rows spaced as a real block on a sheet this size is: the last sits
    # ~500pt below the header — outside the old fixed window, inside this one.
    page.insert_text((300, 300), "BUILDING CODE ANALYSIS", fontsize=24)
    y = 400
    for row in ("COMMON PATH (1006.2.1): 75 LF 38 IN",
                "TRAVEL DISTANCE (1017.2): 250 LF 69 IN",
                "DEAD END (1020.5): 20 LF 0 IN"):
        page.insert_text((300, y), row, fontsize=20)
        y += 250
    path = tmp_path / "large.pdf"
    doc.save(str(path))
    doc.close()

    d = pymupdf.open(str(path))
    got = {x.section for x in code_data_block(d, 0, "BUILDING CODE ANALYSIS", "G-001")}
    # The window this replaced, to show the difference is the window and not
    # the fixture: it reaches the top of the block and crops the rest.
    cropped = {x.section for x in
               code_data_block(d, 0, "BUILDING CODE ANALYSIS", "G-001",
                               width=620, height=260, left_pad=130)}
    d.close()
    # The fixed window reaches the first row and stops; the proportional one
    # reaches the second as well. The third is past both, which is the point:
    # the window is proportional to the sheet, not unbounded.
    assert cropped == {"1006.2.1"}, cropped
    assert {"1006.2.1", "1017.2"} <= got, got
    assert cropped < got, f"fixed window saw {cropped}, proportional saw {got}"
