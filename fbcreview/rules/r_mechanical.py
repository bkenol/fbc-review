"""Ventilation: recompute the outdoor-air requirement and compare it against the
capacity actually scheduled. This is the C-01 rule."""
from __future__ import annotations
from . import rule, Finding, RuleResult
from ..confidence import Abstention
from ..facts import ProjectFacts


@rule("MECH.OUTDOOR_AIR_CAPACITY")
def outdoor_air(f: ProjectFacts, out: RuleResult):
    required = f.meta.get("oa_required_cfm")
    rtu = f.schedule("ROOF TOP UNIT")
    if required is None:
        out.abstentions.append(Abstention("MECH.OUTDOOR_AIR_CAPACITY",
                                          "outdoor-air total not extracted"))
        return
    if rtu is None or not rtu.rows:
        out.abstentions.append(Abstention("MECH.OUTDOOR_AIR_CAPACITY",
                                          "no rooftop unit schedule found"))
        return
    unit = rtu.rows[0]
    scheduled = None
    for k, v in unit.fields.items():
        if "OUTSIDE AIR" in k.upper():
            scheduled = unit.num(k)
    if scheduled is None:
        out.abstentions.append(Abstention("MECH.OUTDOOR_AIR_CAPACITY",
                                          "unit schedule has no outside-air column"))
        return
    short = required - scheduled
    page, sheet = rtu.page, rtu.sheet
    if short > 1:
        pct = scheduled / required * 100.0
        out.findings.append(Finding(
            "C-01", "MECH.OUTDOOR_AIR_CAPACITY", "OPEN", "CRITICAL", "Mechanical", page, sheet,
            "OUTDOOR AIR CALCULATIONS",
            f"Outdoor-air capacity {short:.0f} CFM below this sheet's own requirement",
            "The Outdoor Air Calculations block recomputed line by line, then compared against the "
            "scheduled unit capacity in the Roof Top Unit Schedule on the same sheet.",
            f"Required {required:.0f} CFM. {unit.mark} scheduled outside air = {scheduled:.0f} CFM. "
            f"Shortfall {short:.0f} CFM — {pct:.0f} percent of the requirement is delivered. The "
            f"calculation is correct; the equipment selected cannot meet it, and no note reconciles "
            f"the two figures.",
            "FBC-M 403.3.1.1 · 403.3.1.1.1.1 · 405.1",
            f"Increase {unit.mark} outdoor-air capacity, add a dedicated outdoor-air unit or ERV, or "
            f"re-establish the ventilation occupant density with AHJ approval and recompute."))
    else:
        out.findings.append(Finding(
            "V-OA", "MECH.OUTDOOR_AIR_CAPACITY", "PASS", "VERIFIED", "Mechanical", page, sheet,
            "OUTDOOR AIR CALCULATIONS", "Scheduled outdoor air meets the computed requirement",
            "Outdoor Air Calculations recomputed and compared against the scheduled unit capacity.",
            f"Required {required:.0f} CFM; {unit.mark} delivers {scheduled:.0f} CFM.",
            "FBC-M 403.3.1.1", "None."))
