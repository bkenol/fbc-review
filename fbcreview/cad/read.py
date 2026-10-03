"""A DXF opened, repaired where the converter is known to damage it, and sorted
into the sheets a plotted set would have.

Sheets are paper-space layouts — the tabs along the bottom of AutoCAD — in tab
order. A layout is a sheet when anything is drawn on it; the default `Layout1`
a drafter never touched is not. A drawing with no such layout is plotted from
model space, and becomes one sheet holding everything.

Two repairs, both for faults measured on a real conversion rather than guessed:

* **Viewport status.** LibreDWG writes `status 0` and `id 0` on the VIEWPORTs
  it converts — 65 of the 71 on the reference drawing, every one on most
  layouts. ezdxf (correctly, by the DXF reference) skips a viewport whose
  status is below 1, so those sheets rendered as a title block over an empty
  frame. Whether a viewport is switched off is a separate, reliable flag
  (`0x20000` in group 90), so status is rebuilt from that, layout by layout:
  the layout's own paper viewport first, then every viewport that is not
  switched off. A layout with any status already set (AutoCAD's own DXF
  export, or the few LibreDWG got right) is left alone.
* **External references.** An xref is recorded with the path it had on the
  drafter's machine. It resolves here by file name among the drawings uploaded
  with it; one that was not uploaded is reported by name, and the sheets say
  their content is missing rather than pretending the frame was empty.
"""
from __future__ import annotations

import logging
import math
import os
import re
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

import ezdxf
from ezdxf import recover
from ezdxf.document import Drawing
from ezdxf.layouts import Layout, Paperspace
from ezdxf.math import Vec3

from .source import DWG_VERSIONS

log = logging.getLogger("fbc.cad")

#: VIEWPORT group-90 bit: "Turns the viewport off" (DXF reference, VIEWPORT).
VSF_OFF = 0x20000

#: $INSUNITS -> inches per drawing unit. 0 is "unitless"; see `drawing_units`.
_INCHES_PER_UNIT = {
    1: 1.0, 2: 12.0, 3: 63360.0, 4: 1 / 25.4, 5: 1 / 2.54, 6: 1000 / 25.4,
    7: 1_000_000 / 25.4, 8: 1e-6, 9: 1e-3, 10: 36.0, 14: 100 / 25.4,
    15: 10_000 / 25.4, 16: 100_000 / 25.4,
    # US survey units: the survey foot is 1200/3937 m exactly (NIST), not the
    # international foot's 0.3048 m. The table once held metres per survey foot
    # as if it were inches, 3.28x short of the truth.
    21: 1200 / 3937 / 0.0254, 22: 100 / 3937 / 0.0254, 23: 3600 / 3937 / 0.0254,
    24: 6_336_000 / 3937 / 0.0254,
}
_UNIT_NAMES = {1: "inches", 2: "feet", 4: "millimetres", 5: "centimetres", 6: "metres",
               10: "yards", 21: "US survey feet", 22: "US survey inches",
               23: "US survey yards", 24: "US survey miles"}


class ReadError(Exception):
    """A DXF ezdxf could not read even in recover mode."""


@dataclass
class Units:
    """What one model-space unit is, and how we know."""
    inches: Optional[float]          # inches per drawing unit; None when unknown
    name: str
    basis: str                       # "stated" ($INSUNITS) | "inferred" | "unknown"
    note: str = ""

    @property
    def known(self) -> bool:
        return self.inches is not None

    def to_dict(self) -> dict:
        return {"inches_per_unit": self.inches, "name": self.name, "basis": self.basis,
                "note": self.note}


