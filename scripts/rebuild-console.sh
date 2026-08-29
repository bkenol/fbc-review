#!/usr/bin/env bash
#
# Open the Rebuild Console — the Git Bash and POSIX entry point.
#
#   bash scripts/rebuild-console.sh
#
# Does the same thing as "Rebuild Console.cmd" in the repository root, which is
# what the Desktop shortcut points at. This exists because that file's name has
# a space in it and its path is usually written with backslashes, and Git Bash
# eats an unquoted backslash as an escape:
#
#   git -C C:\Antigravity\fbc-review pull
#   fatal: cannot change to 'C:Antigravityfbc-review'
#
# A POSIX path with no spaces cannot go wrong that way.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Prefer the project virtualenv, on either layout, then whatever is on PATH.
PY="$ROOT/.venv/Scripts/python.exe"
[ -x "$PY" ] || PY="$ROOT/.venv/bin/python"
if [ ! -x "$PY" ]; then
  if command -v python3 >/dev/null 2>&1; then
    PY="python3"
  elif command -v python >/dev/null 2>&1; then
    PY="python"
  else
    printf '\n\033[31mPython was not found, and there is no .venv in %s\033[0m\n\n' "$ROOT" >&2
    exit 1
  fi
fi

# Foreground on purpose: this shell is already open, so it is the natural place
# for the URL and for Ctrl-C to stop the server. The .cmd uses pythonw instead,
# because a double-click has no console to print into.
exec "$PY" "$ROOT/scripts/rebuild_console.py" "$@"
