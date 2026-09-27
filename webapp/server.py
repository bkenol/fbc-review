"""FBC Code Review — HTTP API.

POST a permit set with review parameters, poll the job, then download the
marked-up PDF and findings from Cloud Storage using the signed URLs the job
hands back. The review itself runs on a worker thread and makes **no model
calls**; see ARCHITECTURE.md.

Nothing here streams a PDF back through the app. A finished set is 16-19 MB and
proxying that would pin a Cloud Run instance for the length of the download.
"""
from __future__ import annotations

import contextlib
import dataclasses
import json
import logging
import tempfile
import uuid
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, File, Form, UploadFile
from fastapi.responses import JSONResponse

from fbcreview import declaration_schema
from fbcreview.declaration import ProjectDeclaration
from fbcreview.options import (AVAILABLE_EDITIONS, EDITIONS, OCCUPANCY_GROUPS,
                               SEVERITY_ORDER, ReviewOptions)
from fbcreview.rules import registered
from webapp import (abstentions, assist, calibration, errors, feedback_schema,
                    logging_config, mailer, models, notify, prefill, storage,
                    storage_urls, triage, upload, version)
from webapp.auth import User, current_user
from webapp.calibration import CalibrationProfile, ProfileChange
from webapp.config import settings
from webapp.errors import ApiError
from webapp.feedback_store import (ACCEPTED, ACTIONED, NEW, REJECTED,
                                   FeedbackStore, get_feedback_store)
from webapp.jobs import DONE, JobStore, RateLimited, get_job_store, utcnow
from webapp.storage import Storage, get_storage
from webapp.worker import STAGES, run_review, stages_for

logging_config.configure()
log = logging.getLogger("fbc.api")

#: The full string this build reports: release, channel, build number and
#: commit. `webapp/version.py` owns the scheme and explains it.
VERSION = version.resolve()

#: What the OpenAPI document publishes, and deliberately only the release
#: triple. The contract does not move when a build number does, and CI
#: regenerates this schema and compares it byte-for-byte against what is
#: committed — so nothing that varies by build or by machine can appear in it.
API_VERSION = version.release()

_pool: Optional[ThreadPoolExecutor] = None


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    global _pool
    cfg = settings()
    _pool = ThreadPoolExecutor(max_workers=cfg.workers, thread_name_prefix="review")
    log.info(
        "service starting",
        extra={
            "version": VERSION,
            "channel": version.channel(),
            "workers": cfg.workers,
            "bucket": cfg.bucket,
            "allowlist_size": len(cfg.allowed_emails),
            "backend": cfg.backend,
            "on_cloud_run": cfg.on_cloud_run,
        },
    )
    if cfg.dev_unsafe_auth:
        log.warning("FBC_DEV_UNSAFE_AUTH=1 - every request is treated as signed in")

    # Cloud Run scales to zero; anything left running belongs to a dead
    # instance. Failing to reach Firestore here must not stop the service
    # coming up, or a transient outage becomes a crash loop.
    try:
        (_dev_jobs() if cfg.local_backend else get_job_store()).fail_stale_running()
    except Exception:
        log.exception("startup sweep for orphaned jobs failed")

    yield

    _pool.shutdown(wait=False, cancel_futures=True)


app = FastAPI(
    title="FBC Code Review",
    version=API_VERSION,
    summary="Deterministic Florida Building Code plan review.",
    description=(
        "Upload a vector permit set, choose the review parameters, download a "
        "marked-up PDF and a findings register. No model calls in the request path."
    ),
    lifespan=lifespan,
    responses={
        400: {"model": models.ErrorResponse},
        401: {"model": models.ErrorResponse},
        403: {"model": models.ErrorResponse},
        404: {"model": models.ErrorResponse},
        413: {"model": models.ErrorResponse},
        415: {"model": models.ErrorResponse},
        429: {"model": models.ErrorResponse},
        500: {"model": models.ErrorResponse},
    },
)
errors.install(app)


def custom_openapi() -> Dict[str, Any]:
    """Make the published schema match what the service actually returns.

    Two corrections to FastAPI's default output:

    1. FastAPI documents a 422 as its own `HTTPValidationError`, but
       `errors.install` converts those to the same `{"error": {...}}` envelope
       as everything else. Left alone, the generated client would carry a type
       for a body that is never sent.
    2. `FindingsDocument` is published even though no endpoint returns it. The
       browser fetches `findings.json` straight from Cloud Storage, so without
       this the findings table would be the only hand-typed part of the client.
    """
    if app.openapi_schema:
        return app.openapi_schema

    from fastapi.openapi.utils import get_openapi

    schema = get_openapi(
        title=app.title,
        version=app.version,
        summary=app.summary,
        description=app.description,
        routes=app.routes,
    )
    schemas = schema.setdefault("components", {}).setdefault("schemas", {})

    error_ref = {"$ref": "#/components/schemas/ErrorResponse"}
    for operations in schema.get("paths", {}).values():
        for operation in operations.values():
            for status, declared in (operation.get("responses") or {}).items():
                if status.startswith(("4", "5")):
                    declared["content"] = {"application/json": {"schema": dict(error_ref)}}
                    declared.setdefault("description", "Error")

    # FastAPI only emits these to describe the 422 it no longer sends.
    for dead in ("HTTPValidationError", "ValidationError"):
        schemas.pop(dead, None)

    document = models.FindingsDocument.model_json_schema(
        ref_template="#/components/schemas/{model}"
    )
    for name, definition in document.pop("$defs", {}).items():
        schemas.setdefault(name, definition)
    schemas["FindingsDocument"] = document

    app.openapi_schema = schema
    return schema


app.openapi = custom_openapi  # type: ignore[method-assign]


# -- dependency seams (overridden in tests) --------------------------------
# `local` resolves to the filesystem stand-ins in webapp/devbackend.py, so the
# whole app runs with no GCP project at all.
#
# Keyed on the backend rather than on `dev_unsafe_auth`, which is what selected
# both until this was split. They are genuinely separate questions: Cloud
# Storage is the one part of this stack that requires an open billing account,
# so a deployment that cannot have one still wants the filesystem backend **and
# real Firebase sign-in** — and while one flag meant both, choosing the first
# silently gave away the second. config.py forces `gcp` whenever K_SERVICE is
# set, so neither branch below can pick the filesystem on Cloud Run, where the
# disk is ephemeral.
def job_store() -> JobStore:
    if settings().local_backend:
        return _dev_jobs()
    return get_job_store()


def file_store() -> Storage:
    if settings().local_backend:
        return _dev_files()
    return get_storage()


@lru_cache(maxsize=1)
def _dev_jobs():
    from webapp.devbackend import LocalJobStore

    return LocalJobStore()


@lru_cache(maxsize=1)
def _dev_files():
    from webapp.devbackend import LocalStorage

    return LocalStorage()


def feedback_store() -> FeedbackStore:
    if settings().local_backend:
        return _dev_feedback()
    return get_feedback_store()


@lru_cache(maxsize=1)
def _dev_feedback():
    from webapp.devbackend import LocalFeedbackStore

    return LocalFeedbackStore()


# -- who may change what every future review reports ----------------------
def current_owner(user: User = Depends(current_user)) -> User:
    """The owner gate.

    Deliberately a second, independent list rather than a flag on the allowlist:
    being permitted to run a review is not being permitted to re-level a rule
    for everybody. `FBC_OWNER_EMAILS` empty means nobody, because an unset
    variable must never grant administration.
    """
    if not settings().is_owner(user.email):
        # Absent rather than forbidden, as everywhere else here: the existence
        # of an admin surface is not something to confirm to a caller who is
        # not on it.
        raise ApiError(404, errors.NOT_FOUND, "No such endpoint.")
    return user


def _require_training() -> None:
    if not settings().training_enabled:
        raise ApiError(
            400,
            errors.INVALID_REQUEST,
            "Training mode is not enabled on this deployment.",
        )


def _owned_job(job_id: str, user: User, store: JobStore) -> Dict[str, Any]:
    record = store.get(job_id)
    if not record or record.get("uid") != user.uid:
        raise ApiError(404, errors.NOT_FOUND, "No such review.")
    return record


# -- health ----------------------------------------------------------------
@app.get(
    "/healthz",
    response_model=models.Health,
    tags=["health"],
    operation_id="healthz",
    summary="Liveness. Unauthenticated and deliberately cheap.",
)
def healthz() -> models.Health:
    # No Firestore, no Cloud Storage, no allowlist. Cloud Run's startup probe
    # hits this and it must answer before dependencies are warm.
    return models.Health(
        ok=True,
        service="fbc-review",
        version=VERSION,
        auth_required=not settings().dev_unsafe_auth,
    )


