"""The checks added to reach the hand register, each against the input that motivated it.

Every fixture is drawn at the real Sculpted set's geometry — 2592 x 1728 sheets,
6.2 pt text, the coordinates the layout layer measured on G-1, A-3 and M-1 —
because the readers are only as good as the shapes they were measured on. When
the real set is available (`FBC_TEST_PDF`), `tests/test_reference_sets.py`
makes the same assertions against the sheets themselves.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pymupdf
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fixtures.permit_sets import _linework, _titleblock  # noqa: E402
from fbcreview.pipeline import build_facts  # noqa: E402
from fbcreview.rules import run_all  # noqa: E402
from webapp import abstentions  # noqa: E402

SIZE = 6.2
ASCENT = 6.7


def _at(page, x, y_top, text, size=SIZE):
    page.insert_text((x, y_top + ASCENT * size / SIZE), text, fontsize=size)


def _build(tmp_path, sheets, name="set.pdf"):
    """`sheets`: (code, title, writer) — a writer draws one sheet's content."""
    doc = pymupdf.open()
    for code, title, write in sheets:
        page = doc.new_page(width=2592, height=1728)
        _linework(page, rows=12)
        if write is not None:
            write(page)
        _titleblock(page, code, title)
    path = tmp_path / name
    doc.save(str(path))
    return str(path)


def _review(path):
    facts = build_facts(path)
    return facts, run_all(facts)


def _by_rule(res, rule_id):
    return [f for f in res.findings if f.rule_id == rule_id]


def _abstained(res, rule_id):
    return [a for a in res.abstentions if a.rule_id == rule_id]


def _project_data(page, occupancy="ASSEMBLY (A-3)", sprinklers="SPRINKLERED"):
    _at(page, 1875, 1032, "OCCUPANCY:")
    _at(page, 1875, 1056, occupancy)
    _at(page, 1875, 1079, "FIRE SPRINKLERS:")
    _at(page, 1875, 1102, sprinklers)


# ══ M-02: the exits rated at one factor, the requirement computed at another ═
def _discharges(cap1, cap2, factor="0.15"):
    def write(page):
        for (x, y), (name, width, cap) in zip(
                [(996, 112), (1141, 1504)],
                [("EXIT DISCHARGE 2:", '36"', cap2), ("EXIT DISCHARGE 1:", '72"', cap1)]):
            _at(page, x, y, name)
            rows = [("WIDTH REQUIRED:", '32"'), ("WIDTH PROVIDED:", width),
                    ("OCCUPANT CAPACITY:", str(cap)), ("OCCUPANT LOAD:", "35")]
            for i, (label, value) in enumerate(rows, start=1):
                _at(page, x, y + 11.4 * i, label)
                _at(page, x + 81, y + 11.4 * i, value)
        _at(page, 1732, 1200, f"EGRESS WIDTH FACTOR: {factor}")
    return write


def test_each_exit_block_is_read_as_one_statement(tmp_path):
    facts, _ = _review(_build(tmp_path, [("G-1", "LIFE SAFETY", _discharges(360, 180))]))
    got = {d.name: (d.width_provided_in, d.capacity, d.occupant_load) for d in facts.discharges}
    assert got == {"EXIT DISCHARGE 1": (72.0, 360.0, 35.0), "EXIT DISCHARGE 2": (36.0, 180.0, 35.0)}


def test_exits_rated_at_020_against_a_015_requirement_is_m02(tmp_path):
    _, res = _review(_build(tmp_path, [("G-1", "LIFE SAFETY", _discharges(360, 180))]))
    [f] = _by_rule(res, "EGRESS.FACTOR_CONSISTENCY")
    assert (f.fid, f.status, f.severity) == ("M-02", "OPEN", "MEDIUM")
    assert "0.20 in/occupant" in f.result and "0.15 in/occupant" in f.result
    assert "conservative way" in f.result


def test_exits_rated_at_the_same_factor_verify(tmp_path):
    _, res = _review(_build(tmp_path, [("G-1", "LIFE SAFETY", _discharges(480, 240))]))
    [f] = _by_rule(res, "EGRESS.FACTOR_CONSISTENCY")
    assert (f.fid, f.status) == ("V-UF", "PASS")


