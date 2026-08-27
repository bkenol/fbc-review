"""OCR adapter for permit sets that were plotted to raster.

Why this exists
---------------
The whole rule corpus keys on text: cited section numbers, labels, and the
values beside them.  A set plotted from AutoCAD LT with the code-analysis block
rendered as a JPEG carries none of that as text, so every rule that needs it
abstains.  On the ITEC Alico Park test set that was 12 abstentions and zero
findings — the engine behaving correctly and being useless.

The design decision that keeps this honest
------------------------------------------
This module does NOT introduce a second parser.  PyMuPDF's `page.get_text("words")`
returns tuples of `(x0, y0, x1, y1, text, block, line, word)`.  OCR here emits the
SAME tuple shape in the SAME (displayed) coordinate space, so every existing
extractor — row clustering, header search, label/value splitting — works on a
raster sheet with no change.  OCR is a text *source*, not a text *pipeline*.

Confidence
----------
OCR output is never HIGH.  Tesseract's per-word confidence is carried through and
words below `MIN_CONF` are dropped rather than guessed at, which means a smudged
value produces an abstention exactly as a missing value would.  That is the
"never guess" rule applied to a new input, not an exception to it.
"""
from __future__ import annotations

import functools
import hashlib
import json
import logging
import os
import pathlib
import shutil
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import pymupdf

log = logging.getLogger(__name__)

# Word tuples are (x0, y0, x1, y1, text, block, line, word) to match PyMuPDF.
Word = Tuple[float, float, float, float, str, int, int, int]

DPI = 300              # 450 measured no better on the test set and is 35% slower
MIN_CONF = 40.0        # tesseract confidence floor; below this the word is dropped
MIN_REGION_FRAC = 0.01  # ignore logos and north arrows
TESSDATA_CANDIDATES = (
    "/usr/share/tesseract-ocr/5/tessdata",
    "/usr/share/tesseract-ocr/4.00/tessdata",
    "/usr/share/tessdata",
)


def tesseract_available() -> bool:
    return shutil.which("tesseract") is not None


def _tessdata() -> Optional[str]:
    env = os.environ.get("TESSDATA_PREFIX")
    if env and os.path.isdir(env):
        return env
    for c in TESSDATA_CANDIDATES:
        if os.path.isdir(c):
            return c
    return None


# ── where the raster is ──────────────────────────────────────────────────────

def raster_regions(page: pymupdf.Page,
                   min_frac: float = MIN_REGION_FRAC) -> List[pymupdf.Rect]:
    """Placed images on the page, in displayed coordinates, large enough to
    plausibly carry drawing content rather than a logo."""
    page_area = abs(page.rect.get_area()) or 1.0
    out: List[pymupdf.Rect] = []
    seen = set()
    for info in page.get_images(full=True):
        xref = info[0]
        if xref in seen:
            continue
        seen.add(xref)
        try:
            rects = page.get_image_rects(xref)
        except Exception:                                   # pragma: no cover
            continue
        for r in rects:
            r = r & page.rect
            if r.is_empty or r.get_area() / page_area < min_frac:
                continue
            out.append(pymupdf.Rect(r))
    return _merge(out)


def _merge(rects: Sequence[pymupdf.Rect], pad: float = 2.0) -> List[pymupdf.Rect]:
    """Coalesce touching image tiles. AutoCAD's PDF driver slices one plotted
    region into horizontal bands; OCRing each band separately cuts words in half
    and destroys row alignment across the seam."""
    boxes = [pymupdf.Rect(r) for r in rects]
    changed = True
    while changed:
        changed = False
        for i in range(len(boxes)):
            for j in range(i + 1, len(boxes)):
                a, b = boxes[i], boxes[j]
                if pymupdf.Rect(a.x0 - pad, a.y0 - pad, a.x1 + pad, a.y1 + pad).intersects(b):
                    boxes[i] = a | b
                    del boxes[j]
                    changed = True
                    break
            if changed:
                break
    return [b for b in boxes if not b.is_empty]


def raster_coverage(page: pymupdf.Page) -> float:
    """Fraction of the sheet covered by placed images. Used to decide whether a
    page is worth the OCR round trip at all."""
    area = abs(page.rect.get_area()) or 1.0
    return min(1.0, sum(r.get_area() for r in raster_regions(page)) / area)


