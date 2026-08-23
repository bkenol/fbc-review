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
