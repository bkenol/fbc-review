#!/usr/bin/env python3
"""Write the real Firebase web config into the Angular client.

Called by scripts/provision.sh with the JSON that `firebase apps:sdkconfig`
produced. Kept as a script rather than inline shell because the CLI has emitted
that JSON in two different shapes over the years and picking the right one is
easier to read — and to fix — in Python.

These values are public by design: they identify the Firebase project, they do
not authorise anything. Access is decided server-side by verifying the ID token
and checking the email allowlist (webapp/auth.py).

    python scripts/write_firebase_config.py sdk.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

TARGET = Path(__file__).resolve().parent.parent / "web/src/app/core/firebase-config.ts"

TEMPLATE = '''/**
 * Firebase web configuration.
 *
 * These values are public by design — they identify the project, they do not
 * authorise anything. Access is decided server-side by verifying the ID token
 * and checking the email allowlist (webapp/auth.py). Committing them is normal
 * Firebase practice and is not a secret leak.
 *
 * Written by scripts/provision.sh from `firebase apps:sdkconfig`. Re-running
 * provisioning regenerates it; hand edits will be overwritten.
 */
export interface FirebaseWebConfig {{
  apiKey: string;
  authDomain: string;
  projectId: string;
  appId: string;
  storageBucket?: string;
  messagingSenderId?: string;
}}

const PLACEHOLDER = 'REPLACE_ME';

export const FIREBASE_CONFIG: FirebaseWebConfig = {{
  apiKey: '{apiKey}',
  authDomain: '{authDomain}',
  projectId: '{projectId}',
  appId: '{appId}',
  storageBucket: '{storageBucket}',
  messagingSenderId: '{messagingSenderId}',
}};

/**
 * False until the project exists. The app then says so plainly instead of
 * throwing an opaque Firebase error into the console on first paint.
 */
export function isFirebaseConfigured(): boolean {{
  return (
    FIREBASE_CONFIG.apiKey !== PLACEHOLDER &&
    FIREBASE_CONFIG.authDomain !== PLACEHOLDER &&
    FIREBASE_CONFIG.projectId !== PLACEHOLDER
  );
}}
'''

REQUIRED = ("apiKey", "authDomain", "projectId", "appId")


def find_config(blob: object) -> dict:
    """The CLI has wrapped this in `sdkConfig`, in `result`, or in nothing."""
    if isinstance(blob, dict):
        if all(k in blob for k in REQUIRED):
            return blob
        for key in ("sdkConfig", "result", "config"):
            inner = blob.get(key)
            found = find_config(inner) if inner is not None else None
            if found:
                return found
        for value in blob.values():
            found = find_config(value)
            if found:
                return found
    elif isinstance(blob, list):
        for value in blob:
            found = find_config(value)
            if found:
                return found
    return {}


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2

    raw = Path(argv[1]).read_text(encoding="utf-8").strip()
    # `firebase apps:sdkconfig` without --json prints a JS snippet around the
    # object, so fall back to the outermost braces.
    try:
        blob = json.loads(raw)
    except json.JSONDecodeError:
        start, end = raw.find("{"), raw.rfind("}")
        if start < 0 or end <= start:
            print("Could not find a config object in the CLI output.", file=sys.stderr)
            return 1
        blob = json.loads(raw[start : end + 1])

    config = find_config(blob)
    missing = [k for k in REQUIRED if not config.get(k)]
    if missing:
        print(f"Config is missing {', '.join(missing)}.", file=sys.stderr)
        return 1

    TARGET.write_text(
        TEMPLATE.format(
            apiKey=config["apiKey"],
            authDomain=config["authDomain"],
            projectId=config["projectId"],
            appId=config["appId"],
            storageBucket=config.get("storageBucket", ""),
            messagingSenderId=config.get("messagingSenderId", ""),
        ),
        encoding="utf-8",
    )
    print(f"wrote {TARGET.name} for project {config['projectId']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