def drawing_units(doc: Drawing) -> Units:
    """The model-space unit, from `$INSUNITS`, or inferred where AutoCAD itself does.

    `$INSUNITS` 0 means "unitless". Architectural and engineering *display* units
    (`$LUNITS` 4 and 3) are feet-and-inches over a drawing unit of one inch —
    that is how AutoCAD defines them, so an inch is an inference with a basis,
    and it is labelled as one. Anything else unitless stays unknown, and a
    measurement that needs it abstains.
    """
    code = int(doc.header.get("$INSUNITS", 0) or 0)
    if code in _INCHES_PER_UNIT:
        return Units(_INCHES_PER_UNIT[code], _UNIT_NAMES.get(code, f"units code {code}"),
                     "stated", "$INSUNITS")
    lunits = int(doc.header.get("$LUNITS", 2) or 2)
    if lunits in (3, 4):
        return Units(1.0, "inches", "inferred",
                     "the drawing declares no insertion units; its "
                     f"{'architectural' if lunits == 4 else 'engineering'} display units "
                     "measure in inches")
    return Units(None, "unknown", "unknown", "the drawing declares no insertion units")


@dataclass
class SheetSpec:
    """One sheet to plot: a layout and the window of it the page shows."""
    layout: str                                  # layout name ("Model" for model space)
    taborder: int
    window: Tuple[float, float, float, float]    # layout coordinates, x0 y0 x1 y1
    paper_mm: Tuple[float, float]                # page width, height in millimetres
    rotation: int = 0                            # plot rotation, degrees
    model: bool = False
    note: str = ""


@dataclass
class Opened:
    """A drawing read and repaired, with what was done to it."""
    doc: Drawing
    name: str                                    # member name, or the upload's
    seconds: float
    audit_fixes: int
    audit_errors: int
    units: Units
    viewports_repaired: int = 0
    xrefs: Dict[str, str] = field(default_factory=dict)   # block -> "embedded" | why not
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"name": self.name, "seconds": round(self.seconds, 2),
                "dxfversion": self.doc.dxfversion,
                # the name AutoCAD sells the format as ("AutoCAD 2018"), as the
                # DWG header check gives it, not ezdxf's "R2018"
                "release": DWG_VERSIONS.get(self.doc.dxfversion, self.doc.acad_release),
                "audit_fixes": self.audit_fixes, "audit_errors": self.audit_errors,
                "units": self.units.to_dict(), "viewports_repaired": self.viewports_repaired,
                "xrefs": dict(self.xrefs), "warnings": list(self.warnings)}


#: How every DXF ends: group code 0 and `EOF` (ASCII), or `EOF` and a NUL
#: (binary). ezdxf's recover mode reads a file cut short without complaint —
#: measured: 90% of a sheet set read back with no layouts, and was reviewed as
#: one model-space drawing — so the end is checked before it reads.
_ASCII_EOF = re.compile(rb"(?:^|\n)[ \t]*0[ \t]*\r?\nEOF[ \t\r\n\x00\x1a]*$")


def _ends_whole(path: str) -> bool:
    with open(path, "rb") as fh:
        fh.seek(max(0, os.path.getsize(path) - 64))
        tail = fh.read()
    return bool(_ASCII_EOF.search(tail)) or tail.rstrip(b"\r\n\x1a").endswith(b"EOF\x00")


def open_dxf(path: str, name: str = "") -> Opened:
    """Read a DXF in recover mode and audit it. Raises ReadError."""
    t0 = time.monotonic()
    try:
        whole = _ends_whole(path)
    except OSError as exc:
        raise ReadError(f"The drawing could not be read ({exc.__class__.__name__}).")
    if not whole:
        raise ReadError("The drawing file is incomplete: it stops before the end marker "
                        "every DXF closes with. It may have been cut short in upload or "
                        "export. Export it again, or upload the DWG, and try again.")
    try:
        doc, auditor = recover.readfile(path)
    except (IOError, OSError) as exc:
        raise ReadError(f"The drawing could not be read ({exc.__class__.__name__}).")
    except ezdxf.DXFStructureError as exc:
        raise ReadError("The drawing's structure is damaged beyond repair "
                        f"({str(exc)[:80]}). Run AUDIT and save it in AutoCAD, then "
                        "upload it again.")
    seconds = time.monotonic() - t0
    opened = Opened(doc=doc, name=name, seconds=seconds, audit_fixes=len(auditor.fixes),
                    audit_errors=len(auditor.errors), units=drawing_units(doc))
    opened.viewports_repaired = repair_viewports(doc)
    log.info("dxf read", extra={"seconds": round(seconds, 2), "fixes": opened.audit_fixes,
                                "errors": opened.audit_errors,
                                "viewports_repaired": opened.viewports_repaired})
    return opened


