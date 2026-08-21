"""Cloud Storage: the upload and the two artefacts.

Layout, one prefix per job:

    uploads/{job_id}/{original_filename}
    outputs/{job_id}/markup.pdf
    outputs/{job_id}/findings.json

The bucket is private — uniform bucket-level access, public access prevention
enforced — and the browser reaches the artefacts through V4 signed URLs only.
A finished set is 16-19 MB; streaming that back through Cloud Run would hold an
instance open for the whole download and burn egress twice.
"""
from __future__ import annotations

import datetime as dt
import logging
from functools import lru_cache
from typing import Optional

from google.auth import default as google_default
from google.auth.transport import requests as google_requests
from google.cloud import storage

from webapp.config import settings

log = logging.getLogger("fbc.storage")

MARKUP = "markup.pdf"
FINDINGS = "findings.json"


def upload_path(job_id: str, filename: str) -> str:
    return f"uploads/{job_id}/{filename}"


def output_path(job_id: str, name: str) -> str:
    return f"outputs/{job_id}/{name}"


class Storage:
    """Thin wrapper over one bucket. Constructed once per process."""

    def __init__(self, bucket_name: Optional[str] = None):
        cfg = settings()
        self.bucket_name = bucket_name or cfg.bucket
        if not self.bucket_name:
            raise RuntimeError("FBC_BUCKET is not set.")
        self._client = storage.Client(project=cfg.project_id or None)
        self._bucket = self._client.bucket(self.bucket_name)
        self._signer_email = cfg.service_account_email or ""

    # ── transfer ──────────────────────────────────────────────────────────
    def upload_file(self, local_path: str, blob_path: str, content_type: str) -> int:
        blob = self._bucket.blob(blob_path)
        blob.upload_from_filename(local_path, content_type=content_type)
        blob.reload()
        return int(blob.size or 0)

    def download_to(self, blob_path: str, local_path: str) -> None:
        self._bucket.blob(blob_path).download_to_filename(local_path)

    def delete(self, blob_path: str) -> None:
        try:
            self._bucket.blob(blob_path).delete()
        except Exception:  # already gone, or lifecycle beat us to it
            log.warning("could not delete blob", extra={"blob": blob_path})

    # ── signing ───────────────────────────────────────────────────────────
    def signed_url(
        self,
        blob_path: str,
        *,
        download_as: Optional[str] = None,
        ttl_seconds: Optional[int] = None,
    ) -> str:
        """A V4 signed GET URL, signed without a key file.

        Cloud Run's credentials carry no private key, so the client library
        signs through the IAM Credentials `signBlob` API instead. That needs
        the runtime service account to hold
        `roles/iam.serviceAccountTokenCreator` **on itself** — the binding this
        deployment most often forgets.
        """
        cfg = settings()
        ttl = ttl_seconds or cfg.signed_url_ttl_seconds
        blob = self._bucket.blob(blob_path)

        kwargs = {
            "version": "v4",
            "expiration": dt.timedelta(seconds=ttl),
            "method": "GET",
        }
        if download_as:
            kwargs["response_disposition"] = f'attachment; filename="{download_as}"'

        email, token = _signer_identity(self._signer_email)
        if email and token:
            kwargs["service_account_email"] = email
            kwargs["access_token"] = token

        return blob.generate_signed_url(**kwargs)

    def expires_at(self, ttl_seconds: Optional[int] = None) -> dt.datetime:
        ttl = ttl_seconds or settings().signed_url_ttl_seconds
        return dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=ttl)


def _signer_identity(configured_email: str):
    """Return (service_account_email, fresh access token) for IAM signing.

    Returns (None, None) when the ambient credentials hold a private key — a
    local key-file or an impersonated credential can sign on their own and
    passing a token would override that for no reason.
    """
    try:
        creds, _ = google_default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        if hasattr(creds, "signer_email") and hasattr(creds, "sign_bytes"):
            try:
                creds.sign_bytes(b"probe")
                return None, None  # can sign locally
            except Exception:
                pass
        creds.refresh(google_requests.Request())
        email = configured_email or getattr(creds, "service_account_email", "") or getattr(
            creds, "signer_email", ""
        )
        return email or None, creds.token
    except Exception:
        log.exception("could not resolve signing identity")
        return None, None


@lru_cache(maxsize=1)
def get_storage() -> Storage:
    return Storage()
