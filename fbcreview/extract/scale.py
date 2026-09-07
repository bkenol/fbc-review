"""Drawing scale, determined from two independent sources and cross-checked.

Why this is not a one-liner: the ISO 32000 §12.9 route (viewport /Measure
dictionaries) is exact but ambiguous in real AutoCAD output.  The test set
carries the SAME 125 viewports on every page, at 23 different conversion
factors, most of them unit-scale artefacts of the layout.  Reverse-iterating
the /VP array as the spec suggests returns the sheet viewport (71.99 pt/ft),
which is wrong by a factor of four.

So: build a candidate set from /Measure, build a second candidate set from the
scale labels printed under each view, and intersect.  Agreement between two
independent sources is high confidence.  One source only is medium.

A page that prints several different scales used to be an ABSTAIN for the whole
sheet, and that is most architectural sheets — a floor plan at 1/4", an enlarged
restroom at 1/2" and a wall section at 1-1/2" are one page carrying three true
scales, not a page missing one.  Scale resolved on 13 of 24 ITEC pages and 18 of
35 Sculpted pages, and the remainder were overwhelmingly this case.

Such a page is now segmented into views (`extract/views.py`), each label is
attributed to the view it is printed under, and each view carries its own
scale.  What is inferred is the *attribution*, never the value: the number is
read off the sheet either way, and every note below says which view it was
attributed to and why, so it can be checked against the drawing.  A view no
label claims, or a view two labels claim, still abstains — the sheet-wide
abstention became a per-view one rather than becoming a guess.
"""
from __future__ import annotations
import re
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

import pymupdf

from ..confidence import Evidence, HIGH, MEDIUM
from ..facts import ViewScale
from .views import Label, View, drawn_views, owning_view, scale_labels

_VP    = re.compile(r"/Type/Viewport(.*?)/BBox\[([-\d\. ]+)\]", re.S)
_C     = re.compile(r"/X\[<</C ([\d\.eE+-]+)")
# 1/4" = 1'-0"   ·   3/8" = 1'-0"   ·   1" = 1'-0"   ·   6" = 1'-0"   ·   1 1/2" = 1'-0"
_LABEL = re.compile(r"""(\d+)?\s*(?:(\d+)\s*/\s*(\d+))?\s*"\s*=\s*1\s*'\s*-\s*0\s*\"""", re.X)

PT_PER_IN = 72.0


def label_value(m: "re.Match") -> Optional[float]:
    """pt-per-foot for one `X" = 1'-0"` match, or None if it names no length.

    Every group in `_LABEL` is optional, so the pattern also matches a bare
    `" = 1'-0"` with no number in front of it. That is not a scale.
    """
    whole, num, den = m.group(1), m.group(2), m.group(3)
    inches = 0.0
    if whole:
        inches += float(whole)
    if num and den:
        d = float(den)
        if d == 0:
            return None
        inches += float(num) / d
    if inches <= 0:
        return None
    return round(inches * PT_PER_IN, 3)


def measure_viewports(doc: pymupdf.Document, page: int) -> List[Tuple[pymupdf.Rect, float]]:
    """Each /VP viewport's box and its declared pt-per-foot.

    The /BBox is PDF user space — origin bottom-left, y increasing upward — and
    everything else in this engine is PyMuPDF space, origin top-left. Flipping
    here rather than at the call sites keeps one convention above this line.
    """
    try:
        typ, val = doc.xref_get_key(doc[page].xref, "VP")
    except Exception:
        return []
    if typ != "array" or not val:
        return []

    box = doc[page].mediabox
    out: List[Tuple[pymupdf.Rect, float]] = []
    for body, bbox in _VP.findall(val):
        m = _C.search(body)
        if not m:
            continue
        try:
            c = float(m.group(1))
        except ValueError:
            continue
        if c <= 0:
            continue
        nums = [float(n) for n in bbox.split() if n]
        if len(nums) != 4:
            continue
        x0, y0, x1, y1 = nums
        rect = pymupdf.Rect(min(x0, x1), box.y1 - max(y0, y1),
                            max(x0, x1), box.y1 - min(y0, y1))
        out.append((rect, round(1.0 / c, 3)))
    return out


def measure_candidates(doc: pymupdf.Document, page: int) -> List[float]:
    """Distinct pt-per-foot values declared in the page's /VP /Measure dictionaries."""
    return sorted({v for _rect, v in measure_viewports(doc, page)})


def label_candidates(page_text: str) -> List[float]:
    """pt-per-foot implied by every `X" = 1'-0"` scale label printed on the sheet."""
    out = set()
    for m in _LABEL.finditer(page_text):
        v = label_value(m)
        if v is not None:
            out.add(v)
    return sorted(out)


