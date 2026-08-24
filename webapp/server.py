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
from typing import Any, Dict, Optional

from fastapi import Depends, FastAPI, File, Form, UploadFile
from fastapi.responses import JSONResponse

from fbcreview import declaration_schema
from fbcreview.declaration import ProjectDeclaration
from fbcreview.options import (AVAILABLE_EDITIONS, EDITIONS, OCCUPANCY_GROUPS,
                               SEVERITY_ORDER, ReviewOptions)
from fbcreview.rules import registered
from webapp import errors, logging_config, mailer, models, prefill, storage, upload
from webapp.auth import User, current_user
from webapp.config import settings
from webapp.errors import ApiError
from webapp.jobs import DONE, JobStore, RateLimited, get_job_store, utcnow
from webapp.storage import Storage, get_storage
from webapp.worker import STAGES, run_review, stages_for

logging_config.configure()
log = logging.getLogger("fbc.api")

VERSION = "1.0.0"

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
            "workers": cfg.workers,
            "bucket": cfg.bucket,
            "allowlist_size": len(cfg.allowed_emails),
            "on_cloud_run": cfg.on_cloud_run,
        },
    )
    if cfg.dev_unsafe_auth:
        log.warning("FBC_DEV_UNSAFE_AUTH=1 - every request is treated as signed in")

    # Cloud Run scales to zero; anything left running belongs to a dead
    # instance. Failing to reach Firestore here must not stop the service
    # coming up, or a transient outage becomes a crash loop.
    try:
        (_dev_jobs() if cfg.dev_unsafe_auth else get_job_store()).fail_stale_running()
    except Exception:
        log.exception("startup sweep for orphaned jobs failed")

    yield

    _pool.shutdown(wait=False, cancel_futures=True)


app = FastAPI(
    title="FBC Code Review",
    version=VERSION,
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
# In local development these resolve to filesystem stand-ins so the Angular
# client can be run and used without a GCP project. settings().dev_unsafe_auth
# cannot be true when K_SERVICE is set, so neither branch is reachable on Cloud
# Run — see webapp/devbackend.py.
def job_store() -> JobStore:
    if settings().dev_unsafe_auth:
        return _dev_jobs()
    return get_job_store()


def file_store() -> Storage:
    if settings().dev_unsafe_auth:
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


# -- config ----------------------------------------------------------------
@app.get(
    "/api/config",
    response_model=models.ConfigResponse,
    tags=["config"],
    operation_id="getConfig",
)
def config(user: User = Depends(current_user)) -> models.ConfigResponse:
    cfg = settings()
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
    )
    return JSONResponse({"id": job_id}, status_code=202)


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

    summary = None
    downloads = None
    if state == DONE and record.get("summary"):
        summary = models.Summary.model_validate(record["summary"])
        job_id = record["id"]
        pdf_name = summary.pdf_name or "markup.pdf"
        downloads = models.Downloads(
            markup_pdf=files.signed_url(
                storage.output_path(job_id, storage.MARKUP), download_as=pdf_name
            ),
            findings_json=files.signed_url(storage.output_path(job_id, storage.FINDINGS)),
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
        downloads=downloads,
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


# -- dev-only artefact download -------------------------------------------
# Stands in for a Cloud Storage signed URL on a laptop. Registered only when the
# dev flag is on, so the deployed service has no such route at all — artefacts
# there are fetched browser-to-GCS and never cross the app.
if settings().dev_unsafe_auth:
    from fastapi.responses import FileResponse

    @app.get("/_dev/blob/{blob_path:path}", include_in_schema=False)
    def dev_blob(blob_path: str, filename: str | None = None):
        from webapp.devbackend import LocalStorage

        root = LocalStorage().dir.resolve()
        target = (root / blob_path).resolve()
        # Refuse anything that escapes the blob root.
        if not str(target).startswith(str(root)) or not target.is_file():
            raise ApiError(404, errors.NOT_FOUND, "No such artefact.")
        return FileResponse(
            target,
            media_type="application/pdf" if target.suffix == ".pdf" else "application/json",
            filename=filename or target.name,
        )


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
