"""Signed URLs for the local filesystem backend.

Cloud Storage hands the browser a V4 signed URL: a link that carries its own
authorisation, expires, and is fetched directly rather than through the app.
The filesystem backend needs the same shape for a reason that is easy to miss —
**a download is a navigation, and a navigation carries no `Authorization`
header.** The Angular client attaches the Firebase token with an interceptor,
which covers XHR and therefore `findings.json`; it cannot cover the click that
downloads a 17 MB marked-up set. Serving that from a route gated on the bearer
token would 401 every time.

So this mints the local equivalent: a path plus an expiry plus an HMAC over
both. The signature *is* the authorisation, exactly as it is for GCS, and it is
only ever minted after the caller's ownership of the job has been checked.

The one property worth stating: the signing string is length-prefixed. A blob
path and a filename are both attacker-influenced in principle, and joining them
with a separator invites two different pairs that render identically —
`("a/b", "c")` and `("a", "b/c")`. Length prefixes make that impossible.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from functools import lru_cache
from typing import Optional
from urllib.parse import quote, urlencode

from webapp.config import settings

#: Where the local backend serves artefacts. Under `/api` so the Firebase
#: Hosting rewrite and `ng serve`'s proxy both already route it.
ARTEFACT_PREFIX = "/api/artefacts"


@lru_cache(maxsize=1)
def _generated_secret() -> str:
    """A per-process key, used only when none is configured.

    Deliberately not a constant. A default signing key committed to a repository
    is not a default, it is a published private key — anyone could mint a link
    to any artefact on any deployment that forgot to override it. Regenerating
    per process means a restart invalidates outstanding links, which is visible
    and harmless: the client refreshes the job and gets new ones.
    """
    return secrets.token_urlsafe(32)


def _key() -> bytes:
    return (settings().artefact_secret or _generated_secret()).encode("utf-8")


def _payload(blob_path: str, expires: int, download_as: str) -> bytes:
    parts = (blob_path, download_as, str(expires))
    return "|".join(f"{len(p)}:{p}" for p in parts).encode("utf-8")


def sign(blob_path: str, expires: int, download_as: str = "") -> str:
    return hmac.new(_key(), _payload(blob_path, expires, download_as),
                    hashlib.sha256).hexdigest()


def build(blob_path: str, *, download_as: Optional[str] = None,
          ttl_seconds: Optional[int] = None) -> str:
    """A signed, expiring URL for one blob."""
    ttl = ttl_seconds or settings().signed_url_ttl_seconds
    expires = int(time.time()) + int(ttl)
    name = download_as or ""
    query = {"expires": expires, "sig": sign(blob_path, expires, name)}
    if name:
        query["name"] = name
    # `quote` with an empty safe list would escape the separators; blob paths
    # are built by webapp.storage and are always `prefix/job/name`.
    return f"{ARTEFACT_PREFIX}/{quote(blob_path)}?{urlencode(query)}"


def verify(blob_path: str, expires: str, sig: str, download_as: str = "") -> bool:
    """True when this signature is ours and has not expired.

    Order matters only for clarity: both checks run, and the comparison is
    constant-time so a wrong signature does not leak how wrong it was.
    """
    try:
        deadline = int(expires)
    except (TypeError, ValueError):
        return False
    if deadline < time.time():
        return False
    return hmac.compare_digest(sign(blob_path, deadline, download_as), sig or "")
