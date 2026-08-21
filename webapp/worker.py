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
import shutil
import tempfile
import time
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional

from fbcreview.options import ReviewOptions
from fbcreview.pipeline import build_facts
from fbcreview.render.markup import render
from fbcreview.rules import registered, run_all
from webapp import convert, mailer, storage
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


def stages_for(convert_raster: bool) -> List[str]:
    """The stage list a given job will actually move through.

    A job that is not rebuilding scanned sheets should not display a stage that
    does nothing, so the list is per job rather than global.
    """
    if not convert_raster:
        return list(STAGES)
    return [STAGES[0], CONVERT_STAGE, *STAGES[1:]]


def run_review(
    *,
    job_id: str,
    uid: str,
    email: str,
    filename: str,
    upload_blob: str,
    options: ReviewOptions,
    store: JobStore,
    store_files: "storage.Storage",
    convert_raster: bool = False,
    raster_pages: Optional[List[int]] = None,
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

        # ── rebuild scanned sheets, when asked ─────────────────────────────
        if convert_raster and raster_pages:
            advance()
            rebuilt = workdir / "converted.pdf"
            report = convert.convert(str(src), raster_pages, str(rebuilt))
            store.update(job_id, conversion=report.to_dict())
            # Only adopt the rebuild if it actually produced a file; a failed
            # OCR pass must not lose the original.
            if rebuilt.exists() and rebuilt.stat().st_size > 0:
                src = rebuilt

        # ── extract ────────────────────────────────────────────────────────
        advance()
        facts = build_facts(str(src))
        pages = len(facts.sheets)

        # ── rules ──────────────────────────────────────────────────────────
        advance()
        result = run_all(facts, options)

        # ── render ─────────────────────────────────────────────────────────
        advance()
        pdf_name = f"{Path(filename).stem} — CODE REVIEW.pdf"
        out_pdf = workdir / "markup.pdf"
        info = render(
            str(src), str(out_pdf), result.findings, facts.sheets, options, result.abstentions
        )

        counts: Dict[str, int] = {}
        for f in result.findings:
            counts[f.severity] = counts.get(f.severity, 0) + 1

        findings = [f.to_dict() for f in result.findings]
        abstentions = [
            {"rule": a.rule_id, "reason": a.reason, "detail": getattr(a, "detail", "") or ""}
            for a in result.abstentions
        ]

        summary: Dict[str, Any] = {
            "sheets": len(facts.sheets),
            "pages": info["pages"],
            "cad_layers": len(facts.meta.get("cad_layers", [])),
            "annotations": info["annots"],
            "marked": info["marked"],
            "counts": counts,
            "open": sum(1 for f in result.findings if f.status == "OPEN"),
            "verified": sum(1 for f in result.findings if f.status != "OPEN"),
            "abstentions": abstentions,
            "rules_run": len(registered()),
            "scale_pages": sum(1 for g in facts.geometry.values() if g.scale_pt_per_ft),
            "pdf_bytes": out_pdf.stat().st_size,
            "pdf_name": pdf_name,
            "findings_count": len(findings),
        }

        out_json = workdir / "findings.json"
        out_json.write_text(
            json.dumps(
                {
                    "job_id": job_id,
                    "options": options.to_dict(),
                    "summary": summary,
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
