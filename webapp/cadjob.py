"""The CAD adapter, run for a review in a process of its own.

A drawing upload is plotted to the PDF the engine reads by `fbcreview.cad`, and
the findings are drawn back into it the same way. Both run here as
`python -m fbcreview.cad …` subprocesses rather than in the worker thread, for
three measured reasons:

* **Memory.** Reading the 23 MB reference DWG peaks at about 1.1 GB. Python does
  not hand that back to the operating system, so in-process it would stay with
  the service for the life of the instance. A subprocess gives it back on exit.
* **Crashes.** The DWG converter is a C program reading a closed binary format.
  If it, or anything under it, dies, a subprocess fails; a worker thread would
  take the service with it.
* **Time.** A subprocess can be killed at a deadline. A thread cannot.

At most `FBC_CAD_CONCURRENCY` of them run at once per instance, each under
`FBC_CAD_TIMEOUT_S`; a later job waits for a slot rather than doubling the
memory. The process group is killed at the deadline, so the converter the
adapter started goes with it — and then reaped. Killing the group orphans the
converter, an orphan is re-parented to PID 1, and in the container PID 1 is
this service (`CMD exec uvicorn`): nothing else would ever collect it, and
every timed-out DWG would leave a zombie in the process table for the life of
the instance.

The subprocess reads an untrusted upload, so it is given only the environment
it needs (`_ADAPTER_ENV`), never the service's: no API key, no credentials
path, no SMTP password. The converter it runs gets less still
(`fbcreview/cad/convert.py`).

Nothing the subprocess prints is logged, or kept. Its stderr carries layout
names, file names and ezdxf's notes on values it could not parse — the values
themselves, the drawing's own text, in any quantity the drawing provokes — so
it is counted as it streams past and dropped: the log gets a line count and an
outcome code. Of stdout, which ends in the one JSON line the adapter prints,
the last 64 KiB are kept.
"""
from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from webapp import errors
from webapp.config import settings

log = logging.getLogger("fbc.cad")

#: Upload formats this module handles; a PDF never comes here.
CAD_FORMATS = ("dwg", "dxf", "zip")

#: The repository root, put on the subprocess's import path so `-m fbcreview.cad`
#: resolves the same package the service runs, whatever the working directory.
_ROOT = Path(__file__).resolve().parent.parent

#: The file names `fbcreview.cad.ingest` writes into its working directory. Kept
#: as literals so the service process need not import the adapter (and ezdxf)
#: just to find two files.
RENDERED_PDF = "rendered.pdf"
SIDECAR = "cad.json"

#: The adapter's refusal codes (`fbcreview/cad/__main__.py`, `source.py`) mapped
#: onto the service's typed codes, so the client matches on a closed set.
_CODES = {
    "unsupported_dwg_version": errors.UNSUPPORTED_CAD,
    "dwg_unavailable": errors.CAD_UNAVAILABLE,
    "dwg_conversion_failed": errors.CORRUPT_CAD,
    "dxf_unreadable": errors.CORRUPT_CAD,
    "corrupt_zip": errors.CORRUPT_CAD,
    "no_sheets": errors.CORRUPT_CAD,
    "zip_too_large": errors.UNSAFE_ARCHIVE,
    "zip_bomb": errors.UNSAFE_ARCHIVE,
    "drawing_too_large": errors.PAYLOAD_TOO_LARGE,
    "no_drawings": errors.UNSUPPORTED_MEDIA,
    "unsupported_media": errors.UNSUPPORTED_MEDIA,
}

_UNREADABLE = (
    "The drawing could not be read. It may be damaged, or saved by software that writes "
    "non-standard DWG or DXF. Run AUDIT and save it in AutoCAD, or export it as DXF, and "
    "upload it again."
)


class CadJobError(Exception):
    """A drawing that cannot be reviewed, with a typed code and prose for the person.

    The message is written for the person who uploaded the drawing. It never
    carries an exception's text, a path or anything the subprocess printed
    beyond the adapter's own refusal prose.
    """

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class Ingested:
    """What the ingest subprocess left in its working directory."""

    pdf: Path
    sidecar: Dict[str, Any]
    seconds: float


# ── the concurrency gate ─────────────────────────────────────────────────────
_gate_lock = threading.Lock()
_gate: Optional[threading.BoundedSemaphore] = None


def _slots() -> threading.BoundedSemaphore:
    """The instance-wide limit on CAD subprocesses, made at first use.

    Process-wide rather than per job: the memory it protects is the instance's.
    Sized once from `FBC_CAD_CONCURRENCY`; a change needs a restart, as every
    other setting does.
    """
    global _gate
    with _gate_lock:
        if _gate is None:
            _gate = threading.BoundedSemaphore(max(1, settings().cad_concurrency))
        return _gate


