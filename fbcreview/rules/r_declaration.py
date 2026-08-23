"""Declared versus drawn — one rule per comparable field.

These are the only rules whose subject is the *review itself* rather than the
building. Each one asks a single question: does what the applicant told us match
what the set says? A finding here names **both values and both sources**, because
"occupancy mismatch" tells a reviewer nothing they can act on.

Severity is by consequence, not by field:

* **HIGH** — the disagreement changes which code threshold applies. Occupancy,
  construction type, sprinkler status, risk category, code edition and whether
  the building is mixed all move the row you look up.
* **MEDIUM** — it changes a computed value but not which rule governs. Area,
  height, storeys, wind speed, exposure.
* **LOW** — the two numbers are close enough that the disagreement is about
  units or rounding rather than about the building.

`status = "CONFLICT"` is a status, not a severity: it records that two
independent sources disagree, while severity still says how much that matters.
"""
from __future__ import annotations

from typing import Optional

from . import rule, Finding, RuleResult
from ..confidence import Abstention
from ..declaration_schema import BY_KEY, tolerance_for
from ..facts import ProjectFacts
from ..reconcile import (CONFLICT, CORROBORATED, DECLARED_ONLY, DRAWN_ONLY,
                         Reconciled, normalise)
from ._context import show

#: Fields whose disagreement moves which row of a code table governs.
_THRESHOLD_FIELDS = {"occupancy_group", "construction_type", "sprinkler_system",
                     "risk_category", "code_edition", "mixed_occupancy",
                     "separation_method"}


def _record(f: ProjectFacts, key: str) -> Optional[Reconciled]:
    rf = f.meta.get("reconciled")
    return rf.fields.get(key) if rf else None


def _severity(key: str, rec: Reconciled) -> str:
    """HIGH when the threshold moves, MEDIUM when only the number does, LOW when
    the two answers are close enough that the argument is about units."""
    if key in _THRESHOLD_FIELDS:
        return "HIGH"
    tol = tolerance_for(key)
    a, b = normalise(key, rec.declared_value), normalise(key, rec.drawn_value)
    if tol is not None and isinstance(a, (int, float)) and isinstance(b, (int, float)):
        # Just outside tolerance is a rounding argument; far outside is a
        # different building.
        if abs(float(a) - float(b)) <= 2 * tol.limit(max(abs(a), abs(b))):
            return "LOW"
    return "MEDIUM"


def _emit(f: ProjectFacts, out: RuleResult, rule_id: str, key: str, *,
          discipline: str, code: str, fid_conflict: str, fid_ok: str,
          subject: str, consequence: str, action: str,
          conflict_body: Optional[str] = None) -> None:
    """The shared shape of every rule in this module.

    Fires on CONFLICT, records the reward on CORROBORATED, and stands down —
    saying which half was missing — in every other state. Standing down is
    correct here: a cross-check between two sources cannot be performed when
    only one source spoke, and the value itself is still reported, with its
    basis, on the declaration page.
    """
    rec = _record(f, key)
    if rec is None:
        out.abstentions.append(Abstention(rule_id, "field not reconciled"))
        return

    label = BY_KEY[key].pro_label if key in BY_KEY else key
    declared, drawn = rec.declared_value, rec.drawn_value

    if rec.state == CONFLICT:
        drawn_source = rec.drawn.source if rec.drawn else "the drawings"
        body = conflict_body or (
            f"You declared {subject} {show(declared)}. {drawn_source} states "
            f"{show(drawn)}. {consequence}")
        out.findings.append(Finding(
            fid_conflict, rule_id, "CONFLICT", _severity(key, rec), discipline,
            rec.drawn.page if rec.drawn and rec.drawn.page is not None else 0,
            drawn_source if rec.drawn and rec.drawn.page is not None
            else f.sheet_code(0),
            label.upper(),
            f"{label}: you declared {show(declared)}, the set says {show(drawn)}",
            f"The {label.lower()} in the project declaration against the value the drawings "
            f"state, normalised so that formatting differences are not reported as "
            f"disagreements.",
            body, code, action, basis="both"))
        return

    if rec.state == CORROBORATED:
        out.findings.append(Finding(
            fid_ok, rule_id, "PASS", "VERIFIED", discipline,
            rec.drawn.page if rec.drawn and rec.drawn.page is not None else 0,
            rec.drawn.source if rec.drawn else f.sheet_code(0),
            label.upper(),
            f"{label} agrees between your declaration and the drawings",
            f"The {label.lower()} you declared against the value the drawings state.",
            f"Declared {show(declared)}; {rec.drawn.source if rec.drawn else 'the set'} "
            f"states {show(drawn)}. Two independent sources agree, so everything downstream "
            f"of this value is checked at high confidence rather than on the drawing alone.",
            code, "None.", basis="both"))
        return

    reasons = {
        DECLARED_ONLY: "you answered this, but the drawings do not state it in text this "
                       "build can read — so there is nothing to cross-check it against",
        DRAWN_ONLY: "the drawings state this but the declaration left it blank, so there "
                    "is no second source to compare",
    }
    out.abstentions.append(Abstention(
        rule_id, reasons.get(rec.state, "neither the drawings nor the declaration state this"),
        detail=label))


