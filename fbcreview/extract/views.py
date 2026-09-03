"""Where the drawings are on a sheet, and where the scale labels are.

A permit sheet is not one drawing. It is three or six or a dozen views —
a floor plan, an enlarged restroom, two wall sections, a door jamb detail —
laid out on one 24x36 page, each drawn at its own scale and each carrying that
scale printed beneath its title.  `extract/scale.py` reads those labels and, on
finding more than one, abstains for the whole page: the sheet states three
scales and nothing said which geometry belonged to which.

That abstention is honest and it is also most of the sheets.  On the ITEC set,
scale resolved on 13 of 24 pages; on Sculpted, 18 of 35.  The eleven and the
seventeen are not sheets missing a scale — they are sheets carrying several.

This module supplies the missing half: the page's geometry clustered into
views, and each printed scale label located on the page, so that a label can be
attributed to the drawing it labels.  It answers *where*, not *what* — the
reading of the label and the cross-check against `/Measure` stay in `scale.py`.

Two properties are deliberate:

* **Coordinates are unrotated PDF space throughout.** `get_drawings()` and
  `get_text()` agree there even on the `/Rotate 270` sheets in the test set, so
  a label rect and a path rect can be compared directly. `page.mediabox` is the
  sheet, not `page.rect`, for the same reason.
* **Over-joining and under-joining both fail safe.** Merging two views leaves
  one view holding two labels; splitting one view leaves a piece holding none.
  `scale.py` abstains on either, so a mis-clustered sheet loses a measurement
  rather than gaining a wrong one.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import pymupdf

#: Occupancy grid resolution, in points. Finer costs time and gains nothing:
#: the question is which part of a 3-foot sheet a line is on, not where it is to
#: the point, and the grid only has to resolve a boundary finer than GAP. Halving
#: this to 6 pt doubles the time per page and returns the same views.
CELL = 12.0

#: How wide a blank gap still reads as the inside of one view, in points.
#: Drafting convention leaves inches between views and hairlines within them;
#: 24 pt is a third of an inch, comfortably inside that margin either way.
GAP = 24.0

#: A cluster smaller than this is a stray callout bubble or a north arrow, not
#: a view. Measured in occupied grid cells, so at CELL=12 roughly half an inch
#: square of drawing.
MIN_CELLS = 12

#: A path covering this fraction of the sheet is the border or a title-block
#: frame — it touches everything and would join every view into one.
BORDER_AREA = 0.55


@dataclass(frozen=True)
class View:
    """One cluster of drawing geometry, in unrotated PDF points."""
    rect: pymupdf.Rect
    paths: int

    def as_tuple(self) -> Tuple[float, float, float, float]:
        r = self.rect
        return (round(r.x0, 2), round(r.y0, 2), round(r.x1, 2), round(r.y1, 2))


@dataclass(frozen=True)
class Label:
    """A printed scale label and the box the words sit in."""
    text: str
    rect: pymupdf.Rect
    pt_per_ft: float


def gap(a: pymupdf.Rect, b: pymupdf.Rect) -> float:
    """Shortest distance between two rectangles; 0 when they touch or overlap."""
    dx = max(a.x0 - b.x1, b.x0 - a.x1, 0.0)
    dy = max(a.y0 - b.y1, b.y0 - a.y1, 0.0)
    return math.hypot(dx, dy)


def contains(outer: pymupdf.Rect, inner: pymupdf.Rect, slack: float = 2.0) -> bool:
    """Is `inner` wholly within `outer`, allowing a hairline's worth of slack?"""
    return (inner.x0 >= outer.x0 - slack and inner.y0 >= outer.y0 - slack
            and inner.x1 <= outer.x1 + slack and inner.y1 <= outer.y1 + slack)


def path_rects(paths, box: pymupdf.Rect) -> List[pymupdf.Rect]:
    """Every drawing path's bounding box, less the sheet border and frames.

    Takes the output of `page.get_drawings()` rather than the page, because that
    call costs about a second on a densely plotted sheet and the caller has
    already made it to histogram the CAD layers. Fetching it twice per page
    doubled the most expensive thing extraction does.

    A border rect is dropped rather than clustered because it abuts every view
    on the sheet: left in, it joins all of them into a single cluster carrying
    every scale label, which is the page-wide abstention this module exists to
    get past.
    """
    limit = abs(box.get_area()) * BORDER_AREA
    out: List[pymupdf.Rect] = []
    for p in paths:
        r = p.get("rect")
        if r is None or r.is_infinite:
            continue
        # NOT `r.is_empty`: a truly horizontal or vertical line has a zero-height
        # or zero-width bounding box and is empty by that test, which discards
        # most of the linework on a plotted sheet. Only a point is nothing.
        if abs(r.x1 - r.x0) <= 0 and abs(r.y1 - r.y0) <= 0:
            continue
        if abs(r.get_area()) >= limit:
            continue
        out.append(r)
    return out