# ══ V-17: ceiling tags on the reflected ceiling plan ═══════════════════════
def _ceilings(finished="+10'-0\""):
    def write(page):
        _at(page, 1098, 280, "A.F.F.")
        _at(page, 1097, 292, finished)
        _at(page, 1251, 637, "B/O BAR JOIST")
        _at(page, 1251, 649, "+14'-8\"")
        # A note giving a mounting height — not a ceiling, never a tag.
        _at(page, 2084, 444, "BOTTOM OF SIGNAGE AT 6'-0\" A.F.F.")
    return write


def test_a_ceiling_plan_is_read_tag_by_tag_and_notes_are_not_tags(tmp_path):
    facts, res = _review(_build(tmp_path, [("A-3", "REFLECTED CEILING PLAN", _ceilings())]))
    assert sorted((round(t.height_ft, 2), t.reference) for t in facts.ceilings) == [
        (10.0, "A.F.F."), (14.67, "B/O BAR JOIST")]
    [f] = _by_rule(res, "EGRESS.CEILING_HEIGHT")
    assert (f.fid, f.status, f.sheet) == ("V-17", "PASS", "A-3")
    assert "Clear by 2'-6\"" in f.result


def test_a_ceiling_under_7_6_is_reported(tmp_path):
    _, res = _review(_build(tmp_path, [("A-3", "REFLECTED CEILING PLAN", _ceilings("+7'-0\""))]))
    [f] = _by_rule(res, "EGRESS.CEILING_HEIGHT")
    assert (f.fid, f.status, f.severity) == ("M-CH", "OPEN", "MEDIUM")


def test_the_same_tags_on_a_floor_plan_are_not_ceilings(tmp_path):
    facts, res = _review(_build(tmp_path, [("A-2", "PROPOSED FLOOR PLAN", _ceilings())]))
    assert facts.ceilings == []
    [a] = _abstained(res, "EGRESS.CEILING_HEIGHT")
    assert abstentions.classify(a.reason) == abstentions.EXTRACTION


# ══ V-04 and V-05: rows with no section citation, read off the layout ═══════
def _grid(page, rows, y0=1470):
    """G-0's egress grid: REQUIRED / PROVIDED headers over label rows, at the
    coordinates `tests/test_layout.py` measured on the real sheet."""
    _at(page, 1772.9, y0, "REQUIRED:")
    _at(page, 1889.5, y0, "PROVIDED:")
    for i, (label, req, prov) in enumerate(rows, start=1):
        y = y0 + 23.3 * i
        _at(page, 1567.3, y, label)
        _at(page, 1773.1, y, req)
        _at(page, 1889.8, y, prov)


def _code_rows(door='32"'):
    def write(page):
        _project_data(page)
        _at(page, 1500, 900, "OCCUPANT LOAD: 70")
        _grid(page, [("NUMBER OF EXITS:", "2", "2"),
                     ("CLEAR OPENING WIDTH (1010.1.1):", door, '32"')])
    return write


def test_an_exit_count_row_without_a_citation_is_checked(tmp_path):
    _, res = _review(_build(tmp_path, [("G-0", "COVER SHEET", _code_rows())]))
    [f] = _by_rule(res, "EGRESS.EXIT_COUNT")
    assert (f.fid, f.status, f.sheet) == ("V-04", "PASS", "G-0")
    assert f.result == "Occupant load 70 requires 2 exits; 2 provided."


def test_the_stated_door_requirement_is_checked(tmp_path):
    _, res = _review(_build(tmp_path, [("G-0", "COVER SHEET", _code_rows())]))
    [f] = _by_rule(res, "DOORS.CLEAR_WIDTH_REQUIREMENT")
    assert (f.fid, f.status) == ("V-05", "PASS")
    assert f.title == "32 in. clear opening is the correct requirement"


def test_an_understated_door_requirement_is_reported(tmp_path):
    _, res = _review(_build(tmp_path, [("G-0", "COVER SHEET", _code_rows('30"'))]))
    [f] = _by_rule(res, "DOORS.CLEAR_WIDTH_REQUIREMENT")
    assert (f.fid, f.status, f.severity) == ("H-DR", "OPEN", "HIGH")


