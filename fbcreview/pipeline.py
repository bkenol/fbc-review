"""PDF in, findings out. No model in this path."""
from __future__ import annotations
import re
from typing import Dict, List, Optional
import pymupdf

from .facts import ProjectFacts, Door, VentilationRow
from .extract.areas import find_tally
from .extract.document import sheet_index, page_geometry, ocg_names
from .extract.blocks import code_data_block, labelled_values, normalise, to_feet, to_inches
from .extract.formblocks import FormRow, find_value, form_block
from .extract.schedules import find_schedule, split_merged_row
from .rules import run_all, RuleResult, registered
from .rules import (r_egress, r_doors, r_mechanical, r_electrical,   # noqa: F401
                    r_crosssheet, r_geometry, r_declaration, r_heightarea,
                    r_occupancy, r_structural, r_code)
from .declaration import ProjectDeclaration

_FTIN = re.compile(r"(\d+)\s*'\s*-\s*(\d+)")


# ── where the data lives ──────────────────────────────────────────────────
#
# A sheet number tells you the discipline and the series. It does not tell you
# what is printed on the sheet, and no two offices number alike: the three sets
# this engine has been measured against use G-001/A-101, AG.001/AA.101 and
# M.101/E.201, and none of them contains a sheet numbered G-0, A-2, M-1 or E-3.
# Keying extraction on a literal sheet number found the code block on none of
# them and the schedules on none of them — the lookups simply returned nothing
# and the review came back empty on a set that carried every table it needed.
#
# What is stable is what the blocks and schedules are *called*, because the name
# is printed on the sheet as the table's own title. So the vocabulary below is
# names, every sheet is searched for them, and where a sheet number appears it
# is carried as provenance rather than used as a key.

#: Headers a code-analysis block is printed under. Order is preference: the
#: first header that yields a row for a given section wins.
CODE_BLOCK_HEADERS = (
    "EGRESS",
    "BUILDING CODE ANALYSIS",
    "CODE COMPLIANCE DATA",
    "CODE COMPLIANCE",
    "CODE ANALYSIS",
)

#: (name the rules ask for, titles a set might actually print).
#:
#: The left-hand name is the contract with `ProjectFacts.schedule()` and must
#: not drift; the right-hand titles are what real title blocks say. ITEC prints
#: "DOOR SCHEDULE" where the JSP set prints "DOOR AND FRAME SCHEDULE", and both
#: are the table the door rules want.
SCHEDULE_VOCABULARY = (
    ("DOOR AND FRAME SCHEDULE",
     ("DOOR AND FRAME SCHEDULE", "DOOR SCHEDULE")),
    ("ROOF TOP UNIT SCHEDULE",
     ("ROOF TOP UNIT SCHEDULE", "ROOF TOP UNIT", "RTU SCHEDULE")),
    ("AIR BALANCE", ("AIR BALANCE",)),
    ("OCCUPANT DENSITY", ("OCCUPANT DENSITY",)),
    ("ELECTRICAL LOAD CALCULATIONS",
     ("ELECTRICAL LOAD CALCULATIONS", "LOAD CALCULATIONS", "LOAD CALCULATION")),
    ("PANEL SCHEDULE", ("PANEL SCHEDULE",)),
    # Not on a predictable sheet either: a tenant fit-out puts one on the
    # life-safety sheet, a shell puts one on the first floor plan.
    ("OCCUPANT LOAD", ("OCCUPANT LOAD",)),
)


_SERIES = re.compile(r"^([A-Za-z]+)")


def _series(code: str) -> str:
    """The letters a sheet number opens with — its discipline series.

    'G-001' -> 'G', 'AG.001' -> 'AG', 'M.101' -> 'M', 'E-3' -> 'E'. Offices
    prefix the discipline letter differently (a plain 'G' general sheet, or an
    'AG' architectural-general one), so callers test the series with startswith
    or endswith rather than equality.
    """
    m = _SERIES.match(code or "")
    return m.group(1).upper() if m else ""


def _comma_float(raw: str) -> float:
    return float(raw.replace(",", ""))


def _first_match(sheets, text, pattern, cast=None):
    """First capture of `pattern` across `sheets`, in sheet order.

    Returns None when no sheet states it, which is the same "not stated" the
    fixed-sheet lookup produced when the sheet was absent — the rules abstain
    on it rather than assuming a value.
    """
    rx = re.compile(pattern, re.I)
    for s in sheets:
        m = rx.search(text.get(s.index) or "")
        if m:
            return cast(m.group(1)) if cast else m.group(1)
    return None


def _sheets_naming(facts, text, needle):
    """Sheets whose own text carries `needle`.

    This text check is what keeps searching every sheet affordable:
    `find_tables()` and `search_for()` both cost real time, and a permit set is
    mostly sheets that carry neither a code block nor a schedule. Reading the
    already-extracted page text first costs nothing and rules almost all of
    them out.
    """
    n = needle.upper()
    return [s for s in facts.sheets if n in (text.get(s.index) or "").upper()]


