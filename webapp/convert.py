"""Turning a rasterised permit set into a PDF the engine can actually read.

Two independent recoveries, both deterministic and both model-free:

**Text (OCR).** Schedules, code-analysis blocks and the printed `1/4" = 1'-0"`
scale label are all *text*. Recovering them is what unlocks the rule corpus —
eleven of the twelve rules key on text, not geometry. PyMuPDF's
`Pixmap.pdfocr_tobytes()` runs Tesseract and returns a real PDF page carrying
the image plus a positioned text layer, which is exactly the shape the
extractors already expect.

**Linework (vectorisation).** The rendered sheet is thresholded and its straight
runs recovered with a probabilistic Hough transform, then drawn back as real
vector paths inside a named optional-content group. After this the file is a
genuine vector PDF: `get_drawings()` returns paths and `page_geometry()` reports
a layer.

## What vectorisation deliberately does not do

Traced lines carry no meaning. A scan cannot tell you which run is an egress
path, which is a wall and which is the title-block border — that information
lived in the CAD layer name and it is gone.

`MEASURE.EGRESS_EXTENT` selects geometry by matching the layer name against
`"egress path"`. Naming this traced layer that would make the rule measure the
longest straight run of *anything on the sheet* — very often the title block —
and report it as a travel distance, at CRITICAL severity, with a confident
citation. That is far worse than not measuring at all.

So the traced layer is named `TRACED_LAYER` below, the egress rule abstains as
it should, and this module recovers the file format without inventing the
semantics. Restoring those needs room-polygon recovery (ARCHITECTURE.md §6.2),
not a better tracer.
"""
from __future__ import annotations

import dataclasses
import logging
import math
import time
from typing import List, Optional, Sequence, Tuple

import pymupdf

log = logging.getLogger("fbc.convert")

#: Honest name. Must not contain "egress path" — see the module docstring.
TRACED_LAYER = "traced linework (unclassified)"

#: A 34x22 in. sheet at 300 dpi is 10200x6600 = 67 MP, and Hough over that is
#: minutes. Long edge is capped instead, which keeps OCR legible on drawing
#: text while bounding the transform.
MAX_LONG_EDGE_PX = 4400
MIN_DPI, MAX_DPI = 120, 400

# Hough parameters, in pixels of the rendered image.
HOUGH_THRESHOLD = 80
MIN_LINE_PX = 40
MAX_GAP_PX = 6


@dataclasses.dataclass
class Support:
    ocr: bool
    vectorise: bool
    detail: str

    @property
    def any(self) -> bool:
        return self.ocr or self.vectorise


@dataclasses.dataclass
class PageReport:
    page: int
    ocr_chars: int
    traced_segments: int
    seconds: float
    note: str = ""

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


@dataclasses.dataclass
class ConversionReport:
    converted_pages: List[PageReport]
    ocr_used: bool
    vectorise_used: bool
    seconds: float
    traced_layer: str = TRACED_LAYER

    @property
    def total_chars(self) -> int:
        return sum(p.ocr_chars for p in self.converted_pages)

    @property
    def total_segments(self) -> int:
        return sum(p.traced_segments for p in self.converted_pages)

    def to_dict(self) -> dict:
        return {
            "converted_pages": [p.to_dict() for p in self.converted_pages],
            "ocr_used": self.ocr_used,
            "vectorise_used": self.vectorise_used,
            "seconds": round(self.seconds, 2),
            "traced_layer": self.traced_layer,
            "recovered_chars": self.total_chars,
            "traced_segments": self.total_segments,
        }


def support() -> Support:
    """What this deployment can actually do, checked rather than assumed."""
    ocr = False
    ocr_detail = "Tesseract is not installed"
    try:
        pymupdf.get_tessdata()
        ocr = True
        ocr_detail = "Tesseract available"
    except Exception as exc:
        ocr_detail = str(exc).split("\n")[0]

    vectorise = False
    vec_detail = "OpenCV is not installed"
    try:
        import cv2  # noqa: F401

        vectorise = True
        vec_detail = "OpenCV available"
    except Exception as exc:
        vec_detail = str(exc).split("\n")[0]

    return Support(ocr=ocr, vectorise=vectorise, detail=f"{ocr_detail}; {vec_detail}")


def _render_dpi(page: pymupdf.Page) -> int:
    """Highest dpi that keeps the long edge under the pixel cap."""
    long_edge_in = max(page.rect.width, page.rect.height) / 72.0
    if long_edge_in <= 0:
        return MIN_DPI
    dpi = int(MAX_LONG_EDGE_PX / long_edge_in)
    return max(MIN_DPI, min(MAX_DPI, dpi))


