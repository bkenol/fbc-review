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
ink. A stacked fraction (`5'-0`, then `3` set over `4`) is rejoined as
`5'-0 3/4`, recognised by the geometry ezdxf gives it (`_fraction`).

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

import numpy as np
import pymupdf
from ezdxf import bbox as ezbbox
from ezdxf.addons.drawing import RenderContext
from ezdxf.addons.drawing import layout as dl
from ezdxf.addons.drawing import pymupdf as pmb
from ezdxf.addons.drawing.config import Configuration, ImagePolicy
from ezdxf.addons.drawing.frontend import UniversalFrontend
from ezdxf.tools import text_layout
from ezdxf.addons.drawing.pipeline import RenderPipeline2d, prepare_string_for_rendering
from ezdxf.document import Drawing
from ezdxf.math import BoundingBox2d, Matrix44, Vec2
from ezdxf.tools.text_layout import Fraction as _EzFraction

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
    #: The entity itself, through every block it is drawn from: `INSERT` or
    #: `INSERT:TEXT`, `INSERT:INSERT:TEXT` for blocks in blocks. `handle` is
    #: the INSERT for everything inside one, so two notes in one block — or a
    #: whole embedded xref — would read as one entity, and agreeing with
    #: itself is not corroboration while two notes are.
    source: str = ""


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
        # Measure the string ezdxf draws, not the one it was given: it draws an
        # MTEXT tab as eight spaces and a TEXT tab as `?`, and the words after a
        # tab were placed about eight spaces left of their ink (measured: 24 pt
        # at 9 pt text). A TEXT tab is written as a space, so a value after it
        # stays its own word rather than fusing with a `?`.
        if dxftype in ("TEXT", "ATTRIB", "ATTDEF"):
            text = text.replace("\t", " ")
        try:
            text = prepare_string_for_rendering(text, dxftype)
        except (TypeError, AssertionError):
            pass
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
        source = ":".join([self._current_entity_handle or ""] + [
            e.source_of_copy.dxf.handle for e in self._stack
            if e.is_virtual and getattr(e, "source_of_copy", None) is not None])
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
            # Each piece spans its own ink, from the pen position the line's
            # advance puts it at. The advance alone is the pen, not the ink: a
            # piece placed by it started 1.2 pt left of its first glyph at 9 pt
            # (measured on `FLOOR`), and the error grows with the text.
            spans = []
            for start, piece in pieces:
                off = self.text_engine.get_text_line_width(text[:start], font, cap_height)
                ink: list = []
                for p in self.text_engine.get_text_glyph_paths(piece, font, cap_height):
                    ink.extend(p.extents())
                if ink:
                    box = BoundingBox2d(ink)
                    spans.append((piece, off + box.extmin.x, off + box.extmax.x))
                else:
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
                x0=px0, x1=px1, cap=cap_height, source=source))


class PlotFrontend(UniversalFrontend):
    """ezdxf's frontend, keeping text its layout engine refuses.

    ezdxf lays out an MTEXT with inline formatting or columns itself, and gives
    up — "invalid width, no usable space left" — on one whose defined width is
    zero, where AutoCAD simply does not wrap. On the reference drawing that was
    a wall note, `2-1/2"`, at width 9.7e-20: drawn in the plot by AutoCAD,
    missing from ours. Drawn as plain MTEXT instead, it keeps its words and
    position and loses only the inline styling.

    And it never opens a file the drawing names. An IMAGEDEF is a path, and
    ezdxf's default is to read it and draw the picture: a DXF naming any image
    on the server had that file embedded in the PDF its uploader downloads.
    Images are drawn as their frames, whatever configuration is passed.
    """

    def __init__(self, ctx, pipeline, config: Optional[Configuration] = None,
                 bbox_cache: Optional[ezbbox.Cache] = None) -> None:
        config = (config or Configuration()).with_changes(image_policy=ImagePolicy.RECT)
        super().__init__(ctx, pipeline, config, bbox_cache)

    def draw_complex_mtext(self, mtext, properties) -> None:
        try:
            super().draw_complex_mtext(mtext, properties)
        except text_layout.LayoutError:
            self.draw_simple_mtext(mtext, properties)

    def draw_composite_entity(self, entity, properties) -> None:
        """An INSERT, and its block's visible constant attributes.

        A constant attribute has no ATTRIB on the INSERT: AutoCAD shows the
        block definition's value, and ezdxf draws nothing (measured: a code
        block's constant RISK_CATEGORY `II` was missing from the plot and the
        text layer, and the value reported as not stated). Each is drawn as
        the ATTRIB AutoCAD would show, placed by the INSERT.
        """
        super().draw_composite_entity(entity, properties)
        if entity.dxftype() != "INSERT":
            return
        try:
            block = entity.block()
            consts = [a for a in block.get_const_attdefs() if not a.is_invisible] \
                if block is not None else []
        except Exception:
            return
        if not consts:
            return
        from ezdxf.entities import Attrib
        self.ctx.push_state(properties)
        try:
            for ins in (entity.multi_insert() if entity.mcount > 1 else [entity]):
                m = ins.matrix44()
                drawn = []
                for a in consts:
                    at = Attrib.new(dxfattribs=a.dxfattribs(drop={"prompt", "handle", "owner"}),
                                    doc=entity.doc)
                    if a.has_embedded_mtext_entity:
                        at.embed_mtext(a.virtual_mtext_entity())
                    at.transform(m)
                    at.set_source_of_copy(a)
                    drawn.append(at)
                self.draw_entities(drawn)
        finally:
            self.ctx.pop_state()


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
    #: Model-space entities on plotting layers that the viewport frames
    #: (`drawn_in`); None when nobody counted.
    drawn: Optional[int] = None

    def to_dict(self) -> dict:
        return {"handle": self.handle, "rect": [round(v, 2) for v in self.rect],
                "scale": self.scale, "model_window": [round(v, 3) for v in self.model_window],
                "twist": self.twist, "to_page": list(self.to_page),
                "frozen_layers": self.frozen_layers, "drawn": self.drawn}


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


