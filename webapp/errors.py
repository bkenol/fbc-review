"""The typed error surface.

Every handler failure leaves this service as `{"error": {"code", "message"}}`
and nothing else. Stack traces go to the log with the job id attached; they
never reach the client.
"""
from __future__ import annotations

import logging
import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger("fbc.error")


class ApiError(Exception):
    """Raise this instead of HTTPException so the code is always explicit."""

    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def _envelope(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status)


# Codes used across the service. Keeping them in one place stops the client
# from having to match on prose.
UNAUTHENTICATED = "unauthenticated"
FORBIDDEN = "forbidden"
NOT_FOUND = "not_found"
NOT_READY = "not_ready"
INVALID_REQUEST = "invalid_request"
UNSUPPORTED_MEDIA = "unsupported_media_type"
PAYLOAD_TOO_LARGE = "payload_too_large"
ENCRYPTED_PDF = "encrypted_pdf"
CORRUPT_PDF = "corrupt_pdf"
TOO_MANY_PAGES = "too_many_pages"
RATE_LIMITED = "rate_limited"
INTERNAL = "internal"

# Fallback prose for bare HTTPExceptions raised by Starlette itself.
_BY_STATUS = {
    401: UNAUTHENTICATED,
    403: FORBIDDEN,
    404: NOT_FOUND,
    405: INVALID_REQUEST,
    409: NOT_READY,
    413: PAYLOAD_TOO_LARGE,
    415: UNSUPPORTED_MEDIA,
    429: RATE_LIMITED,
}


def install(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError):
        return _envelope(exc.status, exc.code, exc.message)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException):
        code = _BY_STATUS.get(exc.status_code, INTERNAL if exc.status_code >= 500 else INVALID_REQUEST)
        detail = exc.detail if isinstance(exc.detail, str) else "Request failed."
        return _envelope(exc.status_code, code, detail)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError):
        # Report the first offending field rather than pydantic's nested blob:
        # the client shows this to a person.
        first = (exc.errors() or [{}])[0]
        loc = ".".join(str(p) for p in first.get("loc", ()) if p not in ("body", "query"))
        msg = first.get("msg", "Invalid request.")
        return _envelope(422, INVALID_REQUEST, f"{loc}: {msg}" if loc else msg)

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):
        ref = uuid.uuid4().hex[:12]
        log.exception(
            "unhandled exception",
            extra={"error_ref": ref, "path": request.url.path, "method": request.method},
        )
        return _envelope(
            500,
            INTERNAL,
            f"Something went wrong on our side. Quote reference {ref} if you report this.",
        )
