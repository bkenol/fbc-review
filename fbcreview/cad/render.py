"""Each sheet of a drawing plotted to a PDF page the engine can read.

ezdxf's PyMuPDF backend draws a layout as vector paths, with every CAD layer as
a PDF optional-content group. It draws *text* as glyph outlines, so the page it
produces has no text layer, and a reader that works from text sees nothing.
This module puts the text back, the way `webapp/convert.py` puts OCR text back
over a scanned sheet: invisible (render mode 3), extractable, never drawn over
the plot. The difference is that nothing here is recognised — every string is
the drafter's own, captured as the renderer draws it.

## Capturing text where the plot puts it

Every piece of text ezdxf draws — TEXT, MTEXT, an ATTRIB in a title block, the
measurement on a DIMENSION, model-space text seen through a viewport at 1:96 —
passes through `RenderPipeline2d.draw_text(text, transform, …)`, with the
transform that places its glyphs. `CapturePipeline` records each call before
drawing it: the string, that transform composed with the viewport's own (a
clipping stage holds it), the glyphs' extent, and whether the viewport's
boundary clips it away. Nothing is re-derived from the entity, so a run is
placed exactly where its outlines are.

## Writing it back in cells

The lesson `convert.py` paid for applies here too: written word by word, words
fuse on extraction (`HOLLOWCORE` `PLANK` -> `HOLLOWCOREPLANK`, because complex
MTEXT is drawn one word per call); written as whole paragraphs, columns drift.
So runs from one entity on one baseline, a word space apart, are joined into a
cell, and each cell is written at its own true origin, scaled so it spans the
ink. A stacked fraction (`5'-0` over `3`/`4`) is rejoined as `5'-0 3/4`.

## What a page remembers

`to_page` is the affine map from the layout's coordinates to the page's points
(origin top-left), and each viewport's own map from model space. They are what
let a finding drawn on the PDF be put back into the drawing (`markup.py`), and
what turns a viewport's scale into an exact points-per-foot (`claims.py`).
"""
from __future__ import annotations

import copy
import logging
import math
import re
import time
import unicodedata
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import pymupdf
from ezdxf import bbox as ezbbox
from ezdxf.addons.drawing import RenderContext
from ezdxf.addons.drawing import layout as dl
from ezdxf.addons.drawing import pymupdf as pmb
from ezdxf.addons.drawing.config import Configuration
from ezdxf.addons.drawing.frontend import UniversalFrontend
from ezdxf.addons.drawing.pipeline import RenderPipeline2d
from ezdxf.document import Drawing
from ezdxf.math import BoundingBox2d, Matrix44, Vec2

from .read import SheetSpec, _is_paper_viewport

log = logging.getLogger("fbc.cad")

Affine = Tuple[float, float, float, float, float, float]
Box = Tuple[float, float, float, float]

#: Invisible text is set at a font size equal to the drafter's cap height, then
#: squeezed or stretched to the width the ink occupies. Not larger: PyMuPDF
#: reports a Helvetica word box 1.374 × the font size tall, and the layout
#: layer drops a stacked value whose box overlaps its label's by more than a
#: quarter of that (`layout/build.py`, `_below`). Measured on stacked label and
#: value pairs: 1.0 × cap reads every pitch a drafter uses down to 1.1 × cap;
#: Helvetica at the drafter's visible cap height (cap / 0.718) loses
#: single-line TEXT stacked at 1.25 × cap. Never shrink to fit width either —
#: that changes the box height every threshold is a multiple of.
FONT_PER_CAP = 1.0

#: Pieces of a line separated by two or more spaces.
_PIECE = re.compile(r"\S+(?: \S+)*")


def affine(m: Matrix44) -> Affine:
    """The 2-D part of an ezdxf matrix as PDF's (a, b, c, d, e, f).

    ezdxf transforms row vectors: x' = x·m00 + y·m10 + m30, y' = x·m01 + y·m11 + m31.
    """
    r0, r1, r3 = m.get_row(0), m.get_row(1), m.get_row(3)
    return (r0[0], r0[1], r1[0], r1[1], r3[0], r3[1])


def apply(a: Affine, x: float, y: float) -> Tuple[float, float]:
    return (a[0] * x + a[2] * y + a[4], a[1] * x + a[3] * y + a[5])


def invert(a: Affine) -> Affine:
    det = a[0] * a[3] - a[1] * a[2]
    if abs(det) < 1e-18:
        raise ValueError("singular transform")
    ia, ib, ic, id_ = a[3] / det, -a[1] / det, -a[2] / det, a[0] / det
    return (ia, ib, ic, id_, -(ia * a[4] + ic * a[5]), -(ib * a[4] + id_ * a[5]))


