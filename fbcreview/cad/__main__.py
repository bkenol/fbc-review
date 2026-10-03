"""`python -m fbcreview.cad` — the CAD adapter in its own process.

    python -m fbcreview.cad ingest SOURCE WORKDIR [--name NAME]
    python -m fbcreview.cad markup WORKDIR FINDINGS_JSON OUT_ZIP [--name NAME]

The worker runs both as subprocesses (`webapp/cadjob.py`): reading a large
drawing holds about a gigabyte that Python will not hand back to the service,
and a converter that crashes takes a subprocess with it, not a worker thread.

Prints one JSON object on stdout. Exit 0 on success; exit 2 with
`{"error": code, "message": prose}` for an upload the user has to fix; any
other exit is an internal failure.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys


def _fail(code: str, message: str) -> int:
    print(json.dumps({"error": code, "message": message}))
    return 2


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m fbcreview.cad")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("ingest")
    a.add_argument("source")
    a.add_argument("workdir")
    a.add_argument("--name", default="")
    a.add_argument("--max-sheets", type=int, default=0)
    m = sub.add_parser("markup")
    m.add_argument("workdir")
    m.add_argument("findings")
    m.add_argument("out_zip")
    m.add_argument("--name", default="")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)

    from . import convert, ingest, load_sidecar, SIDECAR
    from .read import ReadError
    from .source import SourceError
    import os

    if args.cmd == "ingest":
        def progress(msg: str) -> None:
            # A line per step on stderr, for whoever runs this by hand; the
            # service counts stderr lines and keeps none of them (cadjob).
            print(f"PROGRESS {msg}", file=sys.stderr, flush=True)
        try:
            cs = ingest(args.source, args.workdir, name=args.name, progress=progress,
                        max_sheets=args.max_sheets)
        except SourceError as exc:
            return _fail(exc.code, exc.message)
        except convert.ConversionUnavailable as exc:
            return _fail("dwg_unavailable", str(exc))
        except convert.ConversionFailed as exc:
            return _fail("dwg_conversion_failed", str(exc))
        except ReadError as exc:
            return _fail("dxf_unreadable", str(exc))
        print(json.dumps({"pdf": os.path.basename(cs.pdf_path),
                          "sidecar": os.path.basename(cs.sidecar_path),
                          "pages": len(cs.pages), "warnings": len(cs.warnings),
                          "seconds": cs.data.get("seconds")}))
        return 0

    from . import markup
    with open(args.findings, "r", encoding="utf-8") as fh:
        payload = json.load(fh)
    findings = payload.get("findings", payload) if isinstance(payload, dict) else payload
    sidecar = load_sidecar(os.path.join(args.workdir, SIDECAR))
    try:
        summary = markup.write(args.workdir, sidecar, findings, args.out_zip, args.name)
    except ReadError as exc:
        return _fail("dxf_unreadable", str(exc))
    print(json.dumps(summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())