# ── column segmentation ──────────────────────────────────────────────────────
#
# A code-analysis block is usually two or three tables printed side by side.
# OCRing the whole region as one block interleaves them: a row-clustering pass
# then reads "OCCUPANCY:" from the left table and "CONSTRUCTION TYPE: TYPE II-B"
# from the right table as a single row, and every label lands against the wrong
# value.
#
# Whitespace-gutter detection does not work here, because the tables are ruled —
# every column of pixels contains border ink.  What separates the tables is the
# opposite signal: a nearly full-height vertical RULE.  Find those and cut there.

STRUCTURE_DPI = 100          # structure detection needs shape, not legibility
RULE_MIN_HEIGHT = 0.55       # fraction of region height for a major separator
STRIP_MIN_WIDTH = 0.08       # ignore slivers


def column_strips(page: pymupdf.Page, clip: pymupdf.Rect) -> List[pymupdf.Rect]:
    """Split a raster region at its major vertical rules."""
    try:
        import numpy as np
        from PIL import Image
    except ImportError:                                    # pragma: no cover
        return [clip]

    pix = page.get_pixmap(clip=clip, dpi=STRUCTURE_DPI)
    if pix.width < 40 or pix.height < 40:
        return [clip]
    grey = np.asarray(
        Image.frombytes("RGB", (pix.width, pix.height), pix.samples).convert("L"))
    h, w = grey.shape
    ink = (grey < 160).sum(axis=0)

    tall = np.where(ink > RULE_MIN_HEIGHT * h)[0]
    if tall.size == 0:
        return [clip]

    # group contiguous pixel columns into single rules
    groups: List[Tuple[int, int]] = []
    start = prev = int(tall[0])
    for x in tall[1:]:
        x = int(x)
        if x - prev > 3:
            groups.append((start, prev))
            start = x
        prev = x
    groups.append((start, prev))

    interior = [(a + b) // 2 for a, b in groups
                if 0.02 * w < (a + b) / 2 < 0.98 * w]
    if not interior:
        return [clip]

    cuts = [0] + interior + [w]
    strips: List[pymupdf.Rect] = []
    sx = clip.width / w
    for i in range(len(cuts) - 1):
        x0, x1 = cuts[i], cuts[i + 1]
        if (x1 - x0) / w < STRIP_MIN_WIDTH:
            continue
        strips.append(pymupdf.Rect(clip.x0 + x0 * sx, clip.y0,
                                   clip.x0 + x1 * sx, clip.y1))
    return strips or [clip]


# ── OCR ──────────────────────────────────────────────────────────────────────

@dataclass
class OcrResult:
    words: List[Word]
    confidence: Dict[int, float]     # index into `words` -> tesseract confidence
    regions: List[pymupdf.Rect]
    mean_conf: float
    strips: List[pymupdf.Rect] = field(default_factory=list)


def _ocr_region(page: pymupdf.Page, clip: pymupdf.Rect, dpi: int,
                min_conf: float, strip: int = 0) -> Tuple[List[Word], List[float]]:
    import pytesseract
    from PIL import Image

    pix = page.get_pixmap(clip=clip, dpi=dpi)
    if pix.width < 8 or pix.height < 8:
        return [], []
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)

    cfg = "--psm 6"                                  # a uniform block of text
    td = _tessdata()
    if td:
        cfg += f' --tessdata-dir "{td}"'
    data = pytesseract.image_to_data(img, config=cfg,
                                     output_type=pytesseract.Output.DICT)

    # pixmap pixel -> page point.  get_pixmap(clip=) renders the clip at `dpi`,
    # so the scale is uniform and the origin is the clip's top-left.
    sx = clip.width / pix.width
    sy = clip.height / pix.height

    words: List[Word] = []
    confs: List[float] = []
    for i in range(len(data["text"])):
        txt = (data["text"][i] or "").strip()
        if not txt:
            continue
        try:
            conf = float(data["conf"][i])
        except (TypeError, ValueError):
            continue
        if conf < min_conf:
            continue
        x0 = clip.x0 + data["left"][i] * sx
        y0 = clip.y0 + data["top"][i] * sy
        x1 = x0 + data["width"][i] * sx
        y1 = y0 + data["height"][i] * sy
        # `block` carries the STRIP index, not tesseract's block number. Rows
        # must be clustered within a strip: two tables printed side by side
        # share y-coordinates, and clustering across them pairs a label from the
        # left table with a value from the right one.
        words.append((x0, y0, x1, y1, txt,
                      strip, int(data["par_num"][i]), int(data["word_num"][i])))
        confs.append(conf)
    return words, confs