def scale_of(a: Affine) -> float:
    """Length scale of an affine map (geometric mean of its axes)."""
    return math.sqrt(abs(a[0] * a[3] - a[1] * a[2]))


def box_through(a: Affine, box: Box) -> Box:
    xs, ys = zip(*(apply(a, x, y) for x, y in
                   ((box[0], box[1]), (box[2], box[1]), (box[2], box[3]), (box[0], box[3]))))
    return (min(xs), min(ys), max(xs), max(ys))


@dataclass
class TextRun:
    """One string as the renderer drew it."""
    text: str
    layer: str
    handle: str            # the top-level entity: the TEXT, or the INSERT it sits in
    kind: str              # innermost entity type: TEXT, MTEXT, ATTRIB, ATTDEF, …
    parent: str            # outermost entity type: INSERT, DIMENSION, or the same as kind
    tag: str               # ATTRIB / ATTDEF tag
    prompt: str            # ATTDEF prompt
    viewport: str          # viewport handle the run was seen through; "" on paper
    m: Matrix44            # glyph space -> layout coordinates
    x0: float
    x1: float
    cap: float


@dataclass
class Cell:
    """Text written back as one invisible run, in page points."""
    text: str
    origin: Tuple[float, float]
    end: Tuple[float, float]
    cap: float
    runs: List[TextRun] = field(default_factory=list)

    @property
    def angle(self) -> float:
        return math.atan2(self.end[1] - self.origin[1], self.end[0] - self.origin[0])

    @property
    def width(self) -> float:
        return math.hypot(self.end[0] - self.origin[0], self.end[1] - self.origin[1])

    def box(self) -> Box:
        """The cell's box in page points: baseline to cap height, both ends."""
        a = self.angle
        # up, in page space (y grows downward): perpendicular to the baseline
        ux, uy = math.sin(a), -math.cos(a)
        pts = [self.origin, self.end,
               (self.origin[0] + ux * self.cap, self.origin[1] + uy * self.cap),
               (self.end[0] + ux * self.cap, self.end[1] + uy * self.cap)]
        xs, ys = zip(*pts)
        return (min(xs), min(ys), max(xs), max(ys))


class CapturePipeline(RenderPipeline2d):
    """ezdxf's 2-D pipeline, recording every string it draws. See module docstring."""

    def __init__(self, backend):
        super().__init__(backend)
        self.runs: List[TextRun] = []
        self._stack: list = []
        self._viewport = ""
        self.skipped_text = 0

    # entity context ---------------------------------------------------------
    def enter_entity(self, entity, properties) -> None:
        self._stack.append(entity)
        super().enter_entity(entity, properties)

    def exit_entity(self, entity) -> None:
        if self._stack:
            self._stack.pop()
        super().exit_entity(entity)

    def enter_viewport(self, vp) -> bool:
        ok = super().enter_viewport(vp)
        if ok:
            self._viewport = vp.dxf.handle
        return ok

    def exit_viewport(self):
        self._viewport = ""
        super().exit_viewport()

    # text ---------------------------------------------------------------------
    def draw_text(self, text, transform, properties, cap_height, dxftype="TEXT") -> None:
        try:
            self._record(text, transform, properties, cap_height, dxftype)
        except Exception:
            # Recording must never cost the plot its text.
            self.skipped_text += 1
        super().draw_text(text, transform, properties, cap_height, dxftype)

    def _record(self, text, transform, properties, cap_height, dxftype) -> None:
        if not text or not text.strip():
            return
        font = properties.font or self.default_font_face
        paths = self.text_engine.get_text_glyph_paths(text, font, cap_height)
        pts: list = []
        for p in paths:
            pts.extend(p.extents())
        if not pts:
            return
        local = BoundingBox2d(pts)
        x0, x1 = local.extmin.x, local.extmax.x
        m = self.clipping_portal.transform_matrix(transform.copy())
        inner = self._stack[-1] if self._stack else None
        outer = self._stack[0] if self._stack else None
        kind = inner.dxftype() if inner is not None else dxftype
        tag = prompt = ""
        if kind in ("ATTRIB", "ATTDEF"):
            tag = str(inner.dxf.get("tag", "") or "")
            prompt = str(inner.dxf.get("prompt", "") or "") if kind == "ATTDEF" else ""
        # A line laid out in columns with runs of spaces (`40psf LL      FLOOR`)
        # is written piece by piece, each where the CAD font puts it: written
        # whole in another font, the spaces would drift every column after the
        # first.
        pieces = [(mt.start(), mt.group()) for mt in _PIECE.finditer(text)]
        if len(pieces) > 1:
            spans = []
            for start, piece in pieces:
                off = self.text_engine.get_text_line_width(text[:start], font, cap_height)
                width = self.text_engine.get_text_line_width(piece, font, cap_height)
                spans.append((piece, off, off + width))
        else:
            spans = [(text, x0, x1)]
        for piece, px0, px1 in spans:
            if self.clipping_portal.is_active:
                mid = Vec2(transform.transform(((px0 + px1) / 2.0, cap_height / 2.0, 0.0)))
                if self.clipping_portal.clip_point(mid) is None:
                    continue   # outside the viewport's boundary: not on the sheet
            self.runs.append(TextRun(
                text=piece, layer=properties.layer, handle=self._current_entity_handle or "",
                kind=kind, parent=outer.dxftype() if outer is not None else kind,
                tag=tag, prompt=prompt, viewport=self._viewport, m=m,
                x0=px0, x1=px1, cap=cap_height))