def _is_paper_viewport(vp) -> bool:
    """The layout's own viewport: scale 1, centred on itself."""
    d = vp.dxf
    try:
        same_scale = abs(d.view_height - d.height) <= 1e-6 * max(abs(d.height), 1.0)
        centred = (d.view_center_point - d.center).magnitude <= 1e-6 * max(d.height, 1.0)
    except AttributeError:
        return False
    return same_scale and centred


def repair_viewports(doc: Drawing) -> int:
    """Rebuild VIEWPORT status where a converter left every one at 0.

    Returns how many viewports were changed. See the module docstring.
    """
    changed = 0
    for layout in doc.layouts:
        if layout.is_modelspace:
            continue
        vps = sorted(layout.query("VIEWPORT"), key=lambda v: int(v.dxf.handle, 16))
        if not vps or any(v.dxf.get("status", 0) > 0 for v in vps):
            continue
        paper = next((v for v in vps if _is_paper_viewport(v)), None)
        if paper is None and len(vps) > 1 and _contains_all(vps[0], vps[1:]):
            # AutoCAD creates the layout's own viewport first, and it spans the
            # sheet. A first viewport that does not hold the others is a view
            # like any other — on a layout with one viewport, the plan itself:
            # made the paper viewport, it went undrawn (measured).
            paper = vps[0]
        k = 2
        for v in vps:
            if v is paper:
                v.dxf.status = 1
            elif int(v.dxf.get("flags", 0)) & VSF_OFF:
                v.dxf.status = 0
                continue
            else:
                v.dxf.status = k
                k += 1
            changed += 1
    return changed


def _contains_all(outer, others) -> bool:
    """Whether `outer`'s paper rectangle holds every one of `others`."""
    def rect(v):
        c, w, h = v.dxf.center, v.dxf.width / 2, v.dxf.height / 2
        return c.x - w, c.y - h, c.x + w, c.y + h
    try:
        ox0, oy0, ox1, oy1 = rect(outer)
        tol = 1e-6 * max(abs(ox1 - ox0), abs(oy1 - oy0), 1.0)
        return all(ox0 - tol <= x0 and oy0 - tol <= y0 and x1 <= ox1 + tol and y1 <= oy1 + tol
                   for x0, y0, x1, y1 in (rect(v) for v in others))
    except AttributeError:
        return False


def _content(layout: Layout) -> int:
    """Entities drawn on a layout, not counting viewports that show nothing."""
    n = 0
    for e in layout:
        if e.dxftype() == "VIEWPORT":
            if _is_paper_viewport(e):
                continue
        n += 1
    return n


#: A coordinate this far out is a sentinel, not a drawing: AutoCAD and ezdxf
#: initialise a layout's stored extents to +1e20 / -1e20 until something is
#: drawn and the extents are regenerated.
_FAR = 1e15


def _box(x0, y0, x1, y1, ordered: bool = False) -> Optional[Tuple[float, float, float, float]]:
    """The box as (x0, y0, x1, y1), or None when it cannot be a plot window."""
    try:
        vals = [float(v) for v in (x0, y0, x1, y1)]
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(v) and abs(v) < _FAR for v in vals):
        return None
    x0, y0, x1, y1 = vals
    if ordered and (x1 <= x0 or y1 <= y0):
        return None
    if abs(x1 - x0) <= 1e-6 or abs(y1 - y0) <= 1e-6:
        return None
    return (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))


def _drawn(layout: Layout) -> Optional[Tuple[float, float, float, float]]:
    """What is drawn on a layout. The layout's own viewport is not drawn — it
    is the paper's window onto itself, and on a fresh layout it is larger than
    the paper — so, as in AutoCAD's extents, it is left out."""
    from ezdxf import bbox
    box = bbox.extents((e for e in layout
                        if not (e.dxftype() == "VIEWPORT" and _is_paper_viewport(e))),
                       fast=True)
    if not box.has_data:
        return None
    return _box(box.extmin.x, box.extmin.y, box.extmax.x, box.extmax.y)


