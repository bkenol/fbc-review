"""What kind of PDF is this, sheet by sheet?

The engine reads vector geometry, live text and CAD layer names. A permit set
plotted to raster has none of those, and the failure mode without this module is
bad: the review runs, every rule abstains or finds nothing, and the output looks
like a clean set rather than an unreadable one.

So classify first and say so plainly. This is pure PyMuPDF measurement — no
model, no network — and it runs before the engine, in webapp/, leaving
fbcreview/ untouched.
"""
from __future__ import annotations

import dataclasses
import logging
from typing import Dict, List, Literal, Optional

import pymupdf

log = logging.getLogger("fbc.pdfkind")

SheetKind = Literal["vector", "hybrid", "raster", "blank"]
DocumentKind = Literal["vector", "mixed", "raster", "blank"]

# Thresholds. A plotted architectural sheet carries thousands of vector paths
# and hundreds of words; a scanned one carries a single full-bleed image and
# whatever OCR someone ran years ago.
MIN_VECTOR_ITEMS = 120
MIN_LIVE_CHARS = 120
IMAGE_COVERAGE_RASTER = 0.55


@dataclasses.dataclass
class SheetProfile:
    page: int
    kind: SheetKind
    vector_items: int
    live_chars: int
    image_count: int
    image_coverage: float
    reason: str

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


@dataclasses.dataclass
class DocumentProfile:
    kind: DocumentKind
    sheets: List[SheetProfile]
    cad_layers: int
    reviewable_pages: int
    raster_pages: List[int]
    summary: str

    @property
    def needs_conversion(self) -> bool:
        return bool(self.raster_pages)

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "cad_layers": self.cad_layers,
            "reviewable_pages": self.reviewable_pages,
            "raster_pages": list(self.raster_pages),
            "summary": self.summary,
            "sheets": [s.to_dict() for s in self.sheets],
        }


def _vector_items(page: pymupdf.Page) -> int:
    """Count drawing primitives, not path objects.

    A plotted sheet emits one path per stroke, but a single path can hold
    hundreds of items — which is exactly what webapp/convert.py produces when it
    commits traced linework as one shape. Counting paths would score a converted
    sheet as 1 and call it raster again.
    """
    try:
        paths = page.get_cdrawings()
    except Exception:
        try:
            paths = page.get_drawings()
        except Exception:
            return 0
    total = 0
    for path in paths:
        items = path.get("items")
        total += len(items) if items else 1
    return total


def _image_coverage(page: pymupdf.Page) -> tuple[int, float]:
    """Fraction of the page covered by raster images, clamped to 1.0."""
    page_area = abs(page.rect.get_area())
    if page_area <= 0:
        return 0, 0.0

    covered = 0.0
    count = 0
    try:
        for info in page.get_image_info():
            bbox = pymupdf.Rect(info["bbox"])
            covered += abs(bbox.get_area())
            count += 1
    except Exception:
        return 0, 0.0

    return count, min(covered / page_area, 1.0)


def classify_page(page: pymupdf.Page) -> SheetProfile:
    items = _vector_items(page)
    chars = len((page.get_text() or "").strip())
    images, coverage = _image_coverage(page)

    has_vector = items >= MIN_VECTOR_ITEMS
    has_text = chars >= MIN_LIVE_CHARS
    dominated_by_image = coverage >= IMAGE_COVERAGE_RASTER

    if items < 8 and chars < 20 and images == 0:
        kind: SheetKind = "blank"
        reason = "no drawable content"
    elif has_vector and has_text:
        kind = "hybrid" if dominated_by_image else "vector"
        reason = (
            "vector paths and live text, over a raster underlay"
            if dominated_by_image
            else "plotted as vector with live text"
        )
    elif dominated_by_image or (not has_vector and not has_text):
        kind = "raster"
        if not has_text and not has_vector:
            reason = "an image with no live text and no vector geometry"
        elif not has_text:
            reason = "an image with vector geometry but no live text"
        else:
            reason = "an image with live text but no vector geometry"
    else:
        kind = "hybrid"
        reason = "partly readable: " + (
            "vector geometry but little live text" if has_vector else "live text but little vector geometry"
        )

    return SheetProfile(
        page=page.number,
        kind=kind,
        vector_items=items,
        live_chars=chars,
        image_count=images,
        image_coverage=round(coverage, 3),
        reason=reason,
    )


def profile(path: str, doc: Optional[pymupdf.Document] = None) -> DocumentProfile:
    """Classify every page. Accepts an open Document to avoid a second parse."""
    owned = doc is None
    document = doc or pymupdf.open(path)
    try:
        sheets = [classify_page(document[n]) for n in range(document.page_count)]
        try:
            layers = len(document.get_ocgs() or {})
        except Exception:
            layers = 0
    finally:
        if owned:
            document.close()

    counts: Dict[str, int] = {}
    for sheet in sheets:
        counts[sheet.kind] = counts.get(sheet.kind, 0) + 1

    readable = counts.get("vector", 0) + counts.get("hybrid", 0)
    raster_pages = [s.page for s in sheets if s.kind == "raster"]
    content_pages = [s for s in sheets if s.kind != "blank"]

    if not content_pages:
        kind: DocumentKind = "blank"
    elif not raster_pages:
        kind = "vector"
    elif readable == 0:
        kind = "raster"
    else:
        kind = "mixed"

    if kind == "vector":
        summary = (
            f"{readable} of {len(sheets)} sheets are plotted as vector with live text"
            + (f", and {layers} CAD layers are preserved" if layers else "")
            + "."
        )
    elif kind == "raster":
        summary = (
            f"All {len(content_pages)} sheets are raster images with no live text or "
            "vector geometry. The review reads a drawing's vectors and text, so there "
            "is nothing here for it to read."
        )
    elif kind == "blank":
        summary = "This PDF has no drawable content."
    else:
        summary = (
            f"{readable} of {len(content_pages)} sheets are readable; "
            f"{len(raster_pages)} are raster images that no parser can read into. "
            "Those sheets will be reported as not checked rather than as passing."
        )

    return DocumentProfile(
        kind=kind,
        sheets=sheets,
        cad_layers=layers,
        reviewable_pages=readable,
        raster_pages=raster_pages,
        summary=summary,
    )
