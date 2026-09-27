"""Occupant load, computed rather than read.

`Table 1004.5` turns area into occupants with no drawing parsing whatsoever, so
a declared occupancy group and a declared area are enough to check the number
the set states — even on a set whose occupant-load table is a picture.

This is the rule that catches the classic error: a column headed OCCUPANT FACTOR
that is actually a sequence of unit numbers. The factor is looked up in the code
corpus, never written into this file.
"""
from __future__ import annotations

import math
import re
from typing import List, Optional

from . import rule, Finding, RuleResult
from ..codes import fbc2023 as C
from ..confidence import Abstention
from ..facts import ProjectFacts
from ._context import basis, need, source_phrase, where

_STATED = re.compile(r"\bOCCUPANT\s+LOAD\s*[:=]\s*([\d,]+)", re.I)
_AREA_COL = re.compile(r"AREA", re.I)
_FACTOR_COL = re.compile(r"FACTOR|LOAD|OCCUP", re.I)


def space_areas(f: ProjectFacts) -> List[float]:
    """Per-space floor areas, when the set breaks them out.

    1004.5 is applied space by space and each space rounds up, so eight tenant
    units at 150 gross do not produce the same answer as their total does. When
    the breakdown is available it is the right basis; when it is not, the whole
    area is an honest approximation and the finding says which was used.
    """
    for sched in f.schedules:
        if "OCCUPANT" not in sched.name.upper():
            continue
        col = next((c for c in sched.columns
                    if _AREA_COL.search(c) and not _FACTOR_COL.search(c)), None)
        if not col:
            continue
        areas = []
        for row in sched.rows:
            if row.mark.strip().upper().startswith("TOTAL"):
                continue
            v = row.num(col)
            if v and v >= 50:                 # a square-foot area, not a factor
                areas.append(float(v))
        if len(areas) >= 2:
            return areas
    return []


def stated_load(f: ProjectFacts) -> Optional[float]:
    if f.meta.get("occupant_load"):
        return float(f.meta["occupant_load"])
    for sched in f.schedules:
        if "OCCUPANT" not in sched.name.upper():
            continue
        col = next((c for c in sched.columns
                    if "LOAD" in c.upper() or "OCCUPANTS" in c.upper()), None)
        for row in sched.rows:
            if not row.mark.strip().upper().startswith("TOTAL"):
                continue
            if col:
                v = row.num(col)
                if v:
                    return float(v)
            nums = re.findall(r"\d[\d,]*", row.mark.replace(",", ""))
            if nums:
                return float(nums[-1])
    for text in f.text_by_page.values():
        m = _STATED.search(text or "")
        if m:
            return float(m.group(1).replace(",", ""))
    return None


def computed_load(f: ProjectFacts, declared_only: bool = False) -> Optional[float]:
    """Occupant load from Table 1004.5, or `None` if it cannot be computed.

    Shared with the exit-count and risk-category rules, which would otherwise
    each need their own copy and drift apart. Those two predate the declaration,
    so they pass `declared_only=True`: a recomputed load is a new input for them
    and must arrive only because someone answered, never because a text sweep
    happened to find an occupancy on a sheet.
    """
    from ..reconcile import declared_value, value_of

    read = declared_value if declared_only else value_of
    group = read(f, "occupancy_group")
    if not group:
        return None
    factor = C.occupant_load_factor(str(group))
    if not factor:
        return None
    areas = space_areas(f)
    if areas:
        return float(sum(math.ceil(a / factor) for a in areas))
    area = read(f, "building_area_sf") or read(f, "total_area_sf")
    if not area:
        return None
    return float(math.ceil(float(area) / factor))