@app.get("/api/healthz", response_model=models.Health, include_in_schema=False)
def healthz_via_api() -> models.Health:
    """The same answer, on a path the browser can actually reach.

    Firebase Hosting rewrites `/api/**` to Cloud Run and sends everything else
    to `index.html`, so a client fetching `/healthz` gets the app shell back
    rather than this — and the same is true of `ng serve`, whose proxy is keyed
    on `/api`. Cloud Run's startup probe keeps using `/healthz`; the client uses
    this. Off the schema deliberately: it is one alias, not a second endpoint,
    and publishing it would put a duplicate method on the generated client.
    """
    return healthz()


# -- config ----------------------------------------------------------------
@app.get(
    "/api/config",
    response_model=models.ConfigResponse,
    tags=["config"],
    operation_id="getConfig",
)
def config(
    user: User = Depends(current_user),
    feedback: FeedbackStore = Depends(feedback_store),
) -> models.ConfigResponse:
    cfg = settings()

    # The active profile is read here so the client can say which calibration
    # version a standard review will run under, without a second round trip.
    # A store that cannot be reached must not take the whole config down with
    # it — the review path does not depend on any of this.
    try:
        active = feedback.active_profile() if cfg.training_enabled else CalibrationProfile()
    except Exception:
        log.exception("could not read the active calibration profile")
        active = CalibrationProfile()

    return models.ConfigResponse(
        occupancy_groups=[models.OccupancyGroup(id=i, label=l) for i, l in OCCUPANCY_GROUPS],
        editions=[
            models.Edition(id=k, label=v, available=(k in AVAILABLE_EDITIONS))
            for k, v in EDITIONS.items()
        ],
        severities=list(SEVERITY_ORDER),
        stages=list(STAGES),
        max_upload_mb=cfg.max_upload_mb,
        max_pages=cfg.max_pages,
        retain_days=cfg.retain_days,
        rules=registered(),
        mail=models.MailStatus(configured=mailer.configured(), status=mailer.status()),
        defaults=models.ReviewOptions(),
        # Served whole, from the one module that defines it. The client renders
        # what it is handed and carries no copy of a label or an enum value.
        declaration_groups=[
            models.DeclarationGroup(key=k, label=l) for k, l in declaration_schema.GROUPS
        ],
        declaration_fields=[
            models.DeclarationField.model_validate(f) for f in declaration_schema.to_dicts()
        ],
        declaration_unlockable=list(declaration_schema.ALL_UNLOCKED),
        # Served whole from the one module that defines it, exactly as the
        # declaration questionnaire is. The client renders what it is handed and
        # carries no copy of a verdict label.
        training=models.TrainingStatus(
            enabled=cfg.training_enabled,
            is_owner=cfg.is_owner(user.email),
            assist=assist.status(),
            github=notify.github_status(),
            profile_version=active.version,
            profile_label=active.label,
            calibrated_rules=len(active.touched()),
        ),
        feedback_aspects=[
            models.FeedbackAspect.model_validate(a) for a in feedback_schema.to_dicts()
        ],
        markup_kinds=[
            models.MarkupKindInfo(**k) for k in feedback_schema.markup_kinds()
        ],
        markup_colours=[
            models.MarkupColourInfo(**c) for c in feedback_schema.markup_colours()
        ],
        abstention_kinds=[
            models.AbstentionKindInfo(**k) for k in abstentions.catalogue()
        ],
        dispositions=[
            models.DispositionInfo(key=d, label=triage.DISPOSITION_LABELS[d])
            for d in triage.DISPOSITIONS
        ],
        calibration_knobs=[
            models.CalibrationKnob.model_validate(k) for k in calibration.knob_catalogue()
        ],
    )


# -- submit ----------------------------------------------------------------
@app.post(
    "/api/prefill",
    response_model=models.PrefillResponse,
    tags=["reviews"],
    operation_id="prefillDeclaration",
    summary="Read what a permit set already states about itself.",
    description=(
        "Parses a set and returns the declaration answers the drawings themselves "
        "state, so the applicant confirms or corrects them rather than transcribing "
        "their own drawing. Starts nothing, stores nothing, and makes no model calls "
        "— it is the same pure-Python parse the review runs, stopped after the facts "
        "are built. The suggestions are advisory: what the applicant submits to "
        "`POST /api/review` is what gets declared."
    ),
)
async def prefill_declaration(
    file: UploadFile = File(..., description="The permit set, as a PDF."),
    user: User = Depends(current_user),
) -> models.PrefillResponse:
    filename = upload.safe_basename(file.filename or "")
    scratch = Path(tempfile.mkdtemp(prefix="fbc-pre-"))
    local = scratch / "source.pdf"

    try:
        size = await upload.stream_to_disk(file, local)
        # allow_raster: a scanned set is not an error here. It simply states
        # very little, and saying so is more useful than refusing to look.
        pages, source = upload.probe(local, allow_raster=True)
        found = prefill.read(str(local))
    finally:
        with contextlib.suppress(Exception):
            local.unlink(missing_ok=True)
            scratch.rmdir()

    log.info(
        "prefill read",
        extra={
            "uid": user.uid,
            "pages": pages,
            "bytes": size,
            "fields_found": len(found),
            "source_kind": source.kind,
        },
    )

    return models.PrefillResponse(
        filename=filename,
        pages=pages,
        bytes=size,
        source=models.SourceProfile.model_validate(source.to_dict()),
        fields=[models.PrefilledField(**dataclasses.asdict(s)) for s in found],
    )


@app.post(
    "/api/review",
    response_model=models.ReviewAccepted,
    status_code=202,
    tags=["reviews"],
    operation_id="createReview",
    summary="Accept a permit set and start a review.",
)
async def create_review(
    file: UploadFile = File(..., description="The permit set, as a PDF."),
    # Named `review_options` rather than `options`: openapi-generator's
    # typescript-angular services already take a parameter called `options` for
    # the per-request HttpClient settings, and a form field of the same name
    # generates a method with two parameters of that name, which does not
    # compile.
    review_options: str = Form("{}", description="A JSON-encoded ReviewOptions object."),
    declaration: str = Form(
        "{}",
        description=(
            "A JSON-encoded ProjectDeclaration: what the applicant says the building is. "
            "Optional in whole and in part — every field may be omitted, and omitting the "
            "part entirely reproduces the review exactly as it ran before declarations "
            "existed. Values are validated against the schema `/api/config` publishes."
        ),
    ),
    user: User = Depends(current_user),
    store: JobStore = Depends(job_store),
    files: Storage = Depends(file_store),
    feedback: FeedbackStore = Depends(feedback_store),
) -> JSONResponse:
    try:
        raw: Dict[str, Any] = json.loads(review_options or "{}")
    except json.JSONDecodeError:
        raise ApiError(400, errors.INVALID_REQUEST, "options must be a JSON object.")
    if not isinstance(raw, dict):
        raise ApiError(400, errors.INVALID_REQUEST, "options must be a JSON object.")

    # Accept the legacy comma-separated string the reference page sends.
    if isinstance(raw.get("email_to"), str):
        raw["email_to"] = [
            e.strip() for e in raw["email_to"].replace(";", ",").split(",") if e.strip()
        ]
    raw.pop("edition_label", None)

    try:
        parsed = models.ReviewOptions.model_validate(raw)
    except Exception as exc:
        raise ApiError(400, errors.INVALID_REQUEST, f"Invalid review options: {exc}")

    upload.validate_options_edition(parsed.edition, EDITIONS)
    valid_groups = {g for g, _ in OCCUPANCY_GROUPS}
    if parsed.occupancy_group not in valid_groups:
        raise ApiError(
            400,
            errors.INVALID_REQUEST,
            f"{parsed.occupancy_group} is not an occupancy group this build reviews.",
        )

    if parsed.mode == "training":
        _require_training()

    declared = _parse_declaration(declaration, parsed, raw)

    # Rate limits are checked before the body is streamed, so a throttled user
    # does not get to spend our bandwidth first.
    try:
        store.enforce_limits(user.uid)
    except RateLimited as exc:
        raise ApiError(429, errors.RATE_LIMITED, exc.message)

    job_id = uuid.uuid4().hex[:12]
    filename = upload.safe_basename(file.filename or "")
    scratch = Path(tempfile.mkdtemp(prefix=f"fbc-in-{job_id}-"))
    local = scratch / "source.pdf"

    try:
        size = await upload.stream_to_disk(file, local)
        pages, source = upload.probe(local, allow_raster=parsed.convert_raster)

        blob = storage.upload_path(job_id, filename)
        files.upload_file(str(local), blob, "application/pdf")
    finally:
        with contextlib.suppress(Exception):
            local.unlink(missing_ok=True)
            scratch.rmdir()

    # The wire model carries fields the engine does not know about
    # (convert_raster is handled here, before build_facts ever runs), so map by
    # the engine dataclass's own field names rather than splatting.
    engine_fields = {f.name for f in dataclasses.fields(ReviewOptions)}
    engine_options = ReviewOptions(
        **{k: v for k, v in parsed.model_dump().items() if k in engine_fields}
    )
    # Regions count too: a vector sheet with its code table pasted in as a
    # picture needs the rebuild stage just as much as a scanned one does.
    needs_rebuild = parsed.convert_raster and bool(source.raster_pages or source.region_pages)
    job_stages = stages_for(needs_rebuild)

    store.create(
        job_id=job_id,
        uid=user.uid,
        email=user.email,
        filename=filename,
        size_bytes=size,
        pages=pages,
        options=parsed.model_dump(),
        upload_blob=blob,
        stages=job_stages,
        source=source.to_dict(),
        # Part of the audit trail, not a convenience: the register prints what
        # was asserted, and support has to be able to see it after the fact.
        declaration=declared.to_dict(),
    )

    log.info(
        "review accepted",
        extra={
            "job_id": job_id,
            "uid": user.uid,
            "email": user.email,
            "pages": pages,
            "bytes": size,
            "declared_fields": declared.answered_count(),
            "source_kind": source.kind,
            "raster_pages": len(source.raster_pages),
            "region_pages": len(source.region_pages),
        },
    )

    # Which calibration profile this review runs under. Training reviews use
    # the caller's own candidate profile so a reviewer sees the effect of their
    # own accepted feedback immediately; everyone else gets the approved one.
    # Nothing a user has said reaches a standard review until the owner
    # promotes it.
    profile = _profile_for(parsed.mode, user, feedback)

    assert _pool is not None
    _pool.submit(
        run_review,
        job_id=job_id,
        uid=user.uid,
        email=user.email,
        filename=filename,
        upload_blob=blob,
        options=engine_options,
        declaration=declared,
        store=store,
        store_files=files,
        convert_raster=parsed.convert_raster,
        raster_pages=list(source.raster_pages),
        raster_regions={
            sheet.page: [(r.x0, r.y0, r.x1, r.y1) for r in sheet.raster_regions]
            for sheet in source.sheets
            if sheet.has_readable_regions
        },
        profile=profile,
    )
    return JSONResponse({"id": job_id}, status_code=202)


