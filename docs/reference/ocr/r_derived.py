"""Rules that run on the code-analysis scalars alone.

Every rule here needs only values from `facts.building` and `facts.occupant_rows`
— no layer filtering, no vector geometry, no scale resolution.  That is
deliberate: these are exactly the checks that stay available on a set plotted to
raster, where the geometry-dependent rules have nothing to work with.

They are also the checks a plans examiner does first.
"""
from __future__ import annotations

import datetime
import math
import re
from typing import List, Optional

from ..codes import fbc2023 as C
from ..confidence import Abstention
from ..facts import ProjectFacts
from ..extract.building import is_sprinklered
from . import Finding, RuleResult, rule

_SEQ_TOLERANCE = 0.75      # fraction of rows that must look sequential


def _rect_for(facts: ProjectFacts, page: int, *needles: str):
    """The page rectangle of a harvested form row, for anchoring markup on a
    sheet that carries no searchable text."""
    want = [n.upper() for n in needles]
    for r in facts.meta.get("form_rows", {}).get(page, []):
        lab = r["label"].upper()
        if all(w in lab for w in want):
            return tuple(r["rect"])
    return None


def _union(rects):
    rs = [r for r in rects if r]
    if not rs:
        return None
    return (min(r[0] for r in rs), min(r[1] for r in rs),
            max(r[2] for r in rs), max(r[3] for r in rs))


def _today() -> str:
    return datetime.date.today().isoformat()


# ── 1. code edition currency ─────────────────────────────────────────────────

@rule("CODE.EDITION_CURRENT")
def edition_current(facts: ProjectFacts, res: RuleResult) -> None:
    ev = facts.building.get("code_edition")
    if not ev:
        res.abstentions.append(Abstention(
            "CODE.EDITION_CURRENT",
            "no code edition could be read from the set",
            detail="looked for a 'FLORIDA BUILDING CODE nTH EDITION' banner"))
        return
    edition = ev.value
    meta = C.EDITIONS.get(edition)
    if not meta:
        res.abstentions.append(Abstention(
            "CODE.EDITION_CURRENT", f"unknown edition key {edition!r}"))
        return
    today = _today()
    current = C.edition_current_on(edition, today)
    page = ev.page or 0
    sheet = facts.sheet_code(page)
    cur = C.EDITIONS[C.CURRENT_EDITION]

    if current:
        res.findings.append(Finding(
            fid="", rule_id="CODE.EDITION_CURRENT", status="PASS",
            severity="VERIFIED", discipline="Code currency", page=page, sheet=sheet,
            anchor="FLORIDA BUILDING CODE",
            anchor_rect=_rect_for(facts, page, "OCCUPANCY"),
            title=f"The set is drawn to the current code edition",
            checked=f"Code edition cited across the set, against the edition in force on {today}.",
            result=f"{meta['name']} took effect {meta['effective']} and is still in force. Correct.",
            code=f"Florida Building Code, {meta['name']}"))
        return

    res.findings.append(Finding(
        fid="", rule_id="CODE.EDITION_CURRENT", status="OPEN",
        severity="CRITICAL", discipline="Code currency", page=page, sheet=sheet,
        anchor="FLORIDA BUILDING CODE",
        anchor_rect=_rect_for(facts, page, "OCCUPANCY"),
        title="The set is cited to a superseded edition of the Florida Building Code",
        checked=(f"Every code banner on this sheet reads {meta['name']}. "
                 f"Checked against the edition in force on {today}."),
        result=(f"{meta['name']} was superseded on {meta['superseded']}. The edition in "
                f"force is the {cur['name']}, which adopts {cur['ibc']} IBC and "
                f"ASCE 7-{cur['asce7'].split('-')[-1]} in place of "
                f"ASCE 7-{meta['asce7'].split('-')[-1]}."),
        code=f"Florida Building Code, {cur['name']}",
        action=("Confirm whether a permit was issued from the original submittal. If it "
                "lapsed, every code reference has to be updated and the wind analysis "
                "re-run to the current standard — this is a re-analysis, not a "
                "cover-sheet edit."),
        body=(f"The set was legitimately {meta['name']} when it was drawn. This finding "
              f"is about what happens if it is submitted, re-submitted or revived now.")))