# ── occupancy ─────────────────────────────────────────────────────────────
@rule("DECL.OCCUPANCY")
def occupancy(f: ProjectFacts, out: RuleResult):
    _emit(f, out, "DECL.OCCUPANCY", "occupancy_group",
          discipline="Occupancy", code="FBC-B 302.1 · Table 1004.5",
          fid_conflict="X-OCC", fid_ok="V-OCC", subject="Group",
          consequence="Occupancy classification selects the row in Table 1004.5, "
                      "Table 504.3, Table 504.4, Table 506.2 and most of Chapter 10, so "
                      "the two readings do not merely differ in wording — they check the "
                      "building against different limits.",
          action="Confirm the classification and make the code data sheet say it.")


@rule("DECL.MIXED_OCCUPANCY")
def mixed_occupancy(f: ProjectFacts, out: RuleResult):
    rec = _record(f, "mixed_occupancy")
    body = None
    if rec is not None and rec.state == CONFLICT and rec.drawn is not None:
        # The named case: a set that says it is not mixed while citing the table
        # that only applies to mixed buildings.
        body = (f"You declared {'a mixed occupancy' if rec.declared_value else 'not a mixed occupancy'}. "
                f"{rec.drawn.note or rec.drawn.source}. `FBC-B Table 508.4` is the separated "
                f"occupancies method and exists only for a building containing more than one "
                f"occupancy group; `508.3` is its non-separated counterpart. A building that "
                f"is genuinely single-occupancy needs neither. Either the classification is "
                f"wrong or the separation row is, and which one it is changes the required "
                f"fire-resistance ratings between tenant spaces.")
    _emit(f, out, "DECL.MIXED_OCCUPANCY", "mixed_occupancy",
          discipline="Occupancy", code="FBC-B 302.1 · 508.3 · 508.4",
          fid_conflict="X-MIX", fid_ok="V-MIX", subject="mixed occupancy",
          consequence="Whether the building is mixed decides whether 508 applies at all.",
          action="Reconcile the occupancy classification with the separation row on the "
                 "code data sheet.",
          conflict_body=body)


# ── construction and size ─────────────────────────────────────────────────
@rule("DECL.CONSTRUCTION_TYPE")
def construction_type(f: ProjectFacts, out: RuleResult):
    _emit(f, out, "DECL.CONSTRUCTION_TYPE", "construction_type",
          discipline="Construction", code="FBC-B Table 601 · Table 504.3 · Table 506.2",
          fid_conflict="X-CON", fid_ok="V-CON", subject="Type",
          consequence="Construction type selects the allowable height, the allowable "
                      "storey count, the allowable area and every required fire-resistance "
                      "rating. Two types are two different buildings as far as Chapter 5 "
                      "is concerned.",
          action="Confirm the construction type against Table 601 and correct the sheet.")


@rule("DECL.BUILDING_AREA")
def building_area(f: ProjectFacts, out: RuleResult):
    # Both area questions are one check: they disagree with the drawings for the
    # same reason and produce the same consequence.
    for key, fid_c, fid_v in (("building_area_sf", "X-ARE", "V-ARE"),
                              ("total_area_sf", "X-ART", "V-ART")):
        rec = _record(f, key)
        if rec is None or rec.state in (CONFLICT, CORROBORATED):
            _emit(f, out, "DECL.BUILDING_AREA", key,
                  discipline="Height and area", code="FBC-B Table 506.2 · Table 1004.5",
                  fid_conflict=fid_c, fid_ok=fid_v, subject="an area of",
                  consequence="Area drives the Table 506.2 comparison and the occupant "
                              "load, and Table 1004.5 factors are basis-specific — so the "
                              "difference has to be resolved as gross against net before "
                              "either number can be relied on.",
                  action="State the area and its basis (gross or net) on the code data sheet.")
            return
    _emit(f, out, "DECL.BUILDING_AREA", "building_area_sf",
          discipline="Height and area", code="FBC-B Table 506.2 · Table 1004.5",
          fid_conflict="X-ARE", fid_ok="V-ARE", subject="an area of",
          consequence="Area drives Table 506.2 and the occupant load.",
          action="State the area and its basis on the code data sheet.")