def _leaf_inches(raw: str):
    m = _FTIN.search(raw or "")
    if m:
        return float(m.group(1)) * 12 + float(m.group(2))
    return to_inches(raw)


def build_facts(path: str) -> ProjectFacts:
    doc = pymupdf.open(path)
    text = {p: doc[p].get_text() for p in range(doc.page_count)}
    facts = ProjectFacts(source_path=path, text_by_page=text)
    facts.sheets = sheet_index(doc, text)
    facts.meta["cad_layers"] = ocg_names(doc)
    facts.meta["native_vector"] = len(facts.meta["cad_layers"]) > 0

    for p in range(doc.page_count):
        facts.geometry[p] = page_geometry(doc, p, text[p])

    # ── code data blocks (unruled) ────────────────────────────────────────
    seen_data = set()
    for header in CODE_BLOCK_HEADERS:
        for s in _sheets_naming(facts, text, header):
            for d in code_data_block(doc, s.index, header, s.code):
                d = normalise(d)
                # The same row reached through two headers is one row. A block
                # titled "LIFE SAFETY / EGRESS" answers to both.
                key = (d.section, d.label, d.sheet)
                if key in seen_data:
                    continue
                seen_data.add(key)
                facts.code_data.append(d)

    # ── ruled schedules ───────────────────────────────────────────────────
    for canonical, titles in SCHEDULE_VOCABULARY:
        if facts.schedule(canonical):
            continue
        for title in titles:
            found = None
            for s in _sheets_naming(facts, text, title):
                found = find_schedule(doc, s.index, title, s.code, name=canonical)
                if found:
                    facts.schedules.append(found)
                    break
            if found:
                break

    # ── doors ─────────────────────────────────────────────────────────────
    ds = facts.schedule("DOOR AND FRAME SCHEDULE")
    if ds:
        for r in ds.rows:
            num = r.mark.split()[-1] if r.mark else ""
            if not re.match(r"^\d{2,3}[A-Z]?$", num):
                continue
            vals = list(r.fields.values())
            style = vals[1] if len(vals) > 1 else ""
            facts.doors.append(Door(
                number=num, style=style.split()[-1] if style else "",
                width_in=_leaf_inches(vals[2] if len(vals) > 2 else ""),
                height_in=_leaf_inches(vals[3] if len(vals) > 3 else ""),
                thickness_in=to_inches(vals[4] if len(vals) > 4 else "") or None,
                material=vals[5] if len(vals) > 5 else "",
                hardware_set=vals[9] if len(vals) > 9 else "",
                remarks=vals[10] if len(vals) > 10 else "",
                is_existing="EXISTING" in (style or "").upper(),
                sheet=ds.sheet, page=ds.page))

    # ── ventilation totals ────────────────────────────────────────────────
    oa = facts.schedule("OCCUPANT DENSITY")
    if oa:
        for r in oa.rows:
            if r.mark.upper().startswith("TOTAL"):
                nums = re.findall(r"\d[\d,]*\.?\d*", r.mark.replace(",", ""))
                if len(nums) >= 3:
                    facts.meta["area_m1_sf"] = float(nums[0])
                    facts.meta["oa_persons"] = float(nums[1])
                    facts.meta["oa_required_cfm"] = float(nums[2])
            parts = split_merged_row(r)
            if parts:
                for p in parts:
                    v = list(p.fields.values())
                    facts.ventilation.append(VentilationRow(
                        p.mark, _f(v, 1), _f(v, 2), _f(v, 3), _f(v, 4), _f(v, 5), _f(v, 6)))

    # ── scalar project data scraped from the general sheets ───────────────
    #
    # "The general sheets" is a role, not a number. G-001 and AG.001 both fill
    # it. The two area reads stay deliberately separate: XSHEET.BUILDING_AREA
    # is a cross-sheet check, and collapsing them onto one sheet would make it
    # compare a value with itself and never disagree. So each phrasing is
    # searched across the general sheets independently, exactly as the two
    # fixed sheets used to supply them.
    general = [s for s in facts.sheets if _series(s.code).endswith("G")]
    facts.meta["area_g0_sf"] = _first_match(
        general, text, r"AREA:?\s*([\d,]+)\s*SF", cast=_comma_float)
    facts.meta["area_g1_sf"] = _first_match(
        general, text, r"TOTAL\s+([\d,]+)\s*SF", cast=_comma_float)
    facts.meta["risk_category"] = _first_match(
        general, text, r"RISK\s*CATEGORY:?\s*(I{1,3}V?|\d)")
    for key in ("area_g0_sf", "area_g1_sf", "risk_category"):
        if facts.meta[key] is None:
            del facts.meta[key]

    # An area the set tabulates rather than states. Searched across the whole
    # set, general sheets first — unlike the three reads above, which are scoped
    # to the general series and on a submittal with no G sheets never run at
    # all. This is a sum of listed spaces and deliberately does not become
    # `building_area_sf`; see `extract/areas.py`.
    tally = find_tally(text, facts.sheets)
    if tally is not None:
        facts.meta["area_tally"] = tally.as_meta()

    for s in _sheets_naming(facts, text, "BUILDING CODE ANALYSIS"):
        bca = labelled_values(doc, s.index, "BUILDING CODE ANALYSIS")
        if not bca:
            continue
        facts.meta["building_code_analysis"] = bca
        for k, v in bca.items():
            ku = k.upper()
            if ku == "OCCUPANT LOAD":
                facts.meta["occupant_load"] = float(re.sub(r"[^\d.]", "", v) or 0) or None
            elif "SPRINKLER" in ku:
                facts.meta["sprinklered"] = v.strip().upper().startswith("Y")
            elif "EGRESS WIDTH FACTOR" in ku:
                facts.meta["stated_capacity_factor"] = float(re.sub(r"[^\d.]", "", v) or 0) or None
        break

    _read_form_blocks(doc, facts, text)

    electrical = [s for s in facts.sheets if _series(s.code).startswith("E")]
    rating = _first_match(
        electrical, text,
        r"ELECTRICAL\s+PANEL[^\n]*\n(?:[^\n]*\n){0,3}?\s*(\d{3,4})\s*A\b",
        cast=float)
    if rating is not None:
        facts.meta["panel_rating_a"] = rating
    kaic = _first_match(electrical, text, r"(\d{3,4})\s*KAIC", cast=float)
    if kaic is not None:
        facts.meta["service_kaic"] = kaic
    doc.close()
    return facts