# ── 2. single occupancy vs Table 508.4 ───────────────────────────────────────

@rule("OCCUPANCY.SEPARATION_CONTRADICTION")
def separation_contradiction(facts: ProjectFacts, res: RuleResult) -> None:
    sep = facts.building.get("separation_note")
    occ = facts.building.get("occupancy_raw")
    mixed = facts.building.get("mixed_occupancy")
    if not sep:
        res.abstentions.append(Abstention(
            "OCCUPANCY.SEPARATION_CONTRADICTION",
            "no occupancy separation row was found"))
        return
    text = str(sep.value).upper()
    cites_508 = "508.4" in text or "SEPARATED" in text
    page = sep.page or 0
    sheet = facts.sheet_code(page)

    # How many occupancy classifications does the sheet actually declare?
    # This, not the yes/no box, is what decides whether Table 508.4 can apply:
    # 508.4 governs a building containing MORE THAN ONE occupancy. Reading the
    # classification is also more reliable than reading a checkbox answer that
    # often sits in its own narrow column.
    groups: List[str] = []
    if occ is not None:
        raw = str(occ.value).upper()
        groups = sorted(set(re.findall(r"\b([ABEFHIMRSU])\s*-\s*[1-5]\b", raw)))
        if not groups:
            from ..extract.building import _OCCUPANCY_WORDS
            groups = sorted({g for word, g in _OCCUPANCY_WORDS.items() if word in raw})
    declared_single = (len(groups) == 1) if groups else None
    if mixed is not None and mixed.value is True:
        declared_single = False

    if not cites_508:
        res.findings.append(Finding(
            fid="", rule_id="OCCUPANCY.SEPARATION_CONTRADICTION", status="PASS",
            severity="VERIFIED", discipline="Occupancy", page=page, sheet=sheet,
            anchor="OCCUPANCY SEPARATION",
            anchor_rect=_rect_for(facts, page, "OCCUPANCY SEPARATION"),
            title="Occupancy separation is stated consistently",
            checked="Occupancy separation row against the mixed-occupancy declaration.",
            result=f"Reads {sep.value!r}; no separated-occupancy provision is invoked.",
            code="FBC-B 508.3 · 508.4"))
        return

    if declared_single is None:
        res.abstentions.append(Abstention(
            "OCCUPANCY.SEPARATION_CONTRADICTION",
            "the sheet cites Table 508.4 but no occupancy classification could be read",
            page=page))
        return

    if not declared_single:
        res.findings.append(Finding(
            fid="", rule_id="OCCUPANCY.SEPARATION_CONTRADICTION", status="PASS",
            severity="VERIFIED", discipline="Occupancy", page=page, sheet=sheet,
            anchor="OCCUPANCY SEPARATION",
            anchor_rect=_rect_for(facts, page, "OCCUPANCY SEPARATION"),
            title="Separated-occupancy provision matches a mixed-occupancy building",
            checked="Table 508.4 citation against the mixed-occupancy declaration.",
            result="The building is declared mixed occupancy, so Table 508.4 applies.",
            code="FBC-B 508.4"))
        return

    res.findings.append(Finding(
        fid="", rule_id="OCCUPANCY.SEPARATION_CONTRADICTION", status="OPEN",
        severity="HIGH", discipline="Occupancy", page=page, sheet=sheet,
        anchor="OCCUPANCY SEPARATION",
        anchor_rect=_rect_for(facts, page, "OCCUPANCY SEPARATION"),
        title="The occupancy analysis contradicts itself",
        checked=("The occupancy rows on this sheet, read against each other: the "
                 "classification, the mixed-occupancy answer and the separation row."),
        result=(f"The sheet classifies the whole building as a single occupancy — "
                f"{occ.value if occ else 'one group'}, Group {groups[0]} — and then states "
                f"{sep.value!r}. Table 508.4 has meaning only in a building containing "
                f"more than one occupancy classification."),
        code="FBC-B 302.1 · 508.3 · 508.4",
        action=("Resolve which is intended. If the building really is a single Group B "
                "shell, delete the Table 508.4 reference. If tenant uses will differ — "
                "and for a multi-tenant shell they usually will — classify each and "
                "apply 508.4 properly, with the separation ratings it requires."),
        body=("This matters more than a drafting inconsistency on a speculative shell. "
              "The occupant load factor, the egress capacity and the required "
              "separations all follow from the classification.")))


