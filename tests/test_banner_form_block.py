"""A code-analysis block that cites in a banner and answers in words.

ITEC's code sheets are the benchmark this file encodes. Phoenix Associates draw
two tables side by side, put the citation in a BANNER above each one, and then
list bare `LABEL: value` rows underneath:

    TABLES 504.3, 504.4 & 506.2 < > FBC 7TH ED     TABLE 1004.5 < > FBC 7TH ED
    OCCUPANCY:  BUSINESS   MIXED OCCUPANCY?  NO    OCCUPANT LOAD FACTOR:  150
    CONSTRUCTION TYPE:  TYPE II-B NON-COMBUSTIBLE  TOTAL OCCUPANT LOAD:   152

Four things about that shape defeat the older parsers, and each has its own test
below:

* the values are WORDS — `blocks.labelled_values` takes the value to be the
  trailing run of numeric / `YES` / `NO` tokens and reads none of these;
* one visual row carries TWO pairs, and splitting at the single widest gap
  yields `OCCUPANCY = BUSINESS MIXED OCCUPANCY?` — second answer lost, first
  corrupted;
* the citation is one level up, in the banner, so `blocks.code_data_block` —
  which wants `(1004.5)` on every row — sees no rows at all;
* the two tables share a y, so clustering on y alone merges them and the left
  table's banner is inherited by the right table's rows.

And one thing that must NOT happen: `OCCUPANT LOAD FACTOR: 150` sits beside
`TOTAL OCCUPANT LOAD: 152` and both labels contain "OCCUPANT LOAD". Reading the
factor as the load is how a plausible wrong number gets into a review.
"""
from __future__ import annotations

import pymupdf
import pytest

from fbcreview.extract.blocks import labelled_values
from fbcreview.extract.formblocks import form_block
from fbcreview.pipeline import build_facts

HEADER = "CODE COMPLIANCE DATA"
BANNER_L = "TABLES 504.3, 504.4 & 506.2  <  >  FLORIDA BUILDING CODE 7TH EDITION"
BANNER_R = "TABLE 1004.5  <  >  FLORIDA BUILDING CODE 7TH EDITION"

#: Off the ITEC set. 15,376 SF of Group B at the Table 1004.5 gross factor of
#: 150 is 103 occupants; the set states 152, and that gap is finding H-02 in
#: `docs/reference/ITEC Alico Park Findings.md`.
STATED_LOAD = "152"
STATED_FACTOR = "150"
AREA = "15,376"


def banner_sheet(tmp_path, name="g002.pdf"):
    """Two tables side by side, each under its own citation banner.

    Columns are placed the way the sheet places them rather than the way a
    parser would like them: the left table's construction-type value runs long
    enough to sit under the second pair's label column, which is exactly why
    that pair cannot be recovered by splitting the row at one gap.
    """
    doc = pymupdf.open()
    page = doc.new_page(width=2592, height=1728)
    for n in range(60):
        page.draw_line((40 + n * 12, 60), (40 + n * 12, 300))
    page.insert_text((300, 400), HEADER, fontsize=26)
    page.insert_text((100, 470), BANNER_L, fontsize=16)
    page.insert_text((980, 470), BANNER_R, fontsize=16)

    # (x, text) per cell. The left table is four columns wide — label, value,
    # label, value — inside one ruled box.
    rows = [
        [(100, "OCCUPANCY:"), (340, "BUSINESS"),
         (560, "MIXED OCCUPANCY?"), (860, "NO"),
         (980, "OCCUPANT LOAD FACTOR:"), (1400, STATED_FACTOR)],
        [(100, "CONSTRUCTION TYPE:"), (340, "TYPE II-B NON-COMBUSTIBLE NON-RATED"),
         (980, "TOTAL OCCUPANT LOAD:"), (1400, STATED_LOAD)],
        [(100, "SQUARE FOOTAGE PER FLOOR:"), (340, AREA),
         (980, "FIRE SPRINKLER SYSTEM:"), (1400, "YES, PER NFPA 13")],
        # Posed, unanswered: the sheet asks and the answer is not on it.
        [(100, "OCCUPANCY SEPARATION RATING PROVIDED:")],
    ]
    y = 540
    for row in rows:
        for x, cell in row:
            page.insert_text((x, y), cell, fontsize=16)
        y += 60

    r = page.rect
    page.insert_text((r.x0 + 0.84 * r.width, r.y0 + 0.90 * r.height), "G-002", fontsize=30)
    path = tmp_path / name
    doc.save(str(path))
    doc.close()
    return str(path)


@pytest.fixture
def sheet(tmp_path):
    return banner_sheet(tmp_path)


@pytest.fixture
def rows(sheet):
    doc = pymupdf.open(sheet)
    try:
        return form_block(doc, 0, HEADER, "G-002")
    finally:
        doc.close()


def valued(rows):
    return {r.key: r.value for r in rows if r.value}


# ── the before state, so the change has something to be measured against ─────

def test_the_old_parser_reads_nothing_from_this_block(sheet):
    """`labelled_values` is what read this block before, and it returns {}.

    Not a formality: it is the whole reason the reviewer's report said "it
    abstained, but the data is right here". Every value on this sheet is a word,
    and a parser that only accepts a trailing numeric run cannot see one.
    """
    doc = pymupdf.open(sheet)
    try:
        assert labelled_values(doc, 0, HEADER) == {}
    finally:
        doc.close()


# ── what the new parser reads ────────────────────────────────────────────────

