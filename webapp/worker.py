"""The review worker.

Downloads the upload to the instance's local disk, runs the existing pipeline
against local paths exactly as the CLI does, uploads the two artefacts, and
deletes every local copy on the way out.

`fbcreview/` stays unaware that Cloud Storage exists. That is deliberate: the
engine is regression-tested against local files and making it storage-aware
would put a network call inside the deterministic path.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
import time
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional

from fbcreview.ai.readings import (Readings, ReadingsCache, file_sha256, save_readings,
                                   source_identity)
from fbcreview.ai.reader import ReaderConfig
from fbcreview.declaration import ProjectDeclaration
from fbcreview.options import ReviewOptions
from fbcreview.payload import findings_payload
from fbcreview.pipeline import build_facts
from fbcreview.render.markup import render
from fbcreview.rules import ACTIONABLE, registered, run_all
from webapp import calibration, convert, mailer, storage
from webapp.calibration import CalibrationProfile
from webapp.jobs import JobStore

log = logging.getLogger("fbc.worker")

STAGES = [
    "Reading the PDF",
    "Extracting schedules and code data",
    "Running rules",
    "Rendering the markup",
    "Delivering",
]

CONVERT_STAGE = "Rebuilding scanned sheets"
AI_STAGE = "Reading sheets with AI"

#: The file a review's AI readings are stored under, beside findings.json. A
#: re-run of the same upload replays it rather than paying for the reading again,
#: which is also what keeps a re-run's findings identical to the first run's.
READINGS = "readings.json"


def ai_config() -> Optional[ReaderConfig]:
    """The AI reader's configuration, or None when this deployment has it off."""
    return ReaderConfig.from_env()


def stages_for(convert_raster: bool, ai_reading: Optional[bool] = None) -> List[str]:
    """The stage list a given job will actually move through.

    A job that is not rebuilding scanned sheets, or not reading them with AI,
    should not display a stage that does nothing, so the list is per job rather
    than global. The AI stage follows the rebuild: the model reads the same file
    the engine grounds its readings against, OCR text included.
    """
    if ai_reading is None:
        ai_reading = ai_config() is not None
    out = [STAGES[0]]
    if convert_raster:
        out.append(CONVERT_STAGE)
    if ai_reading:
        out.append(AI_STAGE)
    return [*out, *STAGES[1:]]


def sheet_index(sheets) -> List[Dict[str, Any]]:
    """The set's sheets in page order, for the viewer's navigator.

    `fbcreview.extract.document` gives a sheet whose number could not be read
    the code `p{n}`, which is how a rule reports it and how the register prints
    it. That convention is preserved rather than translated — the client shows
    the same string the marked-up PDF does — and `read` carries the distinction
    so the navigator can say "sheet number not read" instead of implying the
    drawing is called p7.
    """
    out: List[Dict[str, Any]] = []
    for s in sheets:
        page = s.index + 1
        code = s.code or f"p{page}"
        out.append({
            "page": page,
            "code": code,
            "title": s.title or "",
            "discipline": s.discipline or "",
            "read": code != f"p{page}",
        })
    return out