# ══ M-05: the assembly occupant-load posting ═══════════════════════════════
def test_an_assembly_set_with_no_posting_anywhere_is_m05(tmp_path):
    _, res = _review(_build(tmp_path, [("G-0", "COVER SHEET", _code_rows())]))
    [f] = _by_rule(res, "EGRESS.OCCUPANT_LOAD_POSTING")
    assert (f.fid, f.status, f.severity) == ("M-05", "OPEN", "MEDIUM")
    assert "symbol" in f.result          # it says what it cannot see


def test_a_posting_note_anywhere_in_the_set_verifies(tmp_path):
    def with_sign(page):
        _code_rows()(page)
        _at(page, 300, 1400, "PROVIDE OCCUPANT LOAD SIGN AT MAIN ENTRY")
    _, res = _review(_build(tmp_path, [("G-0", "COVER SHEET", with_sign)]))
    [f] = _by_rule(res, "EGRESS.OCCUPANT_LOAD_POSTING")
    assert (f.fid, f.status) == ("V-OLP", "PASS")


def test_a_business_occupancy_is_not_asked_for_a_posting(tmp_path):
    def business(page):
        _project_data(page, occupancy="BUSINESS")
    _, res = _review(_build(tmp_path, [("G-0", "COVER SHEET", business)]))
    [a] = _abstained(res, "EGRESS.OCCUPANT_LOAD_POSTING")
    assert abstentions.classify(a.reason) == abstentions.NOT_APPLICABLE


# ══ V-06: the plumbing count ══════════════════════════════════════════════
def _plumbing(provided_wc=("1 UNISEX WC", "1 UNISEX WC"), ratio="1/125 MALE, 1/65 FEMALE"):
    def write(page):
        _project_data(page)
        _at(page, 2106, 1219, "PLUMBING COUNTS", size=8)
        _at(page, 2057, 1259, "ASSEMBLY OCCUPANCY: 70")
        for y, label, value in [(1282, "WC:", ratio), (1294, "LAV:", "1/ 200"),
                                (1305, "DF:", "1/500"), (1317, "SERVICE SINK:", "1")]:
            _at(page, 2057, y, label)
            _at(page, 2138, y, value)
        _at(page, 2057, 1458, "TOTAL REQUIRED:")
        _at(page, 2192, 1458, "TOTAL PROVIDED")
        required = ["1 WC", "1 LAV", "1 WC", "1 LAV", "1 DF", "1 SERVICE SINK"]
        provided = [provided_wc[0], "1 UNISEX LAV", provided_wc[1], "1 UNISEX LAV", "1 DF",
                    "1 SERVICE SINK"]
        for y, req, prov in zip([1493, 1504, 1539, 1551, 1574, 1598], required, provided):
            _at(page, 2057, y, req)
            if prov:
                _at(page, 2192, y, prov)
    return write


def test_the_plumbing_block_is_read_ratio_by_ratio(tmp_path):
    facts, _ = _review(_build(tmp_path, [("G-1", "LIFE SAFETY", _plumbing())]))
    pc = facts.plumbing
    assert pc.occupant_load == 70 and pc.ratios["wc"] == (125.0, 65.0)
    assert pc.ratios["lav"] == (200.0, 200.0) and pc.ratios["drinking_fountain"][0] == 500
    assert pc.provided == {"wc": 2, "lav": 2, "drinking_fountain": 1, "service_sink": 1}
    assert pc.unisex


def test_every_ratio_split_and_round_up_verifies(tmp_path):
    _, res = _review(_build(tmp_path, [("G-1", "LIFE SAFETY", _plumbing())]))
    [f] = _by_rule(res, "PLUMB.FIXTURE_COUNT")
    assert (f.fid, f.status) == ("V-06", "PASS")
    assert "35/200 = 0.18" in f.result and "Auditoriums without permanent seating" in f.result