# ── 3. occupant load factor sanity ───────────────────────────────────────────

@rule("EGRESS.OCCUPANT_FACTOR_VALID")
def occupant_factor_valid(facts: ProjectFacts, res: RuleResult) -> None:
    rows = [r for r in facts.occupant_rows if r.factor is not None]
    if len(rows) < 3:
        res.abstentions.append(Abstention(
            "EGRESS.OCCUPANT_FACTOR_VALID",
            "no occupant-load table with a readable factor column was found"))
        return

    known = {float(f) for f, _ in C.OCCUPANT_LOAD_FACTORS.values()}
    factors = [r.factor for r in rows]
    page, sheet = rows[0].page, rows[0].sheet
    # Anchor on the table row itself. Looking the label up by text would match
    # the unit TAG on the floor plan instead — same words, wrong place, and the
    # marker lands on the drawing rather than on the table the finding is about.
    rect = _union([r.rect for r in rows])

    bad = [f for f in factors if f not in known]
    if not bad:
        res.findings.append(Finding(
            fid="", rule_id="EGRESS.OCCUPANT_FACTOR_VALID", status="PASS",
            severity="VERIFIED", discipline="Means of egress", page=page, sheet=sheet,
            anchor=rows[0].name, anchor_rect=rect,
            title="Every occupant load factor is a Table 1004.5 value",
            checked=f"All {len(rows)} rows of the occupant load table against Table 1004.5.",
            result=f"Factors used: {sorted(set(factors))}. All appear in the table.",
            code="FBC-B Table 1004.5"))
        return

    # Is the column actually a copy of the space numbers?
    seq_like = 0
    for r in rows:
        digits = re.findall(r"\d+", r.name)
        if digits and r.factor is not None and abs(float(digits[-1]) - r.factor) < 0.5:
            seq_like += 1
    looks_like_numbering = seq_like >= _SEQ_TOLERANCE * len(rows)

    if looks_like_numbering:
        result = (f"The factor column reads {[int(f) for f in factors]}, which tracks the "
                  f"space numbers rather than any code value — {seq_like} of {len(rows)} "
                  f"rows carry a 'factor' identical to their own unit number.")
        action = ("Replace the column with the Table 1004.5 factor for each space's actual "
                  "use and re-total the occupant load. Both occupant load tables on this "
                  "sheet carry the same column, so both inherit the error.")
    else:
        result = (f"Factors used: {sorted(set(factors))}. "
                  f"{sorted(set(bad))} do not appear in Table 1004.5.")
        action = "Reconcile each factor with the Table 1004.5 entry for that space's use."

    res.findings.append(Finding(
        fid="", rule_id="EGRESS.OCCUPANT_FACTOR_VALID", status="OPEN",
        severity="HIGH", discipline="Means of egress", page=page, sheet=sheet,
        anchor=rows[0].name, anchor_rect=rect,
        title="The occupant load factor column is not a code factor",
        checked=(f"Every value in the OCCUPANT FACTOR column of the occupant load table "
                 f"({len(rows)} rows), against the allowances in Table 1004.5."),
        result=result, code="FBC-B Table 1004.5", action=action,
        body=("Occupant load is the number the rest of the life safety analysis is built "
              "from — exit capacity, exit count, fixture counts and alarm thresholds all "
              "follow from it.")))


# ── 4. recompute the occupant load ───────────────────────────────────────────

