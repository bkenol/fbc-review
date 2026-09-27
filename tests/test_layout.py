"""The layout layer: the three shapes a code block is drawn in, read the same way.

Each fixture below is built to the geometry measured on the real Sculpted set
(`docs/ENGINE-TEARDOWN.md` §3), because those are the shapes that broke the old
extractors: a stacked block interleaved with two other columns, a label/value
table printed beside another table on nearly the same baselines, and a grid of
cited rows under REQUIRED/PROVIDED headers.

When the real set is available (`FBC_TEST_PDF`), the same assertions are made
against the sheets themselves.
"""
from __future__ import annotations

import os

import pymupdf
import pytest

from fbcreview.layout import INLINE, ROW, STACKED, page_layout, split_inline, viewer_rect


#: The real set's text is 8.5 pt tall from top to bottom of its box, which is
#: what PyMuPDF's Helvetica gives at 6.2 pt. Every threshold in the layout layer
#: is a multiple of text height, so the fixtures are drawn at the real size and
#: `_at` places a word's box top where the real sheet has it.
SIZE = 6.2
ASCENT = 6.7


def _page(width=2592, height=1728):
    doc = pymupdf.open()
    return doc, doc.new_page(width=width, height=height)


def _at(page, x, y_top, text, size=SIZE):
    page.insert_text((x, y_top + ASCENT * size / SIZE), text, fontsize=size)


def _pairs(page):
    return {(p.label, tuple(p.values)): p for p in page_layout(page).pairs}


def _one(layout_pairs, label):
    found = [p for (lab, _v), p in layout_pairs.items() if lab == label]
    assert found, f"no pair labelled {label!r}; have {sorted(l for l, _ in layout_pairs)}"
    return found


# ══ stacked: label on one line, answer beneath, other columns interleaved ══
def _stacked_block(page):
    """G-0's PROJECT DATA, with the applicable-codes column on its left and the
    scope-of-work paragraph on its right, at the real set's coordinates."""
    rows = [(985.8, "TYPE OF CONSTRUCTION:"), (1009.0, "III-B"),
            (1032.4, "OCCUPANCY:"), (1055.8, "ASSEMBLY (A-3)"),
            (1079.2, "FIRE SPRINKLERS:"), (1102.4, "SPRINKLERED"),
            (1172.4, "BASIC WIND SPEED:"), (1195.7, "ULTIMATE: 170 MPH"),
            (1207.4, "NOMINAL: 132 MPH"),
            (1230.8, "INTERNAL PRESSURE"), (1242.6, "COEFFICIENTS:"),
            (1266.0, "±0.18 (ENCLOSED)"),
            (1289.3, "RISK CATEGORY:"), (1312.6, "III")]
    for y, text in rows:
        _at(page, 1875, y, text)
    # The column to the left: a list of codes, each carrying digits.
    for y, text in [(1006.6, "2023 FLORIDA BUILDING CODE 8TH EDITION - BUILDING"),
                    (1052.1, "2023 FLORIDA BUILDING CODE 8TH EDITION - FUEL GAS")]:
        _at(page, 1567, y, text)
    # The column to the right: prose, and a sheet index whose numbers look like
    # occupancy groups.
    _at(page, 2051, 1030, "TO EXISTING MECHANICAL, ELECTRICAL, PLUMBING,")
    _at(page, 2074, 1290.7, "A-3")
    _at(page, 2121, 1290.7, "REFLECTED CEILING PLAN")


def test_a_stacked_block_reads_each_answer_beneath_its_label():
    doc, page = _page()
    _stacked_block(page)
    pairs = _pairs(page)
    assert _one(pairs, "TYPE OF CONSTRUCTION")[0].values == ["III-B"]
    assert _one(pairs, "OCCUPANCY")[0].values == ["ASSEMBLY (A-3)"]
    assert _one(pairs, "FIRE SPRINKLERS")[0].values == ["SPRINKLERED"]
    assert _one(pairs, "RISK CATEGORY")[0].values == ["III"]
    assert all(p.kind == STACKED for p in _one(pairs, "OCCUPANCY"))


def test_a_list_entry_is_not_a_label_for_the_answer_beside_it():
    """`2023 FLORIDA BUILDING CODE 8TH EDITION - BUILDING` sits on III-B's line.
    Reading III-B as its answer is how the construction type went missing."""
    doc, page = _page()
    _stacked_block(page)
    labels = {p.label for p in page_layout(page).pairs}
    assert not any(l.startswith("2023 FLORIDA") for l in labels)


