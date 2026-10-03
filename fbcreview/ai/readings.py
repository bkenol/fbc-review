"""AI readings as data: what the model said about each sheet, stored and replayed.

A `Readings` is the whole output of one AI pass over one file — per sheet, the
`SheetReading` the model returned, plus what failed and what it cost. It is
keyed by what was read (`source_identity`: the file's SHA-256, or for a file
rebuilt from an upload, the upload's plus the rebuild's parameters), the model
and the prompt version, because those three decide what the model saw and what
it was asked.

Everything downstream consumes this, never the API: `build_facts(path,
readings=...)` grounds it against the sheets; the worker stores it with the job
so a re-run replays it; the test suite replays recorded ones. Replaying is what
keeps the review deterministic with a model in it — the same file, the same
readings, the same findings.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

from .schema import SheetReading


def file_sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def source_identity(upload_sha: str, rebuild: Optional[Dict[str, Any]] = None) -> str:
    """What a set of readings is keyed by, so the same input is read once.

    The upload's SHA-256 when the engine reads the upload itself. When it reads
    a file rebuilt from the upload — scanned sheets OCR'd, pasted tables read —
    the rebuilt bytes never repeat, because PyMuPDF writes a fresh document ID
    on every save. The rebuild's inputs do repeat, so the key is the upload
    plus the parameters it was rebuilt with.
    """
    if not rebuild:
        return upload_sha
    params = json.dumps(rebuild, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(f"{upload_sha}|rebuild|{params}".encode("utf-8")).hexdigest()


def plotted_identity(upload_sha: str, sidecar: Dict[str, Any], pdf_path: str) -> str:
    """`source_identity` for a set plotted from a DWG or DXF (`fbcreview/cad`).

    The plotted PDF is rebuilt on every run and its bytes never repeat, so like
    a rebuilt scan it is keyed by the upload plus what made it: the converter
    that actually ran (none for a DXF), the ezdxf and plotter versions — and a
    digest of the text layer the reader is shown. The digest is there because a
    version bump can be forgotten: measured on the reference drawing, one
    plotter edit changed the text layer of every page under an unchanged
    version string, and readings keyed only by the version would have replayed
    against text they were never read from. The words and their boxes are
    stable to the hundredth of a point between plots of the same drawing; the
    page images are not, so they are not hashed.

    Reads the sidecar and the PDF only — no ezdxf, no converter run.
    """
    import pymupdf

    converters = sorted({str((d.get("conversion") or {}).get("converter") or "")
                         for d in sidecar.get("drawings", [])} - {""})
    h = hashlib.sha256()
    with pymupdf.open(pdf_path) as doc:
        for page in doc:
            h.update(f"page {page.number} {round(page.rect.width, 1)} "
                     f"{round(page.rect.height, 1)}\n".encode("utf-8"))
            for x0, y0, x1, y1, word, *_ in page.get_text("words"):
                h.update(f"{x0:.1f},{y0:.1f},{x1:.1f},{y1:.1f},{word}\n".encode("utf-8"))
    return source_identity(upload_sha, {
        "plotted_from": sidecar.get("kind", ""),
        "converters": converters,
        "ezdxf": sidecar.get("ezdxf", ""),
        "render": sidecar.get("render_version", ""),
        "text": h.hexdigest(),
    })


@dataclass
class Readings:
    file_sha256: str
    model: str
    prompt_version: str
    sheets: Dict[int, SheetReading] = field(default_factory=dict)   # 0-based page -> reading
    errors: Dict[int, str] = field(default_factory=dict)            # page -> why it was not read
    usage: Dict[str, int] = field(default_factory=dict)
    #: Filled in by grounding: how many proposals were accepted and rejected.
    grounded: Dict[str, int] = field(default_factory=dict)

    def proposals(self) -> int:
        return sum(len(r.fields) for r in self.sheets.values())

    def transient_errors(self) -> Dict[int, str]:
        """Sheets that were not read for a reason that may not recur.

        A refusal or the sheet limit gives the same answer next time; an API
        error or the deadline may not, so a pass carrying either is not worth
        caching for the next upload of the file. (A re-run still replays it
        from the job's own `readings.json`: that is about the same findings,
        not about completeness.)
        """
        return {p: e for p, e in self.errors.items()
                if e.startswith("failed:") or "deadline" in e}

    def summary(self) -> Dict[str, Any]:
        """JSON-safe and content-free — counts, not sheet text — for job records."""
        return {
            "model": self.model, "prompt_version": self.prompt_version,
            "sheets_read": len(self.sheets), "sheets_failed": len(self.errors),
            "proposals": self.proposals(),
            "accepted": self.grounded.get("accepted", 0),
            "rejected": self.grounded.get("rejected", 0),
            "usage": dict(self.usage),
        }

    def to_json(self) -> Dict[str, Any]:
        return {
            "file_sha256": self.file_sha256, "model": self.model,
            "prompt_version": self.prompt_version,
            "sheets": {str(p): r.model_dump() for p, r in sorted(self.sheets.items())},
            "errors": {str(p): e for p, e in sorted(self.errors.items())},
            "usage": dict(self.usage),
        }

    @classmethod
    def from_json(cls, data: Dict[str, Any]) -> "Readings":
        return cls(
            file_sha256=data.get("file_sha256", ""), model=data.get("model", ""),
            prompt_version=data.get("prompt_version", ""),
            sheets={int(p): SheetReading.model_validate(r)
                    for p, r in (data.get("sheets") or {}).items()},
            errors={int(p): str(e) for p, e in (data.get("errors") or {}).items()},
            usage={k: int(v) for k, v in (data.get("usage") or {}).items()},
        )


def load_readings(path: str) -> Readings:
    with open(path, encoding="utf-8") as fh:
        return Readings.from_json(json.load(fh))


def save_readings(readings: Readings, path: str) -> None:
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(readings.to_json(), fh, indent=1)
    os.replace(tmp, path)


def cache_key(sha: str, model: str, prompt_version: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in "-._" else "_" for ch in f"{model}__{prompt_version}")
    return f"{sha}__{safe}.json"


class ReadingsCache:
    """A directory of readings, one file per (file, model, prompt version).

    The worker points it at local disk; a deployment that wants readings shared
    across instances stores the job's `readings.json` in Cloud Storage beside
    its findings, which is what a re-run reads first.
    """

    def __init__(self, root: str) -> None:
        self.root = Path(root)

    def get(self, sha: str, model: str, prompt_version: str) -> Optional[Readings]:
        p = self.root / cache_key(sha, model, prompt_version)
        if not p.exists():
            return None
        try:
            return load_readings(str(p))
        except (OSError, ValueError):
            return None

    def put(self, readings: Readings) -> None:
        if readings.transient_errors():
            return                                   # the next upload should try those sheets again
        self.root.mkdir(parents=True, exist_ok=True)
        save_readings(readings, str(self.root / cache_key(
            readings.file_sha256, readings.model, readings.prompt_version)))
