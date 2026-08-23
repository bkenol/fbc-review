"""Cross-sheet consistency — the class of error no single-sheet review finds."""
from __future__ import annotations
from . import rule, Finding, RuleResult
from ..confidence import Abstention
from ..codes import fbc2023 as C
from ..facts import ProjectFacts
from ..reconcile import basis_of, declared_value, source_label


def _area_pair(f: ProjectFacts):
    """The two areas to compare, what to call each, and what they rest on.

    The general sheets are the first source, and where both state an area
    nothing below changes: the labels, the wording and the basis are exactly
    what they were before the declaration existed.

    A declared area stands in for one the sheets do not state in readable text —
    which is the whole point of the declaration — but then the labels move with
    it. A finding must never attribute to G-0 a number G-0 does not carry, and
    must not claim a declared basis for something the sheet confirms either.
    """
    a, b = f.meta.get("area_g0_sf"), f.meta.get("area_g1_sf")
    la, lb = "G-0", "the G-1 occupancy tables"
    if a is None and declared_value(f, "building_area_sf") is not None:
        a = float(declared_value(f, "building_area_sf"))
        la = source_label(f, "building_area_sf", "largest floor")
    if b is None and declared_value(f, "total_area_sf") is not None:
        b = float(declared_value(f, "total_area_sf"))
        lb = source_label(f, "total_area_sf", "whole building")
    drawn_only = (la, lb) == ("G-0", "the G-1 occupancy tables")
    keys = [k for k, label in (("building_area_sf", la), ("total_area_sf", lb))
            if label not in ("G-0", "the G-1 occupancy tables")]
    return a, b, la, lb, drawn_only, (basis_of(f, *keys) if keys else "drawings")


@rule("XSHEET.BUILDING_AREA")
def area_agreement(f: ProjectFacts, out: RuleResult):
    a, b, la, lb, drawn_only, basis = _area_pair(f)
    if a is None or b is None:
        out.abstentions.append(Abstention("XSHEET.BUILDING_AREA",
                                          "building area not found on both general sheets"))
        return
    diff = abs(a - b)
    if diff > 1:
        out.findings.append(Finding(
            "M-04", "XSHEET.BUILDING_AREA", "OPEN", "MEDIUM", "Occupancy", 0, "G-0",
            f"{a:,.0f} SF",
            "Building area disagrees between sheets" if drawn_only else
            "The largest floor and the whole building do not agree",
            "The building area stated on each general sheet, compared, and the basis stated for each.",
            (f"G-0 states {a:,.0f} SF; the G-1 occupancy tables total {b:,.0f} SF. Difference "
             if drawn_only else
             f"The largest floor is {a:,.0f} SF ({la}); the whole building is {b:,.0f} SF "
             f"({lb}). Difference ") +
            f"{diff:,.0f} SF ({diff/max(a,b)*100:.1f} percent). Most likely gross versus net, but "
            f"Table 1004.5 factors are basis-specific, so the basis has to be explicit or the "
            f"occupant load cannot be independently checked.",
            "FBC-B 1004.5 · FBC-EBC 601.2",
            "Label both areas with their basis (gross or net).", basis=basis))
    else:
        out.findings.append(Finding(
            "V-AR", "XSHEET.BUILDING_AREA", "PASS", "VERIFIED", "Occupancy", 0, "G-0",
            f"{a:,.0f} SF",
            "Building area agrees across the general sheets" if drawn_only else
            "The largest floor and the whole building agree",
            "Building area on each general sheet, compared.",
            (f"G-0 and G-1 both give {a:,.0f} SF." if drawn_only else
             f"The largest floor and the whole building are both {a:,.0f} SF "
             f"({la}), which is what a single-storey building should show."),
            "FBC-B 1004.5", "None.", basis=basis))


@rule("XSHEET.RISK_CATEGORY")
def risk_category(f: ProjectFacts, out: RuleResult):
    from .r_occupancy import computed_load
    rc = f.meta.get("risk_category") or declared_value(f, "risk_category")
    # A load recomputed from Table 1004.5 is a legitimate second source when the
    # sheets do not state one; the sheet's own figure still wins when it exists,
    # and the recomputation is only reached for on a set someone declared an
    # occupancy for.
    ol = f.meta.get("occupant_load") or computed_load(f, declared_only=True)
    if not rc or not ol:
        out.abstentions.append(Abstention("XSHEET.RISK_CATEGORY",
                                          "risk category or occupant load not extracted"))
        return
    if rc.strip().upper() in ("III", "3") and ol <= C.RISK_III_ASSEMBLY_OL:
        out.findings.append(Finding(
            "M-03", "XSHEET.RISK_CATEGORY", "OPEN", "MEDIUM", "Structural / Occupancy", 0, "G-0",
            "RISK CATEGORY",
            "Risk Category III does not match the stated occupant load",
            "The assigned risk category against Table 1604.5 and the occupant load stated on the "
            "same sheet.",
            f"Risk Category {rc} is assigned. Table 1604.5 sets RC III at a primary assembly "
            f"occupancy with an occupant load greater than {C.RISK_III_ASSEMBLY_OL}. The stated "
            f"occupant load is {ol:g}. RC III is conservative — it raises design wind pressures — "
            f"but it is inconsistent with the occupant load on the same sheet.",
            "FBC-B Table 1604.5", "Confirm Risk Category II, or state the basis for III."))
    else:
        out.findings.append(Finding(
            "V-RC", "XSHEET.RISK_CATEGORY", "PASS", "VERIFIED", "Structural / Occupancy", 0, "G-0",
            "RISK CATEGORY", "Risk category is consistent with the occupant load",
            "Assigned risk category against Table 1604.5.",
            f"RC {rc} at an occupant load of {ol:g}.", "FBC-B Table 1604.5", "None."))
