#!/usr/bin/env python3
"""FBC review from the command line.

Usage: python run.py <permit-set.pdf> [--json out.json] [--declaration decl.json]
                     [--ai] [--readings readings.json] [--save-readings readings.json]

`--declaration` takes a JSON object of ProjectDeclaration fields — what the
applicant says the building is. It is a second source alongside the drawings,
never an override: agreement raises confidence, disagreement is reported, and a
field left out is left out rather than defaulted.

`--ai` also reads each sheet with Claude. It needs ANTHROPIC_API_KEY, from the
environment or `secrets/local.env` (see the "AI sheet reading" section of
`secrets/local.env.example`), and it sends the sheets' images and text to the
Anthropic API. The model only proposes where a value is printed; each proposal
is found on the sheet and parsed before a rule may use it, and the rules stay
pure Python. `--save-readings` keeps what the model read; `--readings` replays
a saved reading with no API call, so the same file and readings give the same
findings. With neither flag the review is deterministic and calls nothing.
"""
import json, os, sys
from fbcreview.declaration import ProjectDeclaration
from fbcreview.declaration_schema import validate
from fbcreview.pipeline import build_facts
from fbcreview.rules import run_all, registered

def _arg(argv, flag):
    return argv[argv.index(flag) + 1] if flag in argv else None

def _readings(argv, path):
    """(readings or None, how they were obtained), or (None, error) to stop on."""
    replay = _arg(argv, "--readings")
    if replay:
        from fbcreview.ai.readings import file_sha256, load_readings
        readings = load_readings(replay)
        # Readings name the file they were read from. Grounding would reject
        # nearly everything from another file, but "nearly" is the problem.
        if readings.file_sha256 and readings.file_sha256 != file_sha256(path):
            return None, f"readings rejected: {replay} was read from a different file"
        return readings, f"replayed from {replay}"
    if "--ai" not in argv:
        return None, None
    from webapp import envfile
    envfile.load()
    from fbcreview.ai.reader import ReaderConfig, read_document
    from fbcreview.ai.readings import ReadingsCache
    # Passing --ai is the consent FBC_AI_READING stands for in a deployment.
    config = ReaderConfig.from_env({**os.environ, "FBC_AI_READING": "on"})
    if config is None:
        return None, "--ai needs ANTHROPIC_API_KEY (see secrets/local.env.example)"
    cache = os.environ.get("FBC_AI_CACHE_DIR")
    readings = read_document(path, config, cache=ReadingsCache(cache) if cache else None)
    return readings, f"read by {config.model} at {config.effort} effort"

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

    readings, how = _readings(argv, path)
    if readings is None and how:
        print(how); return 2
    save = _arg(argv, "--save-readings")
    if save and readings is not None:
        from fbcreview.ai.readings import save_readings
        save_readings(readings, save)

    facts = build_facts(path, readings=readings)
    res = run_all(facts, None, declaration)

    print(f"\nsource      {path}")
    print(f"sheets      {len(facts.sheets)}  ({', '.join(s.code for s in facts.sheets[:8])} …)")
    print(f"cad layers  {len(facts.meta.get('cad_layers', []))} preserved as PDF optional content")
    print(f"code data   {len(facts.code_data)} cited rows extracted")
    print(f"schedules   {', '.join(s.name for s in facts.schedules) or '—'}")
    print(f"doors       {len(facts.doors)}")
    print(f"rules       {len(registered())} registered, pure Python")
    if readings is None:
        print("ai reading  off — deterministic reading only, no model called")
    else:
        ai = readings.summary()
        print(f"ai reading  {ai['sheets_read']} sheets {how}; "
              f"{ai['accepted']} values found on the sheet, {ai['rejected']} rejected"
              + (f"; {ai['sheets_failed']} sheets not read" if ai["sheets_failed"] else ""))
        if save:
            print(f"            saved to {save}")
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
        from fbcreview.payload import findings_payload
        json.dump({"findings": findings_payload(path, facts, res.findings),
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
