"""What the drawing knows that its plot does not, recorded where the sheet shows it.

A PDF plot of a sheet is ink. The drawing behind it is data: a title-block
field knows it is the sheet number, a dimension knows the length it measures as
well as the text it prints, a viewport knows its exact scale, a room outline
knows its area. This module records those, per rendered page, as plain records
in the sidecar — never as findings, and never as anything a rule may mistake for
a statement the set makes.

| Record        | From                                  | Becomes, in `pipeline._read_cad`                 |
| ------------- | ------------------------------------- | ------------------------------------------------ |
| `attribute`   | an ATTRIB or ATTDEF shown on the sheet | a claim, if its tag names a catalog fact (stated) |
| `dimension`   | a DIMENSION seen on the sheet          | data: measured length beside the printed text    |
| `area`        | a closed outline on an area/room layer | data: measured area, `basis = measured`          |
| `blocks`      | block references by name               | data: an inventory, per drawing                  |

Only attributes become claims, because only they are *stated*: a drafter typed
the value into a field whose name says what it is. A measured area is the
outline's arithmetic, at the drawing's declared units; it is kept, labelled as
measured, and fed to no rule that takes a stated value — the inference ladder
(`docs/FEATURE-PROMPT-inference-ladder.md`) is where measured values earn a
rule, with a rung of their own.
"""
from __future__ import annotations

import math
import re
import weakref
from collections import Counter
from typing import Dict, Iterable, List, Optional, Tuple

from .render import TextRun, apply, box_through

Box = Tuple[float, float, float, float]

#: Layer names that hold area outlines. US National CAD Standard area layers are
#: `A-AREA`, `A-AREA-GROS`, `A-AREA-OCCP`, `A-AREA-RENT`…; offices that do not
#: follow it name the same thing ROOM, SPACE or AREA. Matched as words.
_AREA_LAYER = re.compile(r"(^|[^A-Z])(AREA|ROOM|ROOMS|SPACE|SPACES|GROSS|OCCP)([^A-Z]|$)", re.I)

#: Anonymous blocks (`*U12` dynamic-block instances, `*D` dimensions, `*X` hatches,
#: `*T` tables) carry no name a person chose; they are counted under the name
#: of the block they represent where ezdxf can tell, otherwise not at all.
_ANON = re.compile(r"^\*[A-Z]")


def _run_box(run: TextRun, to_page) -> Box:
    pts = []
    for x, y in ((run.x0, 0.0), (run.x1, 0.0), (run.x0, run.cap), (run.x1, run.cap)):
        v = run.m.transform((x, y, 0.0))
        pts.append(apply(to_page, v[0], v[1]))
    xs, ys = zip(*pts)
    return (min(xs), min(ys), max(xs), max(ys))


def _union(boxes: Iterable[Box]) -> Optional[Box]:
    boxes = [b for b in boxes if b]
    if not boxes:
        return None
    return (min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes))


def _r(box: Optional[Box]) -> Optional[List[float]]:
    return [round(v, 1) for v in box] if box else None


def attributes(page: int, runs: List[TextRun], to_page) -> List[dict]:
    """Every block attribute on the sheet: its tag, its prompt, what it shows."""
    out: List[dict] = []
    for r in runs:
        if r.kind not in ("ATTRIB", "ATTDEF"):
            continue
        text = r.text.strip()
        if not text:
            continue
        out.append({"type": "attribute", "page": page, "kind": r.kind, "tag": r.tag,
                    "prompt": r.prompt, "text": text, "layer": r.layer, "handle": r.handle,
                    "source": r.source or r.handle,
                    "viewport": r.viewport, "box": _r(_run_box(r, to_page))})
    return out


def _dim_measurement(dim, units_in: Optional[float]) -> Optional[dict]:
    """A linear or aligned DIMENSION's measured length, as the drawing computes it."""
    try:
        dimtype = int(dim.dxf.get("dimtype", 0)) & 0x0F
        if dimtype not in (0, 1):            # rotated/horizontal/vertical, aligned
            return None
        m = dim.get_measurement()
        if not isinstance(m, (int, float)) or not math.isfinite(m) or m <= 0:
            return None
        lfac = 1.0
        try:
            from ezdxf.entities import DimStyleOverride   # noqa: F401
            lfac = float(dim.override().get("dimlfac", 1.0) or 1.0)
        except Exception:
            lfac = 1.0
        # DIMLFAC scales the number a dimension *prints*, not the geometry it
        # spans: a detail drawn at twice size carries DIMLFAC 0.5 so its text
        # reads true. The geometry is what was measured; the printed value is
        # the drafter's — both are kept, neither stands in for the other.
        # (Measured: ezdxf's own EZDXF dimstyle carries DIMLFAC 100, and a
        # 960-inch wall came out as "measured 96000 in" when the factor was
        # multiplied into the measurement.)
        out = {"measured_units": round(m, 4), "dimlfac": lfac,
               "shown_units": round(m * lfac, 4)}
        if units_in is not None:
            out["measured_in"] = round(m * units_in, 3)
        return out
    except Exception:
        return None