class _SharedPageRender(pmb.PyMuPdfRenderBackend):
    """ezdxf's PyMuPDF render backend, drawing into one document for the whole set.

    Two changes from the stock backend, both about layers:

    * Every sheet is drawn into the same document with one shared table of
      optional-content groups. Plotted one document per sheet and merged with
      `insert_pdf`, the pages kept their marked content but the merged file
      lost its layer table — `get_ocgs()` came back empty, so the review saw
      no CAD layers at all and a viewer showed no layer panel.
    * Layers keep the name the drafter gave them. ezdxf lower-cases them; a
      person knows the layer as `A-DOOR`, not `a-door`.
    """

    def __init__(self, page, settings, shared, ocgs: Dict[str, int], names: Dict[str, str]):
        super().__init__(page, settings)          # makes a scratch document; replaced below
        self.doc = shared
        self.page = shared.new_page(-1, self.page_width_in_pt, self.page_height_in_pt)
        self.content_shape = self.page.new_shape()
        self._optional_content_groups = ocgs
        self._names = names

    def get_optional_content_group(self, layer_name: str) -> int:
        if not self.settings.output_layers:
            return 0
        key = layer_name.lower()
        if key not in self._optional_content_groups:
            self._optional_content_groups[key] = self.doc.add_ocg(
                name=self._names.get(key, layer_name), config=-1, on=True)
        return self._optional_content_groups[key]


class _Backend(pmb.PyMuPdfBackend):
    def __init__(self, shared, ocgs: Dict[str, int], names: Dict[str, str]):
        super().__init__()
        self._shared, self._ocgs, self._names = shared, ocgs, names

    def make_backend(self, page, settings):
        return _SharedPageRender(page, settings, self._shared, self._ocgs, self._names)


class PlotTarget:
    """The one PDF a set is plotted into, and its shared layer table."""

    def __init__(self):
        self.doc = pymupdf.open()
        self.ocgs: Dict[str, int] = {}


@dataclass
class ViewportInfo:
    handle: str
    rect: Box                 # page points, on the rendered page
    scale: float              # layout units per model unit (1/96 at 1/8" = 1'-0")
    model_window: Box         # model-space extents the viewport frames
    twist: float
    to_page: Affine           # model space -> page points
    frozen_layers: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"handle": self.handle, "rect": [round(v, 2) for v in self.rect],
                "scale": self.scale, "model_window": [round(v, 3) for v in self.model_window],
                "twist": self.twist, "to_page": list(self.to_page),
                "frozen_layers": self.frozen_layers}


@dataclass
class RenderedSheet:
    spec: SheetSpec
    page: int                         # index in the shared plot document
    to_page: Affine                   # layout coordinates -> page points
    runs: List[TextRun]
    viewports: List[ViewportInfo]
    seconds: float
    skipped_text: int = 0


def _layer_names(doc: Drawing) -> Dict[str, str]:
    return {layer.dxf.name.lower(): layer.dxf.name for layer in doc.layers}


def model_bbox_cache(doc: Drawing) -> ezbbox.Cache:
    """Bounding boxes of every model-space entity, computed once per drawing.

    Without it ezdxf draws the whole of model space through every viewport and
    clips it afterwards: 72 s for one sheet of the reference drawing. With it,
    one 17 s pass, then 2–5 s a sheet.
    """
    cache = ezbbox.Cache()
    for e in doc.modelspace():
        try:
            ezbbox.extents((e,), cache=cache, fast=True)
        except Exception:
            continue
    return cache