def _window(layout: Paperspace) -> Tuple[float, float, float, float]:
    """The part of a layout the plot shows, in layout coordinates.

    The layout's own plot settings say what it plots (DXF `plot_type`):

    | plot_type | plots                     | tried, in order                      |
    | --------- | ------------------------- | ------------------------------------ |
    | 1         | the drawing's extents     | what is drawn, stored extents, limits |
    | 2, 5      | limits / the layout paper | limits, what is drawn                |
    | 4         | a window                  | the window, limits, what is drawn    |
    | 0, 3      | a display or named view   | limits, what is drawn (neither the    |
    |           |                           | display nor the view is in the file) |

    A candidate that is degenerate, inverted or a sentinel (stored extents of a
    layout never regenerated are ±1e20 — measured: a plot_type 1 layout came
    out as a window 2e20 wide) falls through to the next.
    """
    d = layout.dxf_layout.dxf
    plot_type = d.get("plot_type")
    plot_type = 5 if plot_type is None else int(plot_type)
    limits = None
    lmin, lmax = d.get("limmin"), d.get("limmax")
    if lmin is not None and lmax is not None:
        limits = _box(lmin[0], lmin[1], lmax[0], lmax[1])
    tries: List = []
    if plot_type == 1:
        emin, emax = d.get("extmin"), d.get("extmax")
        stored = (_box(emin[0], emin[1], emax[0], emax[1], ordered=True)
                  if emin is not None and emax is not None else None)
        tries = [lambda: _drawn(layout), lambda: stored, lambda: limits]
    elif plot_type == 4:
        window = _box(d.get("plot_window_x1", 0.0), d.get("plot_window_y1", 0.0),
                      d.get("plot_window_x2", 0.0), d.get("plot_window_y2", 0.0))
        tries = [lambda: window, lambda: limits, lambda: _drawn(layout)]
    else:
        tries = [lambda: limits, lambda: _drawn(layout)]
    for attempt in tries:
        box = attempt()
        if box is not None:
            return box
    return (0.0, 0.0, 36.0, 24.0)


#: ARCH D, landscape — the sheet a model-space-only drawing is fitted to.
ARCH_D_MM = (914.4, 609.6)


#: How many entities one drawing may expand to through its blocks before it is
#: refused rather than plotted (`FBC_CAD_MAX_ENTITIES`). Measured: the
#: reference drawing expands to 304 147 (296 000 at top level) and plots in
#: about three minutes; a 20 KB drawing of blocks nested six deep, ten to a
#: level, is a million lines, and ran past the 600 s timeout at 634 MB and
#: climbing. 2 000 000 is more than six times the reference.
DEFAULT_MAX_ENTITIES = 2_000_000


def max_entities() -> int:
    try:
        return int(float(os.environ.get("FBC_CAD_MAX_ENTITIES", "") or DEFAULT_MAX_ENTITIES))
    except ValueError:
        return DEFAULT_MAX_ENTITIES


def expanded_count(doc: Drawing, limit: Optional[int] = None) -> int:
    """How many entities the drawing draws once every block is expanded:
    each INSERT is its block's contents (times its rows and columns, for an
    array) plus its attributes. Counted from each block once, never drawn —
    0.09 s on the reference drawing. Stops counting past `limit`; a block that
    contains itself counts as past it."""
    cap = (limit if limit is not None else max_entities()) + 1
    memo: Dict[str, int] = {}
    busy: set = set()

    def block(name: str) -> int:
        if name in memo:
            return memo[name]
        if name in busy:
            return cap
        blk = doc.blocks.get(name)
        if blk is None:
            return 0
        busy.add(name)
        n = 0
        for e in blk:
            n = min(cap, n + entity(e))
            if n >= cap:
                break
        busy.discard(name)
        memo[name] = n
        return n

    def entity(e) -> int:
        if e.dxftype() != "INSERT":
            return 1
        copies = max(1, int(e.dxf.get("row_count", 1) or 1)) * \
            max(1, int(e.dxf.get("column_count", 1) or 1))
        return min(cap, copies * (1 + block(e.dxf.name) + len(e.attribs)))

    total = 0
    for layout in doc.layouts:
        for e in layout:
            total = min(cap, total + entity(e))
            if total >= cap:
                return total
    return total


