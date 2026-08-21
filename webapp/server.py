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

from fbcreview.options import EDITIONS, OCCUPANCY_GROUPS, SEVERITY_ORDER, ReviewOptions
from fbcreview.rules import registered
from webapp import errors, logging_config, mailer, models, storage, upload
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
    return models.Health(ok=True, service="fbc-review", version=VERSION)


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
            models.Edition(id=k, label=v, available=(k == "fbc2023"))
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
    )


# -- submit ----------------------------------------------------------------
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
    job_stages = stages_for(parsed.convert_raster and bool(source.raster_pages))

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
    )

    log.info(
        "review accepted",
        extra={
            "job_id": job_id,
            "uid": user.uid,
            "email": user.email,
            "pages": pages,
            "bytes": size,
            "occupancy_group": parsed.occupancy_group,
            "sprinklered": parsed.sprinklered,
            "source_kind": source.kind,
            "raster_pages": len(source.raster_pages),
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
        store=store,
        store_files=files,
        convert_raster=parsed.convert_raster,
        raster_pages=list(source.raster_pages),
    )
    return JSONResponse({"id": job_id}, status_code=202)


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


# No route serves HTML. webapp/static/index.html was the reference client and
# was removed once web/ reached parity; the Angular bundle is served by Firebase
# Hosting, which rewrites only /api/** here.
