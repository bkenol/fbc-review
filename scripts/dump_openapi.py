#!/usr/bin/env python3
"""Write the service's OpenAPI schema to a file.

Dumped from the app object rather than scraped from a running server, so CI can
regenerate the TypeScript client without standing up the service, a bucket or a
Firestore instance.

    python scripts/dump_openapi.py openapi.json
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# settings() is not consulted while building the schema, but importing the app
# should never depend on a live environment either.
os.environ.setdefault("FBC_BUCKET", "schema-dump")
os.environ.setdefault("FBC_PROJECT_ID", "schema-dump")


def main(argv: list[str]) -> int:
    from webapp.server import app

    schema = app.openapi()
    text = json.dumps(schema, indent=2, sort_keys=True) + "\n"

    if len(argv) > 1:
        out = Path(argv[1])
        out.parent.mkdir(parents=True, exist_ok=True)
        previous = out.read_text(encoding="utf-8") if out.exists() else None
        out.write_text(text, encoding="utf-8")
        state = "unchanged" if previous == text else "written"
        print(f"{state}: {out} ({len(text)} bytes)", file=sys.stderr)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