@rule("DECL.HEIGHT")
def height(f: ProjectFacts, out: RuleResult):
    _emit(f, out, "DECL.HEIGHT", "height_ft",
          discipline="Height and area", code="FBC-B 202 · Table 504.3",
          fid_conflict="X-HGT", fid_ok="V-HGT", subject="a height of",
          consequence="Height is compared against Table 504.3, and 202 measures it to the "
                      "average roof from grade plane — a parapet or a mean-roof height "
                      "quoted instead is the usual source of this difference.",
          action="Confirm the height as 202 measures it and state it on the sheet.")


@rule("DECL.STORIES")
def stories(f: ProjectFacts, out: RuleResult):
    _emit(f, out, "DECL.STORIES", "stories",
          discipline="Height and area", code="FBC-B 202 · Table 504.4",
          fid_conflict="X-STY", fid_ok="V-STY", subject="",
          consequence="Storey count is compared against Table 504.4 and there is no "
                      "tolerance on it — a mezzanine counted one way and not the other "
                      "changes which row governs.",
          action="Confirm the storey count as 202 counts it, mezzanines included.")


# ── fire protection ───────────────────────────────────────────────────────
@rule("DECL.SPRINKLER")
def sprinkler(f: ProjectFacts, out: RuleResult):
    _emit(f, out, "DECL.SPRINKLER", "sprinkler_system",
          discipline="Fire protection", code="FBC-B 903.3.1.1 · 903.3.1.2 · Table 504.3",
          fid_conflict="X-SPR", fid_ok="V-SPR", subject="a sprinkler system of",
          consequence="Only a 903.3.1.1 or 903.3.1.2 system buys the height, area and "
                      "Chapter 10 increases. A 13D system does not, and no system at all "
                      "changes almost every limit in this review.",
          action="Confirm which standard the system is designed to and state it.")


# ── structural ────────────────────────────────────────────────────────────
@rule("DECL.WIND_SPEED")
def wind_speed(f: ProjectFacts, out: RuleResult):
    _emit(f, out, "DECL.WIND_SPEED", "wind_speed_mph",
          discipline="Structural", code="FBC-B 1609 · ASCE 7 Fig. 26.5-1",
          fid_conflict="X-WND", fid_ok="V-WND", subject="a wind speed of",
          consequence="Velocity pressure goes with the square of the speed, so the "
                      "difference is not linear in the design loads.",
          action="Re-read Vult for this parcel from the map the governing edition adopts.")


@rule("DECL.EXPOSURE")
def exposure(f: ProjectFacts, out: RuleResult):
    _emit(f, out, "DECL.EXPOSURE", "exposure_category",
          discipline="Structural", code="ASCE 7 26.7.3 · FBC-B 1609.4",
          fid_conflict="X-EXP", fid_ok="V-EXP", subject="Exposure",
          consequence="Exposure category scales velocity pressure materially, and B "
                      "requires closely spaced upwind obstructions for 1,500 ft or 20 "
                      "times the building height in every direction.",
          action="Justify the exposure category from the upwind terrain, or use C.")


@rule("DECL.RISK_CATEGORY")
def risk_category(f: ProjectFacts, out: RuleResult):
    _emit(f, out, "DECL.RISK_CATEGORY", "risk_category",
          discipline="Structural / Occupancy", code="FBC-B Table 1604.5",
          fid_conflict="X-RSK", fid_ok="V-RSK", subject="Risk Category",
          consequence="Risk category sets the importance factor and therefore the design "
                      "wind pressures, and it selects which row of Table 1604.5 the "
                      "building is judged under.",
          action="Confirm the risk category against Table 1604.5 and the occupant load.")


# ── context ───────────────────────────────────────────────────────────────
@rule("DECL.CODE_EDITION")
def code_edition(f: ProjectFacts, out: RuleResult):
    _emit(f, out, "DECL.CODE_EDITION", "code_edition",
          discipline="Administration", code="F.A.C. 61G20 · FBC adoption schedule",
          fid_conflict="X-EDN", fid_ok="V-EDN", subject="edition",
          consequence="The edition decides which referenced standards apply — ASCE 7-16 "
                      "against ASCE 7-22 is a different wind map — so a review run against "
                      "the wrong one checks the wrong thresholds throughout.",
          action="Make every code reference in the set name one edition, and say which.")
