"""Runtime configuration, read once from the environment.

Every value the service reads is declared here so `DEPLOYMENT.md` can enumerate
them without grepping. Nothing in this module touches the network.

`secrets/local.env` is folded in first, where it exists — see `webapp.envfile`
for what that is and why anything already set beats it. On Cloud Run there is
no such file and this behaves exactly as it did before it existed.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from typing import FrozenSet

from webapp import envfile


def _emails(name: str) -> FrozenSet[str]:
    raw = os.environ.get(name, "").replace(";", ",")
    return frozenset(e.strip().lower() for e in raw.split(",") if e.strip())


#: Which stores the service talks to. `gcp` is Firestore plus Cloud Storage;
#: `local` is the filesystem stand-ins in `webapp/devbackend.py`.
BACKEND_GCP, BACKEND_LOCAL = "gcp", "local"


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ[name])
    except (KeyError, ValueError):
        return default


@dataclass(frozen=True)
class Settings:
    # ── identity ──────────────────────────────────────────────────────────
    project_id: str
    bucket: str
    service_account_email: str
    allowed_emails: FrozenSet[str]
    #: Who may read the feedback queue, decide proposals and promote a
    #: calibration profile. A strict subset of the allowlist in practice, but
    #: checked independently: being allowed to run a review is not being allowed
    #: to change what every future review reports. Empty means nobody, which is
    #: the right default — an unset variable must not grant administration.
    owner_emails: FrozenSet[str]

    # ── limits ────────────────────────────────────────────────────────────
    max_upload_mb: int
    max_pages: int
    retain_days: int
    signed_url_ttl_seconds: int
    rate_per_hour: int
    rate_concurrent: int
    #: A PDF review still "running" this long after it started is taken to
    #: belong to a dead instance and marked interrupted.
    stale_running_minutes: int
    #: The same for a drawing review, which runs longer: two CAD steps of up to
    #: `cad_timeout_seconds` each (20 min at the defaults), the AI reading of
    #: its sheets (up to 15 min), and the rules and the result review between.
    #: Kept apart from the PDF window — one window for both left an orphaned
    #: PDF review "running" for three times as long once drawings arrived.
    stale_drawing_minutes: int
    workers: int
    #: Seconds one drawing's ingest (DWG conversion, reading, plotting every
    #: layout) or its DXF markup may run before the subprocess is killed. The
    #: 23 MB reference DWG ingests in 186 s and marks up in 85 s, so 600 s is
    #: three times the measured worst step without letting a pathological drawing
    #: hold a worker for long. A drawing review spends up to two of these, so
    #: `FBC_STALE_DRAWING_MINUTES` must stay well above 2 × this. Drawing jobs run
    #: on threads of their own, as many as `cad_concurrency`, so none waits for
    #: a CAD slot while counted as running.
    cad_timeout_seconds: int
    #: How many CAD subprocesses one instance runs at once. Reading the reference
    #: drawing peaks at about 1.1 GB, so in the 2 GiB a 4 GiB instance keeps for
    #: drawings, one at a time is what fits; later CAD jobs wait for the slot.
    cad_concurrency: int

    # ── misc ──────────────────────────────────────────────────────────────
    #: `gcp` or `local`. Deliberately independent of `dev_unsafe_auth`, which
    #: used to select both. Cloud Storage is the one piece of this stack that
    #: requires an open billing account, so a deployment that cannot have one
    #: needs to run the filesystem backend **with real authentication** — and
    #: while those two were the same flag, it could not.
    backend: str

    collection: str
    feedback_collection: str
    markup_collection: str
    calibration_collection: str
    on_cloud_run: bool
    dev_unsafe_auth: bool

    # ── training mode ─────────────────────────────────────────────────────
    #: Off by default. Training mode writes to the feedback collections and
    #: runs reviews against a per-user candidate profile, so a deployment that
    #: has not opted in behaves exactly as it did before the feature existed.
    training_enabled: bool
    #: Repository escalations become issues in, as `owner/repo`. Inert without
    #: a token, exactly like SMTP.
    github_repo: str

    @property
    def local_backend(self) -> bool:
        return self.backend == BACKEND_LOCAL

    @property
    def artefact_secret(self) -> str:
        """The key that signs local artefact URLs.

        Read at use rather than captured, as `github_token` is: it is a secret
        and this dataclass gets logged.

        With nothing set, `webapp.storage_urls` generates one per process. That
        is a deliberate fail-safe rather than a convenience — links stop working
        when the service restarts, which is visible and harmless, where a
        hard-coded default would be a signing key published in the repository.
        Set it explicitly for a service that restarts often, or one run with
        more than one uvicorn worker: a link minted by one process is not valid
        at another that generated its own.
        """
        return os.environ.get("FBC_ARTEFACT_SECRET", "")

    @property
    def github_token(self) -> str:
        # Read at use rather than captured: a token in a frozen dataclass ends
        # up in every repr of the settings object, and settings gets logged.
        return os.environ.get("FBC_GITHUB_TOKEN", "")

    def is_owner(self, email: str) -> bool:
        return bool(email) and email.lower() in self.owner_emails

    #: When set, the API also serves the built Angular bundle from this path.
    #: Off in the Firebase Hosting deployment, where Hosting serves the client
    #: from a CDN and rewrites only /api/** here. Used for single-origin
    #: deployments — one container behind a tunnel or a plain VM — where there
    #: is no CDN in front and cross-origin would mean CORS for no benefit.
    static_dir: str

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024


@lru_cache(maxsize=1)
def settings() -> Settings:
    # Before anything is read, not at import: this is the one function every
    # value in the service comes through, so doing it here means no module can
    # be imported in an order that reads a variable before the file is folded
    # in. It is a no-op when the file is absent, which is every deployment
    # that is not somebody's own machine.
    envfile.load()

    # Cloud Run always sets K_SERVICE. It is the one signal that cannot be
    # faked by a stray .env, which is why the dev auth bypass keys off it.
    on_cloud_run = bool(os.environ.get("K_SERVICE"))
    dev_unsafe = os.environ.get("FBC_DEV_UNSAFE_AUTH") == "1" and not on_cloud_run

    # An unset or unrecognised value falls back to what the dev flag used to
    # mean on its own, so every existing invocation keeps working unchanged.
    backend = os.environ.get("FBC_BACKEND", "").strip().lower()
    if backend not in (BACKEND_GCP, BACKEND_LOCAL):
        backend = BACKEND_LOCAL if dev_unsafe else BACKEND_GCP
    # Refused on Cloud Run for the same reason the dev flag is, and a worse one:
    # the instance filesystem is ephemeral and instances are replaced freely, so
    # the local backend there loses every job and every artefact without saying
    # anything. Failing over to the real stores is the recoverable direction.
    if on_cloud_run:
        backend = BACKEND_GCP

    return Settings(
        project_id=os.environ.get("FBC_PROJECT_ID", "")
        or os.environ.get("GOOGLE_CLOUD_PROJECT", ""),
        bucket=os.environ.get("FBC_BUCKET", ""),
        service_account_email=os.environ.get("FBC_SIGNER_SA", ""),
        allowed_emails=_emails("FBC_ALLOWED_EMAILS"),
        owner_emails=_emails("FBC_OWNER_EMAILS"),
        max_upload_mb=_int("FBC_MAX_UPLOAD_MB", 120),
        max_pages=_int("FBC_MAX_PAGES", 300),
        retain_days=_int("FBC_RETAIN_DAYS", 30),
        signed_url_ttl_seconds=_int("FBC_SIGNED_URL_TTL", 3600),
        rate_per_hour=_int("FBC_RATE_PER_HOUR", 10),
        rate_concurrent=_int("FBC_RATE_CONCURRENT", 3),
        stale_running_minutes=_int("FBC_STALE_RUNNING_MINUTES", 15),
        stale_drawing_minutes=_int("FBC_STALE_DRAWING_MINUTES", 60),
        workers=_int("FBC_WORKERS", 2),
        cad_timeout_seconds=max(30, _int("FBC_CAD_TIMEOUT_S", 600)),
        cad_concurrency=max(1, _int("FBC_CAD_CONCURRENCY", 1)),
        backend=backend,
        collection=os.environ.get("FBC_COLLECTION", "reviews"),
        feedback_collection=os.environ.get("FBC_FEEDBACK_COLLECTION", "feedback"),
        markup_collection=os.environ.get("FBC_MARKUP_COLLECTION", "markups"),
        calibration_collection=os.environ.get("FBC_CALIBRATION_COLLECTION", "calibration"),
        training_enabled=os.environ.get("FBC_TRAINING_MODE") == "1",
        github_repo=os.environ.get("FBC_GITHUB_REPO", ""),
        on_cloud_run=on_cloud_run,
        dev_unsafe_auth=dev_unsafe,
        static_dir=os.environ.get("FBC_STATIC_DIR", ""),
    )
