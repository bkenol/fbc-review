"""Sheet identification: the pattern, the discipline gate, and the anchor scan.

`sheet_index` used to match the last short token in a hardcoded bottom-right
region against `^([GACSMEPFL])-?(\\d{1,2}[A-Z]?)$`. Both halves were too narrow.
Measured against four real permit sets, title-block region only, that pattern
identified 24 of 24 sheets on one set, 2 of 35 on the next, 0 of 14 on the third
and 1 of 15 on the fourth — three-digit National CAD Standard numbering, dot
separators, two-letter disciplines and a bottom-left title block each defeated
it on their own.

The sets themselves cannot live here: `samples/` is git-ignored and a client PDF
is never committed. These builders reproduce the shapes that broke it.
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

import pymupdf
import pytest

from fbcreview.extract.document import (_anchor, _cells, sheet_index,
                                        sheet_number, sheet_numbers_read)
from fbcreview.pipeline import build_facts
from fbcreview.rules import run_all

RULE = "DOC.SHEET_NUMBERS"

#: Where a title block sits, as a fraction of the sheet. `br` is the common
#: case and the one the old reader assumed; `bl` is the JSP Arch set, which put
#: its sheet number in the bottom-left corner on all fourteen pages.
CORNERS = {"br": (0.84, 0.82), "bl": (0.02, 0.82)}

#: The Sculpted Hot Pilates sheet index, taken from the SHEETS list in
#: `fbcreview/render/v5_markup_reference.py`, which was written against the real
#: 24-sheet set. This is the set that reads 24 of 24 today and the regression
#: gate for the change: if it moves, the fix is worse than the defect.
PILATES: List[str] = [
    "G-0", "G-1", "G-2", "G-3",
    "A-1", "A-2", "A-3", "A-4", "A-5", "A-6", "A-7", "A-8", "A-9", "A-10",
    "A-11", "A-12",
    "M-1", "M-2", "E-1", "E-2", "E-3", "P-1", "P-2", "P-3",
]

#: Equipment tags, hardware codes and insulation values off a real drawing body.
#: Every one of them is shaped exactly like a sheet number. The discipline gate
#: is what keeps them out.
DECOY_TAGS = ("PT-1", "EF-3", "R-19", "B13", "US26D")


def _sheet(page: pymupdf.Page, code: Optional[str], title: str, corner: str) -> None:
    """A title block in the named corner: label, title, then the number."""
    fx, fy = CORNERS[corner]
    r = page.rect
    x, y = r.x0 + fx * r.width, r.y0 + fy * r.height
    page.insert_text((x, y + 8), "SHEET TITLE", fontsize=6)
    page.insert_text((x, y + 22), title, fontsize=8)
    if code is not None:
        page.insert_text((x, y + 46), code, fontsize=15)


def permit_set(codes: Sequence[Optional[str]], corner: str = "br",
               titles: Optional[Sequence[str]] = None,
               tags: Iterable[str] = (),
               notes: Sequence[Tuple[int, str]] = (),
               rotate: Iterable[int] = ()) -> bytes:
    """A set of 1224x792 sheets, one per code.

    `codes` may carry a `None`, which draws a title block with no number on it —
    a sheet the reader cannot identify. `tags` are scattered through the drawing
    body of every page, `notes` places arbitrary text in the bottom-right corner
    of the given page, and `rotate` sets /Rotate 270 on the given pages.
    """
    rotate = set(rotate)
    doc = pymupdf.open()
    for i, code in enumerate(codes):
        page = doc.new_page(width=1224, height=792)
        title = titles[i] if titles else "FLOOR PLAN"
        page.insert_text((40, 60), "GENERAL NOTES", fontsize=9)
        for n, tag in enumerate(tags):
            page.insert_text((40 + n * 90, 120), tag, fontsize=9)
        for pno, text in notes:
            if pno == i:
                page.insert_text((0.84 * page.rect.width, 0.88 * page.rect.height),
                                 text, fontsize=8)
        _sheet(page, code, title, corner)
        if i in rotate:
            page.set_rotation(270)
    buf = doc.tobytes()
    doc.close()
    return buf


def codes_of(data: bytes) -> List[str]:
    doc = pymupdf.open(stream=data, filetype="pdf")
    text = {p: doc[p].get_text() for p in range(doc.page_count)}
    got = [s.code for s in sheet_index(doc, text)]
    doc.close()
    return got


def anchor_of(data: bytes):
    doc = pymupdf.open(stream=data, filetype="pdf")
    got = _anchor([_cells(doc[p]) for p in range(doc.page_count)])
    doc.close()
    return got


def reviewed(tmp_path: Path, data: bytes, name: str = "set.pdf"):
    path = tmp_path / name
    path.write_bytes(data)
    facts = build_facts(str(path))
    facts.meta["as_of"] = dt.date(2026, 8, 23)
    return facts, run_all(facts)


# ══════════════════════════════════════════════════════════════════════════
# 1. The pattern and the discipline gate
# ══════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("token, expected", [
    ("G-0", "G-0"),            # the shape that always worked
    ("A-1", "A-1"),
    ("T-1T", "T-1T"),          # T was not in [GACSMEPFL]; the set that works carries it
    ("G-001", "G-001"),        # National CAD Standard, three digits
    ("A-101", "A-101"),
    ("S-501", "S-501"),
    ("M.101", "M.101"),        # dot separator
    ("E.202", "E.202"),
    ("AG.001", "AG.001"),      # two-letter discipline and a dot
    ("AA.101.1", "AA.101.1"),  # and a sub-number
    ("G0", "G-0"),             # no separator is written with a dash, as before
    ("g-0", "G-0"),
])
def test_a_sheet_number_is_recognised(token, expected):
    assert sheet_number(token) == expected


@pytest.mark.parametrize("token", ["US26D", "R-19", "PT-1", "B13", "EF-3"])
def test_a_drawing_body_tag_is_not_a_sheet_number(token):
    """Widening the pattern alone is not safe: every one of these matches it.

    They are rejected on the discipline prefix — `US`, `R`, `PT`, `B` and `EF`
    are not disciplines — which is half of what makes the wider pattern safe.
    The other half is the position anchor.
    """
    assert sheet_number(token) == ""


@pytest.mark.parametrize("token", ["", "SHEET", "A", "1006.2.1", "ELEVATIONS",
                                   "24.85", "II-B", "1/8"])
def test_ordinary_sheet_text_is_not_a_sheet_number(token):
    assert sheet_number(token) == ""


# ══════════════════════════════════════════════════════════════════════════
# 2. Anchor discovery — the title block is wherever it consistently is
# ══════════════════════════════════════════════════════════════════════════
def test_the_anchor_is_found_bottom_left_when_that_is_where_the_set_puts_it():
    """The JSP Arch shape: 14 pages, sheet number bottom-left, dot separator and
    a two-letter discipline. The old reader identified none of them, because it
    read a fixed bottom-right region that held general notes — text that is
    real, and is not the sheet number."""
    codes = ["AG.001", "AG.002", "AD.101", "AD.102", "AR.201", "AR.202"]
    data = permit_set(codes, corner="bl",
                      notes=[(0, "A-101"), (1, "A-101")])

    assert anchor_of(data) == (0.0, 0.9)
    assert codes_of(data) == codes


def test_the_anchor_is_the_position_on_the_most_pages_not_the_first_one_seen():
    """The bottom-right decoys above are sheet-number-shaped and land in one
    cell of their own. They lose because they are on two pages and the title
    block is on six — the vote is what settles it, not the corner."""
    codes = ["AG.001", "AG.002", "AD.101", "AD.102", "AR.201", "AR.202"]
    decoyed = permit_set(codes, corner="bl", notes=[(i, "A-101") for i in range(4)])

    assert anchor_of(decoyed) == (0.0, 0.9)
    assert codes_of(decoyed) == codes


def test_a_drawing_body_full_of_equipment_tags_does_not_win_the_anchor():
    """Every page carries the same tags in the same place, so position alone
    would let them win. The discipline gate keeps them out of the vote."""
    codes = ["G-001", "A-101", "A-102", "M.101", "E.202"]
    data = permit_set(codes, tags=DECOY_TAGS)

    assert codes_of(data) == codes


def test_national_cad_standard_numbering_is_read():
    """The ITEC shape: `G-001` three-digit numbering, which `\\d{1,2}` rejected
    outright and which is the most common commercial convention."""
    codes = ["G-001", "G-002", "A-101", "A-102", "S-501", "M-201"]
    assert codes_of(codes_pdf := permit_set(codes)) == codes
    assert anchor_of(codes_pdf) == (0.8, 0.9)


def test_dot_separated_numbering_is_read():
    """The JSP MEP shape: the correct corner, rejected by the pattern. The
    separator is reported as drawn — the number in a finding has to be the
    number on the sheet."""
    codes = ["M.101", "M.102", "E.201", "E.202", "P.301"]
    assert codes_of(permit_set(codes)) == codes


def test_a_rotated_sheet_reads_like_its_neighbours():
    """`/Rotate 270` needs no special handling: `get_text("words")` reports
    media-box coordinates, so a rotated sheet buckets where an upright one
    does. Normalising for rotation was tried and changed no result."""
    codes = ["G-0", "A-1", "A-2", "M-1"]
    assert codes_of(permit_set(codes, rotate=(1, 3))) == codes


# ══════════════════════════════════════════════════════════════════════════
# 3. The regression guard — the set that works today must not move
# ══════════════════════════════════════════════════════════════════════════
def test_the_working_set_still_reads_all_twenty_four_sheet_codes_in_order():
    """24 of 24, `G-0` through `P-3`. This is the gate on the whole change: the
    set that reads correctly today is worth more than the three that do not."""
    got = codes_of(permit_set(PILATES, titles=["SHEET"] * len(PILATES)))

    assert got == PILATES
    assert len(got) == 24
    assert got[0] == "G-0" and got[-1] == "P-3"


def test_a_t_series_sheet_is_read(tmp_path):
    """`T-1T` was dropped by `[GACSMEPFL]` — silently, because an unrecognised
    sheet number is indistinguishable from a page with no title block."""
    codes = ["G-0", "T-1T", "A-1", "A-2"]
    facts, res = reviewed(tmp_path, permit_set(codes))

    assert [s.code for s in facts.sheets] == codes
    assert not [f for f in res.findings if f.rule_id == RULE]


def test_the_sheet_title_is_still_read_from_the_lines_around_the_number():
    """The title logic is unchanged; it just reads around the anchor now
    instead of out of a corner. It has to keep working from both corners."""
    codes, titles = ["G-0", "A-1"], ["COVER SHEET", "FLOOR PLAN"]
    for corner in ("br", "bl"):
        doc = pymupdf.open(stream=permit_set(codes, corner=corner, titles=titles),
                           filetype="pdf")
        text = {p: doc[p].get_text() for p in range(doc.page_count)}
        assert [s.title for s in sheet_index(doc, text)] == titles
        doc.close()


def test_the_discipline_is_the_whole_prefix():
    doc = pymupdf.open(stream=permit_set(["AG.001", "M.101"]), filetype="pdf")
    text = {p: doc[p].get_text() for p in range(doc.page_count)}
    assert [s.discipline for s in sheet_index(doc, text)] == ["AG", "M"]
    doc.close()


# ══════════════════════════════════════════════════════════════════════════
# 4. The fallback, and reporting it instead of failing silently
# ══════════════════════════════════════════════════════════════════════════
def test_a_set_with_no_readable_sheet_numbers_falls_back_to_page_numbers(tmp_path):
    data = permit_set([None] * 5, tags=DECOY_TAGS)
    facts, res = reviewed(tmp_path, data)

    assert [s.code for s in facts.sheets] == ["p1", "p2", "p3", "p4", "p5"]
    assert anchor_of(data) is None


def test_the_coverage_gate_is_reported_as_a_finding_the_user_reads(tmp_path):
    """The failure this exists to prevent: every rule that names a sheet stands
    down, and the user is handed a review with no findings — indistinguishable
    from a clean set."""
    facts, res = reviewed(tmp_path, permit_set([None] * 5, tags=DECOY_TAGS))

    hits = [f for f in res.findings if f.rule_id == RULE]
    assert len(hits) == 1
    f = hits[0]
    assert f.status == "OPEN" and f.severity == "HIGH"
    assert f.result == (
        "Sheet numbers were not recognised on 5 of 5 sheets. Checks that reference a "
        "specific sheet will stand down. Expected a discipline-and-number token "
        "(A-101, M.101, G-0) in the title block.")
    # It is a document-level finding: there is nothing on a sheet to box.
    assert f.anchor == ""


def test_the_gate_stays_quiet_while_the_set_reads(tmp_path):
    facts, res = reviewed(tmp_path, permit_set(PILATES))

    assert [s.code for s in facts.sheets] == PILATES
    assert not [f for f in res.findings if f.rule_id == RULE]
    assert [a.reason for a in res.abstentions if a.rule_id == RULE] == [
        "every sheet's number was read from its title block"]


@pytest.mark.parametrize("unread, reported", [(2, False), (3, True)])
def test_the_gate_trips_above_a_fifth_of_the_set(tmp_path, unread, reported):
    """Two sheets in ten is 20% and stays inside the floor; three is 30% and is
    reported. Below the floor the sheets that could not be read are still named
    — under 'Not checked', where the rules that stood down for want of a sheet
    reference are listed."""
    codes: List[Optional[str]] = list(PILATES[:10])
    for i in range(unread):
        codes[i + 2] = None
    facts, res = reviewed(tmp_path, permit_set(codes), name=f"floor{unread}.pdf")

    hits = [f for f in res.findings if f.rule_id == RULE]
    assert bool(hits) is reported
    if reported:
        assert f"{unread} of 10 sheets" in hits[0].result
    else:
        detail = [a.detail for a in res.abstentions if a.rule_id == RULE]
        assert detail == ["numbered by page instead: p3, p4"]


def test_a_set_with_no_sheets_abstains_rather_than_dividing_by_zero():
    """A zero-page PDF cannot be written to disk, so the branch is exercised
    directly. It exists because a coverage ratio needs a denominator."""
    from fbcreview.facts import ProjectFacts
    from fbcreview.rules import RuleResult

    out = RuleResult()
    sheet_numbers_read(ProjectFacts(source_path=""), out)

    assert not out.findings
    assert [a.reason for a in out.abstentions] == ["the set has no sheets to identify"]


# ── what a below-floor miss actually costs ────────────────────────────────
# Feedback f3e3a498675b, raised through Refine analysis on a 15-sheet MEP set
# where one sheet fell back to a page number. The reviewer marked the rule as
# having errored. It had not — the floor is deliberate, and three sheets in ten
# still trips it — but the reason it gave was engine policy rather than
# consequence: "sheet numbers were read on 14 of 15 sheets, which is inside the
# 20% reporting floor" tells a reviewer nothing they can act on or judge.
def test_a_below_floor_miss_says_what_it_costs_not_what_the_threshold_is(tmp_path):
    codes: List[Optional[str]] = list(PILATES[:10])
    codes[3] = None
    _facts, res = reviewed(tmp_path, permit_set(codes), name="cost.pdf")

    assert not [f for f in res.findings if f.rule_id == RULE]
    reason = [a.reason for a in res.abstentions if a.rule_id == RULE][0]

    assert "cannot be cited by any check that needs it" in reason
    assert "recorded here rather than raised as a finding" in reason
    # The sheets are still named, under the same detail line as before.
    assert [a.detail for a in res.abstentions if a.rule_id == RULE] == [
        "numbered by page instead: p4"]


def test_the_reason_still_classifies_for_the_abstention_register(tmp_path):
    """webapp/abstentions.py routes this reason by the phrase 'reporting floor'.
    An unclassified reason is a row the register cannot explain or act on."""
    from webapp import abstentions

    codes: List[Optional[str]] = list(PILATES[:10])
    codes[3] = None
    _facts, res = reviewed(tmp_path, permit_set(codes), name="classify.pdf")
    reason = [a.reason for a in res.abstentions if a.rule_id == RULE][0]

    assert abstentions.classify(reason) == "extraction"


def test_several_missed_sheets_below_the_floor_read_as_plural(tmp_path):
    codes: List[Optional[str]] = list(PILATES[:20])
    codes[3] = codes[7] = codes[11] = None          # 3 of 20 is 15%
    _facts, res = reviewed(tmp_path, permit_set(codes), name="plural.pdf")

    assert not [f for f in res.findings if f.rule_id == RULE]
    reason = [a.reason for a in res.abstentions if a.rule_id == RULE][0]
    assert "3 of 20 sheets" in reason
    assert "cannot be cited by any check that needs them" in reason
