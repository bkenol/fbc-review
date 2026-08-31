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
from typing import Dict, List, Literal, Optional, Tuple

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

# A sheet can be perfectly good vector and still hide its code-analysis table
# inside a pasted image — the ITEC set does exactly that on G-002 and A-101,
# and the hand review of it says so: "plotted from AutoCAD LT with no preserved
# layers and raster code tables".
#
# Those sheets classify as `vector`, correctly, so a whole-sheet raster rebuild
# never fires on them and the table stays unreadable. Regions are tracked
# separately for that reason.
MIN_REGION_COVERAGE = 0.04      # of the page, per image
MIN_REGION_MEGAPIXELS = 0.4     # below this it is a logo, not a table


@dataclasses.dataclass
class RasterRegion:
    """A pasted image big enough to plausibly hold a table."""

    x0: float
    y0: float
    x1: float
    y1: float
    megapixels: float
    coverage: float

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


@dataclasses.dataclass
class SheetProfile:
    page: int
    kind: SheetKind
    vector_items: int
    live_chars: int
    image_count: int
    image_coverage: float
    reason: str
    raster_regions: List[RasterRegion] = dataclasses.field(default_factory=list)

    @property
    def has_readable_regions(self) -> bool:
        """Raster worth OCR-ing, on a sheet that is otherwise fine."""
        return self.kind in ("vector", "hybrid") and bool(self.raster_regions)

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
    def region_pages(self) -> List[int]:
        """Readable sheets that nonetheless hide content inside pasted images."""
        return [s.page for s in self.sheets if s.has_readable_regions]

    @property
    def needs_conversion(self) -> bool:
        return bool(self.raster_pages) or bool(self.region_pages)

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "cad_layers": self.cad_layers,
            "reviewable_pages": self.reviewable_pages,
            "raster_pages": list(self.raster_pages),
            "region_pages": list(self.region_pages),
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


#: How far apart two image rects may sit and still be one plotted region.
#:
#: AutoCAD's PDF driver slices a single plotted raster into horizontal bands and
#: emits each as its own image. Measured on the ITEC set, G-002's code-analysis
#: table arrives as three of them and A-101's egress tables as two apiece. The
#: seam between bands is a hairline; two genuinely separate tables on a permit
#: sheet are inches apart. Two points is comfortably inside that gap and nowhere
#: near closing it.
_SEAM_PT = 2.0

#: How much two rects must overlap on the *other* axis before they count as
#: bands of one thing. Bands of one plot share almost the whole width; two
#: tables that happen to abut do not.
_SEAM_OVERLAP = 0.8


def _abuts(a: pymupdf.Rect, b: pymupdf.Rect) -> bool:
    """Whether these two rects are bands of one sliced region.

    True when they overlap, or when they are within a seam of each other on one
    axis while substantially sharing the other. Deliberately strict on both
    counts: merging two real tables would cluster the left one's labels against
    the right one's values, which is a worse failure than leaving a seam in.
    """
    x_gap = max(a.x0, b.x0) - min(a.x1, b.x1)
    y_gap = max(a.y0, b.y0) - min(a.y1, b.y1)
    if x_gap > _SEAM_PT or y_gap > _SEAM_PT:
        return False

    def share(a0: float, a1: float, b0: float, b1: float) -> float:
        # Against the *larger* extent deliberately. Bands sliced from one plot
        # have the same width as each other; a narrow strip sitting against a
        # wide one is a different object that happens to touch it, and dividing
        # by the smaller extent would score that a perfect match.
        span = min(a1, b1) - max(a0, b0)
        larger = max(a1 - a0, b1 - b0)
        return span / larger if larger > 0 else 0.0

    # Stacked bands share their width; side-by-side ones share their height.
    if y_gap > -_SEAM_PT:
        return share(a.x0, a.x1, b.x0, b.x1) >= _SEAM_OVERLAP
    if x_gap > -_SEAM_PT:
        return share(a.y0, a.y1, b.y0, b.y1) >= _SEAM_OVERLAP
    return True  # genuinely overlapping


