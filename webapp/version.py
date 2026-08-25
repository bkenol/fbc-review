"""The version this build reports, and where each part of it comes from.

One file is the source of truth: `VERSION` at the repository root. It holds the
release triple and the channel, and nothing else:

    1.0.0-alpha

The build number is *not* committed. It is stamped on per build, because a
number that has to be edited by hand is a number that stops being edited.

    ALPHA   1.0.0-alpha.<build>+<sha>    every push to main, incrementing
    BETA    1.0.0-beta.<build>+<sha>     same, once VERSION says `-beta`
    PROD    1.0.0+build.<build>.<sha>    once VERSION drops the channel

That is SemVer 2.0.0 precedence, unmodified, and it orders the way the release
train runs: `1.0.0-alpha.9 < 1.0.0-alpha.10 < 1.0.0-beta.1 < 1.0.0`. Numeric
pre-release identifiers compare numerically, so build 10 outranks build 9 —
which a plain string comparison would get backwards. Build metadata after `+`
is ignored in precedence, which is exactly right: the commit says *which* build
it was, never *whether* it is newer.

Promoting a channel is one deliberate edit to `VERSION`. Everything else moves
on its own.

WHAT PUBLISHES WHAT
    /healthz `version`      the full stamped string — identifies this build
    OpenAPI `info.version`  `release()` only — identifies the API contract

Those are deliberately different. The published schema is regenerated in CI and
compared byte-for-byte against what is committed, so anything that varies by
machine or by build cannot appear in it. The contract does not change when a
build number does.

OUTSIDE CI there is no build number, so the slot carries the working tree
instead — `1.0.0-alpha+local.9f3c1ab` or `…+local.9f3c1ab.dirty`. That is the
line that answers "is the thing I am looking at the thing I just pulled?".

Import-cheap on purpose: no third-party imports, so `python -m webapp.version`
runs without the service's dependencies installed, and `git` is only consulted
when this is a checkout rather than a container.
"""
from __future__ import annotations

import os
import re
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Optional

_ROOT = Path(__file__).resolve().parent.parent
_VERSION_FILE = _ROOT / "VERSION"

#: What `VERSION` is allowed to say. Deliberately narrower than SemVer: no build
#: number and no metadata, because those are stamped on rather than committed.
BASE_PATTERN = re.compile(r"^(?P<release>\d+\.\d+\.\d+)(?:-(?P<channel>alpha|beta))?$")

#: Used only when `VERSION` is missing or unreadable. It sorts below every real
#: version and says so, rather than claiming to be one.
UNKNOWN = "0.0.0-unknown"


class InvalidVersion(ValueError):
    """`VERSION` does not hold a release triple and an optional channel."""


def parse(text: str) -> "tuple[str, str]":
    """Split a base version into `(release, channel)`.

    `channel` is `"prod"` when there is no pre-release tag, so callers never
    have to special-case the empty string.
    """
    match = BASE_PATTERN.match(text.strip())
    if not match:
        raise InvalidVersion(
            f"{text!r} is not MAJOR.MINOR.PATCH with an optional -alpha or -beta channel"
        )
    return match["release"], match["channel"] or "prod"


@lru_cache(maxsize=1)
def base() -> str:
    """The committed base version, e.g. `1.0.0-alpha`.

    A missing or malformed file degrades to `UNKNOWN` rather than raising: this
    is read on the import path of a service whose liveness probe must answer.
    `tests/test_version.py` is what keeps the committed file honest.
    """
    try:
        raw = _VERSION_FILE.read_text(encoding="utf-8")
    except OSError:
        return UNKNOWN
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            parse(line)
        except InvalidVersion:
            return UNKNOWN
        return line
    return UNKNOWN


def release() -> str:
    """`MAJOR.MINOR.PATCH` with the channel stripped — the API contract version.

    This is what the OpenAPI document publishes. It must not move with a build
    number or the committed schema would differ from a freshly generated one on
    every push, and the drift check that keeps the generated client honest would
    fail for a reason that has nothing to do with the contract.
    """
    try:
        return parse(base())[0]
    except InvalidVersion:
        return "0.0.0"


def channel() -> str:
    """`alpha`, `beta` or `prod`."""
    try:
        return parse(base())[1]
    except InvalidVersion:
        return "unknown"


def stamp(base_version: str, *, build: Optional[str] = None, commit: Optional[str] = None) -> str:
    """Apply a build number and a commit to a base version.

    Pure, so the rule this project versions by is one testable function rather
    than something reconstructed from a workflow's shell.

    A pre-release channel takes the build number as a further dotted identifier,
    which is what makes it sort. A production release has no pre-release part to
    extend, and appending a fourth number would not be SemVer at all, so its
    build number goes into the metadata after `+`.
    """
    prerelease = "-" in base_version
    version = f"{base_version}.{build}" if (build and prerelease) else base_version

    metadata = []
    if build and not prerelease:
        metadata.append(f"build.{build}")
    if commit:
        metadata.append(commit)
    return f"{version}+{'.'.join(metadata)}" if metadata else version


@lru_cache(maxsize=1)
def _local_commit() -> Optional[str]:
    """`local.<sha>`, and `.dirty` when the tree has uncommitted changes.

    Only ever runs in a checkout. The container has no `.git` and no `git`, so
    the guard below means the deployed service never shells out — and if the
    call fails for any other reason, an unidentified build beats a failed start.
    """
    if not (_ROOT / ".git").exists():
        return None
    try:
        described = subprocess.run(
            # No tag matches the empty pattern, so --always falls through to the
            # abbreviated SHA. One call rather than rev-parse plus status.
            ["git", "describe", "--always", "--dirty", "--abbrev=7", "--match="],
            cwd=_ROOT,
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    if not described:
        return None
    # `-dirty` becomes `.dirty`: a hyphen is legal in SemVer metadata but reads
    # as a pre-release tag to a human skimming the string.
    return "local." + described.replace("-", ".")


@lru_cache(maxsize=1)
def resolve() -> str:
    """The full version this process reports.

    `FBC_VERSION` wins outright when it is set. That is how CI hands the built
    artefact the number it stamped, and it means the running service reports the
    same string the deploy log does instead of recomputing it from a source tree
    it no longer has.
    """
    declared = os.environ.get("FBC_VERSION", "").strip()
    if declared:
        return declared
    return stamp(base(), build=os.environ.get("FBC_BUILD", "").strip() or None,
                 commit=_local_commit())


def main() -> int:
    """`python -m webapp.version` — what the shell scripts and the runbook use."""
    import argparse

    parser = argparse.ArgumentParser(description="Print this build's version.")
    parser.add_argument("--release", action="store_true", help="the API contract version only")
    parser.add_argument("--channel", action="store_true", help="alpha, beta or prod")
    parser.add_argument("--base", action="store_true", help="the committed base version")
    args = parser.parse_args()

    if args.release:
        print(release())
    elif args.channel:
        print(channel())
    elif args.base:
        print(base())
    else:
        print(resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