def test_a_sheet_number_to_the_right_is_not_the_risk_category():
    doc, page = _page()
    _stacked_block(page)
    risk = _one(_pairs(page), "RISK CATEGORY")
    assert risk[0].values != ["A-3"]


def test_a_value_line_that_is_itself_a_pair_keeps_its_parent_as_context():
    doc, page = _page()
    _stacked_block(page)
    pairs = _pairs(page)
    wind = _one(pairs, "BASIC WIND SPEED")[0]
    assert wind.values == ["ULTIMATE: 170 MPH", "NOMINAL: 132 MPH"]
    ult = _one(pairs, "ULTIMATE")[0]
    assert ult.kind == INLINE and ult.values == ["170 MPH"]
    assert "BASIC WIND SPEED" in ult.context


def test_a_label_wrapped_onto_two_lines_is_joined():
    doc, page = _page()
    _stacked_block(page)
    gcpi = _one(_pairs(page), "INTERNAL PRESSURE COEFFICIENTS")[0]
    assert gcpi.values == ["±0.18 (ENCLOSED)"]


# ══ row: label, then the answer to its right, a second table beside it ═════
def _code_analysis_beside_another_table(page):
    """G-1: BUILDING CODE ANALYSIS at x≈1732/1940, the FFPC occupant-load table
    at x≈2060–2250 on nearly the same baselines."""
    _at(page, 1750, 894.8, "BUILDING CODE ANALYSIS", size=21)
    rows = [(932.7, "GENERAL", None),
            (951.3, "OCCUPANCY", (1928.4, 948.9, "ASSEMBLY")),
            (969.3, "SPRINKLER SYSTEM", (1940.3, 966.9, "YES")),
            (987.3, "OCCUPANT LOAD", (1943.3, 984.7, "70")),
            (1004.7, "EXITS/ EGRESS", None),
            (1023.0, "NUMBER REQUIRED", (1928.9, 1022.3, "2 DOOR(S)")),
            (1059.3, "EGRESS WIDTH FACTOR", (1922.6, 1058.3, '0.15" (DOORS)'))]
    for y, label, value in rows:
        _at(page, 1732, y, label)
        if value:
            _at(page, value[0], value[1], value[2])
    # The neighbouring table: its header words land on OCCUPANT LOAD's baseline.
    _at(page, 2236.4, 983.3, "OCCUPANT")
    _at(page, 2069.3, 1005.3, "RECEPTION")
    _at(page, 2136.5, 1005.3, "BUSINESS")


def test_a_label_value_table_is_not_merged_with_the_table_beside_it():
    doc, page = _page()
    _code_analysis_beside_another_table(page)
    pairs = _pairs(page)
    load = _one(pairs, "OCCUPANT LOAD")[0]
    assert load.values == ["70"] and load.kind == ROW
    assert _one(pairs, "SPRINKLER SYSTEM")[0].values == ["YES"]
    assert _one(pairs, "OCCUPANCY")[0].values == ["ASSEMBLY"]


def test_a_row_label_knows_the_block_it_sits_in():
    doc, page = _page()
    _code_analysis_beside_another_table(page)
    pairs = _pairs(page)
    load = _one(pairs, "OCCUPANT LOAD")[0]
    assert load.heading == "BUILDING CODE ANALYSIS"
    assert "GENERAL" in load.context
    assert "EXITS/ EGRESS" in _one(pairs, "NUMBER REQUIRED")[0].context


def test_a_subheading_with_no_answer_beside_it_stays_a_heading():
    """`EXITS/ EGRESS` has another table's first cell to its right. It must not
    take `RECEPTION` as its answer and stop being the heading it is."""
    doc, page = _page()
    _code_analysis_beside_another_table(page)
    assert not any(p.label == "EXITS/ EGRESS" for p in page_layout(page).pairs)


