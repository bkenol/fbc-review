"""`scripts/tunnel.sh` against a stand-in `cloudflared`.

The script's one piece of parsing is reading a tunnel's UUID out of
`cloudflared tunnel list`, and the first version of it got that wrong in a way
no amount of re-reading would have caught: it parsed `--output json` and
discarded any row with a `deleted_at`, not knowing that cloudflared is written
in Go and Go marshals a zero timestamp as "0001-01-01T00:00:00Z" rather than
null. Every live tunnel therefore looked deleted, and a freshly created one
came back as "could not read its UUID back".

The listing below is captured verbatim from a real cloudflared 2026.8.2 on
Windows, which is the only reason this test is worth anything — a stub written
from imagination is what let the bug through in the first place.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "tunnel.sh"

UUID = "e0533418-4e40-4e12-83ad-80f1d01de6fc"

REAL_LISTING = (
    "You can obtain more detailed information for each tunnel with "
    "`cloudflared tunnel info <name/uuid>`\n"
    "ID                                   NAME       CREATED              CONNECTIONS\n"
    f"{UUID} fbc-review 2026-08-24T01:47:51Z             \n"
)

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None, reason="needs bash (Git Bash on Windows)"
)


class _Health(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler's spelling
        body = json.dumps(
            {"ok": True, "service": "fbc-review", "version": "1.0.0", "auth_required": False}
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def app_port():
    """A stand-in for the running container, so preflight passes."""
    server = HTTPServer(("127.0.0.1", 0), _Health)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield server.server_port
    finally:
        server.shutdown()


def _stub_cloudflared(bin_dir: Path, listing: str) -> None:
    bin_dir.mkdir(parents=True, exist_ok=True)
    stub = bin_dir / "cloudflared"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        'if [ "$1" = "--version" ]; then echo "cloudflared version 2026.8.2"; exit 0; fi\n'
        "shift\n"
        'if [ "$1" = "--config" ]; then CFG="$2"; shift 2; fi\n'
        'case "$1" in\n'
        f"  list) cat <<'LISTING'\n{listing}LISTING\n    ;;\n"
        '  ingress) [ -f "$CFG" ] || exit 9 ;;\n'
        "  route) echo 'Added CNAME' ;;\n"
        '  run) echo "RAN with $CFG" ;;\n'
        "esac\n"
        "exit 0\n",
        newline="\n",
    )
    stub.chmod(0o755)


def _stub_cygpath(bin_dir: Path) -> None:
    r"""Git Bash's path translator, faithful enough to drive the Windows branch.

    Mirrors what MSYS does: a /<drive>/ prefix becomes <DRIVE>:\, anything else
    absolute is mapped under C:\, and separators flip to backslashes.
    """
    stub = bin_dir / "cygpath"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s' \"$2\" "
        "| sed -E 's#^/([a-zA-Z])/#\\1:/#; t; s#^/#C:/#' "
        "| tr '/' '\\\\'\n",
        newline="\n",
    )
    stub.chmod(0o755)


def _run(tmp_path: Path, port: int, listing: str = REAL_LISTING, cygpath: bool = False):
    home = tmp_path / "home"
    (home / ".cloudflared").mkdir(parents=True)
    (home / ".cloudflared" / "cert.pem").write_text("stub")
    (home / ".cloudflared" / f"{UUID}.json").write_text("{}")

    bin_dir = tmp_path / "bin"
    _stub_cloudflared(bin_dir, listing)
    if cygpath:
        _stub_cygpath(bin_dir)

    env = dict(os.environ)
    env.update(
        HOME=str(home),
        PATH=f"{bin_dir}{os.pathsep}{env['PATH']}",
        FBC_PORT=str(port),
    )
    proc = subprocess.run(
        ["bash", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=120
    )
    return proc, home


def test_uuid_is_read_from_a_real_cloudflared_listing(tmp_path, app_port):
    proc, home = _run(tmp_path, app_port)

    assert proc.returncode == 0, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    assert UUID in proc.stdout
    assert "could not read its UUID back" not in proc.stdout + proc.stderr

    config = (home / ".cloudflared" / "fbc-review.yml").read_text()
    assert f"tunnel: {UUID}" in config
    assert f"service: http://127.0.0.1:{app_port}" in config
    assert "hostname: fbc.omniflexfitness.com" in config


def test_the_go_zero_timestamp_does_not_hide_a_live_tunnel(tmp_path, app_port):
    """The regression proper.

    Whatever the listing carries alongside the row, a live tunnel must be found.
    This is the shape that broke it: a zero `deleted_at` that is a string, not
    null, and therefore truthy to anything checking it for emptiness.
    """
    listing = (
        "ID                                   NAME       CREATED              DELETED_AT\n"
        f"{UUID} fbc-review 2026-08-24T01:47:51Z 0001-01-01T00:00:00Z\n"
    )
    proc, home = _run(tmp_path, app_port, listing)

    assert proc.returncode == 0, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    assert f"tunnel: {UUID}" in (home / ".cloudflared" / "fbc-review.yml").read_text()


def test_it_refuses_to_publish_a_hostname_that_fronts_nothing(tmp_path):
    """Preflight, which is the difference between a failed run and a dead site."""
    home = tmp_path / "home"
    (home / ".cloudflared").mkdir(parents=True)
    bin_dir = tmp_path / "bin"
    _stub_cloudflared(bin_dir, REAL_LISTING)

    env = dict(os.environ)
    env.update(
        HOME=str(home),
        PATH=f"{bin_dir}{os.pathsep}{env['PATH']}",
        FBC_PORT="9",  # discard; nothing serves HTTP there
    )
    proc = subprocess.run(
        ["bash", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=120
    )

    assert proc.returncode != 0
    assert "Nothing is answering" in proc.stderr
    assert not (home / ".cloudflared" / "fbc-review.yml").exists()


def test_the_credentials_path_is_written_in_windows_form_under_git_bash(tmp_path, app_port):
    """The second bug this script hit on a real machine.

    MSYS rewrites POSIX-looking paths in arguments, so `--config /c/Users/...`
    reaches cloudflared as a Windows path and works. It does not touch the
    inside of a file, so an MSYS path written into the YAML arrived verbatim
    and cloudflared reported:

        Tunnel credentials file '/c/Users/.../<uuid>.json' doesn't exist

    while bash's own `[ -f ]` on the same string succeeded, because bash
    understands that form and the native binary does not.
    """
    _, home = _run(tmp_path, app_port, cygpath=True)
    config = (home / ".cloudflared" / "fbc-review.yml").read_text()

    line = next(l for l in config.splitlines() if l.startswith("credentials-file:"))

    # Quoted, so YAML keeps the backslashes literal rather than reading escapes.
    assert line.startswith("credentials-file: '") and line.endswith("'"), line

    path = line[len("credentials-file: '") : -1]
    assert re.match(r"^[A-Za-z]:\\", path), path      # a drive-letter root
    assert "/" not in path, path                        # no MSYS form survives
    assert path.endswith(f"{UUID}.json"), path


def test_without_cygpath_the_path_is_left_alone(tmp_path, app_port):
    """Linux and macOS have no cygpath and need no translation."""
    _, home = _run(tmp_path, app_port, cygpath=False)
    config = (home / ".cloudflared" / "fbc-review.yml").read_text()

    line = next(l for l in config.splitlines() if l.startswith("credentials-file:"))
    assert str(home / ".cloudflared" / f"{UUID}.json") in line, line