@rule("EGRESS.OCCUPANT_LOAD_COMPUTED")
def occupant_load_computed(f: ProjectFacts, out: RuleResult):
    v = need(f, out, "EGRESS.OCCUPANT_LOAD_COMPUTED", "occupancy_group")
    if v is None:
        return
    group = str(v["occupancy_group"])
    factor = C.occupant_load_factor(group)
    if factor is None:
        out.abstentions.append(Abstention(
            "EGRESS.OCCUPANT_LOAD_COMPUTED",
            "Table 1004.5 has no single gross factor for this group",
            detail=f"Group {group} is measured use by use — 7 net concentrated, 15 net at "
                   f"tables, 5 net standing, 20 net classroom — so a whole-building factor "
                   f"would be invented, not looked up."))
        return

    areas = space_areas(f)
    if not areas:
        need_area = need(f, out, "EGRESS.OCCUPANT_LOAD_COMPUTED", "building_area_sf")
        if need_area is None:
            return
    computed = computed_load(f)
    if computed is None:
        out.abstentions.append(Abstention(
            "EGRESS.OCCUPANT_LOAD_COMPUTED", "no area to apply the factor to"))
        return

    stated = stated_load(f)
    # The finding belongs on the sheet that carries the occupant-load table,
    # when there is one — that is where the marker has something to box.
    page, sheet = where(f, "building_area_sf", "occupancy_group")
    for sched in f.schedules:
        if "OCCUPANT" in sched.name.upper():
            page, sheet = sched.page, sched.sheet
            break
    b = basis(f, "occupancy_group", "building_area_sf")

    if areas:
        how = (f"{len(areas)} spaces totalling {sum(areas):,.0f} SF, each rounded up "
               f"separately as 1004.5 requires")
    else:
        area = sum(areas) if areas else None
        from ..reconcile import value_of
        area = float(value_of(f, "building_area_sf") or value_of(f, "total_area_sf") or 0)
        how = f"{area:,.0f} SF ({source_phrase(f, 'building_area_sf')}) taken as one space"

    if stated is None:
        out.findings.append(Finding(
            "V-OL", "EGRESS.OCCUPANT_LOAD_COMPUTED", "PASS", "VERIFIED", "Occupancy",
            page, sheet, "OCCUPANT LOAD",
            f"Occupant load computes to {computed:g} from Table 1004.5",
            "Occupant load recomputed from area and the Table 1004.5 factor for the "
            "occupancy group, independent of any number printed on the sheets.",
            f"Group {group} is {factor:g} gross in Table 1004.5. {how} gives "
            f"{computed:g} occupants. The set states no occupant load that could be read, "
            f"so there is nothing to compare it against — this is the load the rest of "
            f"Chapter 10 is checked at.",
            "FBC-B 1004.5 · Table 1004.5", "State the occupant load on the code data sheet.",
            basis=b))
        return

    # A conservative overstatement is not a safety problem, but a factor column
    # that is not a code factor will not survive a plans examiner.
    off = abs(stated - computed)
    tolerable = off <= max(1.0, 0.02 * computed)
    if tolerable:
        out.findings.append(Finding(
            "V-OL", "EGRESS.OCCUPANT_LOAD_COMPUTED", "PASS", "VERIFIED", "Occupancy",
            page, sheet, "OCCUPANT LOAD", "Stated occupant load agrees with Table 1004.5",
            "Occupant load recomputed from area and the Table 1004.5 factor, against the "
            "number the set states.",
            f"Group {group} at {factor:g} gross. {how} gives {computed:g}; the set states "
            f"{stated:g}.", "FBC-B 1004.5 · Table 1004.5", "None.", basis=b))
        return

    conservative = stated > computed
    out.findings.append(Finding(
        "H-OL", "EGRESS.OCCUPANT_LOAD_COMPUTED", "OPEN",
        "HIGH" if not conservative else "MEDIUM", "Occupancy", page, sheet, "OCCUPANT LOAD",
        f"Stated occupant load of {stated:g} does not follow from Table 1004.5",
        "Occupant load recomputed from area and the Table 1004.5 factor for the occupancy "
        "group, against the number the set states.",
        f"Group {group} is {factor:g} gross in Table 1004.5. {how} gives {computed:g} "
        f"occupants; the set states {stated:g} — {off:g} "
        f"{'more' if conservative else 'fewer'}. "
        + ("The stated figure is conservative, so nothing downstream is unsafe, but it does "
           "not follow from the factor and a plans examiner will ask where it came from. "
           "This is what an OCCUPANT FACTOR column filled with unit numbers looks like."
           if conservative else
           "The stated figure is LOWER than the code factor produces, so every egress "
           "quantity derived from it — exit width, exit count, plumbing fixtures — is "
           "understated."),
        "FBC-B 1004.5 · Table 1004.5",
        f"Recompute at {factor:g} gross and correct the occupant-load table.", basis=b))