def run_review(
    *,
    job_id: str,
    uid: str,
    email: str,
    filename: str,
    upload_blob: str,
    options: ReviewOptions,
    store: JobStore,
    declaration: Optional[ProjectDeclaration] = None,
    store_files: "storage.Storage",
    convert_raster: bool = False,
    raster_pages: Optional[List[int]] = None,
    raster_regions: Optional[Dict[int, List[Any]]] = None,
    profile: Optional[CalibrationProfile] = None,
    rerun_of: Optional[str] = None,
) -> None:
    """Executed on a worker thread. Never raises — every failure is recorded
    on the job document instead, because nothing is waiting on the return."""
    started = time.monotonic()
    workdir = Path(tempfile.mkdtemp(prefix=f"fbc-{job_id}-"))
    pages = 0
    step = 0

    def advance() -> int:
        nonlocal step
        step += 1
        store.mark_stage(job_id, step)
        return step

    try:
        store.mark_running(job_id)

        # ── fetch ──────────────────────────────────────────────────────────
        src = workdir / "source.pdf"
        store_files.download_to(upload_blob, str(src))
        upload = src

        # ── rebuild unreadable sheets, when asked ──────────────────────────
        if convert_raster and (raster_pages or raster_regions):
            advance()
            report = None

            # Whole scanned sheets: re-render, OCR and trace.
            if raster_pages:
                rebuilt = workdir / "converted.pdf"
                report = convert.convert(str(src), raster_pages, str(rebuilt))
                if rebuilt.exists() and rebuilt.stat().st_size > 0:
                    src = rebuilt

            # Readable sheets that paste their code table in as a picture. Far
            # more common than a wholly scanned set, and invisible to a
            # whole-sheet check — the sheet is genuinely vector, the table is
            # not. Only the pasted regions are read, and the vector content is
            # left untouched.
            if raster_regions:
                read = workdir / "regions.pdf"
                region_report = convert.read_regions(str(src), raster_regions, str(read))
                if read.exists() and read.stat().st_size > 0:
                    src = read
                report = _merge_reports(report, region_report)

            if report is not None:
                store.update(job_id, conversion=report.to_dict())

        # ── AI reading, when this deployment has it on ─────────────────────
        # Never the review's failure: `_ai_readings` returns None on any error
        # and the deterministic reader has the set either way.
        readings = None
        ai = ai_config()
        if ai is not None:
            advance()
            rebuild = None if src == upload else {
                "raster_pages": sorted(int(p) for p in raster_pages or []),
                "raster_regions": {str(p): [[round(float(v), 2) for v in r] for r in rs]
                                   for p, rs in sorted((raster_regions or {}).items())},
            }
            readings = _ai_readings(str(src), str(upload), rebuild, ai, job_id, rerun_of,
                                    store_files, workdir)
            if readings is not None:
                store.update(job_id, ai_reading=readings.summary())

        # ── extract ────────────────────────────────────────────────────────
        advance()
        facts = build_facts(str(src), readings=readings)
        pages = len(facts.sheets)

        # ── rules ──────────────────────────────────────────────────────────
        advance()
        result = run_all(facts, options, declaration)

        # ── calibration ────────────────────────────────────────────────────
        # Applied here, between the corpus and the renderer, so the marked-up
        # PDF and findings.json cannot disagree about what this review found.
        # Downstream of run_all by construction: the overlay re-levels, re-orders
        # and suppresses a finished list, and can never add to it.
        calibrated = calibration.apply(
            result.findings,
            profile,
            occupancy_group=getattr(declaration, "occupancy_group", "") or "",
        )
        result.findings = calibrated.findings
        # A rule the profile silenced has not passed. Recording the abstention is
        # what keeps "not checked" distinguishable from "checked and passed" —
        # the first design rule in README.md, and the one calibration is most
        # able to quietly break.
        result.abstentions = [*result.abstentions, *calibrated.abstentions]
        calibration_report = calibrated.to_report()

        # ── render ─────────────────────────────────────────────────────────
        advance()
        pdf_name = f"{Path(filename).stem} — CODE REVIEW.pdf"
        out_pdf = workdir / "markup.pdf"
        info = render(
            str(src), str(out_pdf), result.findings, facts.sheets, options,
            result.abstentions, result.reconciled
        )

        counts: Dict[str, int] = {}
        for f in result.findings:
            counts[f.severity] = counts.get(f.severity, 0) + 1

        # Each finding with a unique key, where to draw it on the file the
        # engine read, and the readings it rests on (`fbcreview/payload.py`).
        findings = findings_payload(str(src), facts, result.findings)
        abstentions = [
            {"rule": a.rule_id, "reason": a.reason, "detail": getattr(a, "detail", "") or ""}
            for a in result.abstentions
        ]

        store.update(job_id, calibration=calibration_report)

        summary: Dict[str, Any] = {
            "sheets": len(facts.sheets),
            "pages": info["pages"],
            "cad_layers": len(facts.meta.get("cad_layers", [])),
            "annotations": info["annots"],
            "marked": info["marked"],
            "counts": counts,
            # A conflict is something to act on, so it counts as open. It is a
            # status, not a severity — the ink ramp still says how much it matters.
            "open": sum(1 for f in result.findings if f.status in ACTIONABLE),
            "verified": sum(1 for f in result.findings if f.status not in ACTIONABLE),
            "conflicts": sum(1 for f in result.findings if f.status == "CONFLICT"),
            "abstentions": abstentions,
            "rules_run": len(registered()),
            # Counts a sheet whose views each resolved their own scale, not only
            # one with a single page-wide scale — on a multi-view sheet the
            # latter is always absent and the geometry is still measurable.
            "scale_pages": sum(1 for g in facts.geometry.values() if g.scale_resolved),
            "pdf_bytes": out_pdf.stat().st_size,
            "pdf_name": pdf_name,
            "findings_count": len(findings),
            "sheet_index": sheet_index(facts.sheets),
            # Counts only — never sheet text. None when AI reading was off.
            "ai_reading": facts.meta.get("ai_reading"),
        }

        out_json = workdir / "findings.json"
        out_json.write_text(
            json.dumps(
                {
                    "job_id": job_id,
                    "options": options.to_dict(),
                    "declaration": _declaration_report(result.reconciled),
                    "summary": summary,
                    "calibration": calibration_report,
                    "findings": findings,
                },
                indent=2,
            ),
            encoding="utf-8",
        )

        # ── deliver ────────────────────────────────────────────────────────
        advance()
        store_files.upload_file(
            str(out_pdf), storage.output_path(job_id, storage.MARKUP), "application/pdf"
        )
        store_files.upload_file(
            str(out_json), storage.output_path(job_id, storage.FINDINGS), "application/json"
        )

        if options.email_to and mailer.configured():
            _send_mail(options, filename, summary, result, str(out_pdf))

        store.mark_done(job_id, summary)
        log.info(
            "review complete",
            extra={
                "job_id": job_id,
                "uid": uid,
                "email": email,
                "pages": pages,
                "findings": len(findings),
                "profile_version": calibration_report["profile_version"],
                "calibration_adjusted": calibration_report["adjusted"],
                "elapsed_seconds": round(time.monotonic() - started, 2),
            },
        )

    except Exception as exc:
        log.error(
            "review failed",
            extra={
                "job_id": job_id,
                "uid": uid,
                "email": email,
                "pages": pages,
                "elapsed_seconds": round(time.monotonic() - started, 2),
                "exception": f"{type(exc).__name__}: {exc}",
                "stack_trace": traceback.format_exc(),
            },
        )
        try:
            store.mark_error(
                job_id,
                "review_failed",
                "The review could not be completed for this set. "
                "If it is a scanned or image-only PDF, the engine cannot read it.",
            )
        except Exception:
            log.exception("could not record job failure", extra={"job_id": job_id})

    finally:
        # The instance's disk is small and shared with the next request.
        shutil.rmtree(workdir, ignore_errors=True)