def _dilate(occ: bytearray, nx: int, ny: int, reach: int) -> bytearray:
    """Grow the occupied set by `reach` cells, one axis at a time.

    Separably: a square neighbourhood costs (2r+1)^2 per cell scanned directly,
    and (2r+1) twice when done as a horizontal pass followed by a vertical one.
    At r=4 that is 81 against 18, on a grid of up to 27,000 cells, per page.
    """
    wide = bytearray(nx * ny)
    for cy in range(ny):
        base = cy * nx
        for cx in range(nx):
            if occ[base + cx]:
                lo = cx - reach if cx > reach else 0
                hi = cx + reach if cx + reach < nx else nx - 1
                for k in range(lo, hi + 1):
                    wide[base + k] = 1

    tall = bytearray(nx * ny)
    for cy in range(ny):
        base = cy * nx
        lo = cy - reach if cy > reach else 0
        hi = cy + reach if cy + reach < ny else ny - 1
        for cx in range(nx):
            if wide[base + cx]:
                for k in range(lo, hi + 1):
                    tall[k * nx + cx] = 1
    return tall


def _label_components(grid: bytearray, nx: int, ny: int) -> Tuple[List[int], int]:
    """4-connected components of a boolean grid, as a per-cell label array.

    Iterative: a recursive fill overflows the stack on a sheet whose linework
    fills a few thousand cells, which is every real sheet.
    """
    comp = [-1] * (nx * ny)
    count = 0
    for start in range(nx * ny):
        if not grid[start] or comp[start] >= 0:
            continue
        comp[start] = count
        stack = [start]
        while stack:
            i = stack.pop()
            x = i % nx
            if x > 0 and grid[i - 1] and comp[i - 1] < 0:
                comp[i - 1] = count
                stack.append(i - 1)
            if x < nx - 1 and grid[i + 1] and comp[i + 1] < 0:
                comp[i + 1] = count
                stack.append(i + 1)
            if i >= nx and grid[i - nx] and comp[i - nx] < 0:
                comp[i - nx] = count
                stack.append(i - nx)
            if i + nx < nx * ny and grid[i + nx] and comp[i + nx] < 0:
                comp[i + nx] = count
                stack.append(i + nx)
        count += 1
    return comp, count