def test_a_missing_water_closet_is_p01(tmp_path):
    _, res = _review(_build(tmp_path, [("G-1", "LIFE SAFETY", _plumbing(("1 UNISEX WC", "")))]))
    [f] = _by_rule(res, "PLUMB.FIXTURE_COUNT")
    assert (f.fid, f.status, f.severity) == ("P-01", "OPEN", "HIGH")


def test_a_row_the_corpus_does_not_carry_abstains_and_says_so(tmp_path):
    _, res = _review(_build(tmp_path, [("G-1", "LIFE SAFETY",
                                        _plumbing(ratio="1/150 MALE, 1/75 FEMALE"))]))
    [a] = _abstained(res, "PLUMB.FIXTURE_COUNT")
    assert abstentions.classify(a.reason) == abstentions.CORPUS


# ══ H-01 and L-01: the factor against the function the set names ═══════════
#: G-1's rows as printed, each cell at the x the real sheet puts it — centred
#: in its column, so a lone "0" sits well right of where "150 (GROSS)" starts.
_ROWS = [((2070, 2137, 2192, 2235, 2312), ("RECEPTION", "BUSINESS", "164 SF", "150 (GROSS)", "2")),
         ((2069, 2136, 2193, 2242, 2310), ("MAT STUDIO", "ASSEMBLY", "994 SF", "15 (NET)", "67")),
         ((2057, 2133, 2193, 2255, 2312), ("HALL, RESTROOMS", "ACCESSORY", "204 SF", "0", "0")),
         ((2076, 2138, 2194, 2235, 2312), ("CLOSET", "STORAGE", "13 SF", "300 (GROSS)", "1"))]


def _occupancy_table(page, y0, heading, context):
    """G-1's occupant-load table at its measured coordinates, shifted to `y0`."""
    dy = y0 - 463
    # The real title is 21.4 pt over 8.2 pt text; the same proportion here.
    _at(page, 2083, 463 + dy, heading, size=16)
    _at(page, 2061, 537 + dy, context)
    _at(page, 2239, 594 + dy, "AREA PER")
    _at(page, 2294, 594 + dy, "OCCUPANT")
    _at(page, 2066, 600 + dy, "DESCRIPTION")
    _at(page, 2148, 600 + dy, "USE")
    _at(page, 2194, 600 + dy, "AREA")
    _at(page, 2237, 605 + dy, "OCCUPANT")
    _at(page, 2304, 605 + dy, "LOAD")
    for i, (xs, row) in enumerate(_ROWS):
        for x, text in zip(xs, row):
            _at(page, x, 627 + 18 * i + dy, text)
    _at(page, 2079, 699 + dy, "TOTAL")
    _at(page, 2190, 699 + dy, "1375 SF")
    _at(page, 2310, 699 + dy, "70")


def _occupancy(ffpc_heading="ASSEMBLY - EXERCISE ROOMS WITHOUT EQUIPMENT"):
    def write(page):
        _project_data(page)
        _occupancy_table(page, 463, "OCCUPANCY LOAD - FBC",
                         "OCCUPANCY LOAD (2023 FBC - BUILDING, TABLE 1004.5)")
        _occupancy_table(page, 841, "OCCUPANCY LOAD - FFPC", ffpc_heading)
    return write


def test_both_occupancy_tables_are_read_and_told_apart(tmp_path):
    facts, _ = _review(_build(tmp_path, [("G-1", "LIFE SAFETY", _occupancy())]))
    fbc = [(r.space, r.use, r.factor, r.basis) for r in facts.occupancy_rows if r.code == "FBC"]
    assert ("MAT STUDIO", "ASSEMBLY", 15.0, "net") in fbc
    assert {r.code for r in facts.occupancy_rows} == {"FBC", "FFPC"}


def test_an_exercise_room_at_the_tables_and_chairs_factor_is_h01(tmp_path):
    _, res = _review(_build(tmp_path, [("G-1", "LIFE SAFETY", _occupancy())]))
    h01 = [f for f in _by_rule(res, "OCC.CLASSIFICATION_CONSISTENCY") if f.fid == "H-01"]
    assert len(h01) == 1 and (h01[0].status, h01[0].severity) == ("OPEN", "HIGH")
    assert "EXERCISE ROOMS WITHOUT EQUIPMENT" in h01[0].result
    assert "50 gross" in h01[0].result and "about 20 occupants" in h01[0].result
    ok = [f for f in _by_rule(res, "OCC.CLASSIFICATION_CONSISTENCY") if f.fid == "V-OLF"]
    assert ok and "Reception" in ok[0].result      # BUSINESS at 150 gross holds


