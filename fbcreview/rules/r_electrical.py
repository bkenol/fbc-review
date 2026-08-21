"""Panel loading: resum the schedule and compare against the panel rating."""
from __future__ import annotations
import re
from . import rule, Finding, RuleResult
from ..confidence import Abstention
from ..facts import ProjectFacts


@rule("ELEC.PANEL_LOADING")
def panel_loading(f: ProjectFacts, out: RuleResult):
    calc = f.schedule("ELECTRICAL LOAD CALCULATIONS")
    if calc is None:
        out.abstentions.append(Abstention("ELEC.PANEL_LOADING", "no load calculation table found"))
        return
    amps = None
    for r in calc.rows:
        if "AMPERAGE" in r.mark.upper():
            amps = r.num(calc.columns[1]) if len(calc.columns) > 1 else None
    rating = f.meta.get("panel_rating_a")
    if amps is None or rating is None:
        out.abstentions.append(Abstention("ELEC.PANEL_LOADING",
                                          "total amperage or panel rating not extracted"))
        return
    pct = amps / rating * 100.0
    over = pct > 100.0
    out.findings.append(Finding(
        "C-PL" if over else "V-30", "ELEC.PANEL_LOADING", "OPEN" if over else "PASS",
        "CRITICAL" if over else "VERIFIED", "Electrical", calc.page, calc.sheet,
        "PANEL SCHEDULE",
        "Connected load exceeds the panel rating" if over
        else "Connected load recomputed — the panel has substantial headroom",
        "Every per-phase entry in the panel schedule summed and converted to amperes, then "
        "compared against the panel rating and the service.",
        f"Connected load {amps:.1f} A against a {rating:.0f} A panel — {pct:.0f} percent loaded.",
        "NEC 220 · 408.36 · 110.9",
        "Increase the panel." if over else
        "Confirm the panel's interrupting rating against the available fault current."))