def _profile_for(mode: str, user: User, feedback: FeedbackStore
                 ) -> Optional[CalibrationProfile]:
    """The calibration profile a review should run against.

    Returns `None` when training is off, which is what makes this feature
    inert on a deployment that has not opted in: `calibration.apply` with no
    profile is the identity function, and the review is byte-for-byte the one
    the service produced before any of this existed.

    A store that cannot be reached is not a reason to fail a review. The
    uncalibrated result is the honest fallback and is exactly what the service
    did before — so log it and carry on rather than turning a Firestore blip
    into a failed upload.
    """
    if not settings().training_enabled:
        return None
    try:
        if mode == "training":
            return feedback.candidate_profile(user.uid)
        return feedback.active_profile()
    except Exception:
        log.exception("could not load a calibration profile; reviewing uncalibrated")
        return None


def _parse_declaration(body: str, options: models.ReviewOptions,
                       raw_options: Dict[str, Any]) -> ProjectDeclaration:
    """The submitted declaration, validated against the published schema.

    Nothing is silently dropped or coerced. An enum value this build does not
    know is a 400 naming the field, because accepting it quietly would mean
    reviewing the set against something the user did not choose.

    `occupancy_group` and `sprinklered` on ReviewOptions are the deprecated
    bridge: they are building facts that used to live in the wrong place. They
    seed the declaration only when the client sent them explicitly and the
    declaration itself is silent — a defaulted value is not an answer, and a
    blank field is not permission to guess.
    """
    try:
        parsed: Dict[str, Any] = json.loads(body or "{}")
    except json.JSONDecodeError:
        raise ApiError(400, errors.INVALID_REQUEST, "declaration must be a JSON object.")
    if not isinstance(parsed, dict):
        raise ApiError(400, errors.INVALID_REQUEST, "declaration must be a JSON object.")

    parsed = {k: v for k, v in parsed.items() if v is not None and v != ""}

    problems = declaration_schema.validate(parsed)
    if problems:
        raise ApiError(400, errors.INVALID_REQUEST, " ".join(problems))

    if "occupancy_group" not in parsed and "occupancy_group" in raw_options:
        parsed["occupancy_group"] = options.occupancy_group
    if "sprinkler_system" not in parsed and "sprinklered" in raw_options:
        parsed["sprinkler_system"] = "nfpa13" if options.sprinklered else "none"

    try:
        wire = models.ProjectDeclaration.model_validate(parsed)
    except Exception as exc:
        raise ApiError(400, errors.INVALID_REQUEST, f"Invalid declaration: {exc}")
    return ProjectDeclaration.from_dict(wire.model_dump())


# -- history ---------------------------------------------------------------
@app.get(
    "/api/jobs",
    response_model=models.HistoryResponse,
    tags=["reviews"],
    operation_id="listJobs",
    summary="The caller's own past reviews, newest first.",
    description=(
        "A review is worth keeping: the findings register is the deliverable, and "
        "re-running a 24-sheet set to look at it again is thirty seconds and a "
        "second copy of the same PDF. The marked-up set and findings.json already "
        "live in Cloud Storage, so this lists what is there rather than storing "
        "anything new. Rows carry no signed URLs — those are minted per row on "
        "`GET /api/jobs/{job_id}`, when a row is actually opened."
    ),
)
def list_jobs(
    limit: int = 50,
    user: User = Depends(current_user),
    store: JobStore = Depends(job_store),
) -> models.HistoryResponse:
    limit = max(1, min(int(limit), 100))
    entries: list[models.HistoryEntry] = []

    for record in store.list_for(user.uid, limit):
        summary = record.get("summary") or {}
        options = record.get("options") or {}
        declaration = record.get("declaration") or {}
        entries.append(
            models.HistoryEntry(
                id=record.get("id", ""),
                filename=record.get("filename", ""),
                state=record.get("state", "queued"),
                created_at=record.get("created_at"),
                finished_at=record.get("finished_at"),
                pages=int(record.get("pages") or 0),
                project_name=options.get("project_name") or "",
                edition=options.get("edition") or "",
                error=record.get("error"),
                counts={k: int(v) for k, v in (summary.get("counts") or {}).items()},
                open_findings=int(summary.get("open") or 0),
                # The stored declaration is a flat dict of what was asserted;
                # a null is a question that was left alone.
                declared_fields=sum(1 for v in declaration.values() if v is not None),
            )
        )

    return models.HistoryResponse(entries=entries)


# -- poll ------------------------------------------------------------------
@app.get(
    "/api/jobs/{job_id}",
    response_model=models.Job,
    tags=["reviews"],
    operation_id="getJob",
)
def get_job(
    job_id: str,
    user: User = Depends(current_user),
    store: JobStore = Depends(job_store),
    files: Storage = Depends(file_store),
) -> models.Job:
    record = store.get(job_id)

    # Ownership is enforced here, in the handler. The client never talks to
    # Firestore, so security rules would protect nothing. A job belonging to
    # someone else is reported as absent rather than forbidden, so job ids
    # cannot be probed for existence.
    if not record or record.get("uid") != user.uid:
        raise ApiError(404, errors.NOT_FOUND, "No such review.")

    return _to_model(record, files)


