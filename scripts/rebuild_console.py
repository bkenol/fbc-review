#!/usr/bin/env python3
"""Rebuild Console — a local page with buttons for the local deployment.

    python scripts/rebuild_console.py

Starts a small server on 127.0.0.1, opens a browser at it, and gives you the
whole local deployment as buttons with their output streaming into the page:
Pull, Rebuild, Stop, Container logs, two Publish paths, Config and Doctor. The
status strip says whether the container is running the commit in your working
tree, so "did my rebuild take?" is answered on screen.

WHAT IT WATCHES BESIDES THE BUILD
Mail, the comment assist and GitHub issues are configured in secrets/local.env
and every one of them fails *quietly* when it is not: a review still runs,
feedback still queues, and nothing says otherwise. So the console reports on
that file — which channels have their values, and, separately, whether the
running container agrees. Those are different facts and the gap between them is
the usual failure: the file is edited, nothing is rebuilt, and the container is
still running with the environment it started with.

Names only, never values. This page is served over HTTP on loopback and its log
is scrolled past by whoever is standing there.

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

LAUNCHED LIKE A PROGRAM
`"Rebuild Console.cmd" app-shortcut` puts an icon on the Desktop and in the
Start menu. It opens the console as its own window, with its own icon, and the
console keeps running in the background when that window is closed. Opening the
icon again brings back the console that is already running rather than starting
a second one: it records itself in .console/console.json (git-ignored), and a
new launch that finds a live console for this checkout opens that one and exits.

PERMISSIONS: THE CONSOLE RUNS AS YOU, AND ASKS WHEN IT MUST
Nothing day to day needs an administrator: git, the client build, Docker and
both publish paths all run as the signed-in user. The one thing that does is
the Cloudflare tunnel as a Windows service, which keeps fbc.omniflexfitness.com
up without a window open. Those buttons run scripts/tunnel-service.ps1, which
asks Windows for approval through the usual UAC prompt, does that one job
elevated, and returns.

The console itself is never run elevated, and the shortcut does not ask for it.
A web server that runs commands is the last thing that should hold an
administrator token. Git also refuses a checkout owned by another account when
it runs elevated ("detected dubious ownership"), and every file an elevated
build writes would belong to Administrators, so the next ordinary build could
not replace it.

Docker Desktop does have to be running, which is a separate matter.
"""
from __future__ import annotations

import argparse
import http.server
import json
import os
import re
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
from urllib.parse import parse_qs, quote, urlparse

WINDOWS = os.name == "nt"
ROOT = Path(__file__).resolve().parent.parent

#: What share.ps1 and share.sh both name the container. Kept in one place so
#: Stop and Logs cannot drift from what Rebuild actually starts.
CONTAINER = "fbc-test"

#: How a console recognises another console when it asks one, so the launcher
#: never mistakes some other program on a recorded port for this one.
APP_ID = "meridian-rebuild-console"

#: Where a running console records itself, so the Desktop icon brings that
#: console back instead of starting a second. Git-ignored. Kept out of .devdata,
#: which is the app's own data, because this file holds the console's token.
STATE_DIR = ".console"

#: The console's icon. The shortcut wears it, and the console serves it as the
#: page's favicon so the app window's taskbar button matches the shortcut.
ICON = ROOT / "scripts" / "console-assets" / "rebuild-console.ico"

#: The Windows service `cloudflared service install` creates.
TUNNEL_SERVICE = "Cloudflared"


def _hidden() -> Dict[str, int]:
    """Keyword arguments that stop a child process opening a console window.

    From the Desktop icon the console runs under pythonw.exe, which has no
    console of its own. Windows then gives every console program it starts a
    new window, and the status probes (git, docker, tailscale, sc) run every
    few seconds. CREATE_NO_WINDOW keeps each of those invisible.
    """
    if WINDOWS:
        return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
    return {}

# ── the shared output log ─────────────────────────────────────────────────
# One list, appended by the reader threads and read by the browser at an
# offset. A list of whole lines rather than a byte stream, so the page can ask
# for "everything after line N" and never split a line in half.
_LOG: List[str] = []
_LOG_LOCK = threading.Lock()

#: Colour escapes, removed on the way in.
#
# The scripts these buttons run are written for a terminal and colour their
# output: share.sh has always done it, and setup-secrets.sh is almost entirely
# colour. The page renders text, not a terminal, so an unstripped line arrives
# as `[1msecrets/local.env[0m` — the information is there and it reads like
# corruption. Stripped here rather than at each reader because this is the one
# door every line comes through, including the console's own, where it is a
# no-op.
#
# CSI sequences only. That is what colour and cursor movement use; the exotic
# rest of the standard does not appear in the output of a shell script.
_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


#: Chromium browsers that understand `--app=URL`, most preferred first.
#
# App mode opens the console as its own window: no tab strip, no address bar,
# its own taskbar button and its own icon. For a page that is a control panel
# rather than a document that is the right frame, and it stops the console
# getting lost among thirty tabs.
#
# Only Chromium-family browsers have it. Firefox dropped `-ssb` and Safari's
# equivalent cannot be driven from a command line, so on a machine with neither
# Chrome nor Edge this falls back to the default browser rather than failing.
_APP_BROWSER_PATHS = {
    "nt": (
        r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe",
        r"%ProgramFiles%\Google\Chrome\Application\chrome.exe",
        r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe",
        r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe",
        r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe",
        r"%LOCALAPPDATA%\BraveSoftware\Brave-Browser\Application\brave.exe",
    ),
    "darwin": (
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
    ),
}

#: Names to try on PATH when no install in a known place turned up.
_APP_BROWSER_NAMES = ("google-chrome", "google-chrome-stable", "chromium",
                      "chromium-browser", "microsoft-edge", "chrome")


def app_browser() -> Optional[str]:
    """A Chromium binary that can open a URL as its own window, or None."""
    if WINDOWS:
        candidates = _APP_BROWSER_PATHS["nt"]
    elif sys.platform == "darwin":
        candidates = _APP_BROWSER_PATHS["darwin"]
    else:
        candidates = ()

    for raw in candidates:
        path = os.path.expandvars(raw)
        # An unset variable is left as the literal %NAME% on Windows; that path
        # cannot exist, so the check below discards it without a special case.
        if os.path.isfile(path):
            return path

    for name in _APP_BROWSER_NAMES:
        found = shutil.which(name)
        if found:
            return found
    return None