@rule("EGRESS.OCCUPANT_LOAD_COMPUTED")
def occupant_load_computed(facts: ProjectFacts, res: RuleResult) -> None:
    rows = [r for r in facts.occupant_rows if r.area_sf]
    grp = facts.building.get("occupancy_group")
    if not rows or not grp:
        res.abstentions.append(Abstention(
            "EGRESS.OCCUPANT_LOAD_COMPUTED",
            "needs an occupant-load table with areas and a resolved occupancy group",
            detail=f"rows={len(rows)} group={'yes' if grp else 'no'}"))
        return
    lookup = C.occupant_load_factor(grp.value)
    if not lookup:
        res.abstentions.append(Abstention(
            "EGRESS.OCCUPANT_LOAD_COMPUTED",
            f"Table 1004.5 has no unambiguous entry for Group {grp.value}"))
        return
    factor, basis, cite = lookup

    per = [(r.name, r.area_sf, math.ceil(r.area_sf / factor)) for r in rows]
    computed = sum(p[2] for p in per)
    total_area = sum(r.area_sf for r in rows)
    stated_ev = facts.building.get("occupant_load_stated")
    page, sheet = rows[0].page, rows[0].sheet
    rect = _union([r.rect for r in rows])

    detail = "; ".join(f"{n} {a:,.0f} SF -> {l}" for n, a, l in per)

    if stated_ev is None:
        res.findings.append(Finding(
            fid="", rule_id="EGRESS.OCCUPANT_LOAD_COMPUTED", status="OPEN",
            severity="MEDIUM", discipline="Means of egress", page=page, sheet=sheet,
            anchor=rows[0].name, anchor_rect=rect,
            title=f"Occupant load recomputes to {computed}",
            checked=(f"Occupant load recomputed from the areas on this sheet at the "
                     f"Table 1004.5 factor for Group {grp.value} ({factor} {basis})."),
            result=f"{total_area:,.0f} SF over {len(rows)} spaces gives {computed} occupants. {detail}.",
            code=cite,
            action="No total was readable on the sheet to compare against; confirm the stated load."))
        return

    stated = stated_ev.value
    if abs(stated - computed) <= max(2.0, 0.03 * computed):
        res.findings.append(Finding(
            fid="", rule_id="EGRESS.OCCUPANT_LOAD_COMPUTED", status="PASS",
            severity="VERIFIED", discipline="Means of egress", page=page, sheet=sheet,
            anchor=rows[0].name, anchor_rect=rect,
            title="The stated occupant load recomputes correctly",
            checked=(f"Occupant load recomputed from the tabulated areas at "
                     f"{factor} {basis} per Table 1004.5."),
            result=f"Recomputed {computed} against {stated:.0f} stated. Agrees.",
            code=cite))
        return

    direction = "conservative" if stated > computed else "UNDERSTATED"
    res.findings.append(Finding(
        fid="", rule_id="EGRESS.OCCUPANT_LOAD_COMPUTED", status="OPEN",
        severity="MEDIUM" if stated > computed else "HIGH",
        discipline="Means of egress", page=page, sheet=sheet,
        anchor=rows[0].name, anchor_rect=rect,
        title=f"Stated occupant load of {stated:.0f} does not follow from the tabulated areas",
        checked=(f"Every row of the occupant load table recomputed at the Table 1004.5 "
                 f"factor for Group {grp.value} — {factor} {basis}."),
        result=(f"{total_area:,.0f} SF over {len(rows)} spaces gives **{computed}** occupants, "
                f"against **{stated:.0f}** stated. The stated figure is {direction}. {detail}."),
        code=cite,
        action=("Re-derive the total from the areas and the correct factor, then carry the "
                "corrected number through exit capacity, exit count and fixture counts."),
        body=("A conservative overstatement is not itself unsafe, but a load that cannot be "
              "reproduced from the sheet's own areas will not survive plan review.")))


# ── 5. height, stories and area against Tables 504 / 506 ─────────────────────

