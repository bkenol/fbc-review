#!/usr/bin/env python3
"""How much of the hand-built Sculpted Hot Pilates review does the engine reproduce?

Usage:
    python scripts/scorecard.py "samples/SCULPTED HOT PILATES - PERMIT SET 8.18.2026.pdf"
    python scripts/scorecard.py <set.pdf> --readings readings.json   # replay recorded AI readings
    python scripts/scorecard.py <set.pdf> --json scorecard.json

The ground truth is `fbcreview/render/v5_register_reference.py`: the register a
person built by hand against the real set — 14 open findings and 36 verified
items. Each register entry is mapped to the rule that is meant to reproduce it,
and graded:

    exact    the rule fired with the register's status and severity
    partial  the rule fired, but with a different status or severity
    missed   the rule abstained, or no rule exists for the check yet

Supporting markers (C-01b, C-01c, H-01r) annotate another finding on a second
sheet; they are not separate checks and are left out of the denominator, which
is how the reference note counts "1 CRITICAL, 3 HIGH, 8 MEDIUM, 2 LOW".

This is a measuring tool, not a test. `tests/test_reference_sets.py` is the
gate; this prints the number that says whether a refinement moved it.
"""
from __future__ import annotations

import json
import os
import sys
import time
from typing import Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fbcreview.render.v5_register_reference import R as REGISTER  # noqa: E402

#: Markers that point at another register entry rather than being a check.
MARKERS = {"C-01b", "C-01c", "H-01r"}

#: Register entry -> the rule(s) whose output reproduces it. An entry absent
#: here has no rule yet; it is still counted, as missed, because the point of
#: the number is how much of a real review the engine does.
RULE_FOR: Dict[str, List[str]] = {
    "C-01": ["MECH.OUTDOOR_AIR_CAPACITY"],
    "H-01": ["OCC.CLASSIFICATION_CONSISTENCY"],
    "H-02": ["EGRESS.COMMON_PATH"],
    "H-03": ["DOORS.CLEAR_WIDTH"],
    "M-01": ["EGRESS.CAPACITY_FACTOR"],
    "M-02": ["EGRESS.FACTOR_CONSISTENCY"],
    "M-03": ["XSHEET.RISK_CATEGORY"],
    "M-04": ["XSHEET.BUILDING_AREA"],
    "M-05": ["EGRESS.OCCUPANT_LOAD_POSTING"],
    "V-01": ["EGRESS.TRAVEL_DISTANCE"],
    "V-02": ["EGRESS.DEAD_END"],
    "V-03": ["EGRESS.CORRIDOR_WIDTH"],
    "V-04": ["EGRESS.EXIT_COUNT"],
    "V-05": ["DOORS.CLEAR_WIDTH_REQUIREMENT"],
    "V-06": ["PLUMB.FIXTURE_COUNT"],
    "V-07": ["MEASURE.EGRESS_EXTENT"],
    "V-17": ["EGRESS.CEILING_HEIGHT"],
    "V-30": ["ELEC.PANEL_LOADING"],
    "V-35": ["MECH.OUTDOOR_AIR_ARITHMETIC"],
}


#: Register severities that record a check which passed. MEASURED is a pass
#: established by tracing the drawing rather than by reading a stated value.
PASSING = ("VERIFIED", "MEASURED")


def _expected(entry: dict):
    status = "PASS" if entry["sev"] in PASSING else "OPEN"
    return status, entry["sev"]


def grade(findings: List[dict], abstentions: List[dict]) -> List[dict]:
    rows = []
    by_rule: Dict[str, List[dict]] = {}
    for f in findings:
        by_rule.setdefault(f["rule_id"], []).append(f)
    abstained = {a["rule_id"]: a for a in abstentions}

    for entry in REGISTER:
        fid = entry["fid"]
        if fid in MARKERS:
            continue
        want_status, want_sev = _expected(entry)
        rules = RULE_FOR.get(fid, [])
        got: Optional[dict] = None
        for rid in rules:
            for f in by_rule.get(rid, []):
                # A measured pass reproduces a verified entry and vice versa:
                # both say the check was made and held.
                status = "PASS" if f["severity"] in PASSING else f["status"]
                if status == want_status and (f["severity"] == want_sev
                                              or (want_sev in PASSING
                                                  and f["severity"] in PASSING)):
                    got = dict(f, _grade="exact")
                    break
                got = got or dict(f, _grade="partial")
            if got and got["_grade"] == "exact":
                break
        if got:
            outcome, note = got["_grade"], f"{got['fid']} {got['status']} {got['severity']}"
        elif not rules:
            outcome, note = "missed", "no rule for this check yet"
        else:
            a = next((abstained[r] for r in rules if r in abstained), None)
            outcome = "missed"
            note = f"abstained: {a['reason']}" if a else "rule not registered"
        rows.append({"fid": fid, "severity": entry["sev"], "title": entry["title"],
                     "rules": rules, "outcome": outcome, "note": note})
    return rows


def summarise(rows: List[dict]) -> dict:
    def count(pred):
        return sum(1 for r in rows if pred(r))
    open_rows = [r for r in rows if r["severity"] not in PASSING]
    ver_rows = [r for r in rows if r["severity"] in PASSING]
    return {
        "open_total": len(open_rows),
        "open_exact": count(lambda r: r in open_rows and r["outcome"] == "exact"),
        "open_partial": count(lambda r: r in open_rows and r["outcome"] == "partial"),
        "verified_total": len(ver_rows),
        "verified_exact": count(lambda r: r in ver_rows and r["outcome"] == "exact"),
        "verified_partial": count(lambda r: r in ver_rows and r["outcome"] == "partial"),
    }


def main(argv: List[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    path = argv[1]
    readings = None
    if "--readings" in argv:
        from fbcreview.ai.readings import load_readings
        readings = load_readings(argv[argv.index("--readings") + 1])

    from fbcreview.pipeline import build_facts
    from fbcreview.rules import run_all

    t0 = time.perf_counter()
    facts = build_facts(path, readings=readings) if readings is not None else build_facts(path)
    res = run_all(facts)
    seconds = time.perf_counter() - t0

    findings = [f.to_dict() for f in res.findings]
    abstentions = [a.__dict__ for a in res.abstentions]
    rows = grade(findings, abstentions)
    s = summarise(rows)

    mark = {"exact": "✔", "partial": "~", "missed": "·"}
    print(f"\n{path}\n")
    for r in rows:
        print(f"  {mark[r['outcome']]} {r['fid']:<5} {r['severity']:<9} {r['title'][:58]:<58}  {r['note'][:70]}")
    print(f"\n  open findings reproduced   {s['open_exact']}/{s['open_total']} exact"
          f" (+{s['open_partial']} partial)")
    print(f"  verified items reproduced  {s['verified_exact']}/{s['verified_total']} exact"
          f" (+{s['verified_partial']} partial)")
    print(f"  engine findings {len(findings)} · abstentions {len(abstentions)} · {seconds:.1f} s\n")

    if "--json" in argv:
        out = argv[argv.index("--json") + 1]
        with open(out, "w", encoding="utf-8") as fh:
            json.dump({"summary": s, "rows": rows, "seconds": round(seconds, 2)}, fh, indent=2)
        print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