def open_app_window(url: str, size: str = "1280,900") -> bool:
    """Open `url` as a Chromium app window. False if there is no such browser.

    The user's normal profile is used deliberately — a private `--user-data-dir`
    would give a cleaner window at the price of signing them out of everything
    and starting a second copy of Chrome. When Chrome is already running this
    hands the window to it and returns at once.
    """
    exe = app_browser()
    if not exe:
        return False
    argv = [exe, "--app={}".format(url), "--window-size={}".format(size)]
    try:
        kwargs = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
        if not WINDOWS:
            kwargs["start_new_session"] = True
        subprocess.Popen(argv, **kwargs)
    except OSError:
        return False
    return True


def open_console(url: str, mode: str) -> None:
    """Show the console, by whichever route was asked for."""
    if mode == "none":
        return
    if mode == "app" and open_app_window(url):
        return
    if mode == "app":
        print("No Chrome, Edge or Chromium found for --app mode; using the "
              "default browser instead.")
    try:
        webbrowser.open(url)
    except Exception:  # a headless box has no browser; the URL is printed
        pass


# ── one console per checkout ─────────────────────────────────────────────
# The Desktop icon is meant to behave like a program's: press it and the
# console is there. A second press used to start a second server on another
# port, with its own empty log and no idea what the first one was running.
# Now a console records its URL, and a launch that finds a live one opens it.
def state_file(repo: Path) -> Path:
    return repo / STATE_DIR / "console.json"


def write_state(repo: Path, url: str) -> None:
    """Record this console so the next launch can find it."""
    path = state_file(repo)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        body = json.dumps({"url": url, "pid": os.getpid(), "repo": str(repo)})
        # Owner-only where the platform honours the mode: the URL is the token.
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(body)
    except OSError:
        # A console that cannot record itself still works; the next launch
        # just starts another instead of finding this one.
        pass


def clear_state(repo: Path, url: str) -> None:
    """Remove the record, but only while it is still this console's."""
    path = state_file(repo)
    try:
        if json.loads(path.read_text(encoding="utf-8")).get("url") == url:
            path.unlink()
    except (OSError, ValueError, AttributeError):
        pass