@dataclass
class ModelIndex:
    """Every model-space entity's extents and layer, as arrays, for counting
    what a viewport frames without drawing it."""
    boxes: "np.ndarray"            # (n, 4): x0, y0, x1, y1
    layers: "np.ndarray"           # (n,): lower-case layer names
    plots: "np.ndarray"            # (n,): the layer is on, thawed and plotted


def model_index(doc: Drawing, cache: Optional[ezbbox.Cache] = None) -> ModelIndex:
    """Built from the bounding-box cache, so it costs one pass over boxes the
    plot already computed (measured: ~2 s for the reference drawing's 296 000)."""
    off = set()
    for layer in doc.layers:
        if not layer.is_on() or layer.is_frozen() or not layer.dxf.get("plot", 1):
            off.add(layer.dxf.name.lower())
    boxes, layers = [], []
    for e in doc.modelspace():
        try:
            bb = ezbbox.extents((e,), cache=cache, fast=True)
        except Exception:
            continue
        if bb.has_data:
            boxes.append((bb.extmin.x, bb.extmin.y, bb.extmax.x, bb.extmax.y))
        elif _unresolved_xref(doc, e):
            # An xref that was not uploaded draws nothing here, but the drawing
            # does place it: a view onto it is reported as missing that file
            # (`read.embed_xrefs`), by name, not as framing nothing.
            p = e.dxf.insert
            boxes.append((p.x, p.y, p.x, p.y))
        else:
            continue
        layers.append(str(e.dxf.get("layer", "0")).lower())
    names = np.array(layers, dtype=object)
    return ModelIndex(boxes=np.array(boxes, dtype=float).reshape(-1, 4), layers=names,
                      plots=np.array([n not in off for n in layers], dtype=bool))


def _unresolved_xref(doc: Drawing, e) -> bool:
    if e.dxftype() != "INSERT":
        return False
    block = doc.blocks.get(e.dxf.name)
    return bool(block is not None and getattr(block.block_record, "is_xref", False))


def drawn_in(index: ModelIndex, window: Box, frozen: Iterable[str] = ()) -> int:
    """Entities on plotting layers whose extents meet a viewport's model window.

    An overcount at worst — an entity's box can meet the window where the
    entity does not — so zero means the viewport shows nothing at all.
    """
    if not len(index.boxes):
        return 0
    b = index.boxes
    hit = ((b[:, 0] <= window[2]) & (b[:, 2] >= window[0])
           & (b[:, 1] <= window[3]) & (b[:, 3] >= window[1]) & index.plots)
    cold = {str(n).lower() for n in frozen}
    if cold:
        hit &= ~np.isin(index.layers, list(cold))
    return int(hit.sum())


def empty_view_share(viewports: Sequence[ViewportInfo]) -> float:
    """The share of a sheet's viewport area, on paper, framing nothing drawn."""
    def area(v: ViewportInfo) -> float:
        return abs((v.rect[2] - v.rect[0]) * (v.rect[3] - v.rect[1]))
    counted = [v for v in viewports if v.drawn is not None]
    total = sum(area(v) for v in counted)
    if not total:
        return 0.0
    return sum(area(v) for v in counted if v.drawn == 0) / total


