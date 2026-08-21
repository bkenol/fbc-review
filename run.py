#!/usr/bin/env python3
"""Deterministic FBC review. Usage: python run.py <permit-set.pdf> [--json out.json]"""
import json, sys
from fbcreview.pipeline import build_facts
from fbcreview.rules import run_all, registered

def main(argv):
    if len(argv) < 2:
        print(__doc__); return 2
    path = argv[1]
    facts = build_facts(path)
    res = run_all(facts)

    print(f"\nsource      {path}")
    print(f"sheets      {len(facts.sheets)}  ({', '.join(s.code for s in facts.sheets[:8])} …)")
    print(f"cad layers  {len(facts.meta.get('cad_layers', []))} preserved as PDF optional content")
    print(f"code data   {len(facts.code_data)} cited rows extracted")
    print(f"schedules   {', '.join(s.name for s in facts.schedules) or '—'}")
    print(f"doors       {len(facts.doors)}")
    print(f"rules       {len(registered())} registered, 0 model calls\n")

    scal = [(p, g.scale_pt_per_ft) for p, g in facts.geometry.items()]
    ok = [s for _, s in scal if s]
    print(f"scale       resolved on {len(ok)}/{len(scal)} pages "
          f"({sum(1 for s in ok if s.confidence=='high')} at high confidence)")

    print(f"\n── FINDINGS ({len(res.findings)}) " + "─"*46)
    for f in res.findings:
        print(f"  {f.severity:<9} {f.fid:<7} {f.sheet:<5} {f.title[:88]}")
    print(f"\n── ABSTENTIONS ({len(res.abstentions)}) " + "─"*43)
    for a in res.abstentions:
        print(f"  {a.rule_id:<28} {a.reason}{(' — ' + a.detail) if a.detail else ''}")

    if "--json" in argv:
        out = argv[argv.index("--json") + 1]
        json.dump({"findings": [f.to_dict() for f in res.findings],
                   "abstentions": [a.__dict__ for a in res.abstentions],
                   "meta": {k: v for k, v in facts.meta.items() if k != "cad_layers"}},
                  open(out, "w"), indent=2)
        print(f"\nwrote {out}")
    return 0

if __name__ == "__main__":
    sys.exit(main(sys.argv))