def _agrees(a: float, b: float, tol: float = 0.05) -> bool:
    """Do a label and a /Measure factor name the same scale?

    The tolerance is the one the page-wide check has always used: proportional
    at large scales, with a floor so that 18.0 and 18.005 — the Sculpted
    life-safety viewport — still count as the same number.
    """
    return abs(a - b) <= tol * a / 18.0 + 0.05


def _view_scale(pno: int, index: int, total: int, view: View, labels: List[Label],
                viewports: List[Tuple[pymupdf.Rect, float]]) -> ViewScale:
    """Resolve one view's scale from the labels attributed to it."""
    where = (f"page {pno + 1}, view {index + 1} of {total} "
             f"at ({view.rect.x0:.0f},{view.rect.y0:.0f})-({view.rect.x1:.0f},{view.rect.y1:.0f}) pt")

    if not labels:
        return ViewScale(view.as_tuple(), Evidence.abstain(
            where, "no scale label is printed against this view", pno), view.paths)

    distinct = sorted({l.pt_per_ft for l in labels})
    if len(distinct) > 1:
        return ViewScale(view.as_tuple(), Evidence.abstain(
            where, f"{len(labels)} scale labels sit against this view "
                   f"({', '.join(l.text for l in labels)}); which one governs it is not "
                   f"decidable from position alone", pno), view.paths)

    value = distinct[0]
    printed = labels[0].text

    # Two independent sources agreeing about THIS REGION, rather than agreeing
    # somewhere on a sheet that declares two dozen conversion factors.
    here = [v for rect, v in viewports if view.rect.intersects(rect)]
    if any(_agrees(value, v) for v in here):
        return ViewScale(view.as_tuple(), Evidence(
            value, f"{where}: scale label + overlapping /Measure viewport", HIGH,
            f"'{printed}' printed against this view and confirmed by a /Measure viewport "
            f"covering it", pno), view.paths)

    return ViewScale(view.as_tuple(), Evidence(
        value, f"{where}: printed scale label, attributed by position", MEDIUM,
        f"'{printed}' is the only scale label printed against this view; no /Measure "
        f"viewport over the view corroborates it", pno), view.paths)


def page_views(doc: pymupdf.Document, page: int, paths=None) -> List[ViewScale]:
    """Segment the sheet and resolve a scale for each view.

    `paths` is the caller's already-fetched `page.get_drawings()`; see
    `views.path_rects` for why re-fetching it would be the expensive mistake.
    """
    pg = doc[page]
    views = drawn_views(pg, paths)
    if not views:
        return []

    labels = scale_labels(pg, _LABEL, label_value)
    claimed: Dict[int, List[Label]] = defaultdict(list)
    for lab in labels:
        idx = owning_view(lab, views)
        if idx is not None:
            claimed[idx].append(lab)

    viewports = measure_viewports(doc, page)
    return [_view_scale(page, i, len(views), v, claimed.get(i, []), viewports)
            for i, v in enumerate(views)]


def resolve(doc: pymupdf.Document, page: int, page_text: str,
            tol: float = 0.05, paths=None) -> Tuple[Evidence, List[ViewScale]]:
    """The page-wide scale (or an abstention), and the per-view scales.

    Both are returned because both are true at once: a sheet printing three
    scales genuinely has no page-wide one, and saying so is not the same as
    having failed to read it.
    """
    labels = label_candidates(page_text)
    meas = measure_candidates(doc, page)
    src = f"page {page+1}"

    if not labels and not meas:
        return Evidence.abstain(src, "no scale label and no /Measure viewports", page), []

    if len(labels) == 1:
        lab = labels[0]
        agree = [m for m in meas if _agrees(lab, m, tol)]
        if agree:
            return Evidence(lab, f"{src}: scale label + /Measure viewport {agree[0]}",
                            HIGH, f"label {lab} pt/ft confirmed by {len(agree)} viewport(s)",
                            page), []
        return Evidence(lab, f"{src}: printed scale label only", MEDIUM,
                        "no /Measure viewport corroborates this value", page), []

    if len(labels) > 1:
        views = page_views(doc, page, paths)
        got = sum(1 for v in views if v.scale)
        note = (f"sheet prints {len(labels)} different scales {labels}, so no single "
                f"scale governs the page")
        if views:
            note += (f"; it was segmented into {len(views)} views and {got} of them "
                     f"resolved a scale of their own")
        else:
            note += "; no drawing geometry could be segmented into views"
        return Evidence.abstain(src, note, page), views

    return (Evidence.abstain(src, f"/Measure offers {len(meas)} candidates and the sheet prints "
                                  f"no scale label to disambiguate", page), [])


def page_scale(doc: pymupdf.Document, page: int, page_text: str,
               tol: float = 0.05) -> Evidence:
    """Resolve one governing scale for the page, or abstain."""
    return resolve(doc, page, page_text, tol)[0]
