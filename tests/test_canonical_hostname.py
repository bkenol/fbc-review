"""One service, one name.

The repository carried two hostnames for the deployed reviewer:
`fbc.omniflexfitness.com`, which everything operational has always used and
which is the only one that answers, and `review.omniflexfitness.com`, which
appeared in `CLAUDE.md`'s task line and in the kickoff prompt and was never
implemented anywhere.

That cost real time: a session read the standing rules, tested the name they
gave, got nothing, and reported the deployment unreachable when the only thing
missing was a credential. `CLAUDE.md` now settles the name; this keeps it
settled.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

CANONICAL = "fbc.omniflexfitness.com"
RETIRED = "review.omniflexfitness.com"

#: The two files allowed to name the retired host, and only to say it is retired.
#: Both carry a note explaining the decision; a reader who meets the old name in
#: an issue or an old branch needs somewhere that says what happened to it.
EXPLAINS_THE_HISTORY = {
    "CLAUDE.md",
    "docs/DEPLOYMENT-PROMPT.md",
    "docs/RUNBOOK-mark-feedback-actioned.md",
}


def _tracked_text_files():
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True,
                         text=True, check=True).stdout.split("\n")
    for name in out:
        if not name:
            continue
        path = ROOT / name
        if not path.is_file():
            continue
        try:
            yield name, path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue


def test_the_canonical_hostname_is_written_down_as_decided():
    """Not folklore. A rule somebody can read before they go looking."""
    claude = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    assert CANONICAL in claude
    assert "do not relitigate" in claude.lower()
    stack = claude.split("## Stack")[1].split("\n## ")[0]
    assert CANONICAL in stack, "the hostname belongs with the settled decisions"


def test_no_file_reintroduces_the_retired_hostname():
    offenders = sorted(
        name for name, text in _tracked_text_files()
        if RETIRED in text and name not in EXPLAINS_THE_HISTORY
    )
    assert offenders == [], (
        f"{RETIRED} is retired; these files still name it: {offenders}. "
        f"Use {CANONICAL}."
    )


@pytest.mark.parametrize("name", sorted(EXPLAINS_THE_HISTORY))
def test_the_files_that_do_name_it_say_it_is_the_wrong_one(name):
    """An allowlist that is never checked becomes a place to hide things."""
    text = (ROOT / name).read_text(encoding="utf-8")
    if RETIRED not in text:
        pytest.skip(f"{name} no longer mentions it, which is also fine")
    assert CANONICAL in text, (
        f"{name} names the retired host without naming the canonical one"
    )


def test_the_operational_files_agree_on_the_canonical_host():
    """The scripts and the CORS policy are what actually reach the service."""
    for name in ("cors.json", "scripts/tunnel.sh", "scripts/provision.sh",
                 "scripts/setup-auth.sh"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert CANONICAL in text, f"{name} does not name the canonical host"
        assert RETIRED not in text
