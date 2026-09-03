"""Areas a set tabulates rather than states.

The failing input, written down. Feedback `feac59646e6c`, raised through Refine
analysis against **SUB1- JSP_Naples,FL_MEP.pdf** — an MEP-only submittal with no
architectural sheets at all:

    DECL.BUILDING_AREA — declined to run
    neither the drawings nor the declaration state this
    Building area per storey

    > Should be able to calculate building area

The reason was not merely unhelpful, it was **false**. M.001 prints an
`OCCUPANCY CALCULATION` table listing `LOBBY/RECEPTION 400 SQ. FT.` and
`STUDIO AREA 1300 SQ. FT.`; the drawings state the components of the area in
live text on the sheet. Nothing in the engine summed a table, and every area
pattern in `reconcile._PATTERNS` requires the literal phrase `BUILDING AREA`.

What these tests hold to account is both halves of that. The sum has to be
found — and it must never become `building_area_sf`, because the sum of the
spaces somebody chose to list is not a gross building area, and Table 506.2 and
Table 1004.5 both want gross. Reporting arithmetic for a person to confirm is an
improvement; laundering it into a stated fact would be a regression dressed as
one.
"""
from __future__ import annotations

import pytest

from fbcreview.declaration import ProjectDeclaration
from fbcreview.extract.areas import AreaTally, find_tally, tally_on_page
from fbcreview.pipeline import build_facts, review
from fixtures import permit_sets as P

RULE = "DECL.BUILDING_AREA"
TOTAL = float(P.MEP_AREA_TOTAL)          # 1,700 SF


def mep(tmp_path, name="mep.pdf", **kw) -> str:
    pdf = tmp_path / name
    pdf.write_bytes(P.mep_only(**kw))
    return str(pdf)


def area_findings(res):
    return [f for f in res.findings if f.rule_id == RULE]


def area_abstentions(res):
    return [a for a in res.abstentions if a.rule_id == RULE]


# ── the reproduction ──────────────────────────────────────────────────────
def test_the_set_states_the_components_of_its_area(tmp_path):
    """Before anything else: the numbers really are on the sheet, in live text,
    and there is no general sheet anywhere for a scoped read to find."""
    facts = build_facts(mep(tmp_path))
    assert [s.code for s in facts.sheets] == [
        "M.001", "M.101", "M.501", "P.101", "E.101", "E.501"]
    assert not [s for s in facts.sheets if s.code.startswith("G")]

    text = facts.text_by_page[0]
    assert "LOBBY/RECEPTION" in text and "400 SQ. FT." in text
    assert "STUDIO AREA" in text and "1,300 SQ. FT." in text


def test_the_area_table_is_read_and_summed(tmp_path):
    facts = build_facts(mep(tmp_path))
    tally = facts.meta.get("area_tally")

    assert tally is not None, "the table on M.001 was not found"
    assert tally["sheet"] == "M.001"
    assert tally["heading"] == "OCCUPANCY CALCULATION"
    assert [(r["label"], r["sf"]) for r in tally["rows"]] == [
        ("LOBBY/RECEPTION", 400.0), ("STUDIO AREA", 1300.0)]
    assert tally["summed_sf"] == TOTAL


def test_the_rule_reports_the_arithmetic_instead_of_standing_down(tmp_path):
    """Before this change: one abstention, no finding. After: the reverse."""
    res = review(mep(tmp_path))

    assert area_abstentions(res) == []
    found = area_findings(res)
    assert len(found) == 1

    f = found[0]
    assert (f.fid, f.status, f.severity) == ("D-ARE", "OPEN", "MEDIUM")
    assert f.sheet == "M.001"
    assert f"{TOTAL:,.0f} SF" in f.title
    # The working is shown, so it can be checked against the table itself.
    assert "LOBBY/RECEPTION 400 SF" in f.result
    assert "STUDIO AREA 1,300 SF" in f.result


