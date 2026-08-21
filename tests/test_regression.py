"""Regression test: the deterministic pipeline must keep reproducing the
findings that were originally established by hand on the Sculpted Hot Pilates
permit set. If a refactor loses one of these, the test fails.

Run: python -m pytest tests/ -q     (or: python tests/test_regression.py)
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fbcreview.pipeline import build_facts
from fbcreview.rules import run_all

SET = os.environ.get("FBC_TEST_PDF", "")

EXPECT_OPEN = {
    "MECH.OUTDOOR_AIR_CAPACITY": "CRITICAL",   # 881 required vs 350 scheduled
    "EGRESS.COMMON_PATH":        "HIGH",       # 50 LF stated, 75 ft required
    "DOORS.CLEAR_WIDTH":         "HIGH",       # door 104 at 2'-8"
    "EGRESS.CAPACITY_FACTOR":    "MEDIUM",     # 0.15 without an EVACS
    "XSHEET.RISK_CATEGORY":      "MEDIUM",     # RC III at OL 70
    "XSHEET.BUILDING_AREA":      "MEDIUM",     # 1,436 vs 1,375 SF
}
EXPECT_PASS = {"EGRESS.TRAVEL_DISTANCE", "EGRESS.DEAD_END",
               "EGRESS.CORRIDOR_WIDTH", "ELEC.PANEL_LOADING"}


def main():
    if not SET or not os.path.exists(SET):
        print("set FBC_TEST_PDF to the permit set to run this test"); return 0
    res = run_all(build_facts(SET))
    got_open = {}
    for f in res.findings:
        if f.status == "OPEN":
            got_open.setdefault(f.rule_id, set()).add(f.severity)
    got_pass = {f.rule_id for f in res.findings if f.status == "PASS"}
    bad = 0
    for rid, sev in EXPECT_OPEN.items():
        if sev not in got_open.get(rid, set()):
            print(f"FAIL open {rid}: expected {sev}, got {sorted(got_open.get(rid, []))}"); bad += 1
    for rid in EXPECT_PASS:
        if rid not in got_pass:
            print(f"FAIL pass {rid}: not produced"); bad += 1
    # every rule must either fire or abstain — silence is a bug
    fired = {f.rule_id for f in res.findings} | {a.rule_id for a in res.abstentions}
    from fbcreview.rules import registered
    for rid in registered():
        if rid not in fired:
            print(f"FAIL silent {rid}: neither a finding nor an abstention"); bad += 1
    print("OK" if not bad else f"{bad} failure(s)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
