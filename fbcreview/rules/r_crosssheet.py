"""Cross-sheet consistency — the class of error no single-sheet review finds."""
from __future__ import annotations
from . import rule, Finding, RuleResult
from ..confidence import Abstention
from ..codes import fbc2023 as C
from ..facts import ProjectFacts


@rule("XSHEET.BUILDING_AREA")
def area_agreement(f: ProjectFacts, out: RuleResult):
    a, b = f.meta.get("area_g0_sf"), f.meta.get("area_g1_sf")
    if a is None or b is None:
        out.abstentions.append(Abstention("XSHEET.BUILDING_AREA",
                                          "building area not found on both general sheets"))
        return
    diff = abs(a - b)
    if diff > 1:
        out.findings.append(Finding(
            "M-04", "XSHEET.BUILDING_AREA", "OPEN", "MEDIUM", "Occupancy", 0, "G-0",
            f"{a:,.0f} SF",
            "Building area disagrees between sheets",
            "The building area stated on each general sheet, compared, and the basis stated for each.",
            f"G-0 states {a:,.0f} SF; the G-1 occupancy tables total {b:,.0f} SF. Difference "
            f"{diff:,.0f} SF ({diff/max(a,b)*100:.1f} percent). Most likely gross versus net, but "
            f"Table 1004.5 factors are basis-specific, so the basis has to be explicit or the "
            f"occupant load cannot be independently checked.",
            "FBC-B 1004.5 · FBC-EBC 601.2",
            "Label both areas with their basis (gross or net)."))
    else:
        out.findings.append(Finding(
            "V-AR", "XSHEET.BUILDING_AREA", "PASS", "VERIFIED", "Occupancy", 0, "G-0",
            f"{a:,.0f} SF", "Building area agrees across the general sheets",
            "Building area on each general sheet, compared.",
            f"G-0 and G-1 both give {a:,.0f} SF.", "FBC-B 1004.5", "None."))


@rule("XSHEET.RISK_CATEGORY")
def risk_category(f: ProjectFacts, out: RuleResult):
    rc = f.meta.get("risk_category")
    ol = f.meta.get("occupant_load")
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