def _declaration_report(reconciled) -> Optional[Dict[str, Any]]:
    """What was declared and what the drawings said back.

    Written into findings.json so the client can show the same audit record the
    marked-up PDF prints, without a second round trip.
    """
    if reconciled is None or reconciled.declaration.is_empty():
        return None

    from fbcreview.declaration_schema import FIELDS

    def render(value):
        if value is None:
            return None
        if isinstance(value, bool):
            return "Yes" if value else "No"
        if isinstance(value, float) and value == int(value):
            return f"{int(value):,}"
        if isinstance(value, float):
            return f"{value:,.2f}"
        if isinstance(value, int):
            return f"{value:,}"
        return str(value)

    fields = []
    for key in (f.key for f in FIELDS):
        rec = reconciled.fields.get(key)
        if rec is None:
            continue
        fields.append({
            "field": key,
            "state": rec.state,
            "declared": render(rec.declared_value),
            "drawn": render(rec.drawn_value),
            "drawn_source": rec.drawn.source if rec.drawn else None,
            "note": (rec.drawn.note if rec.drawn else "") or "",
        })
    return {
        "declaration": reconciled.declaration.to_dict(),
        "answered": reconciled.declaration.answered_count(),
        "total_fields": len(FIELDS),
        "fields": fields,
    }