def test_a_heading_over_the_assembly_table_does_not_reclassify_the_reception(tmp_path):
    """The FFPC heading names the assembly use; RECEPTION is BUSINESS in both."""
    _, res = _review(_build(tmp_path, [("G-1", "LIFE SAFETY", _occupancy())]))
    assert not any("Reception" in f.title for f in res.findings if f.fid.startswith("H-01"))


def test_with_no_function_named_the_assembly_space_is_left_alone(tmp_path):
    _, res = _review(_build(tmp_path, [("G-1", "LIFE SAFETY", _occupancy("ASSEMBLY"))]))
    assert not [f for f in _by_rule(res, "OCC.CLASSIFICATION_CONSISTENCY") if f.fid == "H-01"]


def test_a_zero_factor_in_a_table_1004_5_column_is_l01(tmp_path):
    _, res = _review(_build(tmp_path, [("G-1", "LIFE SAFETY", _occupancy())]))
    [f] = [f for f in _by_rule(res, "OCC.CLASSIFICATION_CONSISTENCY") if f.fid == "L-01"]
    assert (f.status, f.severity, f.anchor) == ("OPEN", "LOW", "HALL, RESTROOMS")


# ══ one fact, two sheets ══════════════════════════════════════════════════
def _construction(value):
    def write(page):
        _at(page, 1875, 985, "TYPE OF CONSTRUCTION:")
        _at(page, 1875, 1009, value)
    return write


def test_two_sheets_stating_different_construction_types_is_reported(tmp_path):
    _, res = _review(_build(tmp_path, [("G-0", "COVER SHEET", _construction("III-B")),
                                       ("G-1", "LIFE SAFETY", _construction("V-B"))]))
    [f] = _by_rule(res, "XSHEET.STATED_CONFLICT")
    assert (f.fid, f.status, f.severity) == ("M-XS", "OPEN", "MEDIUM")
    assert "III-B" in f.result and "V-B" in f.result


def test_two_sheets_agreeing_verify(tmp_path):
    _, res = _review(_build(tmp_path, [("G-0", "COVER SHEET", _construction("III-B")),
                                       ("G-1", "LIFE SAFETY", _construction("TYPE III-B"))]))
    [f] = _by_rule(res, "XSHEET.STATED_CONFLICT")
    assert (f.fid, f.status) == ("V-XS", "PASS")


def test_the_common_path_finding_says_the_other_sheet_disagrees(tmp_path):
    def g0(page):
        _project_data(page)
        _grid(page, [("COMMON PATH OF TRAVEL (1006.2.1):", "50 LF", "8'-1\"")])

    def g1(page):
        _at(page, 1500, 900, "MAX. COMMON PATH: 75 LF")
    _, res = _review(_build(tmp_path, [("G-0", "COVER SHEET", g0), ("G-1", "LIFE SAFETY", g1)]))
    [f] = _by_rule(res, "EGRESS.COMMON_PATH")
    assert f.title == "Common path requirement understated on G-0 and contradicts G-1"
    assert "G-1 states 75 LF, which is what the table requires" in f.result
    # Reported once, inside the finding about the row, not again as a conflict.
    assert not [x for x in _by_rule(res, "XSHEET.STATED_CONFLICT") if x.status == "OPEN"]


# ══ V-35: the outdoor-air arithmetic ══════════════════════════════════════
def _ruled_table(page, x0, y0, widths, rows, height=18):
    """A table with every cell ruled, so `find_tables` reads it as one."""
    xs = [x0]
    for w in widths:
        xs.append(xs[-1] + w)
    y = y0
    for row in rows:
        h = height * (2 if any("\n" in c for c in row) else 1)
        for i, cell in enumerate(row):
            page.draw_rect(pymupdf.Rect(xs[i], y, xs[i + 1], y + h), color=(0, 0, 0), width=0.5)
            for j, line in enumerate(cell.split("\n")):
                _at(page, xs[i] + 3, y + 3 + 9 * j, line)
        y += h


