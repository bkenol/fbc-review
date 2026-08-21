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
    on_cloud_run: bool
    dev_unsafe_auth: bool

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
        max_upload_mb=_int("FBC_MAX_UPLOAD_MB", 120),
        max_pages=_int("FBC_MAX_PAGES", 300),
        retain_days=_int("FBC_RETAIN_DAYS", 30),
        signed_url_ttl_seconds=_int("FBC_SIGNED_URL_TTL", 3600),
        rate_per_hour=_int("FBC_RATE_PER_HOUR", 10),
        rate_concurrent=_int("FBC_RATE_CONCURRENT", 3),
        stale_running_minutes=_int("FBC_STALE_RUNNING_MINUTES", 15),
        workers=_int("FBC_WORKERS", 2),
        collection=os.environ.get("FBC_COLLECTION", "reviews"),
        on_cloud_run=on_cloud_run,
        dev_unsafe_auth=dev_unsafe,
    )