def _ai_readings(src: str, upload: str, rebuild: Optional[Dict[str, Any]],
                 config: ReaderConfig, job_id: str, rerun_of: Optional[str],
                 store_files: "storage.Storage", workdir: Path) -> Optional[Readings]:
    """The set's AI readings: replayed from the first run of this upload, or read now.

    `src` is what the model reads and the engine grounds against — the upload,
    or the file rebuilt from it. The readings are keyed by the upload and the
    rebuild's parameters (`source_identity`), because a rebuilt file's bytes
    differ on every run. Stored beside the job's other artefacts either way.
    Returns None — and the review carries on deterministically — on any failure.
    """
    from fbcreview.ai.prompt import PROMPT_VERSION
    from fbcreview.ai.reader import read_document
    from fbcreview.ai.readings import load_readings

    try:
        sha = source_identity(file_sha256(upload), rebuild)
        readings: Optional[Readings] = None
        if rerun_of:
            local = workdir / "parent-readings.json"
            try:
                store_files.download_to(storage.output_path(rerun_of, READINGS), str(local))
                parent = load_readings(str(local))
                if (parent.file_sha256, parent.model, parent.prompt_version) == (
                        sha, config.model, PROMPT_VERSION):
                    readings = parent
            except Exception:                                  # noqa: BLE001 — none stored
                readings = None
        if readings is None:
            cache_dir = os.environ.get("FBC_AI_CACHE_DIR") or str(
                Path(tempfile.gettempdir()) / "fbc-ai-cache")
            readings = read_document(src, config, cache=ReadingsCache(cache_dir),
                                     identity=sha)

        out = workdir / READINGS
        save_readings(readings, str(out))
        store_files.upload_file(str(out), storage.output_path(job_id, READINGS),
                                "application/json")
        log.info("ai reading done", extra={"job_id": job_id, **readings.summary()})
        return readings
    except Exception as exc:                                   # noqa: BLE001 — the floor holds
        log.warning("ai reading skipped", extra={"job_id": job_id,
                                                  "error": type(exc).__name__})
        return None


def _merge_reports(first, second):
    """Combine the whole-sheet rebuild and the region read into one report."""
    if first is None:
        return second
    if second is None:
        return first
    return convert.ConversionReport(
        converted_pages=[*first.converted_pages, *second.converted_pages],
        ocr_used=first.ocr_used or second.ocr_used,
        vectorise_used=first.vectorise_used or second.vectorise_used,
        seconds=first.seconds + second.seconds,
    )


def _send_mail(options, filename, summary, result, pdf_path: str) -> None:
    """Inert unless SMTP is configured. Kept for parity with the laptop build;
    the web client does not surface it."""
    try:
        crit = [f for f in result.findings if f.severity == "CRITICAL"]
        lines = [
            f"Code review complete for {options.project_name or filename}.",
            "",
            f"{summary['open']} open findings, {summary['verified']} verified.",
            "",
        ]
        if crit:
            lines += [f"CRITICAL — {crit[0].title}", f"  {crit[0].result}", ""]
        for f in [f for f in result.findings if f.status == "OPEN"][:12]:
            lines.append(f"  {f.severity:<9} {f.fid:<7} {f.sheet:<5} {f.title}")
        lines += [
            "",
            "The marked-up PDF is attached.",
            "",
            "Advisory only. A licensed design professional remains responsible for "
            "code compliance; this is not a plan approval.",
        ]
        mailer.send_review(
            options.email_to,
            f"Code review — {options.project_name or filename}",
            "\n".join(lines),
            pdf_path,
        )
    except Exception:
        log.exception("mail delivery failed")
