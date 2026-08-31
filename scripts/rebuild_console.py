#!/usr/bin/env python3
"""Rebuild Console — a local page with buttons for the local deployment.

    python scripts/rebuild_console.py

Starts a small server on 127.0.0.1, opens a browser at it, and gives you the
whole local deployment as buttons with their output streaming into the page:
Pull, Rebuild, Stop, Container logs, two Publish paths, and Doctor. The status
strip says whether the container is running the commit in your working tree,
so "did my rebuild take?" is answered on screen.

TWO MACHINES, TWO PUBLISH PATHS
A named Cloudflare tunnel serves fbc.omniflexfitness.com, but its credentials
file lives on whichever machine created it — so exactly one machine can use it,
and on any other it refuses. Tailscale Funnel gives every machine a hostname of
its own, which makes it the answer for the second machine rather than a lesser
substitute. Both are buttons here, Doctor says which of them this machine is
set up for, and the status strip carries that answer without being asked.

WHY PYTHON AND A BROWSER, RATHER THAN A NATIVE WINDOW
The two WinForms attempts before this one both shipped with runtime faults —
the second died on its first line of output — because the environment they were
written in has no Windows and no PowerShell to run them in. This has no such
gap: it is standard-library Python and plain HTML, both of which can be run and
driven end to end before anyone else sees them. Choosing the toolchain that can
be tested is worth more here than choosing the one that is nominally more
native.

Standard library only. No pip install, no new dependency in requirements.txt.

SECURITY
A local server that runs commands is reachable by any page in any browser on
this machine, which is a real cross-site request forgery surface for something
whose whole job is executing scripts. Three things close it:

  * it binds 127.0.0.1 only, so nothing off this machine can reach it at all;
  * every request carries a token minted at startup and passed in the URL, so a
    page that did not come from this process cannot call the API;
  * Host and Origin are checked, which is what stops DNS rebinding — a hostile
    name resolving to 127.0.0.1 arrives with the wrong Host and is refused.

NO ADMINISTRATOR RIGHTS ARE NEEDED. Docker Desktop does have to be running,
which is a separate matter.
"""
from __future__ import annotations

import argparse
import http.server
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import parse_qs, urlparse

WINDOWS = os.name == "nt"
ROOT = Path(__file__).resolve().parent.parent

#: What share.ps1 and share.sh both name the container. Kept in one place so
#: Stop and Logs cannot drift from what Rebuild actually starts.
CONTAINER = "fbc-test"

# ── the shared output log ─────────────────────────────────────────────────
# One list, appended by the reader threads and read by the browser at an
# offset. A list of whole lines rather than a byte stream, so the page can ask
# for "everything after line N" and never split a line in half.
_LOG: List[str] = []
_LOG_LOCK = threading.Lock()


def log_write(text: str) -> None:
    with _LOG_LOCK:
        _LOG.append(text)


def log_since(offset: int) -> Dict[str, object]:
    with _LOG_LOCK:
        if offset < 0 or offset > len(_LOG):
            offset = 0
        return {"offset": len(_LOG), "lines": _LOG[offset:], "total": len(_LOG)}


def log_clear() -> None:
    with _LOG_LOCK:
        _LOG.clear()


def rule(caption: str) -> None:
    log_write("")
    log_write("-- {} {}".format(caption, "-" * max(4, 64 - len(caption))))