def _coalesce(tiles: List[Tuple[pymupdf.Rect, float]]) -> List[Tuple[pymupdf.Rect, float]]:
    """Merge sliced bands back into the regions they were plotted as.

    Transitive, because a region sliced into three bands arrives as A-B and B-C
    and has to come out as one rect rather than two. Repeats until a pass
    changes nothing; the lists here are single digits long, so the quadratic
    inner loop is not worth avoiding.

    Pixel counts are summed rather than recomputed: the bands are disjoint
    slices of one raster, so their megapixels add.
    """
    merged = list(tiles)
    changed = True
    while changed:
        changed = False
        out: List[Tuple[pymupdf.Rect, float]] = []
        for rect, pixels in merged:
            for index, (kept, kept_pixels) in enumerate(out):
                if _abuts(rect, kept):
                    out[index] = (kept | rect, kept_pixels + pixels)
                    changed = True
                    break
            else:
                out.append((pymupdf.Rect(rect), pixels))
        merged = out
    return merged


def _images(page: pymupdf.Page):
    """Coverage, count, and the regions big enough to be worth reading."""
    page_area = abs(page.rect.get_area())
    if page_area <= 0:
        return 0, 0.0, []

    covered = 0.0
    count = 0
    regions: List[RasterRegion] = []

    # get_image_info() reports bboxes in the page's *unrotated* space, but
    # page.rect — and every clip taken against it downstream — is the rotated
    # one. On a /Rotate 270 sheet the two disagree by a quarter turn, so an
    # image bbox lands off the page entirely and the region is clipped to
    # nothing: the pixmap comes back zero-height and pdfocr_tobytes raises
    # "Invalid bandwriter header dimensions". Mapping through rotation_matrix
    # puts the bbox back where the rest of the pipeline expects it. The matrix
    # is the identity on an unrotated page, so this costs nothing there.
    to_page = page.rotation_matrix

    tiles: List[Tuple[pymupdf.Rect, float]] = []
    try:
        for info in page.get_image_info():
            bbox = pymupdf.Rect(info["bbox"]) * to_page
            covered += abs(bbox.get_area())
            count += 1
            tiles.append(
                (bbox, (info.get("width", 0) * info.get("height", 0)) / 1e6)
            )
    except Exception:
        return 0, 0.0, []

    # Coalesce before the size floors, not after.
    #
    # The floors ask "is this worth OCRing", and the honest subject of that
    # question is the region as it was plotted, not the band the PDF driver
    # happened to slice it into. Filtering first drops bands that are small on
    # their own and part of a table that is not — and OCRing the survivors
    # independently cuts words at the seams and stops any row spanning the
    # table, which is how 27,000 recovered characters can yield zero rows.
    for rect, megapixels in _coalesce(tiles):
        area = abs(rect.get_area())
        coverage = area / page_area
        if coverage >= MIN_REGION_COVERAGE and megapixels >= MIN_REGION_MEGAPIXELS:
            regions.append(
                RasterRegion(
                    x0=round(rect.x0, 2), y0=round(rect.y0, 2),
                    x1=round(rect.x1, 2), y1=round(rect.y1, 2),
                    megapixels=round(megapixels, 2),
                    coverage=round(coverage, 3),
                )
            )

    return count, min(covered / page_area, 1.0), regions


def classify_page(page: pymupdf.Page) -> SheetProfile:
    items = _vector_items(page)
    chars = len((page.get_text() or "").strip())
    images, coverage, regions = _images(page)

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
        raster_regions=regions,
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

    embedded = [s.page for s in sheets if s.has_readable_regions]

    if kind == "vector":
        summary = (
            f"{readable} of {len(sheets)} sheets are plotted as vector with live text"
            + (f", and {layers} CAD layers are preserved" if layers else "")
            + "."
        )
        if embedded:
            summary += (
                f" {len(embedded)} sheet{'s' if len(embedded) != 1 else ''} paste part of "
                "the drawing in as an image — a code-analysis table pasted that way is "
                "pixels, and nothing can read it without OCR."
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