class _TimedOut(Exception):
    pass


# ── the subprocess's environment ─────────────────────────────────────────────
#: All of the service's environment the adapter is given, by name. A new
#: variable — a new secret most of all — is withheld until somebody decides the
#: adapter needs it; a prefix would hand it over unasked.
_ADAPTER_ENV = (
    # Running a program at all, and reading file names in the right locale.
    # `SYSTEMROOT` only exists on Windows, where Python does not start without it.
    "PATH", "HOME", "TMPDIR", "LANG", "LC_ALL", "LC_CTYPE", "LD_LIBRARY_PATH", "SYSTEMROOT",
    # Where ezdxf keeps its font cache and reads its configuration. The
    # Dockerfile builds the cache under XDG_CACHE_HOME so the first drawing on a
    # fresh instance does not pay for the font scan; without it, every one would.
    "XDG_CACHE_HOME", "XDG_CONFIG_HOME",
    # What the adapter itself reads: `fbcreview/cad/convert.py`, `__init__.py`
    # and `read.py`. `tests/test_cad_service_fixes.py` fails if it reads one
    # that is not here, which would silently never reach it in production.
    "FBC_DWG2DXF", "FBC_DWG_TIMEOUT_S", "FBC_CAD_MAX_DXF_MB", "FBC_CAD_MAX_ENTITIES",
)


def _adapter_env() -> Dict[str, str]:
    env = {k: os.environ[k] for k in _ADAPTER_ENV if k in os.environ}
    # The repository root first, so `-m fbcreview.cad` resolves the package the
    # service runs, whatever the working directory.
    env["PYTHONPATH"] = os.pathsep.join(
        p for p in (str(_ROOT), os.environ.get("PYTHONPATH", "")) if p)
    return env


# ── its output ───────────────────────────────────────────────────────────────
#: The most of the adapter's stdout kept: its end, where its one JSON line is.
#: That line is counts — and, from markup, the page numbers without a DXF, two
#: kilobytes at the 300-sheet cap — so 64 KiB keeps it whole with room to spare.
_STDOUT_KEPT = 64 * 1024
_CHUNK = 64 * 1024


class _Drain(threading.Thread):
    """Read one of the adapter's pipes to its end, keeping its last `keep` bytes
    and a count of its lines, and nothing else.

    A thread rather than a file in the job's directory: on Cloud Run that disk is
    memory, so a file of stderr would cost what the buffer did. Read through to
    the end so the adapter never blocks on a full pipe, and closed by this thread
    when done, since closing a pipe another thread is reading is not safe.
    """

    def __init__(self, stream, keep: int):
        super().__init__(daemon=True)
        self._stream = stream
        self._keep = keep
        self.tail = bytearray()
        #: Lines by newline, an unterminated last one included.
        self.lines = 0

    def run(self) -> None:
        open_line = False
        try:
            while True:
                chunk = self._stream.read1(_CHUNK)
                if not chunk:
                    break
                self.lines += chunk.count(b"\n")
                open_line = not chunk.endswith(b"\n")
                if self._keep:
                    self.tail += chunk
                    if len(self.tail) > self._keep:
                        del self.tail[:-self._keep]
        except (OSError, ValueError):
            pass
        finally:
            self.lines += open_line
            try:
                self._stream.close()
            except OSError:
                pass


# ── ending it, and everything it started ─────────────────────────────────────
#: Seconds to wait, after the kill, for the group to be gone and its pipes shut.
_REAP_SECONDS = 5.0


def _kill(proc: subprocess.Popen) -> None:
    """Kill the subprocess and everything it started (the DWG converter)."""
    try:
        if hasattr(os, "killpg"):
            os.killpg(proc.pid, signal.SIGKILL)
        else:                                                  # pragma: no cover — Windows
            proc.kill()
    except (ProcessLookupError, PermissionError, OSError):
        pass


def _await_exit(proc: subprocess.Popen, timeout: float) -> Optional[bool]:
    """Wait up to `timeout` s for the adapter to exit.

    False at the deadline. True once it has exited but is not yet reaped: its pid
    is still held, so its process group's id cannot have passed to another
    process, and the group can be signalled without risk of hitting a stranger.
    None once it has exited and is already reaped — by the wait itself where the
    platform has no `waitid` — when the group's id may already be another's.
    """
    if not hasattr(os, "waitid"):                              # pragma: no cover — not Linux
        try:
            proc.wait(timeout=timeout)
            return None
        except subprocess.TimeoutExpired:
            return False
    deadline = time.monotonic() + timeout
    delay = 0.0005
    while True:
        try:
            if os.waitid(os.P_PID, proc.pid,
                         os.WEXITED | os.WNOHANG | os.WNOWAIT) is not None:
                return True
        except ChildProcessError:
            return None
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(delay, remaining))
        delay = min(delay * 2, 0.05)