def test_the_reason_that_was_false_is_gone(tmp_path):
    """It said the drawings do not state this, over a sheet that states it."""
    reasons = [a.reason for a in area_abstentions(review(mep(tmp_path)))]
    assert "neither the drawings nor the declaration state this" not in reasons


# ── the property that must survive ────────────────────────────────────────
def test_the_summed_area_never_becomes_the_building_area(tmp_path):
    """A sum of listed spaces is net of walls, shafts and unlisted circulation.
    Table 506.2 wants gross. Feeding this in would launder an estimate into a
    stated fact, so those checks still stand down."""
    facts = build_facts(mep(tmp_path))
    assert "building_area_sf" not in facts.meta
    assert "total_area_sf" not in facts.meta

    from fbcreview.reconcile import value_of
    assert value_of(facts, "building_area_sf") is None

    stood_down = {a.rule_id for a in review(mep(tmp_path)).abstentions}
    assert "HEIGHT_AREA.TABLE_506_AREA" in stood_down
    assert "XSHEET.BUILDING_AREA" in stood_down


def test_the_finding_says_the_figure_is_not_used_downstream(tmp_path):
    """An inferred value that does not say so is indistinguishable from a
    stated one to whoever reads the report."""
    f = area_findings(review(mep(tmp_path)))[0]
    assert "not a gross building area" in f.result
    assert "NOT used for the Table 506.2" in f.result
    assert "derived from the set's own table" in f.code
    assert "gross or net" in f.action


def test_answering_the_question_supersedes_the_derived_figure(tmp_path):
    """The tabulated tier runs only when nothing above it answered. A declared
    area is above it, so ordinary reconciliation takes over and the derived
    figure is not reported — a declared 1,850 SF gross and a tabulated 1,700 SF
    net are not in conflict, and reporting them as one would be wrong."""
    res = review(mep(tmp_path), None, ProjectDeclaration(building_area_sf=1850.0))

    assert area_findings(res) == []
    assert [a.reason for a in area_abstentions(res)] == [
        "you answered this, but the drawings do not state it in text this build "
        "can read — so there is nothing to cross-check it against"]


def test_a_set_that_states_its_area_is_untouched(tmp_path):
    """The reference sets state theirs, and must review exactly as before."""
    pdf = tmp_path / "sculpted.pdf"
    pdf.write_bytes(P.sculpted_like())
    res = review(str(pdf))
    assert [f.fid for f in area_findings(res)] != ["D-ARE"]


# ── what is not a table, and what is not an area ──────────────────────────
def test_one_row_is_a_note_not_a_table(tmp_path):
    res = review(mep(tmp_path, area_rows=[("STUDIO AREA", 1300)]))
    assert area_findings(res) == []
    assert [a.reason for a in area_abstentions(res)] == [
        "neither the drawings nor the declaration state this"]


def test_a_number_without_an_area_unit_is_not_an_area():
    """Occupant counts, CFM figures and room numbers all sit in tables beside
    labels. Requiring the unit is what keeps them out."""
    text = ("OCCUPANCY CALCULATION\n"
            "LOBBY/RECEPTION   400\n"
            "STUDIO AREA   1300 CFM\n"
            "CORRIDOR   12 PERSONS\n")
    assert tally_on_page(text, 0, "M.001") is None


def test_a_heading_mentioned_in_a_sentence_does_not_open_a_table():
    text = ("SEE M.001 FOR AREA CALCULATIONS AND OCCUPANT LOADS\n"
            "LOBBY/RECEPTION   400 SQ. FT.\n"
            "STUDIO AREA   1300 SQ. FT.\n")
    assert tally_on_page(text, 0, "M.001") is None


@pytest.mark.parametrize("heading", [
    "OCCUPANCY CALCULATION", "AREA CALCULATIONS", "AREA TABULATION",
    "AREA SUMMARY", "ROOM SCHEDULE", "SQUARE FOOTAGE", "TENANT AREAS",
])
def test_the_lexicon_covers_what_offices_actually_call_this_table(heading, tmp_path):
    """Widening the reach of a read is the point; the literal phrase is what
    made the engine miss this in the first place."""
    facts = build_facts(mep(tmp_path, name=f"{heading[:6]}.pdf", heading=heading))
    assert facts.meta["area_tally"]["summed_sf"] == TOTAL


