"""DWG to DXF, through LibreDWG's `dwg2dxf`.

DWG is a closed binary format; DXF is its documented text twin, and `ezdxf`
reads DXF. The conversion is GNU LibreDWG (GPL-3.0), run as a separate program:
the service executes it on its own machine and distributes nothing, and a crash
in the converter is a failed subprocess rather than a dead worker.

Decided by the owner on 2026-10-03 over the ODA File Converter (proprietary;
commercial use appears to need an ODA membership) and Autodesk Platform Services
(paid, and every client drawing leaves the deployment). See
`docs/ARCHITECTURE-V2.md` §5.

What was measured on the first real drawing (an AutoCAD 2018 file, 23 MB, eight
layouts, 296 000 model-space entities):

* ASCII DXF, not binary. `dwg2dxf -b` is twice as fast to read back, and it
  truncates every text-style and linetype name to one character and drops every
  block attribute — 0 against 1 088. The text form keeps them.
* `dwg2dxf` reports ~1 700 ERROR lines on that file and exits 0. Nearly all are
  fields the review never reads (`ATTRIB.keep_duplicate_records`, object-class
  versions); the drawing reads back whole. So the exit code and the stderr
  volume are recorded, and success is judged by whether a DXF came out.

Nothing about the drawing's content or its path is logged — stderr is reduced to
counts by kind, because LibreDWG quotes handles and object names in it.

`dwg2dxf` is a C program reading an untrusted file, so it is given a minimal
environment (`_converter_env`) rather than this process's: whatever else the
process holds — an API key, a credentials path — is none of its business.
"""
from __future__ import annotations

import logging
import os
import re
import resource
import shutil
import signal
import subprocess
import time
from collections import Counter
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Dict, Optional

from .source import SourceError

log = logging.getLogger("fbc.cad")

#: Seconds one DWG may take to convert. The 23 MB reference converts in 7 s.
DEFAULT_TIMEOUT_S = 300


class ConversionUnavailable(Exception):
    """No converter is installed: a DWG cannot be read here (a DXF still can)."""


class ConversionFailed(Exception):
    """The converter ran and produced no usable DXF."""


@dataclass
class Conversion:
    dxf_path: str
    seconds: float
    returncode: int
    converter: str
    #: stderr lines by kind (`ERROR: Invalid ATTRIB…` -> "ERROR Invalid ATTRIB"),
    #: never the lines themselves.
    diagnostics: Dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"converter": self.converter, "seconds": round(self.seconds, 2),
                "returncode": self.returncode, "diagnostics": dict(self.diagnostics)}


def binary() -> Optional[str]:
    """The `dwg2dxf` to run: `FBC_DWG2DXF` if set, else the one on PATH."""
    explicit = os.environ.get("FBC_DWG2DXF", "").strip()
    if explicit:
        return explicit if os.access(explicit, os.X_OK) else None
    return shutil.which("dwg2dxf")


#: All of the environment `dwg2dxf` is given: where to find programs and the
#: library it links against, and the locale it reads a file name in.
#: `SYSTEMROOT` only exists on Windows, where nothing starts without it.
_CONVERTER_ENV = ("PATH", "LD_LIBRARY_PATH", "LANG", "LC_ALL", "SYSTEMROOT")


def _converter_env() -> Dict[str, str]:
    return {k: os.environ[k] for k in _CONVERTER_ENV if k in os.environ}


@lru_cache(maxsize=4)
def _version_of(path: str) -> str:
    try:
        out = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=20,
                             env=_converter_env())
    except (OSError, subprocess.SubprocessError):
        return ""
    first = (out.stdout or out.stderr or "").strip().splitlines()
    return first[0].strip() if first else ""


def version() -> str:
    """`dwg2dxf 0.14`, or "" when no converter is installed.

    Part of a converted set's identity: a different converter can read the same
    DWG differently, so readings cached against one must not replay for another.
    """
    b = binary()
    return _version_of(b) if b else ""


def available() -> bool:
    return bool(version())


_KIND = re.compile(r"^(ERROR|Warning)[:\s]+(.*)$")