def _reap(proc: subprocess.Popen) -> None:
    """Collect the adapter, then anything of its process group left to this process.

    A converter killed with the adapter has lost its parent, and is re-parented
    to the nearest subreaper or PID 1. In the container that is this service, so
    unless it is collected here it stays a zombie for the life of the instance.
    Elsewhere (a developer's machine, the test suite) it goes to an init that
    reaps, and `waitpid` finds nothing of the group: there is nothing to do.
    """
    try:
        proc.wait(timeout=_REAP_SECONDS)
    except subprocess.TimeoutExpired:                          # pragma: no cover
        log.warning("cad subprocess outlived its kill")
    if not hasattr(os, "killpg"):                              # pragma: no cover — Windows
        return
    deadline = time.monotonic() + _REAP_SECONDS
    while True:
        try:
            pid, _ = os.waitpid(-proc.pid, os.WNOHANG)
        except ChildProcessError:
            return                                   # none of the group is ours
        if pid == 0:                                 # ours, still dying
            if time.monotonic() >= deadline:
                log.warning("cad subprocess group outlived its kill")
                return
            time.sleep(0.01)


def _run(args: List[str], cwd: Path, timeout: int) -> Tuple[int, str, int, float]:
    """Run `python -m fbcreview.cad ARGS` under the gate.

    Returns (exit code, the end of stdout, stderr line count, seconds). Raises
    _TimedOut. stderr is counted and dropped as it arrives, never held or
    returned; of stdout only the last `_STDOUT_KEPT` bytes are kept. See the
    module docstring.
    """
    cmd = [sys.executable, "-m", "fbcreview.cad", *args]
    with _slots():
        t0 = time.monotonic()
        proc = subprocess.Popen(
            cmd, cwd=str(cwd), env=_adapter_env(), stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            # Its own process group, so a kill at the deadline reaches the
            # converter the adapter started as well as the adapter.
            start_new_session=hasattr(os, "killpg"),
        )
        out, err = _Drain(proc.stdout, _STDOUT_KEPT), _Drain(proc.stderr, 0)
        out.start()
        err.start()
        exited = _await_exit(proc, timeout)
        if exited is not None:
            # At the deadline, the adapter and the converter it started. After
            # a clean exit, anything it left running — nothing, normally; a
            # converter, if the adapter itself was killed from outside.
            _kill(proc)
        _reap(proc)
        for drain in (out, err):
            drain.join(_REAP_SECONDS)
        if exited is False:
            raise _TimedOut()
        stdout = bytes(out.tail).decode("utf-8", errors="replace")
        return proc.returncode, stdout, err.lines, time.monotonic() - t0


def _last_json(stdout: str) -> Optional[Dict[str, Any]]:
    """The one JSON object the adapter prints, which it prints last."""
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                value = json.loads(line)
            except ValueError:
                return None
            return value if isinstance(value, dict) else None
    return None


def _refusal(payload: Optional[Dict[str, Any]]) -> Optional[CadJobError]:
    if not payload or "error" not in payload:
        return None
    code = _CODES.get(str(payload.get("error")), errors.CORRUPT_CAD)
    message = str(payload.get("message") or "").strip()[:600] or _UNREADABLE
    return CadJobError(code, message)