def _drawn_viewports(layout) -> list:
    """The viewports ezdxf draws through, chosen as its `_draw_viewports` does:
    status above 0, in status order, less the first if its status is 1 (the
    layout's own). A scale reported for any other would describe a view that
    is not on the page."""
    vps = sorted((v for v in layout.query("VIEWPORT") if v.dxf.get("status", 0) > 0),
                 key=lambda v: v.dxf.get("status", 0))
    if vps and vps[0].dxf.get("status", 1) == 1:
        vps.pop(0)
    return vps


def render_sheet(doc: Drawing, spec: SheetSpec, cache: Optional[ezbbox.Cache],
                 target: PlotTarget, names: Optional[Dict[str, str]] = None) -> RenderedSheet:
    """Plot one sheet as the next page of `target`."""
    t0 = time.monotonic()
    names = names if names is not None else _layer_names(doc)
    layout = doc.modelspace() if spec.model else doc.paperspace(spec.layout)
    backend = _Backend(target.doc, target.ocgs, names)
    pipeline = CapturePipeline(backend)
    ctx = RenderContext(doc, export_mode=True)
    fe = PlotFrontend(ctx, pipeline, Configuration(), cache)
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
        for vp in _drawn_viewports(layout):
            if _is_paper_viewport(vp):
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


#: ezdxf sets a stacked fraction (`\S3/4;`) as two cells centred one over the
#: other in a box `HEIGHT_SCALE` × their summed heights tall
#: (`ezdxf.tools.text_layout.Fraction`), so the numerator's baseline sits
#: `(HEIGHT_SCALE − 1)·h_top + HEIGHT_SCALE·h_bottom` above the denominator's —
#: 1.4 × cap for halves at full height, measured as 12.6 pt over 9 pt text.
#: That rise is what tells a fraction from two lines of a paragraph (1.667 ×
#: cap at single spacing): read from the renderer, not assumed.
_STACK = float(getattr(_EzFraction, "HEIGHT_SCALE", 1.2))

#: A run that opens with one of these follows the text before it without a
#: space when it starts within a third of a cap height: the inch mark after a
#: fraction (`3/4"`), a closing bracket. A word space is wider than that in
#: every font measured (0.6–0.7 × cap between words of complex MTEXT).
_CLOSERS = "\"'”’)]},.;:%"


def _frame(angle: float):
    """(along, up) unit vectors of a baseline at `angle` on the page (y down)."""
    return (math.cos(angle), math.sin(angle)), (math.sin(angle), -math.cos(angle))


def _dot(p, q, v) -> float:
    return (q[0] - p[0]) * v[0] + (q[1] - p[1]) * v[1]


def _fraction(a, b) -> Optional[Tuple[tuple, tuple]]:
    """(top, bottom) when two consecutive placed runs are the halves of one
    stacked fraction, as ezdxf lays it out; None otherwise."""
    (ra, oa, ea, ca), (rb, ob, eb, cb) = a, b
    if ra.handle != rb.handle or ra.viewport != rb.viewport:
        return None
    ta, tb = ra.text.strip(), rb.text.strip()
    if not (0 < len(ta) <= 8 and 0 < len(tb) <= 8) or " " in ta + tb:
        return None
    ang_a = math.atan2(ea[1] - oa[1], ea[0] - oa[0])
    ang_b = math.atan2(eb[1] - ob[1], eb[0] - ob[0])
    if abs((ang_a - ang_b + math.pi) % (2 * math.pi) - math.pi) > math.radians(2):
        return None
    u, up = _frame(ang_a)
    h = max(ca, cb)
    mid_a = ((oa[0] + ea[0]) / 2, (oa[1] + ea[1]) / 2)
    mid_b = ((ob[0] + eb[0]) / 2, (ob[1] + eb[1]) / 2)
    if abs(_dot(mid_a, mid_b, u)) > 0.25 * h:            # centred over one another
        return None
    rise = _dot(oa, ob, up)                               # b's baseline above a's
    top, bottom = (b, a) if rise > 0 else (a, b)
    expected = (_STACK - 1.0) * top[3] + _STACK * bottom[3]
    if abs(abs(rise) - expected) > 0.15 * h:
        return None
    return top, bottom


def _fraction_text(top: str, bottom: str) -> str:
    """`3/4`; a tolerance stack (`+0.5` over `-0.2`) is not a fraction, and
    is written as the two values it is."""
    if top.isdigit() and bottom.isdigit():
        return f"{top}/{bottom}"
    return f"{top} {bottom}"


