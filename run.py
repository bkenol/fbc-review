#!/usr/bin/env python3
"""FBC review from the command line.

Usage: python run.py <permit-set.pdf|.dwg|.dxf|.zip> [--json out.json]
                     [--declaration decl.json]
                     [--ai] [--readings readings.json] [--save-readings readings.json]
                     [--review ai_review.json] [--save-review ai_review.json]
                     [--cad-dir DIR] [--markup-dxf out.zip]

The set may be a PDF plotted from CAD, or the drawing itself: a DWG, a DXF, or a
zip of them, recognised from the file's bytes. A drawing is plotted to PDF first
(`fbcreview/cad`; a DWG needs LibreDWG's `dwg2dxf`, on PATH or at `FBC_DWG2DXF`),
and the review reads that PDF with the drawing's sidecar beside it. The plot and
sidecar go to a temporary directory that is removed afterwards, or to `--cad-dir`
to keep them. `--markup-dxf` also writes the findings back into the drawing, on
layers FBC-REVIEW and FBC-REVIEW-TEXT, as zipped DXF.

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

With `--ai`, a reviewer model then reviews and corrects the result: it edits
findings directly — each edit labelled on the finding — and may send sheets back
to be read again, after which the rules run again. Check, edit, verify: at most
three passes (`FBC_AI_MAX_PASSES`, `FBC_AI_REVIEW=off` to skip it).
`--save-review` keeps what it did; `--review` replays that, on top of
`--readings`, with no API call.
"""
import json, os, shutil, sys, tempfile
from fbcreview.declaration import ProjectDeclaration
from fbcreview.declaration_schema import validate
from fbcreview.pipeline import build_facts
from fbcreview.rules import run_all, registered

def _arg(argv, flag):
    return argv[argv.index(flag) + 1] if flag in argv else None

def _plot(argv, path):
    """(rendered PDF, sidecar, readings identity, work dir to remove) for a drawing,
    or (path, None, None, None) for a PDF. Raises ValueError carrying the refusal.

    The identity is what AI readings of a drawing are keyed by: the plotted PDF's
    bytes never repeat, so readings are tied to the drawing and what plotted it
    instead (`fbcreview.ai.readings.plotted_identity`) — the worker's key too.
    """
    from fbcreview.cad import source
    kind = source.sniff(path)
    if kind not in (source.DWG, source.DXF, source.ZIP):
        return path, None, None, None
    from fbcreview.cad import ingest
    from fbcreview.cad.convert import ConversionFailed, ConversionUnavailable
    from fbcreview.cad.read import ReadError
    from fbcreview.ai.readings import file_sha256, plotted_identity
    keep = _arg(argv, "--cad-dir")
    work = keep or tempfile.mkdtemp(prefix="fbc-cad-")
    try:
        cs = ingest(path, work, name=os.path.basename(path),
                    progress=lambda m: print(f"  cad: {m}", file=sys.stderr))
    except BaseException as exc:
        if not keep:
            shutil.rmtree(work, ignore_errors=True)
        if isinstance(exc, (source.SourceError, ConversionUnavailable, ConversionFailed,
                            ReadError)):
            raise ValueError(f"drawing refused: {getattr(exc, 'message', None) or exc}")
        raise
    ident = plotted_identity(file_sha256(path), cs.data, cs.pdf_path)
    return cs.pdf_path, cs.data, ident, (None if keep else work)

def _readings(argv, path, ident=None):
    """(readings or None, how they were obtained), or (None, error) to stop on.

    `ident` is a drawing's readings identity; None keys readings by the PDF itself.
    """
    replay = _arg(argv, "--readings")
    if replay:
        from fbcreview.ai.readings import file_sha256, load_readings
        readings = load_readings(replay)
        # Readings name the file they were read from. Grounding would reject
        # nearly everything from another file, but "nearly" is the problem.
        if readings.file_sha256 and readings.file_sha256 != (ident or file_sha256(path)):
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
    readings = read_document(path, config, cache=ReadingsCache(cache) if cache else None,
                             identity=ident)
    return readings, f"read by {config.model} at {config.effort} effort"

def _review(argv, path, readings, first, declaration, cad=None):
    """(facts, result, readings, trace, how) after the result check, when there is one.

    `how` is an error message, and the trace None, when a replay does not match.
    """
    replay_path = _arg(argv, "--review")
    if readings is None or (replay_path is None and "--ai" not in argv):
        return (*first, readings, None, None)
    from fbcreview.ai import review as RV
    from fbcreview.ai.reader import ReaderConfig, make_client, read_document
    from fbcreview.ai.reviewer import (REVIEW_PROMPT_VERSION, ReviewerConfig, check_result,
                                       system_prompt)

    def run_pass(r):
        # With the drawing's sidecar on every pass: a pass without it would drop
        # what was read from the drawing and review a smaller set.
        f = build_facts(path, readings=r, cad=cad)
        return f, run_all(f, None, declaration)

    if replay_path:
        replay = RV.load_trace(replay_path)
        if (replay.file_sha256, replay.reader) != (readings.file_sha256,
                                                   RV.reader_identity(readings)):
            return (*first, readings, None,
                    f"review rejected: {replay_path} was made from other readings")
        trace = RV.ReviewTrace(replay.file_sha256, replay.model, replay.prompt_version,
                               replay.max_passes, reader=replay.reader)
        facts, res, final = RV.review_loop(first, readings, run_pass, None, None, trace,
                                           replay=replay, pdf_path=path)
        return facts, res, final, trace, f"replayed from {replay_path}"

    reader = ReaderConfig.from_env({**os.environ, "FBC_AI_READING": "on"})
    config = ReviewerConfig.from_env(reader)
    if config is None:
        return (*first, readings, None, None)
    client, system = make_client(reader), system_prompt()
    trace = RV.ReviewTrace(readings.file_sha256, config.model, REVIEW_PROMPT_VERSION,
                           config.max_passes, reader=RV.reader_identity(readings))

    def check(facts, state, number, history):
        return check_result(client, config, system, RV.packet(
            path, facts, state, None, declaration, number, config.max_passes, history))

    def reread(focus):
        return read_document(path, reader, client=client, identity=readings.file_sha256,
                             focus=focus)

    facts, res, final = RV.review_loop(first, readings, run_pass, check, reread, trace,
                                       pdf_path=path)
    return facts, res, final, trace, f"checked by {config.model} at {config.effort} effort"