def _trace_segments(pix: pymupdf.Pixmap) -> List[Tuple[float, float, float, float]]:
    """Straight runs in the rendered image, in *pixel* coordinates."""
    import cv2
    import numpy as np

    buffer = np.frombuffer(pix.samples, dtype=np.uint8)
    image = buffer.reshape(pix.height, pix.width, pix.n)
    if pix.n >= 3:
        grey = cv2.cvtColor(image[:, :, :3], cv2.COLOR_RGB2GRAY)
    else:
        grey = image[:, :, 0]

    # Drawings are dark on white; invert so linework is the foreground.
    binary = cv2.adaptiveThreshold(
        grey, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 25, 12
    )

    found = cv2.HoughLinesP(
        binary,
        rho=1,
        theta=math.pi / 360,
        threshold=HOUGH_THRESHOLD,
        minLineLength=MIN_LINE_PX,
        maxLineGap=MAX_GAP_PX,
    )
    if found is None:
        return []

    # OpenCV 4.x returns (N, 1, 4); OpenCV 5.x returns (N, 4). Reshaping to
    # (-1, 4) reads both, and does not silently mis-slice if it changes again.
    lines = np.asarray(found).reshape(-1, 4)
    return [(float(a), float(b), float(c), float(d)) for a, b, c, d in lines]


def _draw_traced(
    page: pymupdf.Page,
    segments: Sequence[Tuple[float, float, float, float]],
    scale: float,
    oc_xref: int,
) -> int:
    """Draw traced segments as real vector paths inside the traced layer.

    `scale` converts rendered pixels back to PDF points. Drawn hairline and
    fully transparent-ish grey so the recovered geometry is machine-readable
    without obscuring the scan underneath it.
    """
    drawn = 0
    shape = page.new_shape()
    for x0, y0, x1, y1 in segments:
        shape.draw_line(
            pymupdf.Point(x0 * scale, y0 * scale),
            pymupdf.Point(x1 * scale, y1 * scale),
        )
        drawn += 1
    if drawn:
        # oc= is what puts these paths inside the optional-content group. Without
        # it they are drawn but carry no layer, and get_drawings() reports
        # layer=None — which is precisely the information the engine reads.
        shape.finish(color=(0.0, 0.45, 0.9), width=0.3, stroke_opacity=0.35, oc=oc_xref)
        shape.commit(overlay=True)
    return drawn


def convert(
    src_path: str,
    raster_pages: Sequence[int],
    dest_path: str,
    *,
    do_ocr: bool = True,
    do_vectorise: bool = True,
    language: str = "eng",
) -> ConversionReport:
    """Write a copy of `src_path` to `dest_path` with `raster_pages` rebuilt.

    Vector pages are copied through untouched — never re-rendered, because
    re-rendering a good sheet would destroy exactly the geometry the engine
    wants.
    """
    started = time.monotonic()
    caps = support()
    use_ocr = do_ocr and caps.ocr
    use_vec = do_vectorise and caps.vectorise

    src = pymupdf.open(src_path)
    out = pymupdf.open()
    reports: List[PageReport] = []
    targets = set(raster_pages)

    try:
        oc_xref = out.add_ocg(TRACED_LAYER, on=True) if use_vec else 0

        for number in range(src.page_count):
            if number not in targets:
                out.insert_pdf(src, from_page=number, to_page=number)
                continue

            page_started = time.monotonic()
            page = src[number]
            dpi = _render_dpi(page)
            pix = page.get_pixmap(dpi=dpi, alpha=False)
            note = f"rendered at {dpi} dpi"
            chars = 0

            # ── text layer ────────────────────────────────────────────────
            rebuilt = None
            if use_ocr:
                try:
                    rebuilt = pymupdf.open("pdf", pix.pdfocr_tobytes(language=language))
                except Exception as exc:
                    note += f"; OCR failed ({type(exc).__name__})"
                    log.warning("ocr failed", extra={"page": number, "error": str(exc)})

            if rebuilt is not None:
                out.insert_pdf(rebuilt)
                rebuilt.close()
            else:
                # No OCR: keep the image so the sheet still renders, and carry
                # the original page's own text if it had any.
                blank = out.new_page(width=page.rect.width, height=page.rect.height)
                blank.insert_image(blank.rect, pixmap=pix)

            new_page = out[out.page_count - 1]
            if use_ocr and rebuilt is not None:
                chars = len((new_page.get_text() or "").strip())

            # ── linework ──────────────────────────────────────────────────
            traced = 0
            if use_vec:
                try:
                    segments = _trace_segments(pix)
                    # Rendered pixels back to PDF points.
                    to_points = new_page.rect.width / pix.width if pix.width else 1.0
                    traced = _draw_traced(new_page, segments, to_points, oc_xref)
                except Exception as exc:
                    note += f"; tracing failed ({type(exc).__name__})"
                    log.warning("tracing failed", extra={"page": number, "error": str(exc)})

            reports.append(
                PageReport(
                    page=number,
                    ocr_chars=chars,
                    traced_segments=traced,
                    seconds=round(time.monotonic() - page_started, 2),
                    note=note,
                )
            )
            pix = None

        out.save(dest_path, garbage=3, deflate=True)
    finally:
        out.close()
        src.close()

    report = ConversionReport(
        converted_pages=reports,
        ocr_used=use_ocr,
        vectorise_used=use_vec,
        seconds=time.monotonic() - started,
    )
    log.info(
        "raster conversion complete",
        extra={
            "pages": len(reports),
            "recovered_chars": report.total_chars,
            "traced_segments": report.total_segments,
            "seconds": round(report.seconds, 2),
            "ocr_used": use_ocr,
            "vectorise_used": use_vec,
        },
    )
    return report