def _to_model(record: Dict[str, Any], files: Storage) -> models.Job:
    state = record.get("state", "queued")
    stage = int(record.get("stage", 0) or 0)
    stages = list(record.get("stages") or STAGES)
    created = record.get("created_at") or utcnow()
    finished = record.get("finished_at")

    source = record.get("source") or {}
    region_pages = list(source.get("region_pages") or [])
    raster_pages = list(source.get("raster_pages") or [])
    converted = bool(record.get("conversion"))
    measured_off = (record.get("options") or {}).get("include_measured") is False

    summary = None
    downloads = None
    diagnosis: List[models.AbstentionDiagnosis] = []
    if state == DONE and record.get("summary"):
        raw_summary = dict(record["summary"])
        # Classified on the way out rather than at review time, so a review that
        # finished before this module existed is classified too, and so a
        # reclassification here reaches every stored review without a re-run.
        # The engine's own reason string is never touched.
        raw_summary["abstentions"] = abstentions.classify_all(
            raw_summary.get("abstentions") or [],
            unread_pasted_tables=bool(region_pages) and not converted,
        )
        summary = models.Summary.model_validate(raw_summary)
        diagnosis = [
            models.AbstentionDiagnosis.model_validate(d)
            for d in abstentions.diagnose(
                raw_summary.get("abstentions") or [],
                region_pages=region_pages,
                raster_pages=raster_pages,
                converted=converted,
                cad_layers=int(source.get("cad_layers") or 0),
                measured_off=measured_off,
            )
        ]
        job_id = record["id"]
        pdf_name = summary.pdf_name or "markup.pdf"
        downloads = models.Downloads(
            markup_pdf=files.signed_url(
                storage.output_path(job_id, storage.MARKUP), download_as=pdf_name
            ),
            findings_json=files.signed_url(storage.output_path(job_id, storage.FINDINGS)),
            # The set as uploaded, for the in-app viewer to render. The markup
            # PDF has the findings burnt into the page; drawing the interactive
            # layer on top of that would show every marker twice.
            source_pdf=(
                files.signed_url(record["upload_blob"])
                if record.get("upload_blob") else ""
            ),
            expires_at=files.expires_at(),
        )

    end = finished or utcnow()
    elapsed = max(0.0, (end - created).total_seconds())

    return models.Job(
        id=record["id"],
        filename=record.get("filename", ""),
        state=state,
        stage=stage,
        stage_label=stages[min(stage, len(stages) - 1)],
        stages=stages,
        options=models.ReviewOptions.model_validate(record.get("options") or {}),
        declaration=(
            models.ProjectDeclaration.model_validate(record["declaration"])
            if record.get("declaration")
            else None
        ),
        rerun_of=record.get("rerun_of") or None,
        bytes=int(record.get("bytes", 0) or 0),
        pages=record.get("pages"),
        source=(
            models.SourceProfile.model_validate(record["source"])
            if record.get("source")
            else None
        ),
        summary=summary,
        conversion=(
            models.ConversionReport.model_validate(record["conversion"])
            if record.get("conversion")
            else None
        ),
        calibration=(
            models.CalibrationReport.model_validate(record["calibration"])
            if record.get("calibration")
            else None
        ),
        downloads=downloads,
        diagnosis=diagnosis,
        error=record.get("error"),
        error_code=record.get("error_code"),
        created_at=created,
        started_at=record.get("started_at"),
        finished_at=finished,
        elapsed_seconds=round(elapsed, 1),
    )


@app.get(
    "/api/jobs/{job_id}/declaration",
    response_model=models.ProjectDeclaration,
    tags=["reviews"],
    operation_id="getJobDeclaration",
    summary="What was declared with a review. For support and for the audit trail.",
)
def get_job_declaration(
    job_id: str,
    user: User = Depends(current_user),
    store: JobStore = Depends(job_store),
) -> models.ProjectDeclaration:
    record = store.get(job_id)
    # Absent rather than forbidden, as elsewhere: a job id must not be probeable
    # for existence.
    if not record or record.get("uid") != user.uid:
        raise ApiError(404, errors.NOT_FOUND, "No such review.")
    return models.ProjectDeclaration.model_validate(record.get("declaration") or {})


@app.post(
    "/api/jobs/{job_id}/rerun",
    response_model=models.ReviewAccepted,
    status_code=202,
    tags=["reviews"],
    operation_id="rerunReview",
    summary="Review the same set again with more of the declaration answered.",
)
def rerun_review(
    job_id: str,
    body: models.RerunRequest,
    user: User = Depends(current_user),
    store: JobStore = Depends(job_store),
    files: Storage = Depends(file_store),
    feedback: FeedbackStore = Depends(feedback_store),
) -> JSONResponse:
    """A second review of a set already in the bucket.

    The point of it is the abstention register. `DECL.BUILDING_AREA — neither
    the drawings nor the declaration state this` is a rule waiting on one
    number, and before this the only way to give it that number was to upload
    the file again and re-answer every other question with it. So the remedy
    cost more than the finding was worth and nobody took it.

    A new job rather than an amendment of the old one, and deliberately so.
    A review is a dated statement about a set under a stated set of assertions;
    editing one in place would rewrite what somebody was already told. The two
    sit side by side in the history, and `rerun_of` says which came first.

    The file is not re-sent and not re-probed: `upload_blob` still points at the
    PDF and `source` still holds what admission measured, so this costs one run
    of the engine. Rate limits still apply — it is a review.
    """
    record = store.get(job_id)
    if not record or record.get("uid") != user.uid:
        raise ApiError(404, errors.NOT_FOUND, "No such review.")

    blob = record.get("upload_blob")
    if not blob:
        raise ApiError(
            409,
            errors.INVALID_REQUEST,
            "This review has no stored set to re-run. Upload it again.",
        )

    previous = dict(record.get("declaration") or {})
    # Merged rather than replaced: the browser sends the questions it asked
    # about, and a field it left out is unanswered-in-this-request, not
    # withdrawn. Losing an answer the applicant already gave, silently, on a
    # request whose whole purpose is to add one, would be the wrong default.
    supplied = {
        k: v for k, v in body.declaration.model_dump().items()
        if v is not None and v != ""
    }
    merged = {**{k: v for k, v in previous.items() if v is not None}, **supplied}

    problems = declaration_schema.validate(merged)
    if problems:
        raise ApiError(400, errors.INVALID_REQUEST, " ".join(problems))
    declared = ProjectDeclaration.from_dict(
        models.ProjectDeclaration.model_validate(merged).model_dump()
    )

    try:
        parsed = models.ReviewOptions.model_validate(record.get("options") or {})
    except Exception as exc:
        raise ApiError(
            409, errors.INVALID_REQUEST, f"This review's options cannot be replayed: {exc}"
        )
    if body.convert_raster is not None:
        parsed = parsed.model_copy(update={"convert_raster": body.convert_raster})
    if parsed.mode == "training":
        _require_training()

    try:
        store.enforce_limits(user.uid)
    except RateLimited as exc:
        raise ApiError(429, errors.RATE_LIMITED, exc.message)

    source = record.get("source") or {}
    raster_pages = [int(p) for p in (source.get("raster_pages") or [])]
    # Rebuilt from the stored profile rather than re-measured. `probe` reads the
    # PDF, and re-reading a file to recover a fact already written down would
    # make the cheap path expensive for nothing.
    raster_regions: Dict[int, List[tuple]] = {}
    for sheet in source.get("sheets") or []:
        regions = sheet.get("raster_regions") or []
        if regions and sheet.get("kind") in ("vector", "hybrid"):
            raster_regions[int(sheet.get("page") or 0)] = [
                (float(r["x0"]), float(r["y0"]), float(r["x1"]), float(r["y1"]))
                for r in regions
            ]
    needs_rebuild = parsed.convert_raster and bool(raster_pages or raster_regions)

    new_id = uuid.uuid4().hex[:12]
    store.create(
        job_id=new_id,
        uid=user.uid,
        email=user.email,
        filename=record.get("filename") or "",
        size_bytes=int(record.get("bytes") or 0),
        pages=int(record.get("pages") or 0),
        options=parsed.model_dump(),
        # The same object in the bucket, under the first review's id. Reviews do
        # not delete their upload, and copying a permit set to give the second
        # run its own path would double the storage for one file.
        upload_blob=blob,
        stages=stages_for(needs_rebuild),
        source=source or None,
        declaration=declared.to_dict(),
        rerun_of=job_id,
    )

    log.info(
        "review re-run accepted",
        extra={
            "job_id": new_id,
            "rerun_of": job_id,
            "uid": user.uid,
            "declared_fields": declared.answered_count(),
            "added_fields": len(supplied),
        },
    )

    engine_fields = {f.name for f in dataclasses.fields(ReviewOptions)}
    engine_options = ReviewOptions(
        **{k: v for k, v in parsed.model_dump().items() if k in engine_fields}
    )

    assert _pool is not None
    _pool.submit(
        run_review,
        job_id=new_id,
        uid=user.uid,
        email=user.email,
        filename=record.get("filename") or "",
        upload_blob=blob,
        options=engine_options,
        declaration=declared,
        store=store,
        store_files=files,
        convert_raster=parsed.convert_raster,
        raster_pages=raster_pages,
        raster_regions=raster_regions,
        profile=_profile_for(parsed.mode, user, feedback),
        rerun_of=job_id,
    )
    return JSONResponse({"id": new_id}, status_code=202)


# ══ training mode ═════════════════════════════════════════════════════════
# Everything below is inert unless FBC_TRAINING_MODE=1. None of it is reachable
# from `run_review`: the review path is still pure Python over PyMuPDF, and the
# only model call in this service lives in `webapp/assist.py`, on the feedback
# path, behind its own configuration flag.