@rule("HEIGHT_AREA.ALLOWABLE")
def height_area_allowable(facts: ProjectFacts, res: RuleResult) -> None:
    b = facts.building
    grp = b.get("occupancy_group")
    ct = b.get("construction_type")
    spr = b.get("sprinkler_system")
    area = b.get("area_per_floor_sf")
    if not (grp and ct and spr and area):
        missing = [n for n, v in (("occupancy group", grp), ("construction type", ct),
                                  ("sprinkler status", spr), ("area per floor", area))
                   if not v]
        res.abstentions.append(Abstention(
            "HEIGHT_AREA.ALLOWABLE",
            "missing " + ", ".join(missing),
            detail="Tables 504.3 / 504.4 / 506.2 cannot be entered without these"))
        return

    stories_ev = b.get("stories")
    stories = int(stories_ev.value) if stories_ev else 1
    allow = C.allowable(grp.value, ct.value, bool(is_sprinklered(spr.value)), stories)
    if not allow:
        res.abstentions.append(Abstention(
            "HEIGHT_AREA.ALLOWABLE",
            f"no Table 506.2 row loaded for Group {grp.value} / Type {ct.value}"))
        return

    page = area.page or 0
    sheet = facts.sheet_code(page)
    rect = _rect_for(facts, page, "SQUARE FOOTAGE PER FLOOR")
    a = area.value
    stated_allow = b.get("area_allowable_sf")
    ratio = a / allow["area_sf"]

    notes = []
    if stated_allow is not None:
        if abs(stated_allow.value - allow["area_sf"]) <= 1:
            notes.append(f"The sheet's own allowable of {stated_allow.value:,.0f} SF "
                         f"matches the table exactly.")
        else:
            notes.append(f"The sheet states an allowable of {stated_allow.value:,.0f} SF; "
                         f"the table gives {allow['area_sf']:,.0f} SF for these inputs.")

    if a <= allow["area_sf"]:
        res.findings.append(Finding(
            fid="", rule_id="HEIGHT_AREA.ALLOWABLE", status="PASS",
            severity="VERIFIED", discipline="Building height and area",
            page=page, sheet=sheet, anchor="SQUARE FOOTAGE PER FLOOR", anchor_rect=rect,
            title="Area per floor sits inside the Type "
                  f"{ct.value} allowable",
            checked=(f"Group {grp.value}, Type {ct.value}, "
                     f"{'sprinklered' if is_sprinklered(spr.value) else 'not sprinklered'}, "
                     f"{stories} story — entered into Tables 504.3, 504.4 and 506.2."),
            result=(f"{a:,.0f} SF proposed against {allow['area_sf']:,.0f} SF allowable "
                    f"({ratio:.0%} of the allowance). " + " ".join(notes)),
            code=allow["citation"]))
        return

    res.findings.append(Finding(
        fid="", rule_id="HEIGHT_AREA.ALLOWABLE", status="OPEN",
        severity="CRITICAL", discipline="Building height and area",
        page=page, sheet=sheet, anchor="SQUARE FOOTAGE PER FLOOR", anchor_rect=rect,
        title="Area per floor exceeds the tabular allowable",
        checked=(f"Group {grp.value}, Type {ct.value}, "
                 f"{'sprinklered' if is_sprinklered(spr.value) else 'not sprinklered'}, "
                 f"{stories} story — Tables 504.3 / 504.4 / 506.2."),
        result=f"{a:,.0f} SF proposed against {allow['area_sf']:,.0f} SF allowable. "
               + " ".join(notes),
        code=allow["citation"],
        action=("Either take an area increase under 506.3 (frontage) or 507 (unlimited "
                "area), upgrade the construction type, or reduce the footprint.")))


# ── 6. wind design standard follows the edition ──────────────────────────────

