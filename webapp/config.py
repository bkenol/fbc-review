"""Runtime configuration, read once from the environment.

Every value the service reads is declared here so `DEPLOYMENT.md` can enumerate
them without grepping. Nothing in this module touches the network.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from typing import FrozenSet


def _emails(name: str) -> FrozenSet[str]:
    raw = os.environ.get(name, "").replace(";", ",")
    return frozenset(e.strip().lower() for e in raw.split(",") if e.strip())


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
    stale_running_minutes: int
    workers: int

    # ── misc ──────────────────────────────────────────────────────────────
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
    # Cloud Run always sets K_SERVICE. It is the one signal that cannot be
    # faked by a stray .env, which is why the dev auth bypass keys off it.
    on_cloud_run = bool(os.environ.get("K_SERVICE"))
    dev_unsafe = os.environ.get("FBC_DEV_UNSAFE_AUTH") == "1" and not on_cloud_run

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
        workers=_int("FBC_WORKERS", 2),
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