def sheet_count(doc: Drawing) -> int:
    """How many sheets `sheets()` would plot — a page per paper-space layout
    with something on it, or one for model space when there is none — counted
    without working out a window or drawing anything."""
    n = sum(1 for layout in doc.layouts
            if not layout.is_modelspace and _content(layout) > 0)
    if n:
        return n
    return 1 if len(doc.modelspace()) else 0


def has_sheet_layouts(doc: Drawing) -> bool:
    """Whether any paper-space layout has something on it — `sheets()` would
    plot a layout — without working out a single window."""
    return any(not layout.is_modelspace and _content(layout) > 0 for layout in doc.layouts)


def sheets(doc: Drawing) -> List[SheetSpec]:
    """The sheets a drawing plots, in tab order."""
    out: List[SheetSpec] = []
    for layout in doc.layouts:
        if layout.is_modelspace:
            continue
        if _content(layout) == 0:
            continue
        d = layout.dxf_layout.dxf
        w, h = float(d.get("paper_width", 0) or 0), float(d.get("paper_height", 0) or 0)
        rotation = int(d.get("plot_rotation", 0) or 0) * 90
        window = _window(layout)
        note = ""
        if w <= 0 or h <= 0:
            # No page set up, so its limits are a default too (ezdxf's are an
            # A3 sheet in millimetres, 420 x 297, which plotted one unit to the
            # inch made a page ten metres wide — measured). What is drawn is
            # fitted to ARCH D, as model space is; viewport scales stay exact,
            # because they are read through the same placement.
            window = _drawn(layout) or window
            w, h = ARCH_D_MM
            if (window[3] - window[1]) > (window[2] - window[0]):
                w, h = h, w
            note = ("the layout has no page size set up; what is drawn on it was fitted "
                    "to an ARCH D sheet")
        if rotation in (90, 270):
            w, h = h, w
        out.append(SheetSpec(layout=layout.name, taborder=int(d.get("taborder", 0) or 0),
                             window=window, paper_mm=(w, h), rotation=rotation, note=note))
    out.sort(key=lambda s: (s.taborder, s.layout))
    if out:
        return out
    from ezdxf import bbox
    box = bbox.extents(doc.modelspace(), fast=True)
    if not box.has_data:
        return []
    window = (box.extmin.x, box.extmin.y, box.extmax.x, box.extmax.y)
    w, h = ARCH_D_MM
    if (window[3] - window[1]) > (window[2] - window[0]):
        w, h = h, w
    return [SheetSpec(layout="Model", taborder=0, window=window, paper_mm=(w, h), model=True,
                      note="the drawing has no sheet layouts; model space was fitted to an "
                           "ARCH D sheet")]


Loader = Callable[[str], Optional[Drawing]]


#: What an xref that leads back to a drawing already on its chain is recorded
#: as. AutoCAD draws a circular reference once and stops; so does this.
CIRCULAR = "circular reference (drawn once, not again)"


