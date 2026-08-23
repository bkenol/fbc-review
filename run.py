#!/usr/bin/env python3
"""Deterministic FBC review.

Usage: python run.py <permit-set.pdf> [--json out.json] [--declaration decl.json]

`--declaration` takes a JSON object of ProjectDeclaration fields — what the
applicant says the building is. It is a second source alongside the drawings,
never an override: agreement raises confidence, disagreement is reported, and a
field left out is left out rather than defaulted.
"""
import json, sys
from fbcreview.declaration import ProjectDeclaration
from fbcreview.declaration_schema import validate
from fbcreview.pipeline import build_facts
from fbcreview.rules import run_all, registered

def _arg(argv, flag):
    return argv[argv.index(flag) + 1] if flag in argv else None

def main(argv):
    if len(argv) < 2:
        print(__doc__); return 2
    path = argv[1]

    declaration = None
    decl_path = _arg(argv, "--declaration")
    if decl_path:
        raw = json.load(open(decl_path))
        problems = validate(raw)
        if problems:
            print("declaration rejected:")
            for p in problems:
                print(f"  {p}")
            return 2
        declaration = ProjectDeclaration.from_dict(raw)

    facts = build_facts(path)
    res = run_all(facts, None, declaration)

    print(f"\nsource      {path}")
    print(f"sheets      {len(facts.sheets)}  ({', '.join(s.code for s in facts.sheets[:8])} …)")
    print(f"cad layers  {len(facts.meta.get('cad_layers', []))} preserved as PDF optional content")
    print(f"code data   {len(facts.code_data)} cited rows extracted")
    print(f"schedules   {', '.join(s.name for s in facts.schedules) or '—'}")
    print(f"doors       {len(facts.doors)}")
    print(f"rules       {len(registered())} registered, 0 model calls")
    if declaration is not None:
        rf = res.reconciled
        states = {}
        for r in rf.fields.values():
            states[r.state] = states.get(r.state, 0) + 1
        print(f"declaration {declaration.answered_count()} answered  "
              + "  ".join(f"{k.lower()} {v}" for k, v in sorted(states.items())))
    print()

    scal = [(p, g.scale_pt_per_ft) for p, g in facts.geometry.items()]
    ok = [s for _, s in scal if s]
    print(f"scale       resolved on {len(ok)}/{len(scal)} pages "
          f"({sum(1 for s in ok if s.confidence=='high')} at high confidence)")

    print(f"\n── FINDINGS ({len(res.findings)}) " + "─"*46)
    for f in res.findings:
        tag = "" if f.scenario == "both" else f"  [{f.scenario}]"
        tag += "  [declared]" if f.basis == "declaration" else ""
        print(f"  {f.severity:<9} {f.fid:<7} {f.sheet:<5} {f.title[:76]}{tag}")
    print(f"\n── ABSTENTIONS ({len(res.abstentions)}) " + "─"*43)
    for a in res.abstentions:
        print(f"  {a.rule_id:<32} {a.reason}{(' — ' + a.detail) if a.detail else ''}")

    if "--json" in argv:
        out = _arg(argv, "--json")
        json.dump({"findings": [f.to_dict() for f in res.findings],
                   "abstentions": [a.__dict__ for a in res.abstentions],
                   "declaration": declaration.to_dict() if declaration else None,
                   "meta": {k: v for k, v in facts.meta.items()
                            if k not in ("cad_layers", "options", "building",
                                         "reconciled", "declaration")}},
                  open(out, "w"), indent=2, default=str)
        print(f"\nwrote {out}")
    return 0

if __name__ == "__main__":
    sys.exit(main(sys.argv))
