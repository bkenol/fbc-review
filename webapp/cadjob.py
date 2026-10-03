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
adapter started goes with it.

Nothing the subprocess prints is logged. Its stderr carries layout names, file
names and ezdxf's repair notes, all of them the drawing's content; the log gets
counts and an outcome code.
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


def _kill(proc: subprocess.Popen) -> None:
    """Kill the subprocess and everything it started (the DWG converter)."""
    try:
        if hasattr(os, "killpg"):
            os.killpg(proc.pid, signal.SIGKILL)
        else:                                                  # pragma: no cover — Windows
            proc.kill()
    except (ProcessLookupError, PermissionError, OSError):
        pass


def _run(args: List[str], cwd: Path, timeout: int) -> Tuple[int, str, int, float]:
    """Run `python -m fbcreview.cad ARGS` under the gate.

    Returns (exit code, stdout, stderr line count, seconds). Raises _TimedOut.
    stderr is counted and dropped, never returned: see the module docstring.
    """
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        p for p in (str(_ROOT), env.get("PYTHONPATH", "")) if p)
    cmd = [sys.executable, "-m", "fbcreview.cad", *args]
    with _slots():
        t0 = time.monotonic()
        proc = subprocess.Popen(
            cmd, cwd=str(cwd), env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors="replace",
            # Its own process group, so a kill at the deadline reaches the
            # converter the adapter started as well as the adapter.
            start_new_session=hasattr(os, "killpg"),
        )
        try:
            out, err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill(proc)
            proc.communicate()
            raise _TimedOut()
        return proc.returncode, out or "", len((err or "").splitlines()), time.monotonic() - t0


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
