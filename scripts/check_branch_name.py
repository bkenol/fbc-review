#!/usr/bin/env python3
"""Check that a branch is named for what it changes.

    python scripts/check_branch_name.py                 # the current branch
    python scripts/check_branch_name.py claude/foo-bar  # a named one
    python scripts/check_branch_name.py --session-start # SessionStart hook

The rule, from CLAUDE.md (Working discipline), decided by the owner on
2026-10-03 and made permanent and enforced on 2026-10-06:

    <prefix>/<what-changes>, in lowercase kebab-case, e.g.
    claude/cad-dwg-dxf-input. Never a generated name, never a random suffix.

Claude Code sessions are often handed a generated branch such as
`claude/bold-gauss-05oe0f`. Nothing about that name says what the branch does,
so a list of branches, a PR's head, or a merge commit on main tells a reader
nothing. The suffix is what gives it away: six random letters and digits.

Three places use this, so the rule has one implementation:

  * CI fails a pull request whose head branch breaks it (the `branch name` job
    in .github/workflows/deploy.yml);
  * the SessionStart hook in .claude/settings.json tells a new session, before
    its first push, that the branch it was handed has to be renamed;
  * anyone can run it by hand before pushing.

Standard library only: it runs before the virtualenv exists.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from typing import List, Optional

#: Branches that are not topics, and never need to be.
EXEMPT = {"main", "master", "HEAD"}

#: Bots that name their own branches by their own scheme.
EXEMPT_PREFIXES = ("dependabot/", "renovate/", "gh-readonly-queue/")

#: One kebab-case word: lowercase letters and digits.
_WORD = re.compile(r"^[a-z0-9]+$")

#: A topic needs at least this many words. `claude/fix` says nothing more than
#: `claude/changes`.
MIN_WORDS = 2

#: Longer than this stops being a name and starts being a sentence.
MAX_LENGTH = 60


def looks_random(word: str) -> bool:
    """Whether a word looks like a generated suffix rather than a word.

    The suffixes seen on this repository's branches are six characters of
    [a-z0-9]: 05oe0f, 55sv0v, 94jwpp, 3tvsu6, a2k5o9, xiz0ob, st92xo, didj1r,
    6hrhcx. Most hold a digit. One with none is still caught when it has no
    vowel, which an English word of six letters almost always has.
    """
    if len(word) != 6 or not _WORD.match(word):
        return False
    if any(c.isdigit() for c in word):
        return True
    return not any(c in "aeiouy" for c in word)


def problems(name: str) -> List[str]:
    """What is wrong with a branch name; empty when it is fine."""
    name = (name or "").strip()
    if name.startswith("refs/heads/"):
        name = name[len("refs/heads/"):]
    if not name:
        return ["no branch name was given"]
    if name in EXEMPT or name.startswith(EXEMPT_PREFIXES):
        return []

    found: List[str] = []
    if "/" not in name:
        found.append("it has no prefix: name it <prefix>/<what-changes>, "
                     "e.g. claude/{}".format(name.lower() or "what-changes"))
        prefix, topic = "", name
    else:
        prefix, _, topic = name.partition("/")
        if not _WORD.match(prefix):
            found.append("the prefix {!r} is not one lowercase word".format(prefix))
    if "/" in topic:
        found.append("it has more than one '/': use one prefix and one topic")
        topic = topic.replace("/", "-")

    words = topic.split("-")
    if any(not w for w in words):
        found.append("it has an empty word (a doubled, leading or trailing '-')")
    bad = [w for w in words if w and not _WORD.match(w)]
    if bad:
        found.append("it is not lowercase kebab-case: {}".format(", ".join(bad)))
    real = [w for w in words if w]
    if real and looks_random(real[-1]):
        found.append("it ends in a random suffix ({!r}): drop it and name what "
                     "changes".format(real[-1]))
    if len(real) < MIN_WORDS:
        found.append("the topic is one word: say what changes, in two words or more")
    if len(name) > MAX_LENGTH:
        found.append("it is {} characters; keep it to {}".format(len(name), MAX_LENGTH))
    return found


def current_branch() -> Optional[str]:
    try:
        out = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"],
                             capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    name = out.stdout.strip()
    return name if out.returncode == 0 and name else None


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("branch", nargs="?", help="branch to check (default: the current one)")
    parser.add_argument("--session-start", action="store_true",
                        help="SessionStart hook mode: print guidance for a generated "
                             "branch, and always exit 0")
    args = parser.parse_args(argv)

    name = args.branch or current_branch()
    if args.session_start:
        # A hook that fails a session start helps nobody; it only informs.
        if not name:
            return 0
        found = problems(name)
        if found:
            print("Branch naming (CLAUDE.md, Working discipline): this session is on "
                  "`{}`, which is not named for what it changes ({}). Before the first "
                  "push, rename it to claude/<what-changes> in kebab-case: "
                  "`git branch -m {} claude/<what-changes>`, then "
                  "`git push -u origin claude/<what-changes>`. That rule is the owner's "
                  "standing permission to push under the topic name instead of the "
                  "assigned one; CI fails a pull request from a generated "
                  "branch.".format(name, "; ".join(found), name))
        return 0

    if not name:
        print("Could not tell which branch this is; pass it as an argument.", file=sys.stderr)
        return 2
    found = problems(name)
    if not found:
        print("ok: {}".format(name))
        return 0
    print("Branch {!r} is not named for what it changes:".format(name), file=sys.stderr)
    for p in found:
        print("  - {}".format(p), file=sys.stderr)
    print("Rename it (CLAUDE.md, Working discipline): "
          "git branch -m {} claude/<what-changes>".format(name), file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