def running_console(repo: Path, timeout: float = 2.0) -> Optional[str]:
    """The URL of a console already serving this checkout, or None.

    The record alone proves nothing: a console ended from Task Manager leaves
    it behind, and its port may since have gone to something else. So the URL
    is asked, with its token, whether it is a Rebuild Console for this same
    repository. Only a loopback http URL is ever followed. The record is a file
    on disk, and whatever is written in it must not make the launcher open a
    page anywhere else.
    """
    try:
        data = json.loads(state_file(repo).read_text(encoding="utf-8"))
        url = str(data.get("url") or "")
    except (OSError, ValueError, AttributeError):
        return None
    parsed = urlparse(url)
    token = (parse_qs(parsed.query).get("token") or [""])[0]
    try:
        port = parsed.port
    except ValueError:
        return None
    if parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or not port or not token:
        return None
    ping = "http://127.0.0.1:{}/api/ping?token={}".format(port, quote(token, safe=""))
    # No proxy: an HTTP_PROXY in the environment must not see loopback calls.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(ping, timeout=timeout) as resp:
            reply = json.loads(resp.read().decode("utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(reply, dict):
        return None
    if reply.get("app") != APP_ID or reply.get("repo") != str(repo):
        return None
    return "http://127.0.0.1:{}/?token={}".format(port, quote(token, safe=""))


def log_write(text: str) -> None:
    with _LOG_LOCK:
        _LOG.append(_ANSI.sub("", text))


def log_since(offset: int) -> Dict[str, object]:
    """Every line after `offset`, and the two ends of what is being returned.

    `from` is the offset actually used - the request's, unless it was out of
    range and got clamped. The page checks it against its own cursor and drops
    anything that does not line up, so a reply that arrives late can never
    append lines a newer reply already appended.
    """
    with _LOG_LOCK:
        if offset < 0 or offset > len(_LOG):
            offset = 0
        return {"from": offset, "offset": len(_LOG),
                "lines": _LOG[offset:], "total": len(_LOG)}


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
                **_hidden(),  # type: ignore[arg-type]
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
    # Inverted on the wire: the scripts take an opt-out flag, the checkbox
    # reads as the positive, and the default when the key is absent is on -
    # which is what an older page or a hand-made request should get.
    training = opts.get("training")
    training = True if training is None else bool(training)
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
        if not training:
            rebuild += ["-NoTraining"]
        tunnel = ps + [str(repo / "scripts" / "tunnel.ps1")]
        # The always-on tunnel. tunnel-service.ps1 asks for administrator
        # approval itself (a UAC prompt) and only for these; the console and
        # everything else it runs stay at the signed-in user's rights.
        service = ps + [str(repo / "scripts" / "tunnel-service.ps1"),
                        "-Port", str(port), "-Action"]
        extra = {
            "svc_install": service + ["Install"],
            "svc_start": service + ["Start"],
            "svc_stop": service + ["Stop"],
            "svc_uninstall": service + ["Uninstall"],
        }
        # --check only. The console never writes a secrets file: creating one
        # is a deliberate act at a prompt, not something a page does because a
        # button was near the cursor.
        config = ps + [str(repo / "scripts" / "setup-secrets.ps1"), "-Check"]
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
        if not training:
            rebuild += ["--no-training"]
        tunnel = ["bash", str(repo / "scripts" / "tunnel.sh")]
        extra = {}
        config = ["bash", str(repo / "scripts" / "setup-secrets.sh"), "--check"]

    return {
        "pull": pull,
        "rebuild": rebuild,
        "tunnel": tunnel,
        "config": config,
        "funnel": funnel,
        "funnel_off": funnel_off,
        "stop": stop,
        "logs": logs,
        # Every Serve and Funnel rule on this machine. One left over from a
        # test publishes whatever answers on its port, so this is offered as a
        # button rather than left to be remembered.
        "funnel_reset": ["tailscale", "funnel", "reset"],
        **extra,
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
            **_hidden(),  # type: ignore[arg-type]
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    for line in (out.stdout or "").splitlines():
        if line.strip():
            return line.strip()
    return ""


# Anything that spawns a process is answered from a short-lived cache, because
# the page polls every 800 ms and every poll asked for all of these twice - once
# for the status strip and again inside guidance(). On Windows that was three
# process spawns per poll (`git describe` and `tailscale status`, twice), which
# on a slow answer took longer than the poll interval itself. See the note on
# the poll loop in PAGE for what overlapping polls then did to the log.
_PROBE_TTL = 4.0
#: Longer, because "is the Docker daemon up" does not flip minute to minute and
#: asking is the most expensive question here.
_DOCKER_TTL = 10.0
_probe_cache: Dict[str, tuple] = {}
_probe_cache_lock = threading.Lock()


def _cached(key: str, ttl: float, produce):
    """Memoise `produce()` under `key` for `ttl` seconds.

    The lock is not held across `produce`, so two callers arriving together can
    both run it. That costs one extra probe and never a wrong answer, which is
    the better trade against blocking a request thread behind a subprocess.
    """
    now = time.monotonic()
    with _probe_cache_lock:
        hit = _probe_cache.get(key)
        if hit is not None and now - hit[0] < ttl:
            return hit[1]
    value = produce()
    with _probe_cache_lock:
        _probe_cache[key] = (time.monotonic(), value)
    return value


def _cloudflared_ready() -> Dict[str, object]:
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


def cloudflared_ready() -> Dict[str, object]:
    return _cached("cloudflared", _PROBE_TTL, _cloudflared_ready)


def _tailscale_ready() -> Dict[str, object]:
    if shutil.which("tailscale") is None:
        return {"ok": False, "why": "not installed"}
    if _probe(["tailscale", "status"]) is None:
        return {"ok": False, "why": "installed, not signed in"}
    return {"ok": True, "why": "signed in"}


def tailscale_ready() -> Dict[str, object]:
    return _cached("tailscale", _PROBE_TTL, _tailscale_ready)


def _docker_ready() -> Dict[str, object]:
    """Whether `docker build` would reach a daemon.

    `docker` on PATH is not the question - Docker Desktop installs the CLI and
    the CLI is perfectly happy while the engine is stopped. Only something that
    talks to the daemon can tell the difference, and until this existed nothing
    on the page could: a rebuild spent a full client build before docker failed
    with a raw named-pipe error, and the guidance panel went on advising the
    Rebuild that had just failed.
    """
    if shutil.which("docker") is None:
        return {"ok": False, "why": "not installed"}
    version = _probe(["docker", "info", "--format", "{{.ServerVersion}}"], 20.0)
    if version is None:
        return {"ok": False, "why": "installed, but the daemon is not running"}
    return {"ok": True, "why": "server {}".format(version) if version else "running"}


def docker_ready() -> Dict[str, object]:
    return _cached("docker", _DOCKER_TTL, _docker_ready)


def parse_sc_query(returncode: int, text: str) -> str:
    """Read `sc query <name>` into one word.

    1060 is ERROR_SERVICE_DOES_NOT_EXIST, which `sc` returns both as its exit
    code and in its text. The state line reads `STATE : 4  RUNNING`.
    """
    if returncode == 1060 or "1060" in (text or ""):
        return "not installed"
    match = re.search(r"STATE\s*:\s*\d+\s+([A-Z_]+)", text or "")
    if not match:
        return "unknown"
    word = match.group(1).upper()
    return {"RUNNING": "running", "STOPPED": "stopped",
            "START_PENDING": "starting", "STOP_PENDING": "stopping"}.get(
                word, word.lower().replace("_", " "))


def _service_state() -> Dict[str, object]:
    """Whether the tunnel's Windows service exists, and whether it runs.

    `sc query` rather than PowerShell: it ships in System32, answers at once,
    and reading a service's state needs no elevation, only changing it does.
    """
    if not WINDOWS:
        return {"supported": False, "state": "n/a"}
    sc = shutil.which("sc.exe") or shutil.which("sc")
    if not sc:
        return {"supported": True, "state": "unknown"}
    try:
        out = subprocess.run(
            [sc, "query", TUNNEL_SERVICE], capture_output=True, text=True,
            timeout=8, check=False, errors="replace",
            **_hidden(),  # type: ignore[arg-type]
        )
    except (OSError, subprocess.SubprocessError):
        return {"supported": True, "state": "unknown"}
    return {"supported": True,
            "state": parse_sc_query(out.returncode, out.stdout + out.stderr)}


def service_state() -> Dict[str, object]:
    return _cached("service", _PROBE_TTL, _service_state)


# ── local configuration ───────────────────────────────────────────────────
# Mail, the comment assist and GitHub issues all read secrets/local.env, and all
# three fail *quietly* without it: a review still runs, feedback still queues,
# and nothing on this page said otherwise. That is exactly the class of problem
# the console exists to make visible, so it reports on the file.
#
# Names, never values. This page is served over HTTP on loopback and its log is
# scrolled past by whoever is standing there; a mail password and an API key
# have no business in either.
#
# The parser is a second copy of the one in webapp/envfile.py, which is a real
# duplication and a deliberate one: this script is standard library only and
# runs outside the virtualenv, on a machine where the venv may not exist yet.
# Importing the service to read a config file would make the console depend on
# the thing it is meant to diagnose. The rule it copies is one line long — split
# on the first `=`, keep the value verbatim — and `webapp/envfile.py` explains
# why it is that and not more.
CONFIG_CHANNELS = (
    ("Mail", ("FBC_SMTP_HOST", "FBC_SMTP_USER", "FBC_SMTP_PASS", "FBC_MAIL_FROM")),
    ("Comment assist", ("ANTHROPIC_API_KEY",)),
    ("Issues", ("FBC_GITHUB_REPO", "FBC_GITHUB_TOKEN")),
)


def local_config(repo: Path) -> Dict[str, object]:
    """Which names secrets/local.env gives a value to, and nothing else."""
    path = repo / "secrets" / "local.env"
    out: Dict[str, object] = {
        "path": str(path), "exists": False, "readable": True,
        "set": [], "quoted": [], "channels": {},
    }
    if not path.is_file():
        return out
    out["exists"] = True
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        out["readable"] = False
        return out

    names: List[str] = []
    quoted: List[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if not key or not value:
            continue
        names.append(key)
        # Docker's --env-file keeps the quotes, so a quoted value is wrong
        # rather than merely untidy. Flagged by name.
        if len(value) > 1 and value[0] == value[-1] and value[0] in "\"'":
            quoted.append(key)

    out["set"] = names
    out["quoted"] = quoted
    out["channels"] = {
        label: all(n in names for n in required) for label, required in CONFIG_CHANNELS
    }
    # An identity-linked API key is refused until the request names a
    # workspace, and the console is the only place that would notice.
    out["workspace_missing"] = (
        "ANTHROPIC_API_KEY" in names and "ANTHROPIC_WORKSPACE_ID" not in names
    )
    return out


#: The container is asked at most this often. The page polls faster than that,
#: and one more loopback request per poll is a cost with no reader.
_MAIL_TTL = 3.0
_mail_seen: Dict[str, object] = {"at": 0.0, "port": 0, "value": None}


def service_mail(port: int) -> Optional[Dict[str, object]]:
    """What the *running container* says about mail, or None if it cannot say.

    The file on disk and the container's environment are different facts, and
    the gap between them is the most common way this goes wrong: the file is
    edited, nothing is rebuilt, and the container is still running with the
    environment it started with. Only asking the container can catch that.
    """
    now = time.monotonic()
    if _mail_seen["port"] == port and now - float(_mail_seen["at"]) < _MAIL_TTL:
        return _mail_seen["value"]  # type: ignore[return-value]

    value: Optional[Dict[str, object]] = None
    if port_open(port):
        try:
            with urllib.request.urlopen(
                "http://127.0.0.1:{}/api/config".format(port), timeout=2
            ) as response:
                body = json.loads(response.read().decode("utf-8"))
            mail = body.get("mail") or {}
            value = {"configured": bool(mail.get("configured")),
                     "status": str(mail.get("status") or "")}
        except urllib.error.HTTPError as exc:
            # 401 is a real answer: sign-in is on, so the console cannot read
            # the config without a token. /admin in a browser can.
            value = {"unauthorised": exc.code in (401, 403)}
        except (urllib.error.URLError, OSError, ValueError):
            value = None

    _mail_seen.update({"at": now, "port": port, "value": value})
    return value


#: The one hostname. Settled in CLAUDE.md; see tests/test_canonical_hostname.py.
CANONICAL_HOST = "fbc.omniflexfitness.com"


def _auth_mode(port: int) -> Dict[str, object]:
    """Whether the running container is asking anyone to sign in.

    Asked of the container rather than read off the file, for the reason
    `service_mail` gives: the file on disk and the environment the container
    actually started with are different facts.

    A `200` from `/api/config` with no `Authorization` header is the whole
    answer — it means the bypass is on, because every route but `/healthz`
    depends on `current_user`.
    """
    if not port_open(port):
        return {"known": False, "why": "nothing is listening on 127.0.0.1:{}".format(port)}
    try:
        request = urllib.request.Request(
            "http://127.0.0.1:{}/api/config".format(port))
        with urllib.request.urlopen(request, timeout=3) as response:
            body = json.loads(response.read().decode("utf-8"))
        return {"known": True, "enforced": False,
                "owner": bool((body.get("training") or {}).get("is_owner"))}
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            return {"known": True, "enforced": True}
        return {"known": False, "why": "HTTP {}".format(exc.code)}
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return {"known": False, "why": str(exc)[:60]}


def facts(repo: Path, port: int) -> None:
    """Write the values you need to operate this service, ready to paste.

    Every one of these has been looked up by hand at least once, out of the
    source, in the middle of doing something else. They do not change, so the
    console can just say them.

    **No secret is printed here.** `local_config` lists which names
    `secrets/local.env` gives a value to and never the values, and this keeps
    that rule: a token or a key on screen is a token or a key in a screenshot.
    What this does instead is tell you whether you need one at all, which is
    the question that actually blocks people.
    """
    rule("facts")

    mode = _auth_mode(port)
    log_write("Sign-in")
    if not mode.get("known"):
        log_write("  Cannot tell - {}".format(mode.get("why", "no answer")))
        log_write("  Start the app, then press Facts again.")
    elif mode.get("enforced"):
        log_write("  ON. Endpoints need an Authorization: Bearer <id-token> header.")
        log_write("  Get a token: sign in at https://{}, then in DevTools".format(CANONICAL_HOST))
        log_write("  open Console and run:")
        log_write("")
        log_write("    await (await import('https://www.gstatic.com/firebasejs/10.12.0/firebase-app.js'), 0)")
        log_write("    // simpler: the app stores it. Read it straight out of IndexedDB:")
        log_write("    (await new Promise(r => { const o = indexedDB.open('firebaseLocalStorageDb');")
        log_write("      o.onsuccess = () => { const q = o.result.transaction('firebaseLocalStorage',")
        log_write("        'readonly').objectStore('firebaseLocalStorage').getAll();")
        log_write("        q.onsuccess = () => r(q.result.find(x =>")
        log_write("          String(x.fbase_key).startsWith('firebase:authUser:'))); }; }))")
        log_write("      ?.value?.stsTokenManager?.accessToken")
        log_write("")
        log_write("  Tokens last one hour. Fetch one immediately before you use it.")
    else:
        log_write("  OFF - FBC_DEV_UNSAFE_AUTH is set and this is not Cloud Run.")
        log_write("  There is no sign-in token to find, and no Authorization header")
        log_write("  on any request, because nothing is asking for one. Commands")
        log_write("  below need no token.")
        log_write("")
        log_write("  !!! This is the local-development bypass. If a tunnel is")
        log_write("  !!! publishing {} right now, every".format(CANONICAL_HOST))
        log_write("  !!! endpoint is open to anyone who knows the address. Stop")
        log_write("  !!! the tunnel, or unset FBC_DEV_UNSAFE_AUTH and rebuild.")

    log_write("")
    log_write("Where things are")
    log_write("  Canonical host      https://{}".format(CANONICAL_HOST))
    log_write("  Local app           http://127.0.0.1:{}".format(port))
    log_write("  GCP/Firebase project  fbc-reviewer")
    log_write("  Cloud Run service     fbc-review (us-east1)")
    log_write("  Firestore collections feedback, markups, calibration")
    log_write("  Repository          {}".format(repo))

    log_write("")
    log_write("Mark one piece of feedback actioned - paste and edit the id")
    log_write("")
    if mode.get("known") and not mode.get("enforced"):
        log_write("curl -sS -X POST \\")
        log_write("  \"https://{}/api/admin/feedback/PASTE_ID/decision\" \\".format(CANONICAL_HOST))
        log_write("  -H \"Content-Type: application/json\" \\")
        log_write("  -d '{\"decision\": \"action\", \"note\": \"Done.\"}'")
    else:
        log_write("curl -sS -X POST \\")
        log_write("  \"https://{}/api/admin/feedback/PASTE_ID/decision\" \\".format(CANONICAL_HOST))
        log_write("  -H \"Authorization: Bearer $TOKEN\" \\")
        log_write("  -H \"Content-Type: application/json\" \\")
        log_write("  -d '{\"decision\": \"action\", \"note\": \"Done.\"}'")
    log_write("")
    log_write("  decision is one of: action, accept, reject.")
    log_write("  accept returns 400 when the triage attached no proposal;")
    log_write("  action is the right one for a confirmation or a gap report.")

    log_write("")
    log_write("Read the queue")
    log_write("")
    log_write("curl -sS \"https://{}/api/admin/feedback\"".format(CANONICAL_HOST))


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
    log_write("Configuration")
    cfg = local_config(repo)
    if not cfg["exists"]:
        line("warn", "secrets/local.env", "absent - mail and the assist are off")
        log_write("      Create it with:")
        log_write("        {}".format(
            "powershell -ExecutionPolicy Bypass -File scripts\\setup-secrets.ps1"
            if WINDOWS else "bash scripts/setup-secrets.sh"))
    elif not cfg["readable"]:
        line("bad", "secrets/local.env", "present but could not be read")
    else:
        line("ok", "secrets/local.env", "{} value(s) set".format(len(cfg["set"])))
        for label, ready in dict(cfg["channels"]).items():
            line("ok" if ready else "warn", "  " + label,
                 "configured" if ready else "not configured - stays inert, quietly")
        if cfg.get("workspace_missing"):
            line("warn", "  ANTHROPIC_WORKSPACE_ID",
                 "unset - required for an identity-linked key")
            log_write("      A workspace key needs nothing here. An identity-linked one")
            log_write("      is refused with 400 until the request names a workspace.")
        if cfg["quoted"]:
            line("bad", "  quoted values",
                 ", ".join(str(n) for n in cfg["quoted"]))
            log_write("      Docker keeps the quotes, so they become part of the value.")

    # The file and the container are different facts, and the gap between them
    # is the usual failure: edited, not rebuilt.
    said = service_mail(port)
    # "says", not "agrees": it reports what the container has, which is not
    # always what the file has and is not always wrong when it differs — a real
    # environment variable outranks this file by design. The one direction
    # worth calling out is the file being ahead of the container.
    if said is None:
        line("warn", "  container says", "cannot ask - nothing is running")
    elif said.get("unauthorised"):
        line("warn", "  container says", "sign-in is on; read /admin in a browser")
    else:
        agrees = bool(said.get("configured"))
        line("ok" if agrees else "warn", "  container says",
             str(said.get("status") or ("mail live" if agrees else "mail off")))
        if dict(cfg["channels"]).get("Mail") and not agrees:
            log_write("      The file has mail set and the container does not have it.")
            log_write("      It reads its environment once, at start: press Rebuild.")

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
            **_hidden(),  # type: ignore[arg-type]
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
            **_hidden(),  # type: ignore[arg-type]
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


# ── what to do next ───────────────────────────────────────────────────────
# The console could already tell you what was wrong; it could not tell you what
# to do about it. Every hint below is derived from what is actually true on this
# machine right now rather than written as general advice, because the two
# machines need different answers to the same question and picking the wrong one
# is what cost the time.
def guidance(repo: Path, port: int, funnelport: int,
             publishing: str) -> List[Dict[str, object]]:
    hints: List[Dict[str, object]] = []
    live = container_version(port)
    occupied = port_open(port)
    cf = cloudflared_ready()
    ts = tailscale_ready()

    # 1. Something else is on the port. Detected as "the port answers but not
    #    with our health payload" - the one state where Rebuild fails with
    #    `port is already allocated` and the reason is invisible.
    if occupied and live is None:
        hints.append({"tone": "bad", "title":
            "Port {} is taken by something that is not this app".format(port),
            "lines": [
                "Rebuild will fail with \"port is already allocated\" until it is freed.",
                "Find what holds it:",
                ("  netstat -ano | findstr :{}" if WINDOWS else "  lsof -i :{}").format(port),
                "Then either stop that process, or change Port above and Rebuild.",
            ]})
        return hints

    # 2. Nothing running - but say *why* before advising the button. Docker
    #    Desktop stopped is the case that reads as a broken script: the client
    #    builds, the image does not, and the error is a named-pipe path. Only
    #    asked when nothing is answering, since a running container is itself
    #    proof the daemon is up.
    if live is None:
        docker = docker_ready()
        if not docker["ok"]:
            if docker["why"] == "not installed":
                hints.append({"tone": "bad", "title": "Docker is not installed",
                    "lines": [
                        "Rebuild needs it to build and run the image.",
                        "  winget install --id Docker.DockerDesktop",
                        "Open a new terminal afterwards - winget only updates PATH",
                        "for new processes.",
                    ]})
            else:
                hints.append({"tone": "bad", "title": "Docker Desktop is not running",
                    "lines": [
                        "Rebuild will build the client, then fail on the image with",
                        "\"failed to connect to the docker API\". Nothing is wrong with",
                        "the checkout.",
                        "",
                        "Start Docker Desktop and wait for the whale to stop animating,",
                        "then press Pull + Rebuild. To confirm it is up:",
                        "  docker info",
                        "",
                        "To have it come up with Windows: Docker Desktop, Settings,",
                        "General, \"Start Docker Desktop when you sign in\".",
                    ]})
            return hints
        hints.append({"tone": "idle", "title": "Nothing is running",
            "lines": ["Press Pull + Rebuild. It installs client dependencies if the",
                      "branch changed them, builds, and replaces the container."]})
        return hints

    # 3. Running. Rebuilding is safe - said explicitly, because "will this kill
    #    what is already up?" is the question that stops people rebuilding.
    hints.append({"tone": "ok", "title": "Rebuilding while it runs is fine",
        "lines": ["Rebuild removes the old container and starts a new one; you do",
                  "not have to stop anything first. Requests fail for the few",
                  "seconds in between, and a publish stays up across it."]})

    # The one configuration failure worth interrupting for: the file says mail
    # is on and the container disagrees. A container reads its environment once,
    # when it starts, so an edit made after that has changed nothing at all —
    # and every symptom of it looks like a wrong password.
    cfg = local_config(repo)
    said = service_mail(port)
    if cfg["exists"] and cfg["readable"]:
        if cfg["quoted"]:
            hints.append({"tone": "bad", "title": "Quoted values in secrets/local.env",
                "lines": [
                    "Docker's --env-file takes quotes literally, so these arrive with",
                    "the quotes attached and will not authenticate:",
                    "  " + ", ".join(str(n) for n in cfg["quoted"]),
                    "Remove them. A value with spaces in it needs no quoting here.",
                ]})
        if (dict(cfg["channels"]).get("Mail") and said is not None
                and not said.get("unauthorised") and not said.get("configured")):
            hints.append({"tone": "warn",
                "title": "The container has not picked up secrets/local.env",
                "lines": [
                    "The file has mail configured; the running container says mail is",
                    "off. A container reads its environment once, when it starts, so",
                    "an edit made since then has changed nothing.",
                    "",
                    "Press Rebuild. Then Config to re-check, and /admin to see what",
                    "the server itself thinks.",
                ]})
        if cfg.get("workspace_missing"):
            hints.append({"tone": "warn",
                "title": "ANTHROPIC_API_KEY is set without a workspace",
                "lines": [
                    "Harmless for a workspace key. An identity-linked key is refused",
                    "with 400 until the request names a workspace, and the assist",
                    "reports that as \"no summary\" — the same as having no key.",
                    "",
                    "Check the Type column at console.anthropic.com/settings/keys. If",
                    "it does not say Workspace, add ANTHROPIC_WORKSPACE_ID.",
                ]})

    if publishing:
        hints.append({"tone": "ok", "title": "Published", "lines": [
            "Press Stop publishing when you are done. Stopping a Tailscale",
            "funnel also clears its rule, so the port is free next time."]})
        return hints

    # 4. The always-on tunnel is up: nothing left to press.
    if service_state().get("state") == "running":
        hints.append({"tone": "ok",
            "title": "The tunnel service is publishing fbc.omniflexfitness.com",
            "lines": [
                "The Cloudflared Windows service serves the hostname whether or",
                "not this console is open, and it starts with Windows. Rebuild",
                "as often as you like; it keeps serving across it.",
                "",
                "Stop service turns it off (Windows asks for approval).",
            ]})
        return hints

    # 5. Not published yet - and this is where the two machines diverge.
    if cf["ok"]:
        lines = ["Press Publish - Cloudflare for fbc.omniflexfitness.com.",
                 "Only one machine can serve that hostname at a time."]
        if WINDOWS:
            lines += ["",
                      "To keep it up without this console open, press Install",
                      "service instead. Windows asks for approval once."]
        hints.append({"tone": "idle", "title": "To publish", "lines": lines})
    elif ts["ok"]:
        hints.append({"tone": "warn",
            "title": "Publish - Cloudflare will not work on this machine",
            "lines": [
                "cloudflared: {}.".format(cf["why"]),
                "The named tunnel belongs to the Cloudflare account, but its",
                "credentials file lives on whichever machine created it - so this",
                "one cannot serve fbc.omniflexfitness.com. That is the guard, not",
                "a fault.",
                "",
                "Use Publish - Tailscale instead. It gives this machine a hostname",
                "of its own. By hand that is:",
                "  tailscale funnel --https={} {}".format(funnelport, port),
                "",
                "If it answers \"listener already exists for port {}\", a Serve rule".format(funnelport),
                "already holds it. Look, then clear or move:",
                "  tailscale serve status",
                "  tailscale serve reset          # clears every rule on this node",
                "or pick another Funnel port above - 443, 8443 and 10000 are the",
                "only three allowed.",
            ]})
    else:
        hints.append({"tone": "warn", "title": "No publish path is set up here",
            "lines": [
                "cloudflared: {}. tailscale: {}.".format(cf["why"], ts["why"]),
                "Either is fine to add:",
                "  winget install --id Tailscale.Tailscale       # own hostname",
                "  winget install --id Cloudflare.cloudflared    # the shared one",
                "Open a new terminal afterwards - winget only updates PATH for",
                "new processes. docs/DEPLOYMENT.md section 0b covers Tailscale",
                "and 0c covers Cloudflare.",
            ]})
    return hints


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
.rowlabel{align-self:center;font:600 11px/1 var(--mono);letter-spacing:.12em;text-transform:uppercase;color:var(--muted);margin-right:4px}
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
#hints{margin-top:12px;display:flex;flex-direction:column;gap:8px}
.hint{border-left:3px solid var(--hairline);padding:8px 0 8px 12px}
.hint.ok{border-left-color:var(--green)}
.hint.warn{border-left-color:var(--ochre)}
.hint.bad{border-left-color:var(--red)}
.hint h2{margin:0 0 4px;font:600 13px/1.4 var(--sans);color:var(--strong)}
.hint pre{margin:0;white-space:pre-wrap;font:12px/1.6 var(--mono);color:var(--muted)}
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
  <button id="config" class="quiet">Config</button>
  <button id="doctor" class="quiet">Doctor</button>
  <button id="facts" class="quiet">Facts</button>
  <button id="cancel" class="quiet">Cancel</button>
  <button id="clear" class="quiet">Clear log</button>
</div>

<div class="row" id="always">
  <span class="rowlabel">Always on</span>
  <button id="svc_install" title="Run the Cloudflare tunnel as a Windows service. Windows asks for approval.">Install service &#x1F6E1;</button>
  <button id="svc_start" title="Windows asks for approval">Start service &#x1F6E1;</button>
  <button id="svc_stop" title="Windows asks for approval">Stop service &#x1F6E1;</button>
  <button id="svc_uninstall" class="quiet" title="Windows asks for approval">Remove service &#x1F6E1;</button>
  <button id="funnel_reset" class="quiet" title="tailscale funnel reset: removes every Serve and Funnel rule on this machine">Clear Funnel rules</button>
</div>

<div class="opts">
  <label>Port <input type="number" id="port" value="8060" min="1" max="65535"></label>
  <label>Upload MB <input type="number" id="upload" value="95" min="1" max="1000"></label>
  <label><input type="checkbox" id="persistent"> Persistent</label>
  <label><input type="checkbox" id="skipbuild"> Skip client build</label>
  <label><input type="checkbox" id="authenticated"> Require sign-in</label>
  <label><input type="checkbox" id="training" checked> Training mode</label>
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
  <span id="svc">Tunnel service &mdash; ?</span>
  <span id="channels">Mail &amp; assist &mdash; ?</span>
</div>
<div id="verdict" class="idle"></div>
<div id="hints"></div>

<div id="log"></div>

<footer>
  <span id="links"></span> &middot;
  <a href="#" id="quit">Shut down this console</a>
</footer>
</div>
<script>
var TOKEN = new URLSearchParams(location.search).get("token") || "";
var offset = 0, logBox = document.getElementById("log"), pinned = true;
// The poll loop runs one request at a time; see poll() for why.
var polling = false, timer = null;
// Hints are rebuilt only when they change; at 800ms a blind rebuild would
// fight the cursor over any text being selected inside one.
var lastHints = "";

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
    funnelport: parseInt(document.getElementById("funnelport").value, 10) || 8443,
    training: document.getElementById("training").checked
  };
}

function run(action) { api("/api/run", { action: action, opts: opts() }).then(pokePoll); }

document.getElementById("all").onclick = function () { run("all"); };
document.getElementById("pull").onclick = function () { run("pull"); };
document.getElementById("rebuild").onclick = function () { run("rebuild"); };
document.getElementById("stop").onclick = function () { run("stop"); };
document.getElementById("logs").onclick = function () { run("logs"); };
document.getElementById("tunnel").onclick = function () { run("tunnel"); };
document.getElementById("funnel").onclick = function () { run("funnel"); };
document.getElementById("config").onclick = function () { run("config"); };
document.getElementById("doctor").onclick = function () { run("doctor"); };
document.getElementById("facts").onclick = function () { run("facts"); };
document.getElementById("cancel").onclick = function () { run("cancel"); };
["svc_install", "svc_start", "svc_stop", "svc_uninstall", "funnel_reset"].forEach(function (id) {
  document.getElementById(id).onclick = function () { run(id); };
});
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
  // One request at a time, always. This used to be a bare setInterval, which
  // fires whether or not the last reply has landed - and a reply carries the
  // lines after the offset the *request* was sent with. Two requests in flight
  // therefore both said "everything after N" and the page appended the same
  // lines twice, which is why every line arrived doubled and tripled whenever
  // a poll ran long. The probes behind /api/status are cached now so that is
  // rarer, but the loop is the thing that made it possible.
  if (polling) { return; }
  polling = true;
  var fp = document.getElementById("funnelport");
  api("/api/status?offset=" + offset + "&port=" +
      (parseInt(document.getElementById("port").value, 10) || 8060) +
      "&funnelport=" + (fp ? fp.value : 8443)).then(function (s) {
    // Belt and braces over the guard above: a reply whose starting point is
    // not where the cursor now sits describes a stretch of log that has already
    // been written, so its lines are dropped rather than repeated.
    if (s.lines && s.lines.length && s.from === offset) {
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
    // Named for the two channels people actually notice missing. Reads the
    // file, not the container - the container's own view is one line further
    // down, in the hints, where it has room to say what to do about it.
    var ch = s.config && s.config.channels ? s.config.channels : null;
    document.getElementById("channels").textContent = "Mail & assist — " +
      (!s.config || !s.config.exists ? "no local.env"
       : !ch ? "unreadable"
       : (ch["Mail"] ? "mail on" : "mail off") + ", " +
         (ch["Comment assist"] ? "assist on" : "assist off"));
    var svc = s.service || {};
    document.getElementById("svc").textContent = "Tunnel service — " + (svc.state || "?");
    // The service row is Windows only; elsewhere it would offer buttons that
    // can only explain why they do nothing.
    document.getElementById("always").style.display = svc.supported === false ? "none" : "";
    var svcRunning = svc.state === "running", svcThere = svc.state && svc.state !== "not installed" && svc.state !== "n/a";
    document.getElementById("svc_install").disabled = s.task_running || !s.cloudflare.ok;
    document.getElementById("svc_start").disabled = s.task_running || !svcThere || svcRunning;
    document.getElementById("svc_stop").disabled = s.task_running || !svcRunning;
    document.getElementById("svc_uninstall").disabled = s.task_running || !svcThere;
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

    var key = JSON.stringify(s.hints || []);
    if (key !== lastHints) {
      lastHints = key;
      var box = document.getElementById("hints");
      box.textContent = "";
      (s.hints || []).forEach(function (h) {
        var card = document.createElement("div");
        card.className = "hint " + (h.tone || "idle");
        var head = document.createElement("h2");
        head.textContent = h.title;
        var body = document.createElement("pre");
        body.textContent = (h.lines || []).join("\\n");
        card.appendChild(head); card.appendChild(body); box.appendChild(card);
      });
    }

    ["all", "pull", "rebuild", "stop", "logs", "config", "doctor"].forEach(function (id) {
      document.getElementById(id).disabled = s.task_running;
    });
    document.getElementById("cancel").disabled = !s.task_running;
    document.getElementById("links").innerHTML =
      '<a href="http://127.0.0.1:' + s.port + '/" target="_blank" rel="noopener">127.0.0.1:' + s.port + '</a>' +
      ' &middot; <a href="https://fbc.omniflexfitness.com" target="_blank" rel="noopener">fbc.omniflexfitness.com</a>';
  }).catch(function () {
    /* the console was shut down; stop shouting about it */
  }).then(function () {
    // Chained, not scheduled independently: the next poll is 800 ms after this
    // one *finished*, so a slow answer stretches the cadence instead of piling
    // requests up behind it.
    polling = false;
    if (timer) { clearTimeout(timer); }
    timer = setTimeout(poll, 800);
  });
}

// Pressing a button should show its first line straight away rather than up to
// 800 ms later, without breaking the one-at-a-time rule.
function pokePoll() {
  if (timer) { clearTimeout(timer); timer = null; }
  if (!polling) { poll(); }
}

poll();
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
            # The console's own icon, so the app window's taskbar button
            # matches the shortcut. Public by nature: it is a picture.
            try:
                self._send(200, ICON.read_bytes(), "image/x-icon")
            except OSError:
                self._send(204, b"", "image/x-icon")
            return
        if not self._authorised(query):
            self._send(403, b"Forbidden", "text/plain; charset=utf-8")
            return
        if parsed.path == "/":
            self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
        elif parsed.path == "/api/status":
            self._json(self._status(query))
        elif parsed.path == "/api/ping":
            # What a second launch asks before reopening this console rather
            # than starting another. Behind the token like everything else.
            self._json({"ok": True, "app": APP_ID, "repo": str(self.repo),
                        "pid": os.getpid()})
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
            if kind == "cloudflare" and service_state().get("state") == "running":
                # The service already serves the hostname. A second connector
                # from this machine only duplicates it, and stopping this one
                # later would look like it unpublished something.
                rule("cloudflare")
                log_write("The Cloudflared service is already publishing")
                log_write("fbc.omniflexfitness.com from this machine, so there is nothing")
                log_write("to start. Stop service turns it off.")
                log_write("[cloudflare not started]")
                return {"ok": False}
            if kind == "cloudflare":
                ready = cloudflared_ready()
                if not ready["ok"]:
                    # Explained before it is attempted. The script's own
                    # preflight says "cloudflared is not on PATH", which is true
                    # and does not say that installing it would not help either.
                    rule("cloudflare")
                    log_write("cloudflared: {}.".format(ready["why"]))
                    log_write("")
                    log_write("The named tunnel belongs to the Cloudflare account, but its")
                    log_write("credentials file lives on whichever machine created it, so this")
                    log_write("machine cannot serve fbc.omniflexfitness.com.")
                    log_write("")
                    log_write("Publish - Tailscale gives this machine a hostname of its own.")
                    log_write("Running the script anyway, so its own preflight is on record:")
            if TUNNEL.start(kind, commands[action], self.repo):
                TUNNEL_KIND = kind
                return {"ok": True}
            return {"ok": False}

        if action in ("pull", "rebuild", "logs", "config", "funnel_reset"):
            return {"ok": TASK.start(action, commands[action], self.repo)}

        if action in ("svc_install", "svc_start", "svc_stop", "svc_uninstall"):
            if action not in commands:
                rule(action)
                log_write("The tunnel service is a Windows service; on this platform")
                log_write("run scripts/tunnel.sh under your init system instead.")
                log_write("[{} not started]".format(action))
                return {"ok": False}
            # The state changes underneath the cache when this finishes;
            # forget it so the strip shows the new state on the next poll.
            with _probe_cache_lock:
                _probe_cache.pop("service", None)
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
        if action == "facts":
            # Reads only, and fast enough not to need the task slot — it must
            # stay usable while a rebuild is running, which is exactly when
            # somebody wants the address to paste somewhere.
            threading.Thread(
                target=facts, args=(self.repo, int(opts.get("port") or 8060)),
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
                **_hidden(),  # type: ignore[arg-type]
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
        try:
            funnelport = int((query.get("funnelport") or ["8443"])[0])
        except ValueError:
            funnelport = 8443
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
            "service": service_state(),
            "config": local_config(self.repo),
            "hints": guidance(self.repo, port, funnelport,
                              TUNNEL_KIND if TUNNEL.running else ""),
        })
        return payload


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", default=str(ROOT), help="repository root")
    parser.add_argument("--port", type=int, default=0,
                        help="console port (0 picks a free one)")
    parser.add_argument("--no-browser", action="store_true",
                        help="do not open a browser (same as --browser none)")
    parser.add_argument("--browser", choices=("default", "app", "none"),
                        default="default",
                        help="'default' uses the system browser; 'app' opens a "
                             "Chrome/Edge window of its own, with no tab strip "
                             "or address bar; 'none' opens nothing")
    parser.add_argument("--new-instance", action="store_true",
                        help="start a console even if one is already running "
                             "for this checkout (the default reopens that one)")
    args = parser.parse_args(argv)

    repo = Path(args.repo).resolve()
    has_ps = (repo / "scripts" / "share.ps1").exists()
    has_sh = (repo / "scripts" / "share.sh").exists()
    if not (has_ps or has_sh):
        print("No scripts/share.* under {}. Pass --repo.".format(repo), file=sys.stderr)
        return 2

    mode = "none" if args.no_browser else args.browser

    # Pressing the icon again brings this console back rather than starting a
    # second one beside it.
    if not args.new_instance:
        existing = running_console(repo)
        if existing:
            print("Rebuild Console is already running: {}".format(existing))
            open_console(existing, mode)
            return 0

    server, url = serve(repo, args.port)
    write_state(repo, url)
    print("Rebuild Console: {}".format(url))
    print("Ctrl-C, or the link in the page, stops it.")
    log_write("Ready. {}".format(repo))

    open_console(url, mode)

    try:
        while not Console.quit_event.wait(0.4):
            pass
    except KeyboardInterrupt:
        pass
    finally:
        clear_state(repo, url)
    TUNNEL.stop()
    TASK.stop()
    server.shutdown()
    return 0


def serve(repo: Path, port: int = 0):
    """Start the console's server on loopback; return it and the page's URL.

    The server runs on a daemon thread. Shutting it down is the caller's job.
    """
    Console.token = secrets.token_urlsafe(24)
    Console.repo = repo
    Console.quit_event = threading.Event()

    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), Console)
    server.daemon_threads = True
    url = "http://127.0.0.1:{}/?token={}".format(server.server_port, Console.token)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, url


if __name__ == "__main__":
    raise SystemExit(main())
