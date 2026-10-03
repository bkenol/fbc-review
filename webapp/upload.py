"""Upload admission control.

The limit is enforced **while streaming**, before the whole file is on disk, so
a 2 GB body costs us the first 120 MB and a closed connection rather than 2 GB
of disk. Everything here runs before a job record is created.

What an upload *is* comes from its leading bytes, never its name: a PDF plot, a
DWG, a DXF, or a zip of drawings (`fbcreview.cad.source.sniff_bytes`, a
standard-library module that is cheap to import). A PDF is opened here far
enough to refuse what the engine cannot read (`probe`). A drawing is not — no
converter and no DXF reader runs inside a request — so `admit_cad` checks only
what can be checked from a header or a zip's directory, and the worker finds
out the rest.
"""
from __future__ import annotations

import logging
import os
import re
import stat
import zipfile
from pathlib import Path
from typing import List, NamedTuple, Tuple

import pymupdf
from fastapi import UploadFile

from fbcreview.cad import source as cad_source
from webapp import pdfkind
from webapp.config import settings
from webapp.errors import (
    CAD_UNAVAILABLE,
    CORRUPT_CAD,
    CORRUPT_PDF,
    ENCRYPTED_PDF,
    PAYLOAD_TOO_LARGE,
    RASTER_PDF,
    TOO_MANY_PAGES,
    UNSAFE_ARCHIVE,
    UNSUPPORTED_CAD,
    UNSUPPORTED_MEDIA,
    ApiError,
)

log = logging.getLogger("fbc.upload")

CHUNK = 1 << 20  # 1 MiB
#: Bytes held back before deciding what the upload is. An ASCII DXF may open
#: with `999` comment lines before `0 / SECTION`, and the sniffer looks this far.
SNIFF_BYTES = 4096

PDF, DWG, DXF, ZIP = cad_source.PDF, cad_source.DWG, cad_source.DXF, cad_source.ZIP
#: Formats a drawing upload can take. Everything else about them is in `fbcreview/cad`.
CAD_KINDS = (DWG, DXF, ZIP)
#: The extension a stored upload is given, by what its bytes say it is.
EXTENSIONS = {PDF: ".pdf", DWG: ".dwg", DXF: ".dxf", ZIP: ".zip"}
#: Content types for the stored upload. The two drawing types are the ones IANA
#: registers; nothing serves these blobs to a browser, so the choice only labels
#: the object in the bucket.
CONTENT_TYPES = {
    PDF: "application/pdf",
    DWG: "image/vnd.dwg",
    DXF: "image/vnd.dxf",
    ZIP: "application/zip",
}

_NOT_ACCEPTED = (
    "That file is not a PDF, a DWG, a DXF or a zip of drawings. Upload the permit set "
    "as a PDF plotted from CAD, or as the drawing itself."
)


class Received(NamedTuple):
    """What `stream_to_disk` wrote: its size in bytes and what its bytes say it is."""

    size: int
    kind: str


def safe_basename(name: str, kind: str = PDF) -> str:
    """Strip any directory component a client sends, and anything that would
    change the meaning of a storage path.

    The name ends in the extension of what the bytes are, not what the client
    called them: a DWG sent as `set.pdf` is stored as `set.dwg`, because the
    report's file name and the stored object's label both come from here.
    """
    ext = EXTENSIONS.get(kind, ".pdf")
    base = os.path.basename((name or "").replace("\\", "/")).strip()
    base = base.replace("\r", "").replace("\n", "")
    base = "".join(c for c in base if c.isprintable() and c not in '"<>|*?:')
    base = base.lstrip(".") or f"permit-set{ext}"
    if not base.lower().endswith(ext):
        stem, old = os.path.splitext(base)
        # Only one of our own extensions is replaced. `plan.v2` keeps its dot.
        if old.lower() in EXTENSIONS.values() and stem:
            base = stem
        base += ext
    if len(base) > 180:
        base = base[: 180 - len(ext)] + ext
    return base


def _kind_of(head: bytes) -> str:
    kind = cad_source.sniff_bytes(head)
    if kind is None:
        raise ApiError(415, UNSUPPORTED_MEDIA, _NOT_ACCEPTED)
    return kind


async def stream_to_disk(upload: UploadFile, dest: Path) -> Received:
    """Write the body to `dest`, rejecting it as early as the evidence allows.

    The first `SNIFF_BYTES` decide the format before anything is written, so a
    body that is none of ours costs one read. Raises ApiError and removes the
    partial file on any rejection.
    """
    cfg = settings()
    limit = cfg.max_upload_bytes
    size = 0
    kind = ""
    head = bytearray()

    try:
        with dest.open("wb") as fh:
            while True:
                chunk = await upload.read(CHUNK)
                if chunk and not kind:
                    # Held back until there is enough of it to say what it is.
                    head += chunk
                    if len(head) < SNIFF_BYTES:
                        continue
                    chunk, head = bytes(head), bytearray()
                elif not chunk:
                    if kind or not head:
                        break
                    # A body shorter than the sniff window: decided on what there is.
                    chunk, head = bytes(head), bytearray()

                if not kind:
                    kind = _kind_of(chunk)

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
    if not kind:
        dest.unlink(missing_ok=True)
        raise ApiError(415, UNSUPPORTED_MEDIA, _NOT_ACCEPTED)

    return Received(size, kind)


def with_extension(path: Path, kind: str) -> Path:
    """Rename a streamed upload to carry its kind's extension, and return the new path.

    PyMuPDF and the CAD adapter both open a file by name, and an extension that
    agrees with the bytes keeps either from guessing.
    """
    target = path.with_name(path.name + EXTENSIONS.get(kind, ""))
    path.rename(target)
    return target


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