# ── label/value form blocks ──────────────────────────────────────────────────
#
# The same code-analysis blocks, read for the shape the parsers above cannot
# see: values that are words rather than numbers, two `label: value` pairs on
# one visual row, and a citation banner above the table instead of a section
# number on every line. See `extract/formblocks.py`.
#
# This pass never overwrites a value the stricter parsers produced. It is what
# the sheet says when they came back empty, and everything it reads is recorded
# with the label it was printed under, where on the sheet it sits and what the
# drafter cited above it — so a reviewer can check any of it against the paper.

#: Labels that contain OCCUPANT LOAD and are not the occupant load. A code block
#: prints `OCCUPANT LOAD FACTOR: 150` beside `TOTAL OCCUPANT LOAD: 152`; reading
#: the factor as the load is how a plausible wrong number enters a review.
_NOT_A_LOAD = ("FACTOR", "DENSITY", "PER")


def _number(raw: str) -> Optional[float]:
    """The first plain number in a value, or None.

    Deliberately not `re.sub("[^0-9.]")` over the whole string: that turns
    `TYPE II-B` into `2` and `150 SF/OCC` into `150.` — it cannot fail, which
    on a value that is words is exactly the problem.
    """
    m = re.search(r"\d[\d,]*(?:\.\d+)?", raw or "")
    if not m:
        return None
    try:
        return float(m.group().replace(",", "")) or None
    except ValueError:
        return None


def _read_form_blocks(doc, facts: ProjectFacts, text: Dict[int, str]) -> None:
    rows: List[FormRow] = []
    seen = set()
    for header in CODE_BLOCK_HEADERS:
        for s in _sheets_naming(facts, text, header):
            for row in form_block(doc, s.index, header, s.code):
                key = (row.page, row.strip, round(row.y, 1), row.key, row.value)
                if key in seen:
                    continue
                seen.add(key)
                rows.append(row)
    if not rows:
        return

    facts.meta["form_rows"] = [r.as_meta() for r in rows]
    answered = [r for r in rows if r.value]
    if answered:
        bca = facts.meta.setdefault("building_code_analysis", {})
        for r in answered:
            bca.setdefault(r.label, r.value)

    reads: Dict[str, dict] = {}

    def take(meta_key: str, value, row: FormRow) -> None:
        if value is None or facts.meta.get(meta_key) is not None:
            return
        facts.meta[meta_key] = value
        reads[meta_key] = row.as_meta()

    row = find_value(rows, "OCCUPANT LOAD", without=_NOT_A_LOAD)
    if row:
        take("occupant_load", _number(row.value), row)

    row = find_value(rows, "EGRESS WIDTH FACTOR")
    if row:
        take("stated_capacity_factor", _number(row.value), row)

    row = find_value(rows, "SPRINKLER")
    if row:
        # `norm_sprinkler`, not `startswith("Y")`: this block answers the
        # question with a standard as often as with a yes — `YES, PER NFPA 13`,
        # `NFPA 13R`, `NONE` — and a value nobody can classify leaves the key
        # unset so the rules abstain rather than guess.
        from .reconcile import norm_sprinkler
        answer = norm_sprinkler(row.value)
        if answer is not None:
            take("sprinklered", answer != "NONE", row)

    if reads:
        facts.meta["form_reads"] = reads


def _f(vals, i):
    try:
        return float(re.sub(r"[^\d.\-]", "", vals[i]) or 0) or None
    except Exception:
        return None


def review(path: str, options=None, declaration=None) -> RuleResult:
    """PDF in, findings out.

    `declaration` is a ProjectDeclaration — the answers the applicant gave before
    uploading. Omitting it reproduces the review exactly as it ran before the
    declaration existed.
    """
    return run_all(build_facts(path), options, declaration)