def render_sheet(doc: Drawing, spec: SheetSpec, cache: Optional[ezbbox.Cache],
                 target: PlotTarget, names: Optional[Dict[str, str]] = None) -> RenderedSheet:
    """Plot one sheet as the next page of `target`."""
    t0 = time.monotonic()
    names = names if names is not None else _layer_names(doc)
    layout = doc.modelspace() if spec.model else doc.paperspace(spec.layout)
    backend = _Backend(target.doc, target.ocgs, names)
    pipeline = CapturePipeline(backend)
    ctx = RenderContext(doc, export_mode=True)
    fe = UniversalFrontend(ctx, pipeline, Configuration(), cache)
    fe.draw_layout(layout, finalize=True)

    page = dl.Page(spec.paper_mm[0], spec.paper_mm[1], dl.Units.mm)
    window = BoundingBox2d([(spec.window[0], spec.window[1]), (spec.window[2], spec.window[3])])
    settings = dl.Settings(fit_page=True, content_rotation=spec.rotation)
    placement = dl.Layout(window, flip_y=True)
    final = placement.get_final_page(page, settings)
    s2 = copy.copy(settings)
    s2.output_coordinate_space = pmb.get_coordinate_output_space(final)
    m = placement.get_placement_matrix(final, settings=s2, top_origin=True)
    pno = target.doc.page_count
    backend.get_replay(page, settings=settings, render_box=window)
    if target.doc.page_count != pno + 1:
        raise RuntimeError("the plot did not produce exactly one page")
    to_page = affine(m)

    viewports: List[ViewportInfo] = []
    if not spec.model:
        for vp in layout.query("VIEWPORT"):
            if _is_paper_viewport(vp) or vp.dxf.get("status", 0) < 1:
                continue
            try:
                vm = vp.get_transformation_matrix() @ m
                limits = vp.get_modelspace_limits()
            except (ValueError, ZeroDivisionError, AttributeError):
                continue
            c, w, h = vp.dxf.center, vp.dxf.width, vp.dxf.height
            rect = box_through(to_page, (c.x - w / 2, c.y - h / 2, c.x + w / 2, c.y + h / 2))
            viewports.append(ViewportInfo(
                handle=vp.dxf.handle, rect=rect, scale=float(vp.get_scale()),
                model_window=tuple(limits), twist=float(vp.dxf.get("view_twist_angle", 0.0)),
                to_page=affine(vm),
                frozen_layers=sorted(str(n) for n in getattr(vp, "frozen_layers", []) or [])))
    return RenderedSheet(spec=spec, page=pno, to_page=to_page, runs=pipeline.runs,
                         viewports=viewports, seconds=time.monotonic() - t0,
                         skipped_text=pipeline.skipped_text)


# ── the text layer ───────────────────────────────────────────────────────────

#: Characters a CAD font draws that the PDF base font cannot encode, and what a
#: reader should find in their place.
_SUBSTITUTE = {"⌀": "Ø", "″": '"', "′": "'", "≤": "<=", "≥": ">=",
               "≠": "!=", "≈": "~", "∅": "Ø", "∞": "inf",
               "←": "<-", "→": "->", "•": "•", " ": " "}


def _encodable(text: str) -> str:
    out = []
    for ch in text:
        ch = _SUBSTITUTE.get(ch, ch)
        try:
            ch.encode("cp1252")
            out.append(ch)
        except UnicodeEncodeError:
            folded = unicodedata.normalize("NFKD", ch).encode("ascii", "ignore").decode()
            out.append(folded or "?")
    return "".join(out)


def _page_run(run: TextRun, to_page: Affine):
    """A run's baseline start, end and cap height in page points."""
    pm = run.m
    o = apply(to_page, *Vec2(pm.transform((run.x0, 0.0, 0.0))))
    e = apply(to_page, *Vec2(pm.transform((run.x1, 0.0, 0.0))))
    c = apply(to_page, *Vec2(pm.transform((run.x0, run.cap, 0.0))))
    cap = math.hypot(c[0] - o[0], c[1] - o[1])
    return o, e, cap