@rule("STRUCT.WIND_STANDARD")
def wind_standard(facts: ProjectFacts, res: RuleResult) -> None:
    ed = facts.building.get("code_edition")
    wind = facts.building.get("wind_speed_mph")
    if not ed:
        res.abstentions.append(Abstention(
            "STRUCT.WIND_STANDARD", "no code edition resolved, so the governing "
                                    "ASCE 7 version cannot be determined"))
        return
    meta = C.EDITIONS.get(ed.value) or {}
    cur = C.EDITIONS[C.CURRENT_EDITION]
    page = (wind.page if wind else ed.page) or 0
    sheet = facts.sheet_code(page)
    rect = _rect_for(facts, page, "WIND")
    w = f"{wind.value:.0f} mph " if wind else ""

    if meta.get("asce7") == cur["asce7"]:
        res.findings.append(Finding(
            fid="", rule_id="STRUCT.WIND_STANDARD", status="PASS",
            severity="VERIFIED", discipline="Structural", page=page, sheet=sheet,
            anchor="WIND", anchor_rect=rect,
            title="Wind design references the current standard",
            checked="Cited code edition against the ASCE 7 version it adopts.",
            result=f"{meta.get('name','the cited edition')} adopts ASCE {meta['asce7']}, "
                   f"which is current. {w}stated.",
            code=f"FBC-B 1609 · ASCE {meta['asce7']}"))
        return

    res.findings.append(Finding(
        fid="", rule_id="STRUCT.WIND_STANDARD", status="OPEN",
        severity="HIGH", discipline="Structural", page=page, sheet=sheet,
        anchor="WIND", anchor_rect=rect,
        title="Wind design follows a superseded standard",
        checked="Cited code edition against the ASCE 7 version it adopts, and against "
                "the version the current edition adopts.",
        result=(f"{meta.get('name','The cited edition')} adopts ASCE {meta.get('asce7','?')}. "
                f"The {cur['name']} adopts ASCE {cur['asce7']}, which replaces the wind "
                f"speed maps entirely. {w}is stated on the set."),
        code=f"FBC-B 1609 · ASCE {cur['asce7']} Fig. 26.5-1",
        action=("Re-read the design wind speed for this parcel from the ASCE "
                f"{cur['asce7']} maps at the project's risk category, and confirm the "
                "exposure category against the actual upwind terrain rather than "
                "carrying the previous assumption across.")))


# ── 7. vertical datum ────────────────────────────────────────────────────────

@rule("CIVIL.VERTICAL_DATUM")
def vertical_datum(facts: ProjectFacts, res: RuleResult) -> None:
    ff = facts.building.get("finished_floor")
    if not ff:
        res.abstentions.append(Abstention(
            "CIVIL.VERTICAL_DATUM", "no finished floor elevation was found"))
        return
    text = str(ff.value).upper()
    page = ff.page or 0
    sheet = facts.sheet_code(page)
    rect = _rect_for(facts, page, "FINISHED FLOOR")

    if "NGVD" not in text:
        res.findings.append(Finding(
            fid="", rule_id="CIVIL.VERTICAL_DATUM", status="PASS",
            severity="VERIFIED", discipline="Civil / Flood", page=page, sheet=sheet,
            anchor="FINISHED FLOOR", anchor_rect=rect,
            title="Finished floor elevation is not given in a superseded datum",
            checked="Finished floor elevation and the datum it is expressed in.",
            result=f"Reads {ff.value!r}.",
            code="FBC-B 1612 · ASCE 24"))
        return

    res.findings.append(Finding(
        fid="", rule_id="CIVIL.VERTICAL_DATUM", status="OPEN",
        severity="MEDIUM", discipline="Civil / Flood", page=page, sheet=sheet,
        anchor="FINISHED FLOOR", anchor_rect=rect,
        title=f"Finished floor elevation is given in {C.SUPERSEDED_VERTICAL_DATUM}",
        checked="The datum the finished floor elevation is expressed in, against the "
                "datum the jurisdiction and current FEMA mapping use.",
        result=(f"The sheet reads {ff.value!r}. Lee County and current FEMA flood mapping "
                f"both work in {C.CURRENT_VERTICAL_DATUM}. The two datums differ by "
                f"roughly {abs(C.NGVD_TO_NAVD_SW_FL_FT)} ft in this part of the county, and "
                f"an elevation quoted in NGVD 29 reads higher than the same physical point "
                f"in NAVD 88."),
        code="FBC-B 1612 · ASCE 24 · Lee County LDC",
        action=(f"Convert to {C.CURRENT_VERTICAL_DATUM} and state the datum explicitly, or "
                f"give both with the conversion noted. Confirm the converted elevation "
                f"still clears the required freeboard above BFE."),
        body=("A datum mismatch is the kind of error that survives plan review and turns "
              "up at the elevation certificate, after the slab is poured.")))
