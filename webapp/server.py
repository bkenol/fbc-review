"""FBC Code Review — HTTP service.

POST a permit set, choose the review parameters, get back a marked-up PDF and a
findings JSON. The review itself runs in a worker thread and makes no model
calls; see ARCHITECTURE.md.
"""
from __future__ import annotations
import json, os, shutil, sys, tempfile, threading, time, traceback, uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from fbcreview.options import ReviewOptions, OCCUPANCY_GROUPS, EDITIONS, SEVERITY_ORDER
from fbcreview.pipeline import build_facts
from fbcreview.rules import run_all, registered
from fbcreview.render.markup import render
from webapp import mailer

APP_DIR = Path(__file__).resolve().parent
WORK = Path(os.environ.get("FBC_WORK_DIR", tempfile.gettempdir())) / "fbc-jobs"
WORK.mkdir(parents=True, exist_ok=True)
MAX_MB = int(os.environ.get("FBC_MAX_UPLOAD_MB", 120))
RETAIN_HOURS = int(os.environ.get("FBC_RETAIN_HOURS", 24))

app = FastAPI(title="FBC Code Review", version="0.1.0")
_pool = ThreadPoolExecutor(max_workers=int(os.environ.get("FBC_WORKERS", 2)))
_jobs: Dict[str, "Job"] = {}
_lock = threading.Lock()

STAGES = ["Reading the PDF", "Extracting schedules and code data",
          "Running rules", "Rendering the markup", "Delivering"]


@dataclass
class Job:
    id: str
    filename: str
    options: ReviewOptions
    state: str = "queued"          # queued | running | done | error
    stage: int = 0
    created: float = field(default_factory=time.time)
    error: str = ""
    mail: str = ""
    summary: dict = field(default_factory=dict)

    @property
    def dir(self) -> Path:
        return WORK / self.id

    def public(self) -> dict:
        return {"id": self.id, "filename": self.filename, "state": self.state,
                "stage": self.stage, "stage_label": STAGES[min(self.stage, len(STAGES) - 1)],
                "stages": STAGES, "error": self.error, "mail": self.mail,
                "summary": self.summary, "elapsed": round(time.time() - self.created, 1)}


def _sweep():
    cutoff = time.time() - RETAIN_HOURS * 3600
    for d in WORK.iterdir():
        try:
            if d.is_dir() and d.stat().st_mtime < cutoff:
                shutil.rmtree(d, ignore_errors=True)
                _jobs.pop(d.name, None)
        except OSError:
            pass


def _run(job: Job):
    try:
        job.state, job.stage = "running", 0
        src = str(job.dir / "source.pdf")
        job.stage = 1
        facts = build_facts(src)
        job.stage = 2
        res = run_all(facts, job.options)
        job.stage = 3
        out = job.dir / f"{Path(job.filename).stem} — CODE REVIEW.pdf"
        info = render(src, str(out), res.findings, facts.sheets, job.options, res.abstentions)

        counts = {}
        for f in res.findings:
            counts[f.severity] = counts.get(f.severity, 0) + 1
        job.summary = {
            "sheets": len(facts.sheets), "pages": info["pages"],
            "cad_layers": len(facts.meta.get("cad_layers", [])),
            "annotations": info["annots"], "marked": info["marked"],
            "counts": counts,
            "open": sum(1 for f in res.findings if f.status == "OPEN"),
            "verified": sum(1 for f in res.findings if f.status != "OPEN"),
            "abstentions": [{"rule": a.rule_id, "reason": a.reason} for a in res.abstentions],
            "rules_run": len(registered()),
            "scale_pages": sum(1 for g in facts.geometry.values() if g.scale_pt_per_ft),
            "pdf_bytes": out.stat().st_size,
            "pdf_name": out.name,
            "findings": [f.to_dict() for f in res.findings],
        }
        (job.dir / "findings.json").write_text(
            json.dumps({"options": job.options.to_dict(), "summary":
                        {k: v for k, v in job.summary.items() if k != "findings"},
                        "findings": job.summary["findings"]}, indent=2), encoding="utf-8")

        job.stage = 4
        if job.options.email_to:
            crit = [f for f in res.findings if f.severity == "CRITICAL"]
            lines = [f"Code review complete for {job.options.project_name or job.filename}.", "",
                     f"{job.summary['open']} open findings, {job.summary['verified']} verified.",
                     ""]
            if crit:
                lines += [f"CRITICAL — {crit[0].title}", f"  {crit[0].result}", ""]
            for f in [f for f in res.findings if f.status == "OPEN"][:12]:
                lines.append(f"  {f.severity:<9} {f.fid:<7} {f.sheet:<5} {f.title}")
            lines += ["", "The marked-up PDF is attached.", "",
                      "Advisory only. A licensed design professional remains responsible for "
                      "code compliance; this is not a plan approval."]
            job.mail = mailer.send_review(
                job.options.email_to,
                f"Code review — {job.options.project_name or job.filename}",
                "\n".join(lines), str(out))
        job.state = "done"
    except Exception as exc:
        job.state = "error"
        job.error = f"{type(exc).__name__}: {exc}"
        traceback.print_exc()


