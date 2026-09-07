"""Reading `secrets/local.env` into the environment, for a local deployment.

## Why this exists

Every secret this service takes is an environment variable, which is right for
Cloud Run — you set them once on the service and never think about them again.
It is wrong for the local deployment, where the service is a container started
by `scripts/share.sh` from whatever shell happened to be open. Exporting five
SMTP variables and an API key before every start is the kind of step that gets
skipped, and the failure is silent: mail and the comment assist are both inert
without their keys and both say so only if somebody looks at `/admin`.

So there is one file, it is git-ignored, and both paths read it: the container
gets it as `--env-file`, and a bare `uvicorn` run gets it through here.

## The rules, and why they are these rules

**A variable already set always wins.** Cloud Run sets the real thing; a stray
file in a checked-out repo must never override it. On Cloud Run this file does
not exist at all, so the whole module is inert there — but "inert because the
file is missing" is luck, and "inert because set beats unset" is a rule.

**Values are literal.** No quote stripping, no `$VAR` expansion, no escapes.
Not because those would be hard, but because Docker's `--env-file` parser does
none of them, and this file is read by both. A parser that was cleverer than
Docker's would mean `FBC_SMTP_PASS="hunter2"` worked in one path and arrived
with its quotes in the other, which is a worse failure than not supporting
quotes: it authenticates locally and fails in the container. Quoting is
common enough to be worth catching rather than only documenting, so a quoted
value is logged as a warning.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import List, Optional

log = logging.getLogger("fbc.env")

#: Repository-relative, resolved from this file so it does not depend on the
#: working directory the service was started from.
DEFAULT_PATH = Path(__file__).resolve().parent.parent / "secrets" / "local.env"

_loaded = False


def path() -> Path:
    """Where the file is. `FBC_ENV_FILE` overrides, for an unusual layout."""
    override = os.environ.get("FBC_ENV_FILE", "").strip()
    return Path(override) if override else DEFAULT_PATH


def parse(text: str) -> List[tuple[str, str]]:
    """`KEY=value` pairs, in file order. Separated out so it can be tested."""
    pairs: List[tuple[str, str]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        # `export FOO=bar` is what a shell user's fingers type. Accepting it
        # costs one line and saves a variable that silently never arrives.
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key:
            pairs.append((key, value.strip()))
    return pairs


def load(target: Optional[Path] = None) -> List[str]:
    """Set what the file declares and is not already set. Returns those names.

    Never raises. A malformed or unreadable env file must not stop the service
    from starting: the deployment it would take down is the one whose operator
    is in the middle of configuring it.
    """
    global _loaded
    file = target or path()
    try:
        if not file.is_file():
            return []
        text = file.read_text(encoding="utf-8")
    except OSError as exc:
        log.warning("could not read the env file", extra={"error": type(exc).__name__})
        return []

    applied: List[str] = []
    for key, value in parse(text):
        if key in os.environ:
            continue
        if len(value) > 1 and value[0] == value[-1] and value[0] in "\"'":
            # Said once, with the name and never the value: this file holds an
            # API key and a mail password, and a log line is the wrong place
            # for either.
            log.warning(
                "env file value is quoted and the quotes are part of it",
                extra={"key": key},
            )
        os.environ[key] = value
        applied.append(key)

    _loaded = True
    if applied:
        log.info("read local configuration", extra={"count": len(applied)})
    return applied


def loaded() -> bool:
    """Whether `load` has run in this process. For diagnostics only."""
    return _loaded