def _diagnostics(stderr: str) -> Dict[str, int]:
    kinds: Counter = Counter()
    for line in stderr.splitlines():
        m = _KIND.match(line.strip())
        if not m:
            continue
        # The first three words name the problem; the rest is handles and
        # offsets, which identify the drawing and are not ours to log.
        words = re.sub(r"[^A-Za-z_. ]", " ", m.group(2)).split()[:3]
        kinds[f"{m.group(1)} {' '.join(words)}".strip()] += 1
    return dict(kinds.most_common(12))


def _file_size_limit(max_bytes: int):
    """Runs in the converter's process before it starts: it cannot write a
    file past `max_bytes`. A C converter is stopped by SIGXFSZ there; one that
    ignores the signal gets EFBIG. Either way the output stops at the cap."""
    def limit() -> None:
        resource.setrlimit(resource.RLIMIT_FSIZE, (max_bytes, max_bytes))
    return limit


def dwg_to_dxf(dwg_path: str, dxf_path: str, timeout_s: Optional[int] = None,
               max_bytes: Optional[int] = None) -> Conversion:
    """Convert one DWG. Raises ConversionUnavailable or ConversionFailed, or
    SourceError `drawing_too_large` when the DXF would pass `max_bytes`."""
    b = binary()
    if not b:
        raise ConversionUnavailable(
            "DWG conversion is not installed on this deployment. Export the drawing as "
            "DXF (in AutoCAD: SAVEAS, file type DXF) and upload that instead.")
    timeout = timeout_s or int(os.environ.get("FBC_DWG_TIMEOUT_S", DEFAULT_TIMEOUT_S))
    # The converter runs in the output's directory, so a relative path to either
    # file would resolve against the wrong one.
    dwg_path, dxf_path = os.path.abspath(dwg_path), os.path.abspath(dxf_path)
    if os.path.exists(dxf_path):
        os.remove(dxf_path)
    t0 = time.monotonic()
    try:
        proc = subprocess.run([b, "-y", "-o", dxf_path, dwg_path], capture_output=True,
                              text=True, errors="replace", timeout=timeout,
                              cwd=os.path.dirname(dxf_path) or None, env=_converter_env(),
                              preexec_fn=_file_size_limit(max_bytes) if max_bytes else None)
    except subprocess.TimeoutExpired:
        raise ConversionFailed(f"The drawing took longer than {timeout} s to convert and "
                               "was stopped. Purge and audit it in AutoCAD, or export it as "
                               "DXF, and upload it again.")
    except OSError as exc:
        raise ConversionUnavailable(f"The DWG converter could not be started ({exc.strerror}).")
    seconds = time.monotonic() - t0
    diag = _diagnostics(proc.stderr or "")
    size = os.path.getsize(dxf_path) if os.path.exists(dxf_path) else 0
    if max_bytes and (size >= max_bytes or proc.returncode == -signal.SIGXFSZ):
        os.remove(dxf_path)
        log.info("dwg conversion stopped at the size limit",
                 extra={"seconds": round(seconds, 2), "limit_bytes": max_bytes})
        raise SourceError(
            "drawing_too_large",
            f"That drawing converts to more than {max_bytes / 1024 ** 2:,.0f} MB of DXF, beyond "
            "what this service can hold in memory for one review. Purge it, or save the "
            "layouts for this permit to a drawing of their own, and upload that.")
    # A converter killed by a signal (a crash) may still leave a DXF ezdxf can
    # recover — measured: 90% of a sheet set, no layouts, reviewed silently as
    # one model-space drawing. A crash is a failed conversion, whatever it left.
    ok = proc.returncode >= 0 and size > 64
    log.info("dwg converted", extra={"seconds": round(seconds, 2), "returncode": proc.returncode,
                                     "ok": ok, "diagnostic_kinds": len(diag),
                                     "diagnostic_lines": sum(diag.values())})
    if not ok:
        raise ConversionFailed(
            "The drawing could not be converted for reading. It may be damaged, or saved by "
            "software that writes non-standard DWG. Run AUDIT and save it in AutoCAD, or "
            "export it as DXF, and upload it again.")
    return Conversion(dxf_path, seconds, proc.returncode, _version_of(b), diag)