# ══ the factor applied against the function the set names ══════════════════
#: Words that name one Table 1004.5 function — a key of `C.OCCUPANT_LOAD_USES`,
#: where the factor itself lives. Order matters: the most specific first, so
#: "ASSEMBLY - EXERCISE ROOMS" is an exercise room, not generic assembly.
_FUNCTION_WORDS = (
    (re.compile(r"\bEXERCISE\s+ROOMS?\b"), "exercise room"),
    (re.compile(r"\bCLASSROOMS?\b"), "classroom"),
    (re.compile(r"\bSTANDING\s+SPACE\b"), "assembly, standing space"),
    (re.compile(r"\bUNCONCENTRATED\b|\bTABLES\s+AND\s+CHAIRS\b"),
     "assembly, unconcentrated (tables and chairs)"),
    (re.compile(r"\bCONCENTRATED\b|\bCHAIRS\s+ONLY\b"), "assembly, concentrated (chairs only)"),
    (re.compile(r"\bWAREHOUSES?\b"), "warehouse"),
    (re.compile(r"\bSTORAGE\b|\bSTOCK\b|\bSHIPPING\b"), "storage, stock, shipping"),
    (re.compile(r"\bMERCANTILE\b|\bRETAIL\b"), "mercantile"),
    (re.compile(r"\bBUSINESS\b|\bOFFICES?\b"), "business"),
)


_WORD = {2: "two", 3: "three", 4: "four"}


def _function_in(text: str) -> Optional[str]:
    up = (text or "").upper()
    return next((key for pattern, key in _FUNCTION_WORDS if pattern.search(up)), None)


def _same_space(a, b) -> bool:
    return " ".join(a.space.upper().split()) == " ".join(b.space.upper().split())


def _named_function(f: ProjectFacts, row):
    """(function, the words that name it, where) for one occupant-load row, or None.

    The row's own USE column when it names a function (BUSINESS, STORAGE).
    When it names only a group — ASSEMBLY, which Table 1004.5 splits into
    concentrated, unconcentrated, standing and exercise rooms — the set may
    still say which, in a heading over the same space in another table:
    "ASSEMBLY - EXERCISE ROOMS WITHOUT EQUIPMENT". A heading counts only when
    it names the row's own use word, so a table titled for its assembly space
    does not re-classify the reception desk beside it.
    """
    direct = _function_in(row.use)
    if direct:
        return direct, row.use, f"the USE column on {row.sheet}"
    use_word = (row.use.split() or [""])[0].upper()
    if not use_word:
        return None
    for other in [row] + [x for x in f.occupancy_rows if x is not row and _same_space(x, row)]:
        for line in other.context:
            if use_word in line.upper():
                key = _function_in(line)
                if key:
                    table = f", over the {other.code} occupant load table" if other.code else ""
                    return key, line, f"{other.sheet}{table}"
    return None


