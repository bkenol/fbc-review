"""A DXF opened, repaired where the converter is known to damage it, and sorted
into the sheets a plotted set would have.

Sheets are paper-space layouts — the tabs along the bottom of AutoCAD — in tab
order. A layout is a sheet when anything is drawn on it; the default `Layout1`
a drafter never touched is not. A drawing with no such layout is plotted from
model space, and becomes one sheet holding everything.

Two repairs, both for faults measured on a real conversion rather than guessed:

* **Viewport status.** LibreDWG writes `status 0` and `id 0` on every VIEWPORT.
  ezdxf (correctly, by the DXF reference) skips a viewport whose status is below
  1, so every sheet rendered as a title block over an empty frame. Whether a
  viewport is switched off is a separate, reliable flag (`0x20000` in group 90),
  so status is rebuilt from that: the layout's own paper viewport first, then
  every viewport that is not switched off. A file whose statuses are already
  set (AutoCAD's own DXF export) is left alone.
* **External references.** An xref is recorded with the path it had on the
  drafter's machine. It resolves here by file name among the drawings uploaded
  with it; one that was not uploaded is reported by name, and the sheets say
  their content is missing rather than pretending the frame was empty.
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

import ezdxf
from ezdxf import recover
from ezdxf.document import Drawing
from ezdxf.layouts import Layout, Paperspace
from ezdxf.math import Vec3

log = logging.getLogger("fbc.cad")

#: VIEWPORT group-90 bit: "Turns the viewport off" (DXF reference, VIEWPORT).
VSF_OFF = 0x20000

#: $INSUNITS -> inches per drawing unit. 0 is "unitless"; see `drawing_units`.
_INCHES_PER_UNIT = {
    1: 1.0, 2: 12.0, 3: 63360.0, 4: 1 / 25.4, 5: 1 / 2.54, 6: 1000 / 25.4,
    7: 1_000_000 / 25.4, 8: 1e-6, 9: 1e-3, 10: 36.0, 14: 100 / 25.4,
    15: 10_000 / 25.4, 16: 100_000 / 25.4, 21: 12.0 * 1200 / 3937, 22: 1200 / 3937,
}
_UNIT_NAMES = {1: "inches", 2: "feet", 4: "millimetres", 5: "centimetres", 6: "metres",
               10: "yards", 21: "US survey feet", 22: "US survey inches"}


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
                "dxfversion": self.doc.dxfversion, "release": self.doc.acad_release,
                "audit_fixes": self.audit_fixes, "audit_errors": self.audit_errors,
                "units": self.units.to_dict(), "viewports_repaired": self.viewports_repaired,
                "xrefs": dict(self.xrefs), "warnings": list(self.warnings)}


def open_dxf(path: str, name: str = "") -> Opened:
    """Read a DXF in recover mode and audit it. Raises ReadError."""
    t0 = time.monotonic()
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
        if paper is None and vps:
            # AutoCAD creates the layout's own viewport first.
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


def _content(layout: Layout) -> int:
    """Entities drawn on a layout, not counting viewports that show nothing."""
    n = 0
    for e in layout:
        if e.dxftype() == "VIEWPORT":
            if _is_paper_viewport(e):
                continue
        n += 1
    return n


def _window(layout: Paperspace) -> Tuple[float, float, float, float]:
    """The part of a layout the plot shows, in layout coordinates.

    The layout's own plot settings say what it plots (DXF `plot_type`): a window
    (4), its limits (2, 5), or its extents (1). A window or limits that is
    degenerate falls through to the next, and the last resort is what is drawn.
    """
    d = layout.dxf_layout.dxf

    def ok(x0, y0, x1, y1):
        return abs(x1 - x0) > 1e-6 and abs(y1 - y0) > 1e-6

    plot_type = int(d.get("plot_type", 5) or 5)
    tries = []
    if plot_type == 4:
        tries.append((d.get("plot_window_x1", 0.0), d.get("plot_window_y1", 0.0),
                      d.get("plot_window_x2", 0.0), d.get("plot_window_y2", 0.0)))
    lmin, lmax = d.get("limmin"), d.get("limmax")
    if lmin is not None and lmax is not None:
        tries.append((lmin[0], lmin[1], lmax[0], lmax[1]))
    emin, emax = d.get("extmin"), d.get("extmax")
    if emin is not None and emax is not None and plot_type == 1:
        tries.insert(0, (emin[0], emin[1], emax[0], emax[1]))
    for x0, y0, x1, y1 in tries:
        if ok(x0, y0, x1, y1):
            return (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
    from ezdxf import bbox
    box = bbox.extents(layout, fast=True)
    if box.has_data:
        return (box.extmin.x, box.extmin.y, box.extmax.x, box.extmax.y)
    return (0.0, 0.0, 36.0, 24.0)


#: ARCH D, landscape — the sheet a model-space-only drawing is fitted to.
ARCH_D_MM = (914.4, 609.6)


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
            # No page set up: plot the window at one paper unit to the inch,
            # which is what an unconfigured layout in inches would plot as.
            w, h = (window[2] - window[0]) * 25.4, (window[3] - window[1]) * 25.4
            note = "the layout has no page size set up; plotted at its own size"
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


def embed_xrefs(opened: Opened, resolve: Loader) -> None:
    """Bring each external reference's model space into the drawing.

    `resolve(file_name)` returns the referenced drawing, already converted and
    read, or None when it was not uploaded. Every xref's outcome is recorded on
    `opened.xrefs`; an xref left unresolved also becomes a warning that names it,
    because the sheets that show it are missing content.
    """
    from ezdxf import xref as xr
    doc = opened.doc
    for block in list(doc.blocks):
        rec = block.block_record
        if not getattr(rec, "is_xref", False):
            continue
        path = str(block.block.dxf.get("xref_path", "") or "")
        fname = os.path.basename(path.replace("\\", "/"))
        if not fname:
            opened.xrefs[block.name] = "no path recorded"
            continue
        if opened.xrefs.get(block.name) in ("not uploaded", "embedded"):
            continue          # settled in an earlier round
        loaded = resolve(fname)
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
            loader = xr.Loader(loaded, doc, conflict_policy=xr.ConflictPolicy.XREF_PREFIX)
            loader.load_modelspace(block)
            loader.execute(xref_prefix=block.name)
            block.block.set_flag_state(ezdxf.const.BLK_XREF | ezdxf.const.BLK_EXTERNAL,
                                       state=False)
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