# ── ingest ───────────────────────────────────────────────────────────────────
def ingest(upload: Path, out_dir: Path, name: str, job_id: str) -> Ingested:
    """Plot a drawing upload to `out_dir/rendered.pdf` with its sidecar beside it.

    `name` is the upload's file name, which becomes the drawing's name inside
    the sidecar and the marked-up DXF; it is passed to the subprocess and never
    logged. Raises CadJobError for every way this can fail.
    """
    timeout = settings().cad_timeout_seconds
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        code, stdout, err_lines, seconds = _run(
            # `--name=` in one argument: a file called `--help.dwg` is a name, not a flag.
            ["ingest", str(upload.resolve()), str(out_dir.resolve()), f"--name={name}"],
            cwd=out_dir, timeout=timeout)
    except _TimedOut:
        log.warning("cad ingest stopped", extra={"job_id": job_id, "timeout_seconds": timeout})
        raise CadJobError(
            errors.CORRUPT_CAD,
            f"Reading the drawing took longer than {timeout // 60} minutes and was stopped. "
            "Purge and audit it in AutoCAD, or upload only the sheets for this permit, "
            "and try again.")
    except OSError as exc:
        log.error("cad ingest could not start", extra={"job_id": job_id,
                                                       "error": type(exc).__name__})
        raise CadJobError(errors.CORRUPT_CAD, _UNREADABLE)

    payload = _last_json(stdout)
    log.info("cad ingest finished", extra={
        "job_id": job_id, "exit_code": code, "seconds": round(seconds, 2),
        "stderr_lines": err_lines, "pages": (payload or {}).get("pages")})

    refusal = _refusal(payload) if code == 2 else None
    if refusal is not None:
        raise refusal

    pdf, sidecar_path = out_dir / RENDERED_PDF, out_dir / SIDECAR
    if code != 0 or payload is None or not pdf.is_file() or not sidecar_path.is_file():
        raise CadJobError(errors.CORRUPT_CAD, _UNREADABLE)
    try:
        sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise CadJobError(errors.CORRUPT_CAD, _UNREADABLE)
    if not isinstance(sidecar, dict):
        raise CadJobError(errors.CORRUPT_CAD, _UNREADABLE)
    return Ingested(pdf=pdf, sidecar=sidecar, seconds=seconds)


# ── the marked-up drawing ────────────────────────────────────────────────────
def markup(cad_dir: Path, findings_json: Path, out_zip: Path, name: str,
           job_id: str) -> Optional[Dict[str, Any]]:
    """Draw the findings into the drawing as zipped DXF at `out_zip`.

    Returns the adapter's counts, or None when it could not be written. Never
    raises: the marked-up PDF is the review of record, and a DXF that failed
    makes a smaller delivery, not a failed review.
    """
    timeout = settings().cad_timeout_seconds
    try:
        code, stdout, err_lines, seconds = _run(
            ["markup", str(cad_dir.resolve()), str(findings_json.resolve()),
             str(out_zip.resolve()), f"--name={name}"],
            cwd=cad_dir, timeout=timeout)
    except _TimedOut:
        log.warning("cad markup stopped", extra={"job_id": job_id, "timeout_seconds": timeout})
        return None
    except Exception as exc:                                   # noqa: BLE001 — never fatal
        log.warning("cad markup could not start", extra={"job_id": job_id,
                                                          "error": type(exc).__name__})
        return None

    payload = _last_json(stdout)
    ok = code == 0 and payload is not None and "error" not in payload and out_zip.is_file()
    counts = {k: payload.get(k) for k in ("drawings", "placed", "listed", "bytes")} \
        if ok and payload else {}
    log.info("cad markup finished", extra={
        "job_id": job_id, "exit_code": code, "ok": ok, "seconds": round(seconds, 2),
        "stderr_lines": err_lines, **counts})
    return payload if ok else None


# ── what the job record keeps ────────────────────────────────────────────────
#: Bounds on the warnings copied onto the job record, whose document is capped
#: at 1 MiB. A zip of a hundred drawings each missing an xref would otherwise
#: write a page of prose per job.
_MAX_WARNINGS = 20
_MAX_WARNING_CHARS = 300


def report(sidecar: Dict[str, Any], kind: str, seconds: float, claims: int = 0) -> Dict[str, Any]:
    """The `models.CadReport` for a job record: counts, and warnings in prose.

    Counts only — no layer names, attribute text or block inventory — because
    this is stored on the job document beside the summary.
    """
    pages = [p for p in sidecar.get("pages") or [] if isinstance(p, dict)]
    records = [r for r in sidecar.get("claims") or [] if isinstance(r, dict)]
    warnings = [str(w)[:_MAX_WARNING_CHARS] for w in (sidecar.get("warnings") or [])]
    if len(warnings) > _MAX_WARNINGS:
        more = len(warnings) - _MAX_WARNINGS
        warnings = warnings[:_MAX_WARNINGS] + [f"… and {more} more."]
    return {
        "format": kind if kind in CAD_FORMATS else (
            sidecar.get("kind") if sidecar.get("kind") in CAD_FORMATS else "dxf"),
        "drawings": len(sidecar.get("drawings") or []),
        "sheets": len(pages),
        "sheets_identified": sum(1 for p in pages if p.get("number")),
        "layers": len(sidecar.get("layers") or []),
        "viewports": sum(len(p.get("viewports") or []) for p in pages),
        "attributes": sum(1 for r in records if r.get("type") == "attribute"),
        "dimensions": sum(1 for r in records if r.get("type") == "dimension"),
        "claims": int(claims),
        "converter": str(sidecar.get("converter") or ""),
        "render_version": str(sidecar.get("render_version") or ""),
        "seconds": round(float(seconds), 2),
        "warnings": warnings,
    }