def test_word_values_are_read(rows):
    v = valued(rows)
    assert v.get("OCCUPANCY") == "BUSINESS"
    assert v.get("CONSTRUCTION TYPE") == "TYPE II-B NON-COMBUSTIBLE NON-RATED"


def test_both_pairs_on_one_visual_row_survive(rows):
    """`OCCUPANCY: BUSINESS   MIXED OCCUPANCY? NO` is one row and two answers.

    Split at the single widest gap it becomes `OCCUPANCY = BUSINESS MIXED
    OCCUPANCY?` — and a rule reading that string for the word MIXED passes a
    sheet it should have failed.
    """
    v = valued(rows)
    assert v.get("MIXED OCCUPANCY") == "NO"
    assert v.get("OCCUPANCY") == "BUSINESS", "the first pair was corrupted by the second"


def test_a_posed_but_unanswered_label_is_kept_and_marked(rows):
    """A label with no answer is not the same as a label that is not there.

    Dropping it makes "the sheet does not say" indistinguishable from "nothing
    asked"; inferring an answer from the label text is worse. It is carried with
    an empty value and `unanswered`, so a rule can abstain with a reason.
    """
    posed = [r for r in rows if r.key.startswith("OCCUPANCY SEPARATION RATING")]
    assert posed, "the unanswered label was dropped"
    assert posed[0].unanswered is True
    assert posed[0].value == ""


def test_each_table_inherits_its_own_banner(rows):
    """Two tables at the same y are two rows, not one — and two citations.

    Cluster on y alone and the left table's `TABLES 504.3, 504.4 & 506.2` is
    stamped on the right table's occupant-load rows, which is a citation
    pointing at the wrong table.
    """
    by_key = {r.key: r for r in rows}
    assert by_key["OCCUPANCY"].sections == ["504.3", "504.4", "506.2"]
    assert by_key["TOTAL OCCUPANT LOAD"].sections == ["1004.5"]
    assert by_key["OCCUPANCY"].strip != by_key["TOTAL OCCUPANT LOAD"].strip


def test_the_label_value_gutter_is_not_mistaken_for_a_table_boundary(rows):
    """The gap between a label column and its value column is full-height too.

    Split there and every label lands in one strip and every value in another,
    and no pair is ever assembled again. What tells them apart is that a second
    table poses its own questions and a value column does not.
    """
    assert valued(rows), "the label and value columns were split apart"
    assert len({r.strip for r in rows}) == 2, "expected exactly two side-by-side tables"


def test_the_banner_itself_is_not_recorded_as_an_answer(rows):
    """A banner is a citation, not a label/value pair.

    Left in, it splits at its own widest gap — on a two-table sheet, literally
    one banner labelled with the other.
    """
    assert not any("FLORIDA BUILDING CODE" in r.label.upper() for r in rows)
    assert not any("EDITION" in (r.value or "").upper() for r in rows)


def test_every_row_says_where_it_came_from(rows):
    """No value without a derivation somebody can check on the sheet."""
    for r in rows:
        meta = r.as_meta()
        assert meta["sheet"] == "G-002"
        assert meta["page"] == 0
        assert len(meta["rect"]) == 4 and meta["rect"][2] > meta["rect"][0]
        assert meta["confidence"]


# ── what the pipeline does with them ─────────────────────────────────────────

def test_the_stated_occupant_load_reaches_the_facts(sheet):
    facts = build_facts(sheet)
    assert facts.meta.get("occupant_load") == 152.0


def test_the_occupant_load_factor_is_not_read_as_the_load(sheet):
    """Both labels contain OCCUPANT LOAD and only one of them is the load.

    150 is the Table 1004.5 factor printed beside the total. Reading it as the
    stated load would have the rule compare the factor against the computed
    load and report a large, confident, meaningless discrepancy.
    """
    facts = build_facts(sheet)
    assert facts.meta.get("occupant_load") != 150.0


def test_the_sprinkler_answer_is_read_as_a_standard_not_a_first_letter(sheet):
    facts = build_facts(sheet)
    assert facts.meta.get("sprinklered") is True


def test_the_facts_carry_the_provenance_of_what_was_read(sheet):
    facts = build_facts(sheet)
    read = facts.meta.get("form_reads", {}).get("occupant_load")
    assert read, "a value entered the facts with no derivation attached"
    assert read["label"] == "TOTAL OCCUPANT LOAD"
    assert read["sheet"] == "G-002"
    assert read["sections"] == ["1004.5"]
    assert any(r["unanswered"] for r in facts.meta["form_rows"]), \
        "the unanswered labels did not survive into the facts"


def test_the_stated_load_now_carries_the_sheet_it_was_read_off(sheet):
    """The number was reachable before this change; where it came from was not.

    `r_occupancy.stated_load` falls back to a regex sweep over the whole
    document, so `TOTAL OCCUPANT LOAD: 152` was already matching — on whatever
    sheet it happened to appear, under whatever table, with no page, no
    rectangle and no citation attached. The finding it produced had nothing for
    a reviewer to check and nothing for the marker to box.

    Read off the block, the same 152 arrives with the label it was printed
    under, the sheet and page it sits on, the rectangle it occupies and the
    section its table is drawn under.
    """
    from fbcreview.rules import r_occupancy

    facts = build_facts(sheet)
    assert r_occupancy.stated_load(facts) == 152.0
    assert facts.meta["occupant_load"] == 152.0, "the sweep answered, the block did not"
    read = facts.meta["form_reads"]["occupant_load"]
    assert read["page"] == 0 and read["sheet"] == "G-002"
    assert read["sections"] == ["1004.5"]