def _outdoor_air(total="881"):
    def write(page):
        head = ["ROOM", "AREA", "OCCUPANT DENSITY\n#/1000 SF", "RP\nCFM/PERSON",
                "RA CFM/SQFT", "TOTAL\nPERSONS", "TOTAL\n(CFM)"]
        rows = [head,
                ["RECEPTION", "164", "10", "5", "0.06", "2", "20"],
                ["MAT STUDIO", "994", "40", "20", "0.06", "40", "860"],
                ["HALL", "98", "----", "----", "----", "----", "----"],
                ["STORAGE", "14", "----", "----", "0.12", "----", "2"],
                ["TOTAL", "1358", "", "", "", "42", total]]
        _at(page, 1913, 1238, "OUTDOOR AIR CALCULATIONS", size=8)
        _ruled_table(page, 1764, 1260, [70, 50, 110, 90, 90, 60, 60], rows)
    return write


def test_the_outdoor_air_rows_are_read_one_room_at_a_time(tmp_path):
    facts, _ = _review(_build(tmp_path, [("M-1", "MECHANICAL PLAN", _outdoor_air())]))
    rows = {v.room: (v.area_sf, v.density_per_1000, v.persons, v.total_cfm)
            for v in facts.ventilation}
    assert rows["MAT STUDIO"] == (994.0, 40.0, 40.0, 860.0)
    assert rows["HALL"] == (98.0, None, None, None)
    assert facts.meta["oa_required_cfm"] == 881.0


def test_the_outdoor_air_arithmetic_verifies_line_by_line(tmp_path):
    _, res = _review(_build(tmp_path, [("M-1", "MECHANICAL PLAN", _outdoor_air())]))
    [f] = _by_rule(res, "MECH.OUTDOOR_AIR_ARITHMETIC")
    assert (f.fid, f.status) == ("V-35", "PASS")
    assert "20 x 40 + 0.06 x 994 = 859.6" in f.result


def test_a_total_that_does_not_add_up_is_reported(tmp_path):
    _, res = _review(_build(tmp_path, [("M-1", "MECHANICAL PLAN", _outdoor_air("781"))]))
    [f] = _by_rule(res, "MECH.OUTDOOR_AIR_ARITHMETIC")
    assert (f.fid, f.status) == ("M-OA", "OPEN")
    assert "not the 781 stated" in f.result


# ══ every new reason is one a reviewer can read ═══════════════════════════
@pytest.mark.parametrize("rule_id", [
    "EGRESS.FACTOR_CONSISTENCY", "EGRESS.CEILING_HEIGHT", "EGRESS.OCCUPANT_LOAD_POSTING",
    "DOORS.CLEAR_WIDTH_REQUIREMENT", "MECH.OUTDOOR_AIR_ARITHMETIC", "PLUMB.FIXTURE_COUNT",
    "OCC.CLASSIFICATION_CONSISTENCY", "XSHEET.STATED_CONFLICT"])
def test_on_an_empty_set_each_new_rule_abstains_with_a_classified_reason(tmp_path, rule_id):
    _, res = _review(_build(tmp_path, [("G-0", "COVER SHEET", None)]))
    stood = _abstained(res, rule_id)
    assert stood, f"{rule_id} said nothing"
    assert abstentions.classify(stood[0].reason) != abstentions.UNKNOWN


def test_one_sheet_stating_a_value_two_ways_says_so(tmp_path):
    def two_ways(page):
        _construction("III-B")(page)
        _at(page, 300, 985, "CONSTRUCTION TYPE:")
        _at(page, 300, 1009, "V-B")
    _, res = _review(_build(tmp_path, [("G-0", "COVER SHEET", two_ways)]))
    [f] = _by_rule(res, "XSHEET.STATED_CONFLICT")
    assert f.title == "G-0 states the construction type two different ways"