def _findings_index(job_id: str, files: Storage) -> Dict[str, Dict[str, Any]]:
    """The finished review's findings, keyed by fid.

    Read from the artefact rather than trusted from the request body: `rule_id`
    decides which rule a calibration proposal would move, so it has to come from
    what the service actually produced, not from what a browser says it
    produced.
    """
    scratch = Path(tempfile.mkdtemp(prefix=f"fbc-fb-{job_id}-"))
    local = scratch / "findings.json"
    try:
        files.download_to(storage.output_path(job_id, storage.FINDINGS), str(local))
        document = json.loads(local.read_text(encoding="utf-8"))
    except Exception:
        log.warning("could not read findings for feedback", extra={"job_id": job_id})
        return {}
    finally:
        with contextlib.suppress(Exception):
            local.unlink(missing_ok=True)
            scratch.rmdir()

    return {
        str(f.get("fid")): f
        for f in (document.get("findings") or [])
        if isinstance(f, dict) and f.get("fid")
    }


def _abstentions_index(job: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """The rules this review stood down on, keyed by rule id.

    Read from the job's own summary for the same reason `_findings_index` reads
    the artefact: an abstention-subject submission names the rule a proposal
    would be about, and a rule id the review never stood down on must not
    become an anchor because a browser said it should.
    """
    summary = job.get("summary") or {}
    return {
        str(a.get("rule")): a
        for a in (summary.get("abstentions") or [])
        if isinstance(a, dict) and a.get("rule")
    }


def _markup_model(record: Dict[str, Any]) -> models.Markup:
    return models.Markup(
        id=record.get("id", ""),
        job_id=record.get("job_id", ""),
        page=int(record.get("page") or 1),
        sheet=record.get("sheet", "") or "",
        kind=record.get("kind", "note"),
        geometry=models.MarkupGeometry.model_validate(record.get("geometry") or {}),
        comment=record.get("comment", "") or "",
        colour=record.get("colour", "") or "",
        finding_fid=record.get("finding_fid", "") or "",
        created_at=record.get("created_at") or utcnow(),
    )


def _feedback_model(record: Dict[str, Any], *, include_email: bool) -> models.Feedback:
    finding = record.get("finding") or None
    markup = record.get("markup") or None
    return models.Feedback(
        id=record.get("id", ""),
        job_id=record.get("job_id", ""),
        subject=record.get("subject", "finding"),
        finding_fid=record.get("finding_fid", "") or "",
        rule_id=record.get("rule_id", "") or "",
        sheet=record.get("sheet", "") or "",
        page=int(record.get("page") or 0),
        answers=dict(record.get("answers") or {}),
        comment=record.get("comment", "") or "",
        markup_id=record.get("markup_id", "") or "",
        disposition=record.get("disposition", triage.ESCALATE),
        rationale=record.get("rationale", "") or "",
        triage=models.TriageResult.model_validate(record.get("triage") or {
            "disposition": record.get("disposition", triage.ESCALATE),
            "label": "",
            "rationale": record.get("rationale", "") or "",
        }),
        state=record.get("state", NEW),
        created_at=record.get("created_at") or utcnow(),
        decided_at=record.get("decided_at"),
        decided_by=record.get("decided_by", "") or "",
        decision_note=record.get("decision_note", "") or "",
        issue_url=record.get("issue_url", "") or "",
        email=(record.get("email", "") or "") if include_email else "",
        filename=record.get("filename", "") or "",
        applied_to_candidate=bool(record.get("applied_to_candidate")),
        finding=models.Finding.model_validate(finding) if finding else None,
        markup=_markup_model(markup) if markup else None,
        sweep=(
            models.MarkupExport.model_validate(record["sweep"])
            if record.get("sweep") else None
        ),
    )


# -- markup ----------------------------------------------------------------
@app.get(
    "/api/jobs/{job_id}/markups",
    response_model=models.MarkupList,
    tags=["training"],
    operation_id="listMarkups",
    summary="The caller's own markup on one review.",
)
def list_markups(
    job_id: str,
    user: User = Depends(current_user),
    store: JobStore = Depends(job_store),
    feedback: FeedbackStore = Depends(feedback_store),
) -> models.MarkupList:
    _require_training()
    _owned_job(job_id, user, store)
    return models.MarkupList(
        markups=[_markup_model(r) for r in feedback.list_markups(job_id, user.uid)]
    )


def _require_geometry(body: "models.MarkupRequest") -> None:
    """Refuse a shape that carries no geometry, where its shape needs one.

    Checked on the way in rather than on the way out: a markup that points
    nowhere is not recoverable later, and the person who could still say where
    they meant is the one drawing it right now.
    """
    complaint = feedback_schema.geometry_complaint(
        str(body.kind), body.geometry.model_dump())
    if complaint:
        raise ApiError(400, errors.INVALID_REQUEST, complaint)


@app.post(
    "/api/jobs/{job_id}/markups",
    response_model=models.Markup,
    tags=["training"],
    operation_id="createMarkup",
    summary="Draw on a sheet.",
    description=(
        "Geometry is in PDF user space, not screen pixels: the viewer zooms and "
        "the window resizes, and a markup recorded in device coordinates would be "
        "in the wrong place at every zoom level but the one it was drawn at."
    ),
)
def create_markup(
    job_id: str,
    body: models.MarkupRequest,
    user: User = Depends(current_user),
    store: JobStore = Depends(job_store),
    feedback: FeedbackStore = Depends(feedback_store),
) -> models.Markup:
    _require_training()
    record = _owned_job(job_id, user, store)

    pages = int(record.get("pages") or 0)
    if pages and body.page > pages:
        raise ApiError(
            400, errors.INVALID_REQUEST,
            f"This set has {pages} sheets; there is no page {body.page}.",
        )
    _require_geometry(body)

    stored = feedback.add_markup({
        "job_id": job_id,
        "uid": user.uid,
        "page": body.page,
        "sheet": body.sheet,
        "kind": str(body.kind),
        "geometry": body.geometry.model_dump(),
        "comment": body.comment,
        "colour": body.colour,
        "finding_fid": body.finding_fid,
    })
    # Never the comment and never the geometry: markup can quote what is drawn
    # on a client's sheet, and CLAUDE.md forbids logging PDF contents.
    log.info(
        "markup created",
        extra={"job_id": job_id, "uid": user.uid, "kind": body.kind, "page": body.page},
    )
    return _markup_model(stored)


@app.patch(
    "/api/jobs/{job_id}/markups/{markup_id}",
    response_model=models.Markup,
    tags=["training"],
    operation_id="updateMarkup",
    summary="Edit a markup's comment or geometry.",
)
def update_markup(
    job_id: str,
    markup_id: str,
    body: models.MarkupRequest,
    user: User = Depends(current_user),
    store: JobStore = Depends(job_store),
    feedback: FeedbackStore = Depends(feedback_store),
) -> models.Markup:
    _require_training()
    _owned_job(job_id, user, store)

    existing = feedback.get_markup(markup_id)
    if not existing or existing.get("uid") != user.uid or existing.get("job_id") != job_id:
        raise ApiError(404, errors.NOT_FOUND, "No such markup.")
    _require_geometry(body)

    feedback.update_markup(
        markup_id,
        page=body.page,
        sheet=body.sheet,
        kind=str(body.kind),
        geometry=body.geometry.model_dump(),
        comment=body.comment,
        colour=body.colour,
        finding_fid=body.finding_fid,
    )
    return _markup_model(feedback.get_markup(markup_id) or existing)


@app.delete(
    "/api/jobs/{job_id}/markups/{markup_id}",
    response_model=models.MarkupList,
    tags=["training"],
    operation_id="deleteMarkup",
    summary="Remove a markup.",
    description=(
        "Answers with the markup that is left rather than an empty 204, so the "
        "viewer redraws from what the server actually holds instead of from its "
        "own optimistic guess at it."
    ),
)
def delete_markup(
    job_id: str,
    markup_id: str,
    user: User = Depends(current_user),
    store: JobStore = Depends(job_store),
    feedback: FeedbackStore = Depends(feedback_store),
) -> models.MarkupList:
    _require_training()
    _owned_job(job_id, user, store)

    existing = feedback.get_markup(markup_id)
    if not existing or existing.get("uid") != user.uid or existing.get("job_id") != job_id:
        raise ApiError(404, errors.NOT_FOUND, "No such markup.")

    feedback.delete_markup(markup_id)
    return models.MarkupList(
        markups=[_markup_model(r) for r in feedback.list_markups(job_id, user.uid)]
    )


def _build_export(
    job_id: str,
    job: Dict[str, Any],
    user: User,
    records: List[Dict[str, Any]],
) -> models.MarkupExport:
    """One annotated pass, as a document.

    Sorted by sheet and then by how far down the page the annotation sits, which
    is the order somebody reads a drawing in. `text` is generated here rather
    than in the browser so the copy the owner reads and the copy the reviewer
    downloaded are the same bytes.
    """
    markups = [_markup_model(r) for r in records]
    markups.sort(key=lambda m: (m.page, m.geometry.y0, m.geometry.x0))

    counts: Dict[str, int] = {}
    for markup in markups:
        counts[str(markup.kind)] = counts.get(str(markup.kind), 0) + 1

    project = (job.get("options") or {}).get("project_name") or ""
    filename = job.get("filename", "")

    lines: List[str] = [
        f"Markup pass — {project or filename}",
        f"Review {job_id} · {len(markups)} annotation"
        f"{'' if len(markups) == 1 else 's'}"
        f" on {len({m.page for m in markups})} sheet"
        f"{'' if len({m.page for m in markups}) == 1 else 's'}",
        "",
    ]
    current = None
    for markup in markups:
        if markup.page != current:
            current = markup.page
            label = f"Sheet {markup.sheet}" if markup.sheet else f"Page {markup.page}"
            lines.append(f"── {label} (page {markup.page}) " + "─" * 20)
        # The colour is a category, so it belongs in the line rather than being
        # lost the moment this leaves the screen.
        tag = f"[{markup.colour}] " if markup.colour else ""
        anchor = f" (about {markup.finding_fid})" if markup.finding_fid else ""
        body = markup.comment.strip() or "— no comment —"
        lines.append(f"  {tag}{markup.kind}{anchor}: {body}")
    if not markups:
        lines.append("Nothing was marked up on this review.")

    return models.MarkupExport(
        job_id=job_id,
        filename=filename,
        project_name=project,
        exported_at=utcnow(),
        exported_by=user.email or "",
        markups=markups,
        counts=counts,
        commented=sum(1 for m in markups if m.comment.strip()),
        sheets=sorted({m.page for m in markups}),
        text="\n".join(lines),
    )


@app.get(
    "/api/jobs/{job_id}/markups/export",
    response_model=models.MarkupExport,
    tags=["training"],
    operation_id="exportMarkups",
    summary="The whole annotated pass, in one document.",
    description=(
        "What a reviewer takes away at the end of a session — every annotation "
        "with its comment, its sheet and its geometry, plus a plain-text rendering "
        "of the same thing. The identical payload is what the owner reads when a "
        "pass is submitted, so nobody submits something they could not inspect "
        "first."
    ),
)
def export_markups(
    job_id: str,
    user: User = Depends(current_user),
    store: JobStore = Depends(job_store),
    feedback: FeedbackStore = Depends(feedback_store),
) -> models.MarkupExport:
    _require_training()
    job = _owned_job(job_id, user, store)
    return _build_export(job_id, job, user, feedback.list_markups(job_id, user.uid))


# -- feedback --------------------------------------------------------------
@app.get(
    "/api/jobs/{job_id}/feedback",
    response_model=models.FeedbackList,
    tags=["training"],
    operation_id="listJobFeedback",
    summary="The caller's own feedback on one review.",
)
def list_job_feedback(
    job_id: str,
    user: User = Depends(current_user),
    store: JobStore = Depends(job_store),
    feedback: FeedbackStore = Depends(feedback_store),
) -> models.FeedbackList:
    _require_training()
    _owned_job(job_id, user, store)
    return models.FeedbackList(feedback=[
        _feedback_model(r, include_email=False)
        for r in feedback.list_feedback_for_job(job_id, user.uid)
    ])


@app.post(
    "/api/jobs/{job_id}/feedback",
    response_model=models.FeedbackAccepted,
    tags=["training"],
    operation_id="submitFeedback",
    summary="Say what this review got right and wrong.",
    description=(
        "Answers are validated against the taxonomy `/api/config` publishes; an "
        "aspect or verdict this build does not know is a 400 naming it rather than "
        "a silently dropped answer. The triage verdict comes back in the response, "
        "so somebody who reports a misread table is told at once that it needs "
        "engine work and is not a knob."
    ),
)
def submit_feedback(
    job_id: str,
    body: models.FeedbackRequest,
    user: User = Depends(current_user),
    store: JobStore = Depends(job_store),
    files: Storage = Depends(file_store),
    feedback: FeedbackStore = Depends(feedback_store),
) -> models.FeedbackAccepted:
    _require_training()
    job = _owned_job(job_id, user, store)

    if job.get("state") != DONE:
        raise ApiError(
            409, errors.NOT_READY,
            "This review has not finished, so there is nothing to give feedback on.",
        )

    problems = feedback_schema.validate(str(body.subject), body.answers)
    if problems:
        raise ApiError(400, errors.INVALID_REQUEST, " ".join(problems))

    if body.subject == feedback_schema.SUBJECT_SWEEP:
        # A sweep is the markup bundle, and this endpoint carries no bundle.
        # Routing it here would record a submission with nothing in it.
        raise ApiError(
            400, errors.INVALID_REQUEST,
            "A marked-up pass is submitted through POST "
            f"/api/jobs/{job_id}/markups/submit, which attaches the markup.",
        )

    if body.subject == feedback_schema.SUBJECT_FINDING and not body.finding_fid:
        raise ApiError(
            400, errors.INVALID_REQUEST,
            "Feedback about a finding has to say which finding.",
        )

    if body.subject == feedback_schema.SUBJECT_ABSTENTION and not body.rule_id:
        raise ApiError(
            400, errors.INVALID_REQUEST,
            "Feedback about a rule that stood down has to say which rule.",
        )

    finding: Dict[str, Any] = {}
    if body.finding_fid:
        finding = _findings_index(job_id, files).get(body.finding_fid, {})
        if not finding:
            raise ApiError(404, errors.NOT_FOUND, "No such finding in this review.")

    abstention: Dict[str, Any] = {}
    if body.rule_id:
        abstention = _abstentions_index(job).get(body.rule_id, {})
        if not abstention:
            raise ApiError(
                404, errors.NOT_FOUND,
                "This review did not stand down on that rule, so there is nothing "
                "to report about it.",
            )

    markup: Dict[str, Any] = {}
    if body.markup_id:
        stored_markup = feedback.get_markup(body.markup_id)
        if (not stored_markup or stored_markup.get("uid") != user.uid
                or stored_markup.get("job_id") != job_id):
            raise ApiError(404, errors.NOT_FOUND, "No such markup.")
        markup = stored_markup

    if body.subject == feedback_schema.SUBJECT_COVERAGE and not markup:
        raise ApiError(
            400, errors.INVALID_REQUEST,
            "A report about something the review missed has to point at where on "
            "the sheet it was missed. Draw on the sheet first.",
        )

    declared = job.get("declaration") or {}
    mode = (job.get("options") or {}).get("mode", "standard")
    candidate = feedback.candidate_profile(user.uid)

    # An abstention names its own rule; a finding carries one. Either way the
    # value comes from what the service produced, never from the request body
    # alone — it decides which rule a calibration proposal would move.
    rule_id = finding.get("rule_id", "") or str(abstention.get("rule", ""))

    verdict = triage.triage(
        subject=str(body.subject),
        answers=body.answers,
        rule_id=rule_id,
        comment=body.comment,
        occupancy_group=declared.get("occupancy_group") or "",
        finding=finding or None,
        profile=candidate,
    )

    # An auto-tunable proposal lands in the submitter's own candidate profile
    # straight away, so training mode shows them the effect of their own
    # feedback on their next run. Production is untouched: it reviews against
    # the active profile, and only the owner can promote anything into that.
    applied = False
    if verdict.changes and verdict.disposition in (triage.AUTO_TUNABLE, triage.CONFIRMATION):
        candidate = candidate.with_changes(
            verdict.changes,
            label="Training candidate",
            note=f"from feedback on {job_id}",
            created_by=user.email,
        )
        feedback.save_profile(candidate)
        applied = True

    stored = feedback.add_feedback({
        "job_id": job_id,
        "uid": user.uid,
        "email": user.email,
        "filename": job.get("filename", ""),
        "mode": mode,
        "subject": str(body.subject),
        "finding_fid": body.finding_fid,
        "rule_id": rule_id,
        "abstention": abstention or None,
        "sheet": finding.get("sheet") or markup.get("sheet", ""),
        "page": int(finding.get("page") or markup.get("page") or 0),
        "answers": dict(body.answers),
        "comment": body.comment,
        "markup_id": body.markup_id,
        "finding": finding or None,
        "markup": markup or None,
        "disposition": verdict.disposition,
        "rationale": verdict.rationale,
        "triage": verdict.to_dict(),
        "applied_to_candidate": applied,
    })

    log.info(
        "feedback submitted",
        extra={
            "job_id": job_id,
            "uid": user.uid,
            "feedback_id": stored["id"],
            "subject": str(body.subject),
            "disposition": verdict.disposition,
            "signals": ",".join(verdict.signals),
            "applied_to_candidate": applied,
        },
    )

    # Mail can take thirty seconds on a bad SMTP day. The person has already
    # been told their feedback was received, so notifying must not be what they
    # wait on.
    if verdict.disposition in notify.URGENT and _pool is not None:
        _pool.submit(_notify_owner, dict(stored), dict(job), feedback)

    return models.FeedbackAccepted(
        id=stored["id"],
        triage=models.TriageResult.model_validate(verdict.to_dict()),
        applied_to_candidate=applied,
        candidate_version=candidate.version,
        message=_feedback_message(verdict, applied),
    )


@app.post(
    "/api/jobs/{job_id}/markups/submit",
    response_model=models.FeedbackAccepted,
    tags=["training"],
    operation_id="submitMarkupPass",
    summary="Hand a marked-up pass over for review.",
    description=(
        "Submits every annotation the caller has made on this review as one piece "
        "of feedback, with the whole bundle attached as a snapshot. The bundle is "
        "read from the server's own store rather than from the request, so a pass "
        "that was never drawn cannot be submitted, and editing a markup afterwards "
        "does not change what the owner was handed."
    ),
)
def submit_markup_pass(
    job_id: str,
    body: models.SweepRequest,
    user: User = Depends(current_user),
    store: JobStore = Depends(job_store),
    feedback: FeedbackStore = Depends(feedback_store),
) -> models.FeedbackAccepted:
    _require_training()
    job = _owned_job(job_id, user, store)

    if job.get("state") != DONE:
        raise ApiError(
            409, errors.NOT_READY,
            "This review has not finished, so there is nothing to mark up.",
        )

    problems = feedback_schema.validate(feedback_schema.SUBJECT_SWEEP, body.answers)
    if problems:
        raise ApiError(400, errors.INVALID_REQUEST, " ".join(problems))

    records = feedback.list_markups(job_id, user.uid)
    if not records:
        raise ApiError(
            400, errors.INVALID_REQUEST,
            "There is no markup on this review to submit. Draw on the sheets first.",
        )

    export = _build_export(job_id, job, user, records)
    declared = job.get("declaration") or {}
    candidate = feedback.candidate_profile(user.uid)

    # No `rule_id`: a sweep is about the review, not about one rule, so there is
    # no lever it could argue for and nothing for the overlay to move. Every
    # defect verdict on this aspect is COMPONENT or JUDGEMENT, so it routes to a
    # person by construction rather than by a special case here.
    verdict = triage.triage(
        subject=feedback_schema.SUBJECT_SWEEP,
        answers=body.answers,
        comment=body.comment,
        occupancy_group=declared.get("occupancy_group") or "",
        profile=candidate,
    )

    stored = feedback.add_feedback({
        "job_id": job_id,
        "uid": user.uid,
        "email": user.email,
        "filename": job.get("filename", ""),
        "mode": (job.get("options") or {}).get("mode", "standard"),
        "subject": feedback_schema.SUBJECT_SWEEP,
        "finding_fid": "",
        "rule_id": "",
        "sheet": "",
        "page": export.sheets[0] if export.sheets else 0,
        "answers": dict(body.answers),
        "comment": body.comment,
        "markup_id": "",
        "sweep": export.model_dump(mode="json"),
        "disposition": verdict.disposition,
        "rationale": verdict.rationale,
        "triage": verdict.to_dict(),
        "applied_to_candidate": False,
    })

    log.info(
        "markup pass submitted",
        extra={
            "job_id": job_id,
            "uid": user.uid,
            "feedback_id": stored["id"],
            "markups": len(export.markups),
            "sheets": len(export.sheets),
            "disposition": verdict.disposition,
        },
    )

    if verdict.disposition in notify.URGENT and _pool is not None:
        _pool.submit(_notify_owner, dict(stored), dict(job), feedback)

    return models.FeedbackAccepted(
        id=stored["id"],
        triage=models.TriageResult.model_validate(verdict.to_dict()),
        applied_to_candidate=False,
        candidate_version=candidate.version,
        message=(
            f"{len(export.markups)} annotation"
            f"{'' if len(export.markups) == 1 else 's'} on "
            f"{len(export.sheets)} sheet{'' if len(export.sheets) == 1 else 's'} "
            "handed over. " + _feedback_message(verdict, False)
        ),
    )


def _notify_owner(record: Dict[str, Any], job: Dict[str, Any],
                  feedback: FeedbackStore) -> None:
    """Runs on the pool. Never raises — nothing is waiting on it."""
    try:
        result = notify.notify_owner(record, job)
        feedback.update_feedback(record["id"], notified=result.startswith("sent"))
    except Exception:
        log.exception("owner notification failed", extra={"feedback_id": record.get("id")})


def _feedback_message(verdict: "triage.TriageVerdict", applied: bool) -> str:
    """What the submitter is told. Specific, because vague thanks teaches nothing.

    Somebody who takes the trouble to report a misread schedule should learn
    that it is an extraction bug rather than a setting — that is what makes the
    next piece of feedback they write more useful.
    """
    if verdict.disposition == triage.CONFIRMATION:
        return "Recorded as a confirmation. Agreement is signal too — it is what stops one dissent from re-levelling a rule."
    if verdict.disposition == triage.AUTO_TUNABLE:
        if applied:
            return ("Applied to your training profile, so your next training run will "
                    "reflect it. It is queued for approval before it reaches anyone else.")
        return "Queued for approval."
    if verdict.disposition == triage.NEEDS_COMPONENT:
        return ("This one needs engine work rather than a setting — the calibration "
                "overlay runs over a finished findings list and cannot re-read a "
                "drawing or invent a check. It has been raised as a build item.")
    return "Sent for review. Nothing has been changed automatically."


# -- the owner's queue -----------------------------------------------------
@app.get(
    "/api/admin/overview",
    response_model=models.AdminOverview,
    tags=["admin"],
    operation_id="adminOverview",
    summary="What is waiting, and which channels are live.",
)
def admin_overview(
    owner: User = Depends(current_owner),
    feedback: FeedbackStore = Depends(feedback_store),
) -> models.AdminOverview:
    open_rows = feedback.list_feedback(state=NEW, limit=200)
    counts: Dict[str, int] = {}
    for row in open_rows:
        key = row.get("disposition", "")
        counts[key] = counts.get(key, 0) + 1

    active = feedback.active_profile()
    return models.AdminOverview(
        open_feedback=len(open_rows),
        counts=counts,
        mail=models.MailStatus(configured=mailer.configured(), status=mailer.status()),
        github=notify.github_status(),
        assist=assist.status(),
        active_version=active.version,
        calibrated_rules=len(active.touched()),
    )


@app.get(
    "/api/admin/feedback",
    response_model=models.FeedbackList,
    tags=["admin"],
    operation_id="adminListFeedback",
    summary="The feedback queue, newest first.",
)
def admin_list_feedback(
    state: Optional[str] = None,
    disposition: Optional[str] = None,
    limit: int = 100,
    owner: User = Depends(current_owner),
    feedback: FeedbackStore = Depends(feedback_store),
) -> models.FeedbackList:
    if state and state not in (NEW, ACCEPTED, REJECTED, ACTIONED):
        raise ApiError(400, errors.INVALID_REQUEST, f"{state!r} is not a feedback state.")
    if disposition and disposition not in triage.DISPOSITIONS:
        raise ApiError(400, errors.INVALID_REQUEST, f"{disposition!r} is not a disposition.")

    rows = feedback.list_feedback(
        state=state, disposition=disposition, limit=max(1, min(int(limit), 200))
    )
    return models.FeedbackList(
        feedback=[_feedback_model(r, include_email=True) for r in rows]
    )


@app.get(
    "/api/admin/feedback/{feedback_id}",
    response_model=models.Feedback,
    tags=["admin"],
    operation_id="adminGetFeedback",
)
def admin_get_feedback(
    feedback_id: str,
    owner: User = Depends(current_owner),
    feedback: FeedbackStore = Depends(feedback_store),
) -> models.Feedback:
    record = feedback.get_feedback(feedback_id)
    if not record:
        raise ApiError(404, errors.NOT_FOUND, "No such feedback.")
    return _feedback_model(record, include_email=True)


@app.get(
    "/api/admin/feedback/{feedback_id}/prompt",
    response_model=models.PromptExport,
    tags=["admin"],
    operation_id="adminFeedbackPrompt",
    summary="The feedback as a runnable prompt, in this repo's house style.",
)
def admin_feedback_prompt(
    feedback_id: str,
    owner: User = Depends(current_owner),
    store: JobStore = Depends(job_store),
    feedback: FeedbackStore = Depends(feedback_store),
) -> models.PromptExport:
    record = feedback.get_feedback(feedback_id)
    if not record:
        raise ApiError(404, errors.NOT_FOUND, "No such feedback.")
    job = store.get(record.get("job_id", "")) or {}
    return models.PromptExport(
        feedback_id=feedback_id,
        filename=f"FEEDBACK-PROMPT-{feedback_id}.md",
        markdown=notify.feature_prompt(record, job),
    )


@app.post(
    "/api/admin/feedback/{feedback_id}/issue",
    response_model=models.IssueCreated,
    tags=["admin"],
    operation_id="adminFeedbackIssue",
    summary="Open a GitHub issue for this feedback.",
)
def admin_feedback_issue(
    feedback_id: str,
    owner: User = Depends(current_owner),
    store: JobStore = Depends(job_store),
    feedback: FeedbackStore = Depends(feedback_store),
) -> models.IssueCreated:
    record = feedback.get_feedback(feedback_id)
    if not record:
        raise ApiError(404, errors.NOT_FOUND, "No such feedback.")
    if record.get("issue_url"):
        return models.IssueCreated(
            feedback_id=feedback_id, url=record["issue_url"], status="already open"
        )
    if not notify.github_configured():
        return models.IssueCreated(
            feedback_id=feedback_id, url="", status=notify.github_status()
        )

    job = store.get(record.get("job_id", "")) or {}
    url = notify.create_issue(record, job)
    if url:
        feedback.update_feedback(feedback_id, issue_url=url, state=ACTIONED)
    return models.IssueCreated(
        feedback_id=feedback_id,
        url=url,
        status="opened" if url else "GitHub rejected the request; see the server log",
    )


@app.post(
    "/api/admin/feedback/{feedback_id}/decision",
    response_model=models.Feedback,
    tags=["admin"],
    operation_id="adminDecideFeedback",
    summary="Approve, reject, or mark as actioned.",
    description=(
        "Approving an `auto_tunable` proposal promotes it: the changes are applied "
        "to the active profile, which is written as a new immutable version and "
        "becomes what every standard review runs against. Nothing else changes "
        "production."
    ),
)
def admin_decide_feedback(
    feedback_id: str,
    body: models.DecisionRequest,
    owner: User = Depends(current_owner),
    feedback: FeedbackStore = Depends(feedback_store),
) -> models.Feedback:
    record = feedback.get_feedback(feedback_id)
    if not record:
        raise ApiError(404, errors.NOT_FOUND, "No such feedback.")

    state = {"accept": ACCEPTED, "reject": REJECTED, "action": ACTIONED}[str(body.decision)]

    if body.decision == "accept":
        changes = [
            ProfileChange.from_dict(c)
            for c in ((record.get("triage") or {}).get("changes") or [])
        ]
        if not changes:
            raise ApiError(
                400, errors.INVALID_REQUEST,
                "There is no calibration proposal on this feedback to approve. "
                "Mark it actioned instead.",
            )
        active = feedback.active_profile()
        promoted = active.with_changes(
            changes,
            label=f"Approved from feedback {feedback_id}",
            note=body.note or record.get("rationale", ""),
            created_by=owner.email,
        )
        feedback.promote(promoted, by=owner.email)
        log.info(
            "proposal approved",
            extra={
                "feedback_id": feedback_id,
                "by": owner.email,
                "version": promoted.version,
                "rules": ",".join(sorted({c.rule_id for c in changes})),
            },
        )

    feedback.update_feedback(
        feedback_id,
        state=state,
        decided_at=utcnow(),
        decided_by=owner.email,
        decision_note=body.note,
    )
    return _feedback_model(feedback.get_feedback(feedback_id) or record, include_email=True)


@app.get(
    "/api/admin/calibration",
    response_model=models.CalibrationView,
    tags=["admin"],
    operation_id="adminCalibration",
    summary="The active profile, its history, and every lever there is.",
)
def admin_calibration(
    owner: User = Depends(current_owner),
    feedback: FeedbackStore = Depends(feedback_store),
) -> models.CalibrationView:
    active = feedback.active_profile()
    return models.CalibrationView(
        active=models.CalibrationProfile.model_validate(active.to_dict()),
        versions=[
            models.CalibrationProfile.model_validate(v)
            for v in feedback.profile_versions()
        ],
        knobs=[
            models.CalibrationKnob.model_validate(k) for k in calibration.knob_catalogue()
        ],
        pending=0,
    )


@app.post(
    "/api/admin/digest",
    response_model=models.MailStatus,
    tags=["admin"],
    operation_id="adminSendDigest",
    summary="Mail everything still waiting.",
)
def admin_send_digest(
    owner: User = Depends(current_owner),
    feedback: FeedbackStore = Depends(feedback_store),
) -> models.MailStatus:
    waiting = feedback.list_feedback(state=NEW, limit=200)
    result = notify.digest(waiting)
    return models.MailStatus(configured=mailer.configured(), status=result)


# -- artefacts, on the filesystem backend ---------------------------------
# Stands in for a Cloud Storage signed URL when there is no bucket. Registered
# unconditionally and gated inside the handler, rather than behind a
# module-level `if`: route registration happens at import, and a deployment's
# backend is not knowable then in a test that sets it afterwards.
#
# Deliberately off the published schema. It serves a 17 MB PDF, not a typed
# body, and the URL reaches the client inside `Downloads`, which is typed. The
# rest of the API is the contract; this is a file.
@app.get("/api/artefacts/{blob_path:path}", include_in_schema=False)
def artefact(blob_path: str, expires: str = "", sig: str = "", name: str = ""):
    """One stored artefact, authorised by its signature.

    No bearer token is required and that is the point: this URL is handed to a
    browser to *navigate* to, and a navigation carries no `Authorization`
    header. The HMAC is the authorisation, exactly as it is for a GCS V4 signed
    URL, and it is only minted after ownership of the job has been checked in
    `GET /api/jobs/{job_id}`.
    """
    from fastapi.responses import FileResponse

    if not settings().local_backend:
        # On the GCP backend the browser fetches straight from Cloud Storage
        # and nothing should be asking this service for a blob.
        raise ApiError(404, errors.NOT_FOUND, "No such artefact.")

    if not storage_urls.verify(blob_path, expires, sig, name):
        # One message for a bad signature, a missing one and an expired one.
        # Which of the three it was is not something a caller needs to know.
        raise ApiError(403, errors.FORBIDDEN, "This link is not valid any more.")

    from webapp.devbackend import LocalStorage

    root = LocalStorage().dir.resolve()
    target = (root / blob_path).resolve()
    # Belt and braces behind the signature: a signed path still must not escape
    # the blob root.
    if not str(target).startswith(str(root)) or not target.is_file():
        raise ApiError(404, errors.NOT_FOUND, "No such artefact.")

    media = {"pdf": "application/pdf", "json": "application/json"}.get(
        target.suffix.lstrip("."), "application/octet-stream"
    )
    return FileResponse(target, media_type=media, filename=name or target.name)


# -- the client, when this deployment serves it itself ---------------------
# The Firebase Hosting deployment does not: Hosting serves the bundle from a CDN
# and rewrites only /api/** here, so FBC_STATIC_DIR is unset and nothing below
# runs. It exists for single-origin deployments — one container behind a tunnel,
# or a plain VM — where putting a separate static host in front would buy
# nothing and cost a CORS configuration.
#
# Mounted last, so /api, /healthz and /openapi.json are matched first.
if settings().static_dir:
    from starlette.exceptions import HTTPException as StarletteHTTPException
    from starlette.staticfiles import StaticFiles

    class SinglePageFiles(StaticFiles):
        """Static files with an SPA fallback.

        The Angular router owns paths like /sign-in, which exist in the client
        and not on disk. Without the fallback a reload on one of them 404s.
        """

        async def get_response(self, path: str, scope):
            try:
                return await super().get_response(path, scope)
            except StarletteHTTPException as exc:
                if exc.status_code == 404:
                    return await super().get_response("index.html", scope)
                raise

    _static = Path(settings().static_dir)
    if _static.is_dir():
        app.mount("/", SinglePageFiles(directory=str(_static), html=True), name="client")
        log.info("serving the client from disk", extra={"static_dir": str(_static)})
    else:
        log.warning("FBC_STATIC_DIR does not exist", extra={"static_dir": str(_static)})