#: Archive formats a zip member may be. One level of zip is unpacked and no more:
#: a nested archive is either a mistake or an attempt to get past these checks.
_ARCHIVES = (".zip", ".7z", ".rar", ".tar", ".gz", ".tgz", ".bz2", ".xz")
_DRAWINGS = (".dwg", ".dxf")


def admit_cad(path: Path, kind: str) -> None:
    """Refuse a drawing upload on what can be told without reading the drawing.

    Never runs the converter or the DXF reader: those take minutes and a
    gigabyte on a real set, and belong to the worker. What is checked here is
    the header of a DWG, and the directory of a zip. A DXF has nothing more to
    check cheaply than the sniff that already passed. Raises ApiError.
    """
    from fbcreview.cad import convert

    if kind == DWG:
        try:
            cad_source.check_dwg_version(str(path))
        except cad_source.SourceError as exc:
            raise ApiError(400, UNSUPPORTED_CAD, exc.message)
        if convert.binary() is None:
            raise ApiError(422, CAD_UNAVAILABLE, _NO_CONVERTER)
    elif kind == ZIP:
        names = check_archive(path)
        if convert.binary() is None and any(n.lower().endswith(".dwg") for n in names):
            raise ApiError(422, CAD_UNAVAILABLE, _NO_CONVERTER)
    elif kind != DXF:
        raise ApiError(415, UNSUPPORTED_MEDIA, _NOT_ACCEPTED)


def _escapes(name: str) -> bool:
    """Would extracting this member write outside the directory it is unpacked into?"""
    if name.startswith("/") or re.match(r"^[A-Za-z]:", name):
        return True
    parts = [p for p in name.split("/") if p not in ("", ".")]
    return not parts or ".." in parts


_NO_CONVERTER = (
    "This deployment cannot read DWG files: no DWG converter is installed. Export the "
    "drawing as DXF (in AutoCAD: SAVEAS, file type DXF) and upload that, zipped if it "
    "is large."
)


def check_archive(path: Path) -> List[str]:
    """The drawings a zip holds, from its directory alone; raises ApiError to refuse it.

    Nothing is extracted. `fbcreview.cad.source.unpack` re-checks all of this when
    the worker unpacks, and would skip a hostile member rather than refuse the
    set; at the door the whole archive is refused instead, so the person learns
    now rather than from a thinner review. The limits are that module's, so the
    two can never disagree about what fits.

    Refused: an unreadable directory; more members than `MAX_MEMBERS`; a total
    uncompressed size over `MAX_UNPACKED_BYTES`; a member that inflates beyond
    `MAX_RATIO`; an absolute path, a drive letter or a `..`; a symlink; a nested
    archive; a password-protected drawing; and a zip with no drawing in it.
    """
    try:
        with zipfile.ZipFile(path) as zf:
            infos = zf.infolist()
    except (zipfile.BadZipFile, zipfile.LargeZipFile, OSError, ValueError):
        raise ApiError(
            400, CORRUPT_CAD,
            "That zip file could not be opened. It may be damaged or incompletely "
            "uploaded — zip the drawings again and upload that.",
        )

    if len(infos) > cad_source.MAX_MEMBERS:
        raise ApiError(
            413, UNSAFE_ARCHIVE,
            f"That zip holds {len(infos)} files. The limit is {cad_source.MAX_MEMBERS}; "
            "zip the permit set's drawings only.",
        )

    total = 0
    drawings = []
    for info in infos:
        name = info.filename.replace("\\", "/")
        if info.is_dir():
            continue
        if _escapes(name):
            raise ApiError(
                400, UNSAFE_ARCHIVE,
                "That zip names a file outside the archive (an absolute path or a “..”). "
                "It was not opened. Zip the drawings again from their own folder.",
            )
        mode = (info.external_attr >> 16) & 0xFFFF
        if mode and stat.S_ISLNK(mode):
            raise ApiError(
                400, UNSAFE_ARCHIVE,
                "That zip holds a symbolic link. It was not opened. Zip the drawings "
                "themselves rather than links to them.",
            )
        total += info.file_size
        if info.compress_size and info.file_size / max(info.compress_size, 1) > cad_source.MAX_RATIO:
            raise ApiError(
                413, UNSAFE_ARCHIVE,
                "That zip expands far beyond what a drawing set could. It was not opened.",
            )
        base = name.rsplit("/", 1)[-1].lower()
        if name.startswith("__MACOSX/") or base.startswith("._"):
            continue
        if base.endswith(_ARCHIVES):
            raise ApiError(
                400, UNSAFE_ARCHIVE,
                "That zip holds another archive inside it. Only one level is unpacked: "
                "put the drawings themselves in a single zip.",
            )
        if base.endswith(_DRAWINGS):
            if info.flag_bits & 0x1:
                raise ApiError(
                    400, UNSAFE_ARCHIVE,
                    "A drawing in that zip is password-protected, so it cannot be read. "
                    "Zip it again without a password.",
                )
            drawings.append(name)

    if total > cad_source.MAX_UNPACKED_BYTES:
        raise ApiError(
            413, UNSAFE_ARCHIVE,
            f"That zip unpacks to {total / 1024 ** 3:.1f} GB. The limit is "
            f"{cad_source.MAX_UNPACKED_BYTES / 1024 ** 3:.0f} GB.",
        )
    if not drawings:
        raise ApiError(415, UNSUPPORTED_MEDIA, "That zip holds no DWG or DXF drawings.")
    return drawings


def validate_options_edition(edition: str, editions: dict) -> None:
    from webapp.errors import INVALID_REQUEST

    if edition != "fbc2023":
        raise ApiError(
            400, INVALID_REQUEST,
            f"{editions.get(edition, edition)} — pick the 2023 8th Edition.",
        )
