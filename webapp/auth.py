"""Firebase Authentication, verified server-side.

Every route except `/healthz` depends on `current_user`. The allowlist is
checked here and only here — a check in the browser is decoration, because the
browser is the thing being authenticated.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Optional

import firebase_admin
from fastapi import Depends, Header
from firebase_admin import auth as fb_auth

from webapp.config import settings
from webapp.errors import FORBIDDEN, UNAUTHENTICATED, ApiError

log = logging.getLogger("fbc.auth")

_init_lock = threading.Lock()

CONTACT = "Ask the administrator to add your address to the allowlist."


@dataclass(frozen=True)
class User:
    uid: str
    email: str


def _ensure_app() -> None:
    """Initialise the Admin SDK once, from the ambient service account."""
    if firebase_admin._apps:
        return
    with _init_lock:
        if not firebase_admin._apps:
            cfg = settings()
            options = {"projectId": cfg.project_id} if cfg.project_id else None
            firebase_admin.initialize_app(options=options)


def _bearer(header: Optional[str]) -> str:
    if not header:
        raise ApiError(401, UNAUTHENTICATED, "Sign in to run a review.")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise ApiError(401, UNAUTHENTICATED, "Expected an `Authorization: Bearer <id-token>` header.")
    return token.strip()


def _authorise(uid: str, email: str, email_verified: bool) -> User:
    """Allowlist gate. Runs after the token is proven genuine."""
    cfg = settings()
    address = (email or "").lower()

    if not address:
        raise ApiError(403, FORBIDDEN, f"This account has no email address. {CONTACT}")
    if not email_verified:
        raise ApiError(
            403, FORBIDDEN,
            "This account's email address is not verified. Sign in with Google and try again.",
        )
    if address not in cfg.allowed_emails:
        log.warning("allowlist rejection", extra={"uid": uid, "email": address})
        raise ApiError(403, FORBIDDEN, f"{address} is not authorised to use this service. {CONTACT}")

    return User(uid=uid, email=address)


async def current_user(authorization: Optional[str] = Header(default=None)) -> User:
    cfg = settings()

    # Local development only. Cloud Run always sets K_SERVICE, and config.py
    # refuses to set this flag when it is present, so this branch cannot be
    # reached on the deployed service whatever the environment says.
    if cfg.dev_unsafe_auth:
        log.warning("FBC_DEV_UNSAFE_AUTH is on — authentication is bypassed")
        email = next(iter(sorted(cfg.allowed_emails)), "dev@localhost")
        return User(uid="dev-local", email=email)

    token = _bearer(authorization)
    _ensure_app()

    try:
        claims = fb_auth.verify_id_token(token, check_revoked=True)
    # ExpiredIdTokenError and RevokedIdTokenError both subclass
    # InvalidIdTokenError, so they must be caught first or they are swallowed.
    except fb_auth.ExpiredIdTokenError:
        raise ApiError(401, UNAUTHENTICATED, "Your session expired. Sign in again.")
    except fb_auth.RevokedIdTokenError:
        raise ApiError(401, UNAUTHENTICATED, "Your session was revoked. Sign in again.")
    except fb_auth.UserDisabledError:
        raise ApiError(403, FORBIDDEN, f"This account is disabled. {CONTACT}")
    except fb_auth.InvalidIdTokenError:
        raise ApiError(401, UNAUTHENTICATED, "That sign-in token is not valid.")
    except fb_auth.CertificateFetchError:
        log.exception("could not fetch Firebase signing certificates")
        raise ApiError(503, "unavailable", "Cannot verify sign-in right now. Try again shortly.")
    except ValueError:
        raise ApiError(401, UNAUTHENTICATED, "That sign-in token is malformed.")

    return _authorise(
        uid=claims.get("uid") or claims.get("sub", ""),
        email=claims.get("email", ""),
        email_verified=bool(claims.get("email_verified")),
    )


#: Use as `user: User = RequireUser` on every route that is not `/healthz`.
RequireUser = Depends(current_user)
