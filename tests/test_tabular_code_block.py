"""A code-analysis block that tabulates rather than punctuates.

ITEC's G-002 is the benchmark this file encodes. It states nothing as
`LABEL: value` — every value sits in a column beside its row heading, under a
`PROPOSED | FBC ALLOWABLE*` header:

    HEIGHT                              26'-4"    75 FT.
    STORIES                             1         4
    SQUARE FOOTAGE PER FLOOR (MAXIMUM)  15,376    92,000

Two things have to hold. The values must be found at all — patterns that
require a colon read none of this. And the *first* column must be the one
taken: the second is the code limit, and reading it as the building would
compare the limit against itself and pass every set ever drawn.
"""
from __future__ import annotations

import pymupdf
import pytest

from fbcreview.pipeline import build_facts
from fbcreview.reconcile import drawn_declaration

#: The proposed column, and the allowable beside it. Taken off G-002.
PROPOSED_HEIGHT, ALLOWABLE_HEIGHT = "26'-4\"", "75 FT."
PROPOSED_STORIES, ALLOWABLE_STORIES = "1", "4"
PROPOSED_AREA, ALLOWABLE_AREA = "15,376", "92,000"


def tabular_sheet(tmp_path, name="g002.pdf"):
    """G-002's shape: a row heading, then two value cells beside it.

    Each cell is written as its own text run, which is how the region reader
    lays OCR back onto a sheet — so the extracted text carries the row as
    "LABEL\\n15,376\\n92,000" rather than as one line.
    """
    doc = pymupdf.open()
    page = doc.new_page(width=2592, height=1728)
    for n in range(60):
        page.draw_line((40 + n * 12, 60), (40 + n * 12, 300))

    rows = [
        ("USE AND OCCUPANCY CLASSIFICATION", "", ""),
        ("OCCUPANCY:", "BUSINESS", ""),
        ("CONSTRUCTION TYPE:", "TYPE II-B NON-COMBUSTIBLE", ""),
        ("FIRE SPRINKLER SYSTEM:", "YES, PER NFPA 13", ""),
        ("", "PROPOSED", "FBC ALLOWABLE*"),
        ("HEIGHT", PROPOSED_HEIGHT, ALLOWABLE_HEIGHT),
        ("STORIES", PROPOSED_STORIES, ALLOWABLE_STORIES),
        ("SQUARE FOOTAGE PER FLOOR (MAXIMUM)", PROPOSED_AREA, ALLOWABLE_AREA),
    ]
    y = 500
    for label, proposed, allowable in rows:
        if label:
            page.insert_text((300, y), label, fontsize=22)
        if proposed:
            page.insert_text((1500, y), proposed, fontsize=22)
        if allowable:
            page.insert_text((2000, y), allowable, fontsize=22)
        y += 70

    r = page.rect
    page.insert_text((r.x0 + 0.84 * r.width, r.y0 + 0.90 * r.height), "G-002", fontsize=30)
    page.insert_text((r.x0 + 0.84 * r.width, r.y0 + 0.86 * r.height),
                     "CODE COMPLIANCE DATA", fontsize=16)
    path = tmp_path / name
    doc.save(str(path))
    doc.close()
    return build_facts(str(path))


def test_tabulated_values_are_read_without_a_colon(tmp_path):
    drawn = drawn_declaration(tabular_sheet(tmp_path))
    assert drawn.get("height_ft") is not None, "height not read from a tabular row"
    assert drawn.get("stories") is not None, "storeys not read from a tabular row"
    assert drawn.get("building_area_sf") is not None, "area not read from a tabular row"


def test_the_proposed_column_is_taken_and_not_the_allowable(tmp_path):
    """The failure this prevents passes every building ever drawn.

    Read the allowable as the building and the rule compares 92,000 against
    92,000, 4 storeys against 4, 75 ft against 75 ft — always inside the
    limit, always silent, and always wrong.
    """
    drawn = drawn_declaration(tabular_sheet(tmp_path))

    assert drawn["building_area_sf"].value == 15376.0, drawn["building_area_sf"].value
    assert drawn["building_area_sf"].value != 92000.0
    assert drawn["stories"].value == 1.0, drawn["stories"].value
    assert drawn["stories"].value != 4.0
    # 26'-4" is 26 + 4/12 feet; the allowable beside it is 75.
    assert 26.0 < drawn["height_ft"].value < 26.5, drawn["height_ft"].value
    assert drawn["height_ft"].value != 75.0


def test_the_rest_of_the_block_still_reads(tmp_path):
    drawn = drawn_declaration(tabular_sheet(tmp_path))
    assert drawn["occupancy_group"].value == "B"
    assert drawn["construction_type"].value == "II-B"
    assert drawn["sprinkler_system"].value == "YES"