@pytest.mark.parametrize("unit", ["SQ. FT.", "SQ.FT.", "SF", "S.F.", "SQ FT"])
def test_the_unit_is_written_several_ways(unit):
    text = f"AREA SUMMARY\nLOBBY   400 {unit}\nSTUDIO   1300 {unit}\n"
    tally = tally_on_page(text, 0, "M.001")
    assert tally is not None and tally.summed_sf == TOTAL


# ── the table's own total is the arithmetic check ─────────────────────────
def test_a_stated_total_that_agrees_is_reported_as_the_total(tmp_path):
    facts = build_facts(mep(tmp_path, stated_total=True))
    tally = facts.meta["area_tally"]
    assert tally["stated_total"] == TOTAL
    assert tally["total_sf"] == TOTAL

    f = area_findings(review(mep(tmp_path, name="t2.pdf", stated_total=True)))[0]
    assert "agrees with the sum of its rows" in f.result


def test_a_stated_total_that_disagrees_means_the_table_was_misread():
    """The table is checking our arithmetic for us. When the two disagree we
    did not read the table we think we read, and reporting nothing is the only
    defensible answer."""
    text = ("AREA SUMMARY\n"
            "LOBBY   400 SQ. FT.\n"
            "STUDIO   1300 SQ. FT.\n"
            "TOTAL   9500 SQ. FT.\n")
    tally = tally_on_page(text, 0, "M.001")
    assert tally is not None and tally.reliable is False
    # find_tally drops an unreliable read rather than returning it.
    assert find_tally({0: text}, [_sheet(0, "M.001")]) is None


def test_a_total_inside_the_tolerance_still_agrees():
    """Rounding in the drafter's own arithmetic is not a misread."""
    text = ("AREA SUMMARY\n"
            "LOBBY   400 SQ. FT.\n"
            "STUDIO   1300 SQ. FT.\n"
            "TOTAL   1710 SQ. FT.\n")
    tally = tally_on_page(text, 0, "M.001")
    assert tally.reliable is True
    assert tally.total_sf == 1710.0        # the table's own number wins


@pytest.mark.parametrize("value", ["2", "4000000"])
def test_an_implausible_area_is_not_a_room(value):
    text = f"AREA SUMMARY\nLOBBY   {value} SQ. FT.\nSTUDIO   1300 SQ. FT.\n"
    assert tally_on_page(text, 0, "M.001") is None


# ── where it looks ────────────────────────────────────────────────────────
class _sheet:
    def __init__(self, index, code):
        self.index, self.code = index, code


def test_a_general_sheet_is_preferred_over_a_discipline_sheet():
    """A code-data sheet is the authority for what the set claims."""
    pages = {
        0: "AREA SUMMARY\nLOBBY   400 SQ. FT.\nSTUDIO   1300 SQ. FT.\n",
        1: "AREA SUMMARY\nSHED   50 SQ. FT.\nSTORE   60 SQ. FT.\n",
    }
    sheets = [_sheet(0, "M.001"), _sheet(1, "G-002")]
    assert find_tally(pages, sheets).sheet == "G-002"


def test_it_carries_on_past_the_general_sheets_when_there_are_none():
    """The scoped reads in pipeline.py stop at the general series and so never
    run on an MEP-only set. This one keeps going, which is the whole point."""
    pages = {0: "AREA SUMMARY\nLOBBY   400 SQ. FT.\nSTUDIO   1300 SQ. FT.\n"}
    assert find_tally(pages, [_sheet(0, "M.001")]).sheet == "M.001"


def test_a_set_with_no_table_at_all_yields_nothing():
    assert find_tally({0: "MECHANICAL NOTES\nSEE SPECIFICATIONS\n"},
                      [_sheet(0, "M.001")]) is None
