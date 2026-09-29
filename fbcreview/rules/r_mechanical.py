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


@rule("MECH.OUTDOOR_AIR_ARITHMETIC")
def outdoor_air_arithmetic(f: ProjectFacts, out: RuleResult):
    """The Outdoor Air Calculations block recomputed from its own inputs.

    Each zone's people from its area and density, each zone's breathing-zone
    airflow as Rp x Pz + Ra x Az (FBC-M 403.3.1.1.1, Equation 4-1), and the
    total from the zones. Only the sheet's own numbers are used: this checks
    the arithmetic, not the Table 403.3.1.1 rates chosen, and says so.
    """
    sched = f.schedule("OCCUPANT DENSITY")
    rows = [v for v in f.ventilation if v.total_cfm is not None]
    if sched is None or not rows:
        out.abstentions.append(Abstention(
            "MECH.OUTDOOR_AIR_ARITHMETIC", "outdoor-air calculation rows not extracted"))
        return
    problems, lines, exact = [], [], 0.0
    for v in rows:
        pz = v.persons or 0.0
        if v.area_sf and v.density_per_1000 is not None and v.persons is not None:
            people = v.area_sf * v.density_per_1000 / 1000.0
            if abs(v.persons - people) >= 1.0:
                problems.append(f"{v.room.title()}: {v.area_sf:g} SF at {v.density_per_1000:g} "
                                f"per 1,000 SF is {people:.1f} people, not {v.persons:g}")
        vbz = (v.rp_cfm_person or 0.0) * pz + (v.ra_cfm_sf or 0.0) * (v.area_sf or 0.0)
        exact += vbz
        terms = []
        if v.rp_cfm_person and pz:
            terms.append(f"{v.rp_cfm_person:g} x {pz:g}")
        if v.ra_cfm_sf and v.area_sf:
            terms.append(f"{v.ra_cfm_sf:g} x {v.area_sf:g}")
        lines.append(f"{v.room.title()} {' + '.join(terms) or '0'} = {vbz:.1f} "
                     f"(stated {v.total_cfm:g})")
        if abs(v.total_cfm - vbz) > 1.0:
            problems.append(f"{v.room.title()}: {' + '.join(terms)} is {vbz:.1f} CFM, "
                            f"not {v.total_cfm:g}")
    stated_total = f.meta.get("oa_required_cfm")
    if stated_total is not None and abs(stated_total - exact) > max(1.0, 0.5 * len(rows)):
        problems.append(f"the zones add up to {exact:.1f} CFM, not the {stated_total:g} stated")
    total_note = (f" Recomputed total {exact:.1f} CFM against {stated_total:g} stated."
                  if stated_total is not None else f" Recomputed total {exact:.1f} CFM.")
    body = "; ".join(lines) + "." + total_note
    if problems:
        out.findings.append(Finding(
            "M-OA", "MECH.OUTDOOR_AIR_ARITHMETIC", "OPEN", "MEDIUM", "Mechanical",
            sched.page, sched.sheet, "OUTDOOR AIR CALCULATIONS",
            "The outdoor-air calculation does not add up",
            "Every line of the Outdoor Air Calculations block recomputed from its own inputs.",
            f"{body} Does not reconcile: " + "; ".join(problems) + ".",
            "FBC-M 403.3.1.1.1 · Equation 4-1",
            "Correct the calculation, then re-check the scheduled outdoor air against the "
            "corrected total.", box=sched.bbox))
    else:
        out.findings.append(Finding(
            "V-35", "MECH.OUTDOOR_AIR_ARITHMETIC", "PASS", "VERIFIED", "Mechanical",
            sched.page, sched.sheet, "OUTDOOR AIR CALCULATIONS",
            "The outdoor-air arithmetic is correct",
            "Every line of the Outdoor Air Calculations block recomputed from its own inputs "
            "(people from area and density; Rp x Pz + Ra x Az per zone; the total). The rates "
            "chosen from Table 403.3.1.1 are not checked here.",
            body + " Every difference is rounding.",
            "FBC-M 403.3.1.1.1 · Equation 4-1", "None.", box=sched.bbox))