def main(argv):
    if len(argv) < 2 or argv[1] in ("-h", "--help"):
        print(__doc__); return 2
    if not os.path.isfile(argv[1]):
        print(f"No such file: {os.path.basename(argv[1])}"); return 2
    try:
        path, cad, ident, scratch = _plot(argv, argv[1])
    except ValueError as exc:
        print(exc); return 2
    try:
        return _main(argv, argv[1], path, cad, ident)
    finally:
        if scratch:
            shutil.rmtree(scratch, ignore_errors=True)

def _cad_lines(cad, facts):
    """What the drawing gave, for the console: sheets, layers, each viewport's scale."""
    pages = cad.get("pages", [])
    conv = cad.get("converter") or "no conversion"
    print(f"drawing     {cad.get('kind')}, {len(cad.get('drawings', []))} drawing(s) read "
          f"({conv}; {cad.get('render_version')}) in {cad.get('seconds')} s")
    print(f"plotted     {len(pages)} sheets, "
          f"{sum(1 for p in pages if p.get('number'))} numbered by their title block")
    for p in pages:
        pno = int(p["page"])
        views = facts.geometry[pno].views if pno in facts.geometry else []
        label = p.get("number") or f"p{pno + 1}"
        if p.get("model"):
            g = facts.geometry.get(pno)
            v = g.scale_pt_per_ft.value if g is not None and g.scale_pt_per_ft else None
            print(f"  {label:<8} model space"
                  + (f", fitted at {v:g} pt/ft" if v else ", no scale"))
            continue
        vps = p.get("viewports", [])
        print(f"  {label:<8} layout '{p.get('layout')}', {len(vps)} viewport(s)")
        for i, vp in enumerate(vps):
            ratio = f"1:{1 / vp['scale']:.4g}" if vp.get("scale") else "unknown ratio"
            ev = views[i].scale if i < len(views) else None
            ptft = f" = {ev.value:g} pt/ft" if ev is not None and ev else ""
            print(f"           viewport {vp.get('handle')}  {ratio}{ptft}")
    if cad.get("warnings"):
        print(f"warnings    {len(cad['warnings'])} (see the sidecar)")

def _main(argv, given, path, cad, ident):

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

    readings, how = _readings(argv, path, ident)
    if readings is None and how:
        print(how); return 2
    save = _arg(argv, "--save-readings")
    if save and readings is not None:
        from fbcreview.ai.readings import save_readings
        save_readings(readings, save)

    facts = build_facts(path, readings=readings, cad=cad)
    res = run_all(facts, None, declaration)
    facts, res, readings, trace, review_how = _review(argv, path, readings, (facts, res),
                                                      declaration, cad=cad)
    if trace is None and review_how:
        print(review_how); return 2
    save_review = _arg(argv, "--save-review")
    if save_review and trace is not None:
        from fbcreview.ai.review import save_trace
        save_trace(trace, save_review)

    print(f"\nsource      {given}")
    print(f"sheets      {len(facts.sheets)}  ({', '.join(s.code for s in facts.sheets[:8])} …)")
    if cad is not None:
        print(f"cad layers  {len(facts.meta.get('cad_layers', []))} in the drawing's layer table")
        _cad_lines(cad, facts)
    else:
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
    if trace is not None:
        rv = trace.summary()
        print(f"ai review   {rv['passes']} of at most {rv['max_passes']} passes {review_how}; "
              f"stopped: {rv['outcome'].replace('_', ' ')}; {rv['findings_revised']} revised, "
              f"{rv['findings_added']} added, {rv['findings_withdrawn']} withdrawn, "
              f"{rv['edits_rejected']} edits not applied; {rv['sheets_reread']} sheets re-read")
        if save_review:
            print(f"            saved to {save_review}")
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
        json.dump({"findings": findings_payload(path, facts, res.findings,
                                                revisions=trace.revision_map() if trace else None),
                   "abstentions": [a.__dict__ for a in res.abstentions],
                   "declaration": declaration.to_dict() if declaration else None,
                   "meta": {k: v for k, v in facts.meta.items()
                            if k not in ("cad_layers", "options", "building",
                                         "reconciled", "declaration")}},
                  open(out, "w"), indent=2, default=str)
        print(f"\nwrote {out}")

    markup_out = _arg(argv, "--markup-dxf")
    if markup_out:
        if cad is None:
            print("\n--markup-dxf needs a drawing (.dwg, .dxf or .zip); this set is a PDF")
            return 2
        from fbcreview.cad import markup
        from fbcreview.payload import findings_payload
        work = os.path.dirname(path)
        payload = findings_payload(path, facts, res.findings,
                                   revisions=trace.revision_map() if trace else None)
        done = markup.write(work, cad, payload, markup_out, os.path.basename(given))
        print(f"\nwrote {markup_out}  ({done['placed']} findings clouded, "
              f"{done['listed']} listed beside their sheet, {done['drawings']} drawing(s))")
    return 0

if __name__ == "__main__":
    sys.exit(main(sys.argv))