@rule("OCC.CLASSIFICATION_CONSISTENCY")
def classification_consistency(f: ProjectFacts, out: RuleResult):
    """Each tabulated occupant load factor against the Table 1004.5 function the set names.

    The factor is looked up in the corpus by the function the set's own words
    give — never guessed from a room name. A space whose function the set does
    not name is left alone.
    """
    table = [r for r in f.occupancy_rows if r.code != "FFPC"]
    if not table:
        out.abstentions.append(Abstention(
            "OCC.CLASSIFICATION_CONSISTENCY", "occupant-load table not extracted"))
        return
    # A zero in a column the set presents as Table 1004.5 is not a Table 1004.5
    # factor: the table has none. Only where the table says it is 1004.5.
    for i, r in enumerate(x for x in table if x.code == "FBC" and x.factor == 0):
        out.findings.append(Finding(
            "L-01" if i == 0 else f"L-01{chr(ord('b') + i - 1)}",
            "OCC.CLASSIFICATION_CONSISTENCY", "OPEN", "LOW", "Occupancy", r.page, r.sheet,
            r.space,
            f"'{r.use or 'no use'} / 0' is not a Table 1004.5 function",
            "The occupant load factor column of each occupant-load table against the function "
            "list in Table 1004.5.",
            f"{r.space.title()} is shown as '{r.use}' with a factor of 0 ({r.sheet}). Table "
            f"1004.5 has no function with a zero factor. Assigning zero to circulation and toilet "
            f"rooms is accepted practice under 1004.2.1 — their occupants are counted in the "
            f"spaces they serve — but a zero in a column presented as Table 1004.5 invites a "
            f"comment.",
            "FBC-B Table 1004.5 · 1004.2.1",
            "Footnote the row to 1004.2.1 rather than presenting it as a Table 1004.5 factor."))
    zeros = any(r.code == "FBC" and r.factor == 0 for r in table)
    rows = [r for r in table if r.factor]
    if not rows:
        if not zeros:
            out.abstentions.append(Abstention(
                "OCC.CLASSIFICATION_CONSISTENCY", "occupant load factors not extracted"))
        return
    matched, wrong = [], []
    for r in rows:
        named = _named_function(f, r)
        if named is None:
            continue
        key, words, where_ = named
        want, want_basis = C.OCCUPANT_LOAD_USES[key]
        same = abs(r.factor - want) < 0.01 and (not r.basis or r.basis == want_basis)
        (matched if same else wrong).append((r, key, words, where_, want, want_basis))
    if not matched and not wrong and not zeros:
        out.abstentions.append(Abstention(
            "OCC.CLASSIFICATION_CONSISTENCY",
            "the set does not name a Table 1004.5 function for its tabulated spaces",
            detail=", ".join(sorted({r.space.title() for r in rows}))))
        return
    for i, (r, key, words, where_, want, want_basis) in enumerate(wrong):
        vent = next((v for v in f.ventilation if " ".join(v.room.upper().split())
                     == " ".join(r.space.upper().split()) and v.density_per_1000), None)
        densities = [f"{r.sheet} {r.code + ' ' if r.code else ''}occupant load table: "
                     f"{r.area_sf:g} SF at {r.factor:g} {r.basis} per occupant"
                     f"{f' = {r.load:g} occupants' if r.load else ''}" if r.area_sf else
                     f"{r.sheet} occupant load table: {r.factor:g} {r.basis} per occupant"]
        at_want = f" — about {r.area_sf / want:.0f} occupants on the same {r.area_sf:g} SF" \
            if r.area_sf else ""
        densities.append(f"Table 1004.5 '{key}': {want:g} {want_basis} per occupant{at_want}, "
                         f"applied nowhere")
        if vent is not None:
            sched = f.schedule("OCCUPANT DENSITY")
            densities.append(f"{sched.sheet if sched else 'the mechanical sheet'} ventilation: "
                             f"{vent.density_per_1000:g} people per 1,000 SF"
                             f"{f' = {vent.persons:g} people' if vent.persons else ''}")
        conservative = r.factor < want
        out.findings.append(Finding(
            "H-01" if i == 0 else f"H-01{chr(ord('b') + i - 1)}",
            "OCC.CLASSIFICATION_CONSISTENCY", "OPEN", "HIGH", "Occupancy", r.page, r.sheet,
            r.space,
            f"{r.space.title()} carries {_WORD.get(len(densities), len(densities))} different "
            f"occupant densities"
            if len(densities) >= 3 else
            f"{r.space.title()}'s occupant load factor is not the one for the function the "
            f"set names",
            "The occupant load factor applied to each tabulated space, against the Table 1004.5 "
            "function the set's own words give it, and against any other density the set "
            "assigns the same space.",
            f"The set describes {r.space.title()} as \"{words.strip()}\" ({where_}), which is "
            f"Table 1004.5's '{key}' at {want:g} {want_basis}. " + "; ".join(densities) + ". "
            + ("The factor applied is conservative for egress, so this is not unsafe — but it is "
               "not the function's factor, and no sheet says which governs. " if conservative else
               "The factor applied is less conservative than the function's, which understates "
               "the occupant load. ")
            + "The occupant load drives the exit count, egress width, plumbing fixtures and the "
              "risk category.",
            "FBC-B 1004.5 · Table 1004.5",
            "State one governing occupant load with its Table 1004.5 basis, state any "
            "ventilation density separately with its own basis, and cross-reference the two."))
    if matched:
        r0 = matched[0][0]
        out.findings.append(Finding(
            "V-OLF", "OCC.CLASSIFICATION_CONSISTENCY", "PASS", "VERIFIED", "Occupancy",
            r0.page, r0.sheet, r0.space,
            "Occupant load factors match the functions the set names"
            + (" — except where noted" if wrong else ""),
            "The occupant load factor applied to each tabulated space, against the Table 1004.5 "
            "function the set's own words give it.",
            "; ".join(f"{r.space.title()} ({words.strip().title()}): {r.factor:g} {r.basis}, "
                      f"Table 1004.5 '{key}' is {want:g} {want_basis}"
                      for r, key, words, _w, want, want_basis in matched) + ".",
            "FBC-B Table 1004.5", "None."))