def embed_xrefs(opened: Opened, resolve: Loader,
                unreadable: Optional[Dict[str, str]] = None, *,
                member_of: Optional[Callable[[str], Optional[str]]] = None,
                chain: Optional[Dict[str, frozenset]] = None) -> None:
    """Bring each external reference's model space into the drawing.

    `resolve(file_name)` returns the referenced drawing, already converted and
    read, or None when it was not uploaded. `unreadable` maps the lower-case
    file names (and stems) of drawings that were uploaded but could not be
    read to why — so an xref among them is reported as unreadable, not as
    missing from the upload. Every xref's outcome is recorded on
    `opened.xrefs`; an xref left unresolved also becomes a warning that names
    it, because the sheets that show it are missing content.

    Called again, it settles the xrefs the last call brought in nested. Two
    things keep that faithful to what AutoCAD shows:

    * **Overlays do not nest.** An xref a referenced drawing *overlays* is
      left out when that drawing is copied in; only attached xrefs come
      along. A drawing's own overlays are embedded as before.
    * **A chain never loops.** With `member_of(file_name)` naming the uploaded
      drawing a file resolves to, and `chain` (kept up to date here) naming
      the drawings each nested xref block came through, an xref that leads
      back to the host or to a drawing already on its chain is recorded as
      `CIRCULAR` and not drawn again — never as missing from the upload.
    """
    from ezdxf import xref as xr
    doc = opened.doc
    chain = chain if chain is not None else {}
    for block in list(doc.blocks):
        rec = block.block_record
        if not getattr(rec, "is_xref", False):
            continue
        path = str(block.block.dxf.get("xref_path", "") or "")
        fname = os.path.basename(path.replace("\\", "/"))
        if not fname:
            opened.xrefs[block.name] = "no path recorded"
            continue
        if block.name in opened.xrefs:
            continue          # settled in an earlier round, whatever the outcome
        came_through = chain.setdefault(block.name, frozenset({opened.name}))
        member = member_of(fname) if member_of is not None else None
        if member is not None and member in came_through:
            opened.xrefs[block.name] = CIRCULAR
            continue
        loaded = resolve(fname)
        why = (unreadable or {}).get(fname.lower()) or \
            (unreadable or {}).get(os.path.splitext(fname.lower())[0])
        if loaded is None and why:
            opened.xrefs[block.name] = "unreadable"
            opened.warnings.append(
                f"External reference {fname} was uploaded but could not be read ({why}); "
                "whatever it draws is missing from the sheets that show it.")
            continue
        if loaded is None:
            opened.xrefs[block.name] = "not uploaded"
            opened.warnings.append(
                f"External reference {fname} was not included in the upload; whatever it "
                "draws is missing from the sheets that show it. Upload the drawings as one "
                "zip with their xrefs to review that content.")
            continue
        try:
            # `xref.embed()` would first look for the file at its recorded path,
            # which is the drafter's — a Windows path that never exists here.
            # This is the rest of what it does, given the drawing we resolved.
            if loaded.dxfversion > doc.dxfversion:
                raise ezdxf.DXFVersionError("xref saved in a newer format than its host")
            # What the referenced drawing overlays stays out: AutoCAD does not
            # carry an overlay into a drawing that references the one
            # overlaying it. Read off `loaded` as uploaded — the bit is
            # cleared only on a drawing's own embedded overlays.
            nested_overlays = {
                b.name for b in loaded.blocks
                if getattr(b.block_record, "is_xref", False)
                and b.block.dxf.get("flags", 0) & ezdxf.const.BLK_XREF_OVERLAY}
            before = {b.name for b in doc.blocks}
            loader = xr.Loader(loaded, doc, conflict_policy=xr.ConflictPolicy.XREF_PREFIX)
            loader.load_modelspace(block, filter_fn=lambda e: not (
                e.dxftype() == "INSERT" and e.dxf.name in nested_overlays))
            loader.execute(xref_prefix=block.name)
            if member is not None:
                for b in doc.blocks:
                    if b.name not in before and getattr(b.block_record, "is_xref", False):
                        chain[b.name] = came_through | {member}
            # ezdxf's own `embed()` clears XREF and EXTERNAL but leaves the
            # OVERLAY bit, so an embedded overlay (the reference drawing's
            # title block is one: flags 12) would still report itself as an
            # unresolved xref. Embedded is embedded.
            block.block.set_flag_state(ezdxf.const.BLK_XREF | ezdxf.const.BLK_XREF_OVERLAY
                                       | ezdxf.const.BLK_EXTERNAL, state=False)
            origin = loaded.header.get("$INSBASE")
            if origin:
                block.block.dxf.base_point = Vec3(origin)
            opened.xrefs[block.name] = "embedded"
        except Exception as exc:     # ezdxf raises several unrelated types here
            opened.xrefs[block.name] = f"could not be embedded ({exc.__class__.__name__})"
            opened.warnings.append(
                f"External reference {fname} could not be merged into the drawing "
                f"({exc.__class__.__name__}); its content is missing from the sheets.")


def layer_names(doc: Drawing) -> List[str]:
    """The drawing's layer table, original case, sorted."""
    return sorted({layer.dxf.name for layer in doc.layers})