def cells(runs: Sequence[TextRun], to_page: Affine) -> List[Cell]:
    """Join runs into the cells they were drawn as. See module docstring."""
    out: List[Cell] = []
    cur: Optional[Cell] = None
    pending_stack: List[Tuple[TextRun, tuple, tuple, float]] = []

    def flush_stack():
        nonlocal cur
        if len(pending_stack) == 2 and cur is not None:
            # numerator is the upper of the two (smaller page y)
            a, b = sorted(pending_stack, key=lambda r: r[1][1])
            cur.text = f"{cur.text} {a[0].text.strip()}/{b[0].text.strip()}"
            cur.end = (max(cur.end[0], a[2][0], b[2][0]), cur.end[1]) if abs(cur.angle) < 1e-3 \
                else cur.end
            cur.runs.extend([a[0], b[0]])
        else:
            for r, o, e, cap in pending_stack:
                _start(r, o, e, cap)
        pending_stack.clear()

    def _start(r, o, e, cap):
        nonlocal cur
        if cur is not None:
            out.append(cur)
        cur = Cell(text=r.text.strip(), origin=o, end=e, cap=cap, runs=[r])

    for r in runs:
        o, e, cap = _page_run(r, to_page)
        if cap < 0.05 or math.hypot(e[0] - o[0], e[1] - o[1]) < 0.05:
            continue
        if cur is None:
            _start(r, o, e, cap)
            continue
        same_entity = r.handle == cur.runs[-1].handle and r.viewport == cur.runs[-1].viewport
        ang = math.atan2(e[1] - o[1], e[0] - o[0])
        dang = abs((ang - cur.angle + math.pi) % (2 * math.pi) - math.pi)
        # distance of this run's origin along and across the current baseline
        ca, sa = math.cos(cur.angle), math.sin(cur.angle)
        dx, dy = o[0] - cur.end[0], o[1] - cur.end[1]
        along = dx * ca + dy * sa
        across = -dx * sa + dy * ca
        small = cap < 0.85 * cur.cap
        if (same_entity and dang < math.radians(2) and small
                and -cur.cap <= along <= 1.2 * cur.cap and abs(across) <= 1.3 * cur.cap):
            # half of a stacked fraction, set smaller and off the baseline
            pending_stack.append((r, o, e, cap))
            if len(pending_stack) == 2:
                flush_stack()
            continue
        if pending_stack:
            flush_stack()
        if (same_entity and dang < math.radians(2) and abs(across) <= 0.25 * cur.cap
                and -0.3 * cur.cap <= along <= 1.1 * cur.cap and 0.6 <= cap / cur.cap <= 1.6):
            sep = "" if along < 0.12 * cur.cap else " "
            cur.text = f"{cur.text}{sep}{r.text.strip()}"
            cur.end = e
            cur.runs.append(r)
            continue
        _start(r, o, e, cap)
    if pending_stack:
        flush_stack()
    if cur is not None:
        out.append(cur)
    return [c for c in out if c.text.strip()]


def write_cells(page: pymupdf.Page, cell_list: Iterable[Cell]) -> int:
    """Write cells onto a page as invisible text. Returns how many were written."""
    font = pymupdf.Font("helv")
    n = 0
    for c in cell_list:
        text = _encodable(c.text)
        if not text.strip() or c.width < 0.2 or c.cap < 0.2:
            continue
        size = c.cap * FONT_PER_CAP
        natural = font.text_length(text, fontsize=size)
        if natural <= 0:
            continue
        # `morph` rotates in PDF's y-up sense: Matrix(θ) sends +x to (cos θ, −sin θ)
        # in page space, so the page angle is negated. Measured, not assumed.
        deg = -math.degrees(c.angle)
        mat = pymupdf.Matrix(c.width / natural, 1) * pymupdf.Matrix(deg)
        origin = pymupdf.Point(*c.origin)
        try:
            page.insert_text(origin, text, fontsize=size, fontname="helv", render_mode=3,
                             morph=(origin, mat))
            n += 1
        except (ValueError, RuntimeError):
            continue
    return n


@dataclass
class SheetPage:
    """What the sidecar keeps about one rendered page."""
    page: int
    drawing: str
    layout: str
    model: bool
    paper_mm: Tuple[float, float]
    window: Box
    to_page: Affine
    viewports: List[ViewportInfo]
    cells: int
    runs: int
    seconds: float
    note: str = ""
    #: sheet number and title as the drawing's own title-block fields give them
    number: str = ""
    number_source: str = ""
    title: str = ""
    #: Each text cell's box and the drawing entities it was drawn from — what
    #: lets a claim read off the PDF name its source. Boxes and handles only;
    #: the strings are in the PDF.
    text: List[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"page": self.page, "drawing": self.drawing, "layout": self.layout,
                "model": self.model, "paper_mm": [round(v, 2) for v in self.paper_mm],
                "window": [round(v, 4) for v in self.window], "to_page": list(self.to_page),
                "viewports": [v.to_dict() for v in self.viewports], "cells": self.cells,
                "runs": self.runs, "seconds": round(self.seconds, 2), "note": self.note,
                "number": self.number, "number_source": self.number_source,
                "title": self.title, "text": self.text}