@functools.lru_cache(maxsize=256)
def _cache_key(doc_id: int, pno: int, dpi: int, min_conf: float):   # pragma: no cover
    return (doc_id, pno, dpi, min_conf)


_MEMO: Dict[tuple, OcrResult] = {}

# Disk cache. OCR is by far the most expensive step in a review — roughly 30 s
# per raster sheet against ~2 s for everything else on a 35-sheet set — and it
# is a pure function of (file bytes, page, dpi, threshold). Caching it on the
# document hash is what makes re-running a review, or re-rendering the markup
# after a rule change, cheap. This is the Tier-B cache described in
# ARCHITECTURE.md, applied to OCR rather than to a model call.
CACHE_DIR = pathlib.Path(os.environ.get("FBC_OCR_CACHE", "/tmp/fbc-ocr-cache"))


@functools.lru_cache(maxsize=32)
def _doc_hash(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def _cache_path(page: pymupdf.Page, dpi: int, min_conf: float):
    src = getattr(page.parent, "name", "") or ""
    if not src or not os.path.exists(src):
        return None
    try:
        key = f"{_doc_hash(src)}-p{page.number}-{dpi}-{int(min_conf)}"
    except OSError:                                        # pragma: no cover
        return None
    return CACHE_DIR / f"{key}.json"


def _cache_load(path) -> Optional[OcrResult]:
    if path is None or not path.exists():
        return None
    try:
        raw = json.loads(path.read_text())
    except Exception:                                      # pragma: no cover
        return None
    return OcrResult(
        words=[tuple(w[:4]) + (w[4],) + tuple(w[5:]) for w in raw["words"]],
        confidence={int(k): v for k, v in raw["confidence"].items()},
        regions=[pymupdf.Rect(*r) for r in raw["regions"]],
        mean_conf=raw["mean_conf"],
        strips=[pymupdf.Rect(*r) for r in raw.get("strips", [])],
    )


def _cache_store(path, res: OcrResult) -> None:
    if path is None:
        return
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "words": [list(w) for w in res.words],
            "confidence": {str(k): v for k, v in res.confidence.items()},
            "regions": [list(r) for r in res.regions],
            "strips": [list(r) for r in res.strips],
            "mean_conf": res.mean_conf,
        }))
    except Exception as exc:                               # pragma: no cover
        log.debug("OCR cache write failed: %s", exc)


def ocr_page(page: pymupdf.Page, dpi: int = DPI,
             min_conf: float = MIN_CONF) -> OcrResult:
    """OCR every raster region on the page. Memoised per (document, page) — the
    pipeline asks for the same page from several extractors and OCR is the most
    expensive thing in the whole run at roughly 5 s per raster sheet."""
    key = (id(page.parent), page.number, dpi, min_conf)
    hit = _MEMO.get(key)
    if hit is not None:
        return hit

    cpath = _cache_path(page, dpi, min_conf)
    hit = _cache_load(cpath)
    if hit is not None:
        _MEMO[key] = hit
        return hit

    regions = raster_regions(page)
    words: List[Word] = []
    confs: List[float] = []
    if regions and tesseract_available():
        clips: List[pymupdf.Rect] = []
        for region in regions:
            clips.extend(column_strips(page, region))
        strip_rects = list(clips)
        for si, clip in enumerate(clips):
            try:
                w, c = _ocr_region(page, clip, dpi, min_conf, strip=si)
            except Exception as exc:                       # pragma: no cover
                log.warning("OCR failed on page %s region %s: %s",
                            page.number, clip, exc)
                continue
            words.extend(w)
            confs.extend(c)
    elif regions:
        log.warning("page %s has %d raster regions but tesseract is not installed",
                    page.number, len(regions))

    res = OcrResult(
        words=words,
        confidence={i: c for i, c in enumerate(confs)},
        regions=regions,
        mean_conf=(sum(confs) / len(confs)) if confs else 0.0,
        strips=locals().get("strip_rects", []),
    )
    _MEMO[key] = res
    _cache_store(cpath, res)
    return res


def reset_cache() -> None:
    _MEMO.clear()