@app.get("/", response_class=HTMLResponse)
def index():
    return (APP_DIR / "static" / "index.html").read_text(encoding="utf-8")


@app.get("/api/config")
def config():
    return {"occupancy_groups": OCCUPANCY_GROUPS,
            "editions": [{"id": k, "label": v, "available": k == "fbc2023"}
                         for k, v in EDITIONS.items()],
            "severities": SEVERITY_ORDER,
            "max_upload_mb": MAX_MB,
            "mail": {"configured": mailer.configured(), "status": mailer.status()},
            "rules": registered(),
            "retain_hours": RETAIN_HOURS}


@app.post("/api/review")
async def review(file: UploadFile = File(...), options: str = Form("{}")):
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(415, "Upload a PDF permit set.")
    try:
        raw = json.loads(options)
    except json.JSONDecodeError:
        raise HTTPException(400, "options must be JSON")
    emails = [e.strip() for e in str(raw.get("email_to", "")).replace(";", ",").split(",")
              if e.strip()]
    opt = ReviewOptions(
        edition=raw.get("edition", "fbc2023"),
        occupancy_group=raw.get("occupancy_group", "A-3"),
        sprinklered=bool(raw.get("sprinklered", True)),
        min_severity=raw.get("min_severity", "LOW"),
        include_verified=bool(raw.get("include_verified", True)),
        include_measured=bool(raw.get("include_measured", True)),
        project_name=(raw.get("project_name") or "").strip(),
        email_to=emails, notes=(raw.get("notes") or "").strip())
    if opt.edition != "fbc2023":
        raise HTTPException(400, f"{EDITIONS.get(opt.edition, opt.edition)} — pick the 8th Edition.")

    jid = uuid.uuid4().hex[:12]
    job = Job(jid, os.path.basename(file.filename), opt)
    job.dir.mkdir(parents=True, exist_ok=True)
    dest = job.dir / "source.pdf"
    size = 0
    with dest.open("wb") as fh:
        while chunk := await file.read(1 << 20):
            size += len(chunk)
            if size > MAX_MB * 1024 * 1024:
                fh.close(); shutil.rmtree(job.dir, ignore_errors=True)
                raise HTTPException(413, f"Larger than the {MAX_MB} MB limit.")
            fh.write(chunk)
    with _lock:
        _jobs[jid] = job
    _pool.submit(_run, job)
    _pool.submit(_sweep)
    return JSONResponse({"id": jid}, status_code=202)


def _job(jid: str) -> Job:
    j = _jobs.get(jid)
    if not j:
        raise HTTPException(404, "No such job — results are cleared after "
                                 f"{RETAIN_HOURS} hours.")
    return j


@app.get("/api/jobs/{jid}")
def job_status(jid: str):
    return _job(jid).public()


@app.get("/api/jobs/{jid}/markup.pdf")
def job_pdf(jid: str):
    j = _job(jid)
    if j.state != "done":
        raise HTTPException(409, "Not finished.")
    p = j.dir / j.summary["pdf_name"]
    return FileResponse(p, media_type="application/pdf", filename=p.name)


@app.get("/api/jobs/{jid}/findings.json")
def job_json(jid: str):
    j = _job(jid)
    if j.state != "done":
        raise HTTPException(409, "Not finished.")
    return FileResponse(j.dir / "findings.json", media_type="application/json",
                        filename=f"{Path(j.filename).stem}-findings.json")


@app.get("/healthz")
def healthz():
    return {"ok": True, "jobs": len(_jobs), "rules": len(registered())}
