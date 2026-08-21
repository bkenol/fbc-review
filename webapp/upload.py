"""Upload admission control.

The limit is enforced **while streaming**, before the whole file is on disk, so
a 2 GB body costs us the first 120 MB and a closed connection rather than 2 GB
of disk. Everything here runs before a job record is created.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Tuple

import pymupdf
from fastapi import UploadFile

from webapp import pdfkind
from webapp.config import settings
from webapp.errors import (
    CORRUPT_PDF,
    ENCRYPTED_PDF,
    PAYLOAD_TOO_LARGE,
    RASTER_PDF,
    TOO_MANY_PAGES,
    UNSUPPORTED_MEDIA,
    ApiError,
)

log = logging.getLogger("fbc.upload")

MAGIC = b"%PDF-"
CHUNK = 1 << 20  # 1 MiB


def safe_basename(name: str) -> str:
    """Strip any directory component a client sends, and anything that would
    change the meaning of a storage path."""
    base = os.path.basename((name or "").replace("\\", "/")).strip()
    base = base.replace("\r", "").replace("\n", "")
    base = "".join(c for c in base if c.isprintable() and c not in '"<>|*?:')
    base = base.lstrip(".") or "permit-set.pdf"
    if not base.lower().endswith(".pdf"):
        base += ".pdf"
    return base[:180]


async def stream_to_disk(upload: UploadFile, dest: Path) -> int:
    """Write the body to `dest`, rejecting it as early as the evidence allows.

    Raises ApiError and removes the partial file on any rejection.
    """
    cfg = settings()
    limit = cfg.max_upload_bytes
    size = 0
    checked_magic = False

    try:
        with dest.open("wb") as fh:
            while True:
                chunk = await upload.read(CHUNK)
                if not chunk:
                    break

                if not checked_magic:
                    if not chunk.startswith(MAGIC):
                        raise ApiError(
                            415, UNSUPPORTED_MEDIA,
                            "That file is not a PDF. Upload the permit set as a PDF "
                            "plotted from CAD.",
                        )
                    checked_magic = True

                size += len(chunk)
                if size > limit:
                    raise ApiError(
                        413, PAYLOAD_TOO_LARGE,
                        f"That file is larger than the {cfg.max_upload_mb} MB limit.",
                    )
                fh.write(chunk)
    except ApiError:
        dest.unlink(missing_ok=True)
        raise
    except Exception:
        dest.unlink(missing_ok=True)
        log.exception("upload stream failed")
        raise

    if size == 0:
        dest.unlink(missing_ok=True)
        raise ApiError(400, UNSUPPORTED_MEDIA, "That file is empty.")
    if not checked_magic:
        dest.unlink(missing_ok=True)
        raise ApiError(415, UNSUPPORTED_MEDIA, "That file is not a PDF.")

    return size


def probe(path: Path, *, allow_raster: bool = False) -> Tuple[int, "pdfkind.DocumentProfile"]:
    """Open the PDF far enough to reject what the engine cannot review.

    Returns (page count, source profile). Raises ApiError with prose rather
    than letting a PyMuPDF traceback escape.
    """
    cfg = settings()
    doc = None
    try:
        try:
            doc = pymupdf.open(str(path))
        except Exception:
            raise ApiError(
                400, CORRUPT_PDF,
                "That PDF could not be opened. It may be damaged or incompletely "
                "downloaded — try re-exporting it.",
            )

        if doc.needs_pass:
            raise ApiError(
                400, ENCRYPTED_PDF,
                "That PDF is password-protected. Remove the encryption and upload it "
                "again — the review has to read the drawing's vector geometry.",
            )

        pages = doc.page_count
        if pages < 1:
            raise ApiError(400, CORRUPT_PDF, "That PDF has no pages.")
        if pages > cfg.max_pages:
            raise ApiError(
                413, TOO_MANY_PAGES,
                f"That set is {pages} pages. The limit is {cfg.max_pages}.",
            )

        source = pdfkind.profile(str(path), doc)

        # Running a review over a scanned set produces the worst possible
        # output: no findings, which reads as a clean set rather than an
        # unreadable one. Refuse it, and say which sheets and why.
        if source.kind == "blank":
            raise ApiError(400, CORRUPT_PDF, "That PDF has no drawable content.")
        if source.kind == "raster" and not allow_raster:
            raise ApiError(
                422, RASTER_PDF,
                f"{source.summary} Re-plot the set from CAD if you can — that gives a "
                "far better review. Otherwise switch on “Rebuild scanned sheets” "
                "and submit again: the sheets will be OCR’d and their linework traced, "
                "which takes minutes rather than seconds.",
            )

        return pages, source
    finally:
        if doc is not None:
            doc.close()


def validate_options_edition(edition: str, editions: dict) -> None:
    from webapp.errors import INVALID_REQUEST

    if edition != "fbc2023":
        raise ApiError(
            400, INVALID_REQUEST,
            f"{editions.get(edition, edition)} — pick the 2023 8th Edition.",
        )