def cells(runs: Sequence[TextRun], to_page: Affine) -> List[Cell]:
    """Join runs into the cells they were drawn as. See module docstring."""
    placed = []
    for r in runs:
        o, e, cap = _page_run(r, to_page)
        if cap < 0.05 or math.hypot(e[0] - o[0], e[1] - o[1]) < 0.05:
            continue
        placed.append((r, o, e, cap))

    out: List[Cell] = []
    cur: Optional[Cell] = None

    def start(r, o, e, cap, text=None, extra=()):
        nonlocal cur
        if cur is not None:
            out.append(cur)
        cur = Cell(text=(text if text is not None else r.text.strip()), origin=o, end=e,
                   cap=cap, runs=[r, *extra])

    def relation(r, o, e, cap):
        """(same entity and baseline direction, along, across) of a run to `cur`."""
        same_entity = ((r.source or r.handle) == (cur.runs[-1].source or cur.runs[-1].handle)
                       and r.viewport == cur.runs[-1].viewport)
        ang = math.atan2(e[1] - o[1], e[0] - o[0])
        dang = abs((ang - cur.angle + math.pi) % (2 * math.pi) - math.pi)
        u, up = _frame(cur.angle)
        # this run's origin along and across the current cell's baseline, from its end
        return same_entity and dang < math.radians(2), _dot(cur.end, o, u), _dot(cur.end, o, up)

    i = 0
    while i < len(placed):
        r, o, e, cap = placed[i]
        stack = _fraction(placed[i], placed[i + 1]) if i + 1 < len(placed) else None
        if stack is not None:
            (rt, ot, et, ct), (rb, ob, eb, cb) = stack
            text = _fraction_text(rt.text.strip(), rb.text.strip())
            joined = False
            if cur is not None:
                aligned, along, across = relation(rb, ob, eb, cb)
                u, _ = _frame(cur.angle)
                left = min(_dot(cur.end, ot, u), along)
                if (aligned and abs(across) <= 0.3 * cur.cap
                        and -0.3 * cur.cap <= left <= 1.2 * cur.cap):
                    reach = max(_dot(cur.origin, cur.end, u), _dot(cur.origin, et, u),
                                _dot(cur.origin, eb, u))
                    cur.text = f"{cur.text} {text}"
                    cur.end = (cur.origin[0] + u[0] * reach, cur.origin[1] + u[1] * reach)
                    cur.runs.extend([rt, rb])
                    joined = True
            if not joined:
                # a fraction that opens a line: a cell of its own, on the
                # denominator's baseline, spanning both halves
                u, _ = _frame(math.atan2(eb[1] - ob[1], eb[0] - ob[0]))
                lo = min(0.0, _dot(ob, ot, u))
                hi = max(_dot(ob, eb, u), _dot(ob, et, u))
                start(rb, (ob[0] + u[0] * lo, ob[1] + u[1] * lo),
                      (ob[0] + u[0] * hi, ob[1] + u[1] * hi), cb, text=text, extra=(rt,))
            i += 2
            continue
        i += 1
        if cur is None:
            start(r, o, e, cap)
            continue
        aligned, along, across = relation(r, o, e, cap)
        if (aligned and abs(across) <= 0.25 * cur.cap
                and -0.3 * cur.cap <= along <= 1.1 * cur.cap and 0.6 <= cap / cur.cap <= 1.6):
            word = r.text.strip()
            closer = along < 0.35 * cur.cap and word[:1] in _CLOSERS
            if r.kind == "MTEXT":
                # ezdxf draws complex MTEXT one word per call, splitting at the
                # spaces, so a new run of the same MTEXT *is* a new word — however
                # close the substitute font set the inks. Measured on the
                # reference drawing: word gaps of -0.014 to 0.08 x cap in its
                # italic notes ('CLIENT APPROVAL:', 'PRODUCE ALL'), which the gap
                # rule below fused. Only closing punctuation drawn as its own
                # cell (the inch mark after a stacked fraction) closes up.
                tight = closer
            else:
                tight = along < 0.12 * cur.cap or closer
            cur.text = f"{cur.text}{'' if tight else ' '}{word}"
            cur.end = e
            cur.runs.append(r)
            continue
        start(r, o, e, cap)
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
    #: The share of the sheet's viewport area framing nothing drawn
    #: (`empty_view_share`). Above a half, the sheet showed its title block
    #: and little else, and says so.
    empty_view_share: float = 0.0

    def to_dict(self) -> dict:
        return {"page": self.page, "drawing": self.drawing, "layout": self.layout,
                "model": self.model, "paper_mm": [round(v, 2) for v in self.paper_mm],
                "window": [round(v, 4) for v in self.window], "to_page": list(self.to_page),
                "viewports": [v.to_dict() for v in self.viewports], "cells": self.cells,
                "runs": self.runs, "seconds": round(self.seconds, 2), "note": self.note,
                "number": self.number, "number_source": self.number_source,
                "title": self.title, "text": self.text,
                "empty_view_share": round(self.empty_view_share, 3)}