# ══ grid: cited rows under REQUIRED / PROVIDED headers ══════════════════════
def _egress_grid(page):
    _at(page, 1772.9, 1470, "REQUIRED:")
    _at(page, 1889.5, 1470, "PROVIDED:")
    rows = [(1493.2, "MAX TRAVEL DISTANCE (1017.2):", "250 LF", "69'-4\""),
            (1516.6, "DEAD END CORRIDOR (1020.5):", "20 LF", "N/A"),
            (1540.0, "MIN. CORRIDOR WIDTH (1020.3):", '44"', '44"'),
            (1563.4, "NUMBER OF EXITS:", "2", "2"),
            (1586.6, "EXIT WIDTH REQUIRED (1005.3.2):", '10.50"', '108.00"'),
            (1609.6, "COMMON PATH OF TRAVEL (1006.2.1):", "50 LF", "8'-1\""),
            (1633.4, "CLEAR OPENING WIDTH (1010.1.1):", '32"', '32"')]
    for y, label, req, prov in rows:
        _at(page, 1567.3, y, label)
        _at(page, 1773.1, y, req)
        _at(page, 1889.8, y, prov)
    # The sheet index column beyond: sheet numbers on the same baselines.
    _at(page, 2074.3, 1609.6, "P-1")


def test_a_grid_row_reads_required_and_provided_under_their_headers():
    doc, page = _page()
    _egress_grid(page)
    pairs = _pairs(page)
    travel = _one(pairs, "MAX TRAVEL DISTANCE (1017.2)")[0]
    assert travel.values == ["250 LF", "69'-4\""]
    assert travel.headers == ["REQUIRED", "PROVIDED"]


def test_a_column_header_does_not_take_the_first_value_as_its_answer():
    doc, page = _page()
    _egress_grid(page)
    labels = {p.label for p in page_layout(page).pairs}
    assert "REQUIRED" not in labels and "PROVIDED" not in labels


def test_a_grid_row_stops_at_the_last_column():
    doc, page = _page()
    _egress_grid(page)
    common = _one(_pairs(page), "COMMON PATH OF TRAVEL (1006.2.1)")[0]
    assert common.values == ["50 LF", "8'-1\""]


# ══ inline ═════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("text, expected", [
    ("OCCUPANCY: BUSINESS", ("OCCUPANCY", "BUSINESS")),
    ("MIXED OCCUPANCY? NO", ("MIXED OCCUPANCY", "NO")),
    ("RISK CATEGORY:", ("RISK CATEGORY", "")),
    ("SLOPE 1:2", None),
    ("±0.18", None),
])
def test_inline_split(text, expected):
    assert split_inline(text) == expected


def test_one_text_object_label_and_trailing_value():
    doc, page = _page(1224, 792)
    page.insert_text((40, 580), "OCCUPANT LOAD 70", fontsize=9)
    page.insert_text((40, 600), "SPRINKLERED YES", fontsize=9)
    pairs = _pairs(page)
    assert _one(pairs, "OCCUPANT LOAD")[0].values == ["70"]
    assert _one(pairs, "SPRINKLERED")[0].values == ["YES"]


# ══ coordinates ════════════════════════════════════════════════════════════
def test_a_box_is_rotated_into_the_space_a_viewer_draws_in():
    doc, page = _page(1224, 792)
    page.insert_text((100, 700), "OCCUPANT LOAD: 70", fontsize=9)
    upright = viewer_rect(page, (100, 690, 150, 703))
    assert upright == [100.0, 690.0, 150.0, 703.0]
    page.set_rotation(270)
    rotated = viewer_rect(page, (100, 690, 150, 703))
    # A 270° page is 792 wide and 1224 tall as displayed.
    assert rotated[2] <= 792 and rotated[3] <= 1224
    assert rotated != upright


# ══ the real set ═══════════════════════════════════════════════════════════
@pytest.mark.skipif(not os.environ.get("FBC_TEST_PDF"),
                    reason="set FBC_TEST_PDF to check against the real permit set")
def test_the_real_code_blocks_read_as_printed():
    doc = pymupdf.open(os.environ["FBC_TEST_PDF"])
    g0 = {p.label: p for p in page_layout(doc[0]).pairs}
    assert g0["TYPE OF CONSTRUCTION"].values == ["III-B"]
    assert g0["OCCUPANCY"].values == ["ASSEMBLY (A-3)"]
    assert g0["FIRE SPRINKLERS"].values == ["SPRINKLERED"]
    assert g0["RISK CATEGORY"].values == ["III"]
    assert g0["COMMON PATH OF TRAVEL (1006.2.1)"].values == ["50 LF", "8'-1\""]
    g1 = [p for p in page_layout(doc[1]).pairs if p.label == "OCCUPANT LOAD"]
    by_context = {tuple(p.context[:1]): p.values for p in g1}
    assert by_context[("GENERAL",)] == ["70"]
    assert by_context[("EXIT DISCHARGE 1",)] == ["35"]