def dimensions(page: int, runs: List[TextRun], to_page, doc,
               units_in: Optional[float]) -> List[dict]:
    """Each dimension on the sheet: the text it prints and the length it measures.

    Only model-space dimensions seen through a viewport (or on a model-space
    sheet) are recorded. A paper-space dimension measures across a viewport at
    its scale, and that conversion is a second inference this record does not
    make.
    """
    # one record per dimension per viewport it is seen through
    by_handle: Dict[Tuple[str, str], List[TextRun]] = {}
    for r in runs:
        if r.parent == "DIMENSION" and r.handle:
            by_handle.setdefault((r.handle, r.viewport), []).append(r)
    out: List[dict] = []
    for (handle, viewport), group in by_handle.items():
        try:
            dim = doc.entitydb.get(handle)
        except Exception:
            dim = None
        if dim is None or dim.dxftype() != "DIMENSION":
            continue
        owner_is_model = dim.dxf.owner == doc.modelspace().block_record_handle
        if not owner_is_model:
            continue
        meas = _dim_measurement(dim, units_in)
        if meas is None:
            continue
        printed = " ".join(r.text.strip() for r in group if r.text.strip())
        override = str(dim.dxf.get("text", "") or "")
        rec = {"type": "dimension", "page": page, "handle": handle, "layer": dim.dxf.layer,
               "viewport": viewport, "printed": printed, "override": override,
               "overridden": bool(override) and "<>" not in override,
               "box": _r(_union(_run_box(r, to_page) for r in group))}
        rec.update(meas)
        out.append(rec)
    return out


def _shoelace(pts: List[Tuple[float, float]]) -> float:
    a = 0.0
    n = len(pts)
    for i in range(n):
        x0, y0 = pts[i]
        x1, y1 = pts[(i + 1) % n]
        a += x0 * y1 - x1 * y0
    return abs(a) / 2.0


#: Outlines found per drawing, held only as long as the drawing is. Keyed by
#: the drawing itself, not `id()`: an id is reused once a drawing is freed, and
#: a long-lived process reading drawing after drawing would then be handed the
#: outlines of a different one.
_OUTLINES: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()


def _outlines(doc, units_in: float) -> List[tuple]:
    """Closed straight-sided outlines on area layers: (handle, layer, sf, n, model box).

    Computed once per drawing — model space can hold a quarter of a million
    polylines, and every sheet asks the same question of them.
    """
    held = _OUTLINES.setdefault(doc, {})
    if units_in in held:
        return held[units_in]
    found: List[tuple] = []
    want = {layer.dxf.name.lower() for layer in doc.layers if _AREA_LAYER.search(layer.dxf.name)}
    if want:
        for e in doc.modelspace().query("LWPOLYLINE"):
            if e.dxf.layer.lower() not in want or not e.closed:
                continue
            try:
                pts = [(float(x), float(y), float(b)) for x, y, b in e.get_points("xyb")]
            except Exception:
                continue
            if len(pts) < 3 or any(abs(b) > 1e-9 for *_, b in pts):
                # arcs in the outline: the shoelace of its vertices is not its area
                continue
            xy = [(x, y) for x, y, _ in pts]
            sf = _shoelace(xy) * units_in * units_in / 144.0
            if sf < 1.0:
                continue
            xs, ys = zip(*xy)
            found.append((e.dxf.handle, e.dxf.layer, sf, len(xy),
                          (min(xs), min(ys), max(xs), max(ys))))
    held[units_in] = found
    return found


def areas(page: int, sheet_viewports, doc, units_in: Optional[float], model_sheet_to_page=None,
          limit: int = 400) -> List[dict]:
    """Closed outlines on area layers, measured, where the sheet shows them."""
    if units_in is None:
        return []
    out: List[dict] = []
    for handle, layer, sf, n, mbox in _outlines(doc, units_in):
        cx, cy = (mbox[0] + mbox[2]) / 2, (mbox[1] + mbox[3]) / 2
        where = []
        if model_sheet_to_page is not None:
            where.append(("", box_through(model_sheet_to_page, mbox)))
        for vp in sheet_viewports:
            w = vp.model_window
            if w[0] <= cx <= w[2] and w[1] <= cy <= w[3]:
                where.append((vp.handle, box_through(vp.to_page, mbox)))
        for viewport, box in where:
            out.append({"type": "area", "page": page, "handle": handle, "layer": layer,
                        "viewport": viewport, "area_sf": round(sf, 1), "vertices": n,
                        "box": _r(box), "basis": "measured"})
            if len(out) >= limit:
                return out
    return out


def blocks(doc) -> List[dict]:
    """Block references in model space, by the name a person gave the block."""
    counts: Counter = Counter()
    layers: Dict[str, Counter] = {}
    for ins in doc.modelspace().query("INSERT"):
        name = ins.dxf.name
        if _ANON.match(name):
            try:
                eff = ins.block().block_record.dxf.get("name", name)
            except Exception:
                eff = name
            if _ANON.match(eff):
                continue
            name = eff
        counts[name] += 1
        layers.setdefault(name, Counter())[ins.dxf.layer] += 1
    return [{"type": "blocks", "name": n, "count": c,
             "layers": dict(layers[n].most_common(3))} for n, c in counts.most_common(200)]


def from_sheet(opened, sheet_page, runs: List[TextRun]) -> List[dict]:
    """Everything recorded for one rendered page."""
    page = sheet_page.page
    units_in = opened.units.inches
    doc = opened.doc
    out = attributes(page, runs, sheet_page.to_page)
    if sheet_page.model:
        # A block inserted more than once in model space is a tag — room, door,
        # equipment — and describes what it tags, not the building.
        counts: Dict[str, int] = {}
        for ins in doc.modelspace().query("INSERT"):
            counts[ins.dxf.name] = counts.get(ins.dxf.name, 0) + 1
        for rec in out:
            ins = doc.entitydb.get(rec.get("handle", ""))
            if ins is not None and ins.dxftype() == "INSERT" and counts.get(ins.dxf.name, 0) > 1:
                rec["repeated"] = True
    out.extend(dimensions(page, runs, sheet_page.to_page, doc, units_in))
    model_map = sheet_page.to_page if sheet_page.model else None
    out.extend(areas(page, sheet_page.viewports, doc, units_in, model_map))
    return out