def cluster(rects: List[pymupdf.Rect], box: pymupdf.Rect) -> List[View]:
    """Group path bounding boxes into views. Pure geometry, no page needed.

    The geometry is stamped onto a coarse occupancy grid, the grid is grown by
    the gap tolerance, and the result is flood-filled. That is linear in the
    linework and bounded by the grid, rather than quadratic in the path count —
    which matters because a plotted sheet carries tens of thousands of paths.

    A view's box is drawn around the cells the geometry actually occupies, not
    around the dilated ones, so the reported rect hugs the drawing.
    """
    if not rects or box.is_empty:
        return []

    ox, oy = box.x0, box.y0
    nx = int(box.width // CELL) + 1
    ny = int(box.height // CELL) + 1
    if nx < 1 or ny < 1:
        return []

    occ = bytearray(nx * ny)
    hits = [0] * (nx * ny)
    for r in rects:
        cx0 = int((min(r.x0, r.x1) - ox) // CELL)
        cx1 = int((max(r.x0, r.x1) - ox) // CELL)
        cy0 = int((min(r.y0, r.y1) - oy) // CELL)
        cy1 = int((max(r.y0, r.y1) - oy) // CELL)
        cx0 = 0 if cx0 < 0 else cx0
        cy0 = 0 if cy0 < 0 else cy0
        cx1 = nx - 1 if cx1 > nx - 1 else cx1
        cy1 = ny - 1 if cy1 > ny - 1 else cy1
        if cx1 < cx0 or cy1 < cy0:
            continue
        for cy in range(cy0, cy1 + 1):
            base = cy * nx
            for cx in range(cx0, cx1 + 1):
                i = base + cx
                occ[i] = 1
                hits[i] += 1

    reach = max(1, int(round(GAP / CELL)))
    comp, count = _label_components(_dilate(occ, nx, ny, reach), nx, ny)
    if not count:
        return []

    #: per component: [cells, paths, min cx, min cy, max cx, max cy]
    acc: Dict[int, List[int]] = {}
    for i in range(nx * ny):
        if not occ[i]:
            continue
        c = comp[i]
        if c < 0:
            continue
        cx, cy = i % nx, i // nx
        a = acc.get(c)
        if a is None:
            acc[c] = [1, hits[i], cx, cy, cx, cy]
        else:
            a[0] += 1
            a[1] += hits[i]
            if cx < a[2]:
                a[2] = cx
            if cy < a[3]:
                a[3] = cy
            if cx > a[4]:
                a[4] = cx
            if cy > a[5]:
                a[5] = cy

    views = [View(pymupdf.Rect(ox + a[2] * CELL, oy + a[3] * CELL,
                               ox + (a[4] + 1) * CELL, oy + (a[5] + 1) * CELL), a[1])
             for a in acc.values() if a[0] >= MIN_CELLS]
    views.sort(key=lambda v: (v.rect.y0, v.rect.x0))
    return views


def drawn_views(page: pymupdf.Page, paths=None) -> List[View]:
    """Cluster the page's drawing geometry into views.

    `paths` is `page.get_drawings()` when the caller already holds it; passing it
    is what keeps this from being the second full traversal of the page.
    """
    if paths is None:
        paths = page.get_drawings()
    return cluster(path_rects(paths, page.mediabox), page.mediabox)


def _line_boxes(page: pymupdf.Page) -> List[Tuple[str, List[Tuple[str, pymupdf.Rect]]]]:
    """Each text line as its joined string plus its spans, for offset mapping."""
    out = []
    for block in page.get_text("dict").get("blocks", []):
        for line in block.get("lines", []):
            spans = [(s.get("text", ""), pymupdf.Rect(s["bbox"]))
                     for s in line.get("spans", []) if s.get("text")]
            if spans:
                out.append(("".join(t for t, _ in spans), spans))
    return out


def _span_rect(spans: List[Tuple[str, pymupdf.Rect]], start: int, end: int) -> pymupdf.Rect:
    """The box around characters [start, end) of the joined line.

    Span granularity, not character: a scale label is usually its own span or
    two, and interpolating within a span would claim a precision the text
    extractor does not offer.
    """
    box = pymupdf.Rect()
    box.x0, box.y0, box.x1, box.y1 = 0, 0, -1, -1     # empty
    pos = 0
    for text, rect in spans:
        lo, hi = pos, pos + len(text)
        pos = hi
        if hi <= start or lo >= end:
            continue
        box = rect if box.is_empty else box | rect
    return box


def scale_labels(page: pymupdf.Page, pattern: re.Pattern,
                 to_pt_per_ft) -> List[Label]:
    """Every `X" = 1'-0"` label on the sheet, with where it is printed.

    `pattern` and `to_pt_per_ft` come from `scale.py` so that the vocabulary of
    what counts as a scale label is stated once. This module only finds where
    the match landed.
    """
    out: List[Label] = []
    for text, spans in _line_boxes(page):
        for m in pattern.finditer(text):
            value = to_pt_per_ft(m)
            if value is None:
                continue
            rect = _span_rect(spans, m.start(), m.end())
            if rect.is_empty:
                continue
            out.append(Label(m.group(0).strip(), rect, value))
    return out


def owning_view(label: Label, views: List[View],
                reach: float = 96.0, margin: float = 12.0) -> Optional[int]:
    """Index of the view this label labels, or None when that is not decidable.

    Drafting convention puts the scale under the view title, under the view, so
    the first question asked is which view sits *above* the label and overlaps
    it horizontally. A label inside a view's own box belongs to that view. Only
    when neither holds does it fall back to plain nearest-rectangle.

    None is returned for a label no view claims within `reach`, and for a label
    two views claim about equally well — a tie is a genuine "cannot tell", and
    `scale.py` turns it into an abstention naming the sheet rather than into a
    coin flip.
    """
    if not views:
        return None

    inside = [i for i, v in enumerate(views) if contains(v.rect, label.rect)]
    if len(inside) == 1:
        return inside[0]
    if len(inside) > 1:
        return None

    above = []
    for i, v in enumerate(views):
        if v.rect.y1 <= label.rect.y0 + margin and _overlaps_x(v.rect, label.rect):
            above.append((label.rect.y0 - v.rect.y1, i))
    if above:
        above.sort()
        if len(above) == 1 or above[1][0] - above[0][0] > margin:
            return above[0][1] if above[0][0] <= reach else None
        return None

    ranked = sorted((gap(label.rect, v.rect), i) for i, v in enumerate(views))
    if ranked[0][0] > reach:
        return None
    if len(ranked) > 1 and ranked[1][0] - ranked[0][0] <= margin:
        return None
    return ranked[0][1]


def _overlaps_x(a: pymupdf.Rect, b: pymupdf.Rect) -> bool:
    return a.x0 <= b.x1 and b.x0 <= a.x1