# ── one running command ───────────────────────────────────────────────────
class Slot:
    """At most one process, with its output pumped into the shared log.

    Two of these exist: a task slot for pull and rebuild, which run to
    completion, and a tunnel slot which stays up until stopped. They are
    separate because the tunnel has to keep serving while a rebuild happens.
    """

    def __init__(self, name: str) -> None:
        self.name = name
        self._proc: Optional[subprocess.Popen] = None
        self._label = ""
        self._lock = threading.Lock()

    @property
    def running(self) -> bool:
        with self._lock:
            return self._proc is not None and self._proc.poll() is None

    @property
    def label(self) -> str:
        return self._label

    @property
    def pid(self) -> Optional[int]:
        with self._lock:
            return self._proc.pid if self._proc else None

    def start(self, label: str, argv: List[str], cwd: Path) -> bool:
        with self._lock:
            if self._proc is not None and self._proc.poll() is None:
                return False
            rule(label)
            kwargs: Dict[str, object] = {}
            if WINDOWS:
                # No console flash for each child, and its own process group so
                # the whole tree can be killed.
                kwargs["creationflags"] = (
                    getattr(subprocess, "CREATE_NO_WINDOW", 0)
                    | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                )
            else:
                kwargs["start_new_session"] = True
            try:
                proc = subprocess.Popen(
                    argv,
                    cwd=str(cwd),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL,
                    text=True,
                    bufsize=1,
                    errors="replace",
                    **kwargs,  # type: ignore[arg-type]
                )
            except OSError as exc:
                log_write("[could not start {}: {}]".format(label, exc))
                return False
            self._proc = proc
            self._label = label
        threading.Thread(target=self._pump, args=(proc, label), daemon=True).start()
        return True

    def _pump(self, proc: subprocess.Popen, label: str) -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            log_write(line.rstrip("\r\n"))
        proc.wait()
        log_write("[{} finished, exit {}]".format(label, proc.returncode))

    def stop(self) -> bool:
        """Kill the process and everything it started.

        The tree matters: tunnel.ps1 runs tunnel.sh under Git Bash which runs
        cloudflared, so killing only the top process would leave cloudflared
        serving with nothing owning it.
        """
        with self._lock:
            proc = self._proc
            if proc is None or proc.poll() is not None:
                return False
            pid = proc.pid
        if WINDOWS:
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        else:
            import signal

            try:
                os.killpg(os.getpgid(pid), signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                proc.terminate()
        return True


TASK = Slot("task")
TUNNEL = Slot("tunnel")

#: Which publish path the tunnel slot is currently running - "cloudflare",
#: "tailscale" or "". Stopping a Tailscale funnel needs a follow-up `funnel
#: off`, and stopping a Cloudflare one does not, so the slot alone is not
#: enough to know what to do.
TUNNEL_KIND = ""


# ── what each button runs ─────────────────────────────────────────────────
def build_commands(repo: Path, opts: Dict[str, object]) -> Dict[str, List[str]]:
    """The argv for each action, per platform.

    Windows drives the PowerShell scripts, anything else the shell ones. Both
    sets exist in the repository and do the same work; nothing here
    reimplements what they do, so there is no second copy of the procedure to
    drift from the first.
    """
    port = int(opts.get("port") or 8060)
    upload = int(opts.get("upload") or 95)
    persistent = bool(opts.get("persistent"))
    skipbuild = bool(opts.get("skipbuild"))
    # Real Firebase sign-in over the same filesystem stores. Needs
    # FBC_PROJECT_ID and FBC_ALLOWED_EMAILS in the environment and a service
    # account key; both scripts refuse with a named reason rather than starting
    # a container that will not verify a token.
    authenticated = bool(opts.get("authenticated"))
    # Funnel's public port. Tailscale allows only 443, 8443 and 10000, and a
    # machine that already has a Serve rule on 443 rejects a second listener
    # there - which is why this is a choice rather than a constant.
    try:
        funnelport = int(opts.get("funnelport") or 8443)
    except (TypeError, ValueError):
        funnelport = 8443
    if funnelport not in (443, 8443, 10000):
        funnelport = 8443

    pull = ["git", "-C", str(repo), "pull"]

    # Container lifecycle. Named once here rather than in each branch: the
    # docker CLI is the same command on every platform, unlike the scripts.
    stop = ["docker", "rm", "-f", CONTAINER]
    logs = ["docker", "logs", "--tail", "200", CONTAINER]

    # The two publish paths, and why there are two. A named Cloudflare tunnel
    # serves fbc.omniflexfitness.com, but its credentials file lives on the one
    # machine that created it, so a second machine cannot use it. Tailscale
    # Funnel gives every machine a hostname of its own, which is what makes it
    # the answer for the second machine rather than a lesser alternative.
    funnel = ["tailscale", "funnel", "--https={}".format(funnelport), str(port)]
    # Off takes every flag the on command took - a bare `funnel off` does not
    # match a rule created with --https.
    funnel_off = ["tailscale", "funnel", "--https={}".format(funnelport), "off"]

    if WINDOWS:
        ps = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File"]
        rebuild = ps + [str(repo / "scripts" / "share.ps1")]
        if port != 8060:
            rebuild += ["-Port", str(port)]
        if upload != 95:
            rebuild += ["-MaxUploadMb", str(upload)]
        if persistent:
            rebuild += ["-Persistent"]
        if skipbuild:
            rebuild += ["-SkipBuild"]
        if authenticated:
            rebuild += ["-Authenticated"]
        tunnel = ps + [str(repo / "scripts" / "tunnel.ps1")]
    else:
        rebuild = ["bash", str(repo / "scripts" / "share.sh")]
        if port != 8060:
            rebuild += ["--port", str(port)]
        if upload != 95:
            rebuild += ["--max-upload-mb", str(upload)]
        if persistent:
            rebuild += ["--persistent"]
        if skipbuild:
            rebuild += ["--skip-build"]
        if authenticated:
            rebuild += ["--authenticated"]
        tunnel = ["bash", str(repo / "scripts" / "tunnel.sh")]

    return {
        "pull": pull,
        "rebuild": rebuild,
        "tunnel": tunnel,
        "funnel": funnel,
        "funnel_off": funnel_off,
        "stop": stop,
        "logs": logs,
    }


# ── what this machine can actually do ─────────────────────────────────────
# The question this answers is the one that cost the most time setting up the
# second machine: "which of these buttons work here?" A named Cloudflare tunnel
# belongs to the account but its credentials file belongs to one machine, so
# Publish · Cloudflare works on exactly one of them and fails on every other in
# a way that reads like a broken script rather than a deliberate refusal.
def _probe(argv: List[str], timeout: float = 8.0) -> Optional[str]:
    """Run something read-only and return its first line, or None."""
    exe = shutil.which(argv[0])
    if not exe:
        return None
    try:
        out = subprocess.run(
            [exe] + argv[1:], capture_output=True, text=True,
            timeout=timeout, check=False, errors="replace",
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    for line in (out.stdout or "").splitlines():
        if line.strip():
            return line.strip()
    return ""


def cloudflared_ready() -> Dict[str, object]:
    """Present, authorised, and holding credentials for some tunnel.

    All three are needed. cloudflared installed but with no `<uuid>.json` is
    exactly the second machine's situation: the tunnel is real and visible in
    the account, and this machine still cannot serve it.
    """
    if shutil.which("cloudflared") is None:
        return {"ok": False, "why": "not installed"}
    cf = Path.home() / ".cloudflared"
    if not (cf / "cert.pem").exists():
        return {"ok": False, "why": "installed, not logged in (no cert.pem)"}
    creds = [f for f in cf.glob("*.json") if len(f.stem) == 36]
    if not creds:
        return {"ok": False,
                "why": "logged in, but no tunnel credentials on this machine"}
    return {"ok": True, "why": "{} tunnel credential(s)".format(len(creds))}


def tailscale_ready() -> Dict[str, object]:
    if shutil.which("tailscale") is None:
        return {"ok": False, "why": "not installed"}
    if _probe(["tailscale", "status"]) is None:
        return {"ok": False, "why": "installed, not signed in"}
    return {"ok": True, "why": "signed in"}


def doctor(repo: Path, port: int) -> None:
    """Write a prerequisites report into the shared log."""
    def line(state: str, label: str, detail: str = "") -> None:
        mark = {"ok": "  ok  ", "warn": "  !   ", "bad": "  X   "}.get(state, "      ")
        log_write("{}{}{}".format(mark, label.ljust(26), detail))

    rule("doctor")
    log_write("Repository: {}".format(repo))
    log_write("")
    log_write("Build")

    git = _probe(["git", "--version"])
    line("ok" if git else "bad", "git", git or "not installed")

    if shutil.which("docker") is None:
        line("bad", "docker", "not installed - Docker Desktop")
    else:
        info = _probe(["docker", "info", "--format", "{{.ServerVersion}}"], 20.0)
        if info is None:
            line("bad", "docker", "installed but not running - start Docker Desktop")
        else:
            line("ok", "docker", "server {}".format(info))

    node = _probe(["node", "--version"])
    line("ok" if node else "warn", "node", node or "not on PATH (share.* may find nvm's)")

    venv = repo / (".venv/Scripts/python.exe" if WINDOWS else ".venv/bin/python")
    line("ok" if venv.exists() else "warn", "python venv",
         str(venv) if venv.exists() else "absent - run scripts/setup.ps1")

    modules = repo / "web" / "node_modules"
    line("ok" if modules.exists() else "warn", "client dependencies",
         "installed" if modules.exists() else "absent - Rebuild installs them")

    log_write("")
    log_write("Serving")
    live = container_version(port)
    line("ok" if live else "warn", "container",
         live or "nothing on 127.0.0.1:{}".format(port))

    log_write("")
    log_write("Publish")
    cf = cloudflared_ready()
    line("ok" if cf["ok"] else "warn", "Cloudflare tunnel", str(cf["why"]))
    ts = tailscale_ready()
    line("ok" if ts["ok"] else "warn", "Tailscale Funnel", str(ts["why"]))

    log_write("")
    if cf["ok"]:
        log_write("  This machine can serve fbc.omniflexfitness.com.")
        log_write("  Only one machine can, so do not publish from another at")
        log_write("  the same time.")
    elif ts["ok"]:
        log_write("  Publish with Tailscale. This machine holds no Cloudflare")
        log_write("  tunnel credentials, so Publish - Cloudflare will refuse:")
        log_write("  that is the guard, not a fault.")
    else:
        log_write("  No publish path is set up here. Either is fine to add;")
        log_write("  docs/DEPLOYMENT.md section 0b covers Tailscale and 0c")
        log_write("  covers Cloudflare.")
    log_write("[doctor finished]")


# ── status ────────────────────────────────────────────────────────────────
def tree_state(repo: Path) -> Optional[str]:
    """The same `git describe` webapp/version.py stamps with, so the two
    strings are directly comparable and the scheme is not reimplemented."""
    git = shutil.which("git")
    if not git:
        return None
    try:
        out = subprocess.run(
            [git, "-C", str(repo), "describe", "--always", "--dirty",
             "--abbrev=7", "--match="],
            capture_output=True, text=True, timeout=10, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() or None


def port_open(port: int) -> bool:
    """Checked before the HTTP call: on localhost a closed port refuses
    instantly, so the common case during a rebuild costs nothing."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.25)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def container_exists() -> bool:
    """Whether a container by that name is present, running or not.

    `docker ps -aq --filter` prints nothing and exits 0 when there is no
    match, so absence is not an error - which is the whole point of asking
    before removing.
    """
    docker = shutil.which("docker")
    if not docker:
        return False
    try:
        out = subprocess.run(
            [docker, "ps", "-aq", "--filter", "name=^{}$".format(CONTAINER)],
            capture_output=True, text=True, timeout=15, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return bool(out.stdout.strip())


def container_version(port: int) -> Optional[str]:
    if not port_open(port):
        return None
    try:
        with urllib.request.urlopen(
            "http://127.0.0.1:{}/healthz".format(port), timeout=2
        ) as response:
            body = json.loads(response.read().decode("utf-8"))
        version = body.get("version")
        return version if isinstance(version, str) else None
    except (urllib.error.URLError, OSError, ValueError):
        return None


def verdict(tree: Optional[str], live: Optional[str]) -> Dict[str, str]:
    if live is None:
        return {"state": "idle", "text": "Not running. Press Pull + Rebuild."}
    marker = "+local."
    if marker not in live:
        return {"state": "warn",
                "text": "No local stamp - cannot tell which commit is running."}
    live_tree = live.split(marker, 1)[1]
    # version.py writes `local.<sha>` with the hyphen of `--dirty` turned into a
    # dot, so the tree string is normalised the same way before comparing.
    want = tree.replace("-", ".") if tree else None
    if want and live_tree == want:
        if live_tree.endswith(".dirty"):
            return {"state": "warn",
                    "text": "LIVE - matches the tree, which has uncommitted changes."}
        return {"state": "ok", "text": "LIVE - this is the commit in your working tree."}
    return {"state": "bad", "text": "STALE - container is on another commit. Rebuild."}


# ── the page ──────────────────────────────────────────────────────────────
# Meridian's own ink ramp and accents, from web/src/styles/meridian/tokens. A
# system font stack rather than the brand faces: this page is served from
# 127.0.0.1 and must render with no network at all.
PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Meridian Rebuild Console</title>
<style>
:root {
  --ink-1:#16191C; --ink-3:#555B61; --ink-4:#8B9299; --ink-5:#B9C0C7;
  --paper-1:#EEF1F5; --white:#fff; --edge:#D3DAE2;
  --blue:#1F4E9C; --red:#B3251E; --green:#1e6b45; --ochre:#8a5a12;
  --page:var(--paper-1); --card:var(--white); --sunk:#E6EBF1;
  --text:#2B2F33; --strong:var(--ink-1); --muted:var(--ink-3); --faint:var(--ink-4);
  --hairline:var(--edge); --accent:var(--red); --primary:var(--blue); --primary-ink:#fff;
  --mono:"Consolas","SFMono-Regular",Menlo,monospace;
  --sans:"Segoe UI",system-ui,-apple-system,sans-serif;
}
@media (prefers-color-scheme: dark) {
  :root {
    --page:var(--ink-1); --card:#212528; --sunk:#101315;
    --text:var(--ink-5); --strong:var(--paper-1); --muted:var(--ink-4); --faint:#6d757c;
    --hairline:#2f3438; --accent:#e55c4c; --primary:#7D9BC4; --primary-ink:var(--ink-1);
    --green:#5fa97f; --ochre:#c88a2f; --red:#e55c4c;
  }
}
*{box-sizing:border-box}
body{margin:0;background:var(--page);color:var(--text);font:15px/1.5 var(--sans)}
.wrap{max-width:1000px;margin:0 auto;padding:22px 24px 32px}
h1{font:700 22px/1.2 var(--sans);color:var(--strong);margin:6px 0 2px}
.eyebrow{font:11px/1.4 var(--mono);letter-spacing:.18em;text-transform:uppercase;color:var(--muted)}
.repo{font:11px/1.4 var(--mono);color:var(--faint);margin-top:4px}
.row{display:flex;flex-wrap:wrap;gap:8px;margin-top:18px}
button{font:600 13px/1 var(--sans);padding:10px 16px;border:1px solid var(--primary);
  background:var(--card);color:var(--primary);cursor:pointer}
button.filled{background:var(--accent);border-color:var(--accent);color:#fff}
button.quiet{border-color:var(--hairline);color:var(--muted)}
button:disabled{opacity:.4;cursor:default}
button:hover:not(:disabled){filter:brightness(1.08)}
.strip{margin-top:18px;padding-top:14px;border-top:1px solid var(--hairline);
  display:flex;flex-wrap:wrap;gap:6px 28px;font:11px/1.6 var(--mono);color:var(--muted)}
#verdict{margin-top:10px;font:12px/1.5 var(--mono)}
.ok{color:var(--green)} .warn{color:var(--ochre)} .bad{color:var(--red)} .idle{color:var(--faint)}
#log{margin-top:14px;background:var(--sunk);color:var(--text);border:1px solid var(--hairline);
  padding:12px 14px;height:52vh;min-height:260px;overflow:auto;white-space:pre;
  font:12px/1.55 var(--mono)}
.opts{margin-top:16px;font-size:13px;color:var(--muted);display:flex;flex-wrap:wrap;gap:8px 20px;align-items:center}
.opts input[type=number],.opts select{width:88px;font:12px var(--mono);padding:5px 7px;
  background:var(--sunk);color:var(--strong);border:1px solid var(--faint)}
.opts label{display:flex;align-items:center;gap:6px}
a{color:var(--primary)}
footer{margin-top:16px;font:11px/1.5 var(--mono);color:var(--faint)}
</style></head><body><div class="wrap">
<div class="eyebrow">Meridian &middot; Rebuild Console</div>
<h1>Pull, rebuild, publish.</h1>
<div class="repo" id="repo"></div>

<div class="row">
  <button id="all" class="filled">Pull + Rebuild</button>
  <button id="pull">Pull only</button>
  <button id="rebuild">Rebuild only</button>
  <button id="stop">Stop app</button>
  <button id="logs">Container logs</button>
</div>

<div class="row">
  <button id="tunnel">Publish &middot; Cloudflare</button>
  <button id="funnel">Publish &middot; Tailscale</button>
  <button id="doctor" class="quiet">Doctor</button>
  <button id="cancel" class="quiet">Cancel</button>
  <button id="clear" class="quiet">Clear log</button>
</div>

<div class="opts">
  <label>Port <input type="number" id="port" value="8060" min="1" max="65535"></label>
  <label>Upload MB <input type="number" id="upload" value="95" min="1" max="1000"></label>
  <label><input type="checkbox" id="persistent"> Persistent</label>
  <label><input type="checkbox" id="skipbuild"> Skip client build</label>
  <label><input type="checkbox" id="authenticated"> Require sign-in</label>
  <label>Funnel port
    <select id="funnelport">
      <option value="8443" selected>8443</option>
      <option value="443">443</option>
      <option value="10000">10000</option>
    </select>
  </label>
</div>

<div class="strip">
  <span id="tree">Working tree &mdash; ?</span>
  <span id="live">Container &mdash; ?</span>
  <span id="tun">Publishing &mdash; ?</span>
  <span id="paths">Publish paths &mdash; ?</span>
</div>
<div id="verdict" class="idle"></div>

<div id="log"></div>

<footer>
  <span id="links"></span> &middot;
  <a href="#" id="quit">Shut down this console</a>
</footer>
</div>
<script>
var TOKEN = new URLSearchParams(location.search).get("token") || "";
var offset = 0, logBox = document.getElementById("log"), pinned = true;

logBox.addEventListener("scroll", function () {
  pinned = logBox.scrollTop + logBox.clientHeight >= logBox.scrollHeight - 24;
});

function api(path, body) {
  var url = path + (path.indexOf("?") === -1 ? "?" : "&") + "token=" + encodeURIComponent(TOKEN);
  return fetch(url, body ? {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body)
  } : {}).then(function (r) { return r.json(); });
}

function opts() {
  return {
    port: parseInt(document.getElementById("port").value, 10) || 8060,
    upload: parseInt(document.getElementById("upload").value, 10) || 95,
    persistent: document.getElementById("persistent").checked,
    skipbuild: document.getElementById("skipbuild").checked,
    authenticated: document.getElementById("authenticated").checked,
    funnelport: parseInt(document.getElementById("funnelport").value, 10) || 8443
  };
}

function run(action) { api("/api/run", { action: action, opts: opts() }).then(poll); }

document.getElementById("all").onclick = function () { run("all"); };
document.getElementById("pull").onclick = function () { run("pull"); };
document.getElementById("rebuild").onclick = function () { run("rebuild"); };
document.getElementById("stop").onclick = function () { run("stop"); };
document.getElementById("logs").onclick = function () { run("logs"); };
document.getElementById("tunnel").onclick = function () { run("tunnel"); };
document.getElementById("funnel").onclick = function () { run("funnel"); };
document.getElementById("doctor").onclick = function () { run("doctor"); };
document.getElementById("cancel").onclick = function () { run("cancel"); };
document.getElementById("clear").onclick = function () {
  api("/api/clear", {}).then(function () { logBox.textContent = ""; offset = 0; });
};
document.getElementById("quit").onclick = function (e) {
  e.preventDefault();
  api("/api/quit", {}).then(function () {
    document.body.innerHTML = "<div class='wrap'><h1>Console stopped.</h1>" +
      "<p>You can close this tab.</p></div>";
  });
};

function poll() {
  api("/api/status?offset=" + offset).then(function (s) {
    if (s.lines && s.lines.length) {
      logBox.textContent += s.lines.join("\\n") + "\\n";
      offset = s.offset;
      if (pinned) logBox.scrollTop = logBox.scrollHeight;
    }
    document.getElementById("repo").textContent = s.repo;
    document.getElementById("tree").textContent = "Working tree — " + (s.tree || "git unavailable");
    document.getElementById("live").textContent = "Container — " + (s.live || "nothing on 127.0.0.1:" + s.port);
    var kind = s.tunnel_kind === "cloudflare" ? "Cloudflare"
             : s.tunnel_kind === "tailscale" ? "Tailscale" : "";
    document.getElementById("tun").textContent = "Publishing — " +
      (s.tunnel_running ? kind + " (pid " + s.tunnel_pid + ")" : "no");
    document.getElementById("paths").textContent = "Publish paths — Cloudflare " +
      (s.cloudflare.ok ? "yes" : "no") + ", Tailscale " + (s.tailscale.ok ? "yes" : "no");
    var v = document.getElementById("verdict");
    v.textContent = s.verdict.text;
    v.className = s.verdict.state;

    // Whichever is publishing becomes the stop button; the other is disabled
    // rather than hidden, so the pair does not reflow under the cursor.
    var cf = document.getElementById("tunnel"), ts = document.getElementById("funnel");
    cf.textContent = s.tunnel_kind === "cloudflare" ? "Stop publishing" : "Publish · Cloudflare";
    ts.textContent = s.tunnel_kind === "tailscale" ? "Stop publishing" : "Publish · Tailscale";
    cf.disabled = s.tunnel_running && s.tunnel_kind !== "cloudflare";
    ts.disabled = s.tunnel_running && s.tunnel_kind !== "tailscale";
    // Titles carry the reason a path is unavailable; the button still works,
    // because the underlying script explains the refusal better than a
    // greyed-out control does.
    cf.title = s.cloudflare.ok ? "Serves fbc.omniflexfitness.com" : "cloudflared: " + s.cloudflare.why;
    ts.title = s.tailscale.ok ? "Serves this machine's own .ts.net hostname" : "tailscale: " + s.tailscale.why;

    ["all", "pull", "rebuild", "stop", "logs", "doctor"].forEach(function (id) {
      document.getElementById(id).disabled = s.task_running;
    });
    document.getElementById("cancel").disabled = !s.task_running;
    document.getElementById("links").innerHTML =
      '<a href="http://127.0.0.1:' + s.port + '/" target="_blank" rel="noopener">127.0.0.1:' + s.port + '</a>' +
      ' &middot; <a href="https://fbc.omniflexfitness.com" target="_blank" rel="noopener">fbc.omniflexfitness.com</a>';
  }).catch(function () { /* the console was shut down; stop shouting about it */ });
}

poll();
setInterval(poll, 800);
</script></body></html>
"""


# ── the server ────────────────────────────────────────────────────────────
class Console(http.server.BaseHTTPRequestHandler):
    server_version = "MeridianRebuildConsole"
    token = ""
    repo = ROOT
    quit_event: threading.Event

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        pass  # the browser is the log; the terminal stays quiet

    # -- guards ------------------------------------------------------------
    def _authorised(self, query: Dict[str, List[str]]) -> bool:
        """Token, Host and Origin, in that order.

        Host and Origin are what stop DNS rebinding: a hostile name that
        resolves to 127.0.0.1 still arrives carrying its own name, not ours.
        """
        supplied = (query.get("token") or [""])[0]
        if not secrets.compare_digest(supplied, self.token):
            return False
        host = (self.headers.get("Host") or "").split(":")[0]
        if host not in ("127.0.0.1", "localhost"):
            return False
        origin = self.headers.get("Origin")
        if origin:
            parsed = urlparse(origin)
            if parsed.hostname not in ("127.0.0.1", "localhost"):
                return False
        return True

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        # Nothing here should ever be framed or sniffed.
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload: Dict[str, object], code: int = 200) -> None:
        self._send(code, json.dumps(payload).encode("utf-8"), "application/json")

    # -- routes ------------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        # The browser asks for this on its own, without the token, and a 403
        # for it shows up as a console error on an otherwise healthy page.
        # Answering "no content" leaks nothing and keeps the log clean.
        if parsed.path == "/favicon.ico":
            self._send(204, b"", "image/x-icon")
            return
        if not self._authorised(query):
            self._send(403, b"Forbidden", "text/plain; charset=utf-8")
            return
        if parsed.path == "/":
            self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
        elif parsed.path == "/api/status":
            self._json(self._status(query))
        else:
            self._send(404, b"Not found", "text/plain; charset=utf-8")

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        if not self._authorised(query):
            self._send(403, b"Forbidden", "text/plain; charset=utf-8")
            return
        length = int(self.headers.get("Content-Length") or 0)
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}

        if parsed.path == "/api/clear":
            log_clear()
            self._json({"ok": True})
        elif parsed.path == "/api/quit":
            self._json({"ok": True})
            self.quit_event.set()
        elif parsed.path == "/api/run":
            self._json(self._run(payload))
        else:
            self._send(404, b"Not found", "text/plain; charset=utf-8")

    # -- actions -----------------------------------------------------------
    def _run(self, payload: Dict[str, object]) -> Dict[str, object]:
        action = str(payload.get("action") or "")
        opts = payload.get("opts")
        opts = opts if isinstance(opts, dict) else {}
        commands = build_commands(self.repo, opts)

        global TUNNEL_KIND

        if action == "cancel":
            return {"ok": TASK.stop()}

        if action in ("tunnel", "funnel"):
            kind = "cloudflare" if action == "tunnel" else "tailscale"
            if TUNNEL.running:
                # Either button stops whatever is publishing. Pressing the
                # other one while something is up would otherwise start a
                # second publisher for the same port.
                was = TUNNEL_KIND
                TUNNEL.stop()
                TUNNEL_KIND = ""
                if was == "tailscale":
                    # A foreground funnel killed rather than interrupted can
                    # leave its rule behind, and the rule is what holds the
                    # port - so the next start would fail with "listener
                    # already exists". Cleared explicitly.
                    self._funnel_off(commands["funnel_off"])
                return {"ok": True, "stopped": True}
            if TUNNEL.start(kind, commands[action], self.repo):
                TUNNEL_KIND = kind
                return {"ok": True}
            return {"ok": False}

        if action in ("pull", "rebuild", "logs"):
            return {"ok": TASK.start(action, commands[action], self.repo)}

        if action == "stop":
            # Asked before attempted, for the same reason share.ps1 does it:
            # `docker rm` on nothing is an error, and an error here reads as a
            # failure rather than as "there was nothing to stop".
            if not container_exists():
                rule("stop")
                log_write("Nothing named {} is present.".format(CONTAINER))
                log_write("[stop finished, exit 0]")
                return {"ok": True}
            return {"ok": TASK.start("stop", commands["stop"], self.repo)}

        if action == "doctor":
            if TASK.running:
                return {"ok": False}
            threading.Thread(
                target=doctor, args=(self.repo, int(opts.get("port") or 8060)),
                daemon=True,
            ).start()
            return {"ok": True}
        if action == "all":
            # Chained in a thread rather than a shell string, so the rebuild
            # starts only if the pull actually succeeded and neither command
            # has to be quoted into another language.
            if TASK.running:
                return {"ok": False}
            threading.Thread(
                target=self._pull_then_rebuild, args=(commands, self.repo), daemon=True
            ).start()
            return {"ok": True}
        return {"ok": False, "error": "unknown action"}

    @staticmethod
    def _funnel_off(argv: List[str]) -> None:
        """Clear a Tailscale funnel rule after killing its process.

        Short and synchronous - it is one control-plane call, not a server -
        so it does not need the slot machinery, and running it inline keeps
        the "stopped" reply honest about the rule actually being gone.
        """
        exe = shutil.which(argv[0])
        if not exe:
            return
        try:
            out = subprocess.run(
                [exe] + argv[1:], capture_output=True, text=True,
                timeout=15, check=False, errors="replace",
            )
        except (OSError, subprocess.SubprocessError) as exc:
            log_write("[could not clear the funnel rule: {}]".format(exc))
            return
        log_write("[{}]".format(" ".join(argv)))
        for line in (out.stdout + out.stderr).splitlines():
            if line.strip():
                log_write(line.rstrip())

    @staticmethod
    def _pull_then_rebuild(commands: Dict[str, List[str]], repo: Path) -> None:
        if not TASK.start("pull", commands["pull"], repo):
            return
        while TASK.running:
            time.sleep(0.2)
        # The pump has already written the exit line; read it back rather than
        # keeping a second copy of the status.
        with _LOG_LOCK:
            last = _LOG[-1] if _LOG else ""
        if "exit 0]" not in last:
            log_write("[pull failed - not rebuilding]")
            return
        TASK.start("rebuild", commands["rebuild"], repo)

    def _status(self, query: Dict[str, List[str]]) -> Dict[str, object]:
        try:
            offset = int((query.get("offset") or ["0"])[0])
        except ValueError:
            offset = 0
        try:
            port = int((query.get("port") or ["8060"])[0])
        except ValueError:
            port = 8060
        tree = tree_state(self.repo)
        live = container_version(port)
        payload = log_since(offset)
        payload.update({
            "repo": str(self.repo),
            "port": port,
            "tree": tree,
            "live": live,
            "verdict": verdict(tree, live),
            "task_running": TASK.running,
            "task_label": TASK.label,
            "tunnel_running": TUNNEL.running,
            "tunnel_pid": TUNNEL.pid,
            "tunnel_kind": TUNNEL_KIND if TUNNEL.running else "",
            "cloudflare": cloudflared_ready(),
            "tailscale": tailscale_ready(),
        })
        return payload


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", default=str(ROOT), help="repository root")
    parser.add_argument("--port", type=int, default=0,
                        help="console port (0 picks a free one)")
    parser.add_argument("--no-browser", action="store_true",
                        help="do not open a browser")
    args = parser.parse_args(argv)

    repo = Path(args.repo).resolve()
    has_ps = (repo / "scripts" / "share.ps1").exists()
    has_sh = (repo / "scripts" / "share.sh").exists()
    if not (has_ps or has_sh):
        print("No scripts/share.* under {}. Pass --repo.".format(repo), file=sys.stderr)
        return 2

    Console.token = secrets.token_urlsafe(24)
    Console.repo = repo
    Console.quit_event = threading.Event()

    server = http.server.ThreadingHTTPServer(("127.0.0.1", args.port), Console)
    server.daemon_threads = True
    url = "http://127.0.0.1:{}/?token={}".format(server.server_port, Console.token)

    threading.Thread(target=server.serve_forever, daemon=True).start()
    print("Rebuild Console: {}".format(url))
    print("Ctrl-C, or the link in the page, stops it.")
    log_write("Ready. {}".format(repo))

    if not args.no_browser:
        try:
            webbrowser.open(url)
        except Exception:  # a headless box has no browser; the URL is printed
            pass

    try:
        while not Console.quit_event.wait(0.4):
            pass
    except KeyboardInterrupt:
        pass
    TUNNEL.stop()
    TASK.stop()
    server.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
