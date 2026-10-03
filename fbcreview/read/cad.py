"""A drawing's sidecar read into the engine's own terms.

`fbcreview/cad` plots a DWG or DXF to the PDF the engine reads and writes what
the plot cannot carry into `cad.json`. This module turns that file — plain data,
no ezdxf — into what the engine already speaks:

* **Sheet numbers** from title-block fields that say they are the sheet number.
* **Claims** from block attributes, through the same catalog the layout reader
  uses: an attribute's tag (or an attribute definition's prompt) is its label,
  its text is the value, and `claims_from_pair` decides whether that names a
  fact, with every disqualifier and parser it applies to a printed pair. The
  claim says it came from the drawing (`method = cad`) and which entity.
* **Sources** stamped on every claim read off a drawn sheet, so two readers
  finding one entity are counted as one reading (`factstore.independent`).
* **Exact scales** for each viewport, as `ViewScale`s with HIGH evidence that
  says where the number came from — and a page-wide abstention, because a sheet
  of viewports at several scales has no single scale.

Nothing here decides compliance, and nothing measured is written under a key a
rule reads as stated. Measured areas and dimension lengths stay in the sidecar
summary as data.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Tuple

from ..cad import EMPTY_VIEW_SHARE
from ..confidence import HIGH, Evidence
from ..extract.scale import _LABEL, _agrees, label_value
from ..facts import ViewScale
from ..factstore import CAD, Claim
from ..layout.model import Pair, PageLayout
from .deterministic import claims_from_pair

Box = Tuple[float, float, float, float]


def pages(sidecar: Optional[dict]) -> Dict[int, dict]:
    return {int(p["page"]): p for p in (sidecar or {}).get("pages", [])}


# ── sheet identity ────────────────────────────────────────────────────────────

def sheet_numbers(sidecar: dict) -> Dict[int, Tuple[str, str, str]]:
    """page -> (number, where it was read, title) for pages whose title block
    has a field naming the sheet number. A layout's tab name is never one."""
    out = {}
    for pno, p in pages(sidecar).items():
        if p.get("number"):
            out[pno] = (p["number"], p.get("number_source", ""), p.get("title", ""))
    return out


# ── claims ────────────────────────────────────────────────────────────────────

def _label(rec: dict) -> str:
    """An attribute's label as a person would write it: `OCCUPANCY_GROUP` -> `OCCUPANCY GROUP`."""
    raw = rec.get("prompt") if rec.get("kind") == "ATTDEF" and rec.get("prompt") else rec.get("tag")
    return " ".join(str(raw or "").replace("_", " ").replace("-", " ").split()).upper()


def attribute_claims(sidecar: dict, codes: Dict[int, str]) -> List[Claim]:
    """Catalog facts the drawing's block attributes state."""
    out: List[Claim] = []
    page_info = pages(sidecar)
    for rec in sidecar.get("claims", []):
        if rec.get("type") != "attribute" or not rec.get("box"):
            continue
        label = _label(rec)
        if not label:
            continue
        box = tuple(rec["box"])
        pno = int(rec["page"])
        pair = Pair(label=label, values=[rec["text"]], kind="attribute", page=pno,
                    label_box=box, value_boxes=[box])
        layout = page_info.get(pno, {}).get("layout", "")
        for c in claims_from_pair(pair, codes.get(pno, f"p{pno + 1}"), method=CAD):
            c.raw = rec["text"]            # what is printed; the tag is not
            c.source = f"dxf:{rec.get('source') or rec.get('handle', '')}"
            c.layout = layout
            c.layer = rec.get("layer", "")
            c.note = (f"the drawing's {rec.get('kind', 'ATTRIB')} '{label}' — the field's "
                      "name is the drawing's, not printed on the sheet")
            out.append(c)
    return out


def _covered(claim: Box, cell: Box, slack: float = 0.5) -> bool:
    """Is most of a text cell inside a claim's box?

    A claim's box is made of PDF word boxes, which reach about 0.3 × the cap
    height below the baseline and a little above the cap line; a cell is cap
    line to baseline. In a block stacked at 1.25 × cap that reach overlaps the
    next row's label (measured: the occupancy claim of a tight stack named the
    risk-category label as its source when any touch counted). Half the cell's
    area inside the claim is what makes the cell part of the claim.
    """
    x0, y0 = max(claim[0] - slack, cell[0]), max(claim[1] - slack, cell[1])
    x1, y1 = min(claim[2] + slack, cell[2]), min(claim[3] + slack, cell[3])
    if x1 <= x0 or y1 <= y0:
        return False
    area = max(cell[2] - cell[0], 0.1) * max(cell[3] - cell[1], 0.1)
    return (x1 - x0) * (y1 - y0) >= 0.5 * area


def stamp_sources(claims: Iterable[Claim], sidecar: dict) -> int:
    """Name the drawing entities behind each claim read off a drawn sheet.

    A claim the layout or AI reader took from the text layer is stamped with
    every entity whose text cell lies mostly inside its box. Returns how many
    were stamped.
    """
    info = pages(sidecar)
    n = 0
    for c in claims:
        if c.source or c.box is None or c.page not in info:
            continue
        handles = set()
        for cell in info[c.page].get("text", []):
            if _covered(tuple(c.box), tuple(cell["box"])):
                handles.update(h for h in cell.get("handles", []) if h)
        if handles:
            c.source = "+".join(f"dxf:{h}" for h in sorted(handles))
            c.layout = info[c.page].get("layout", "")
            n += 1
    return n


# ── scale ─────────────────────────────────────────────────────────────────────

def _pt_per_unit(a: List[float]) -> float:
    return abs(a[0] * a[3] - a[1] * a[2]) ** 0.5


def _labels(layout: Optional[PageLayout]) -> List[Tuple[float, str]]:
    if layout is None:
        return []
    out = []
    for seg in layout.segments:
        for m in _LABEL.finditer(seg.text):
            v = label_value(m)
            if v is not None:
                out.append((v, m.group().strip()))
    return out


def page_scales(sidecar: dict, pno: int, sheet: str,
                layout: Optional[PageLayout] = None) -> Optional[Tuple[Evidence, List[ViewScale]]]:
    """(page-wide scale, per-viewport scales) for one page plotted from a drawing,
    or None when the page was not."""
    p = pages(sidecar).get(pno)
    if p is None:
        return None
    units = _units(sidecar, p.get("drawing", ""))
    src = f"{sheet}: layout '{p.get('layout', '')}'"
    if units is None or units.get("inches_per_unit") is None:
        why = "the drawing declares no units, so its coordinates cannot be converted to feet"
        return Evidence.abstain(src, why, pno), [
            ViewScale(tuple(v["rect"]), Evidence.abstain(f"{src}, viewport {v['handle']}", why, pno))
            for v in p.get("viewports", [])]
    per_ft = 12.0 / float(units["inches_per_unit"])
    basis = ("" if units.get("basis") == "stated"
             else f"; units {units.get('name')} inferred — {units.get('note', '')}")
    printed = _labels(layout)

    if p.get("model"):
        v = round(_pt_per_unit(p["to_page"]) * per_ft, 3)
        ev = Evidence(v, f"{src} (model space)", HIGH,
                      f"exact from the drawing: model space fitted to the sheet at {v:g} pt per "
                      f"foot; there is no plotted scale to read{basis}", pno)
        return ev, []

    views: List[ViewScale] = []
    distinct = []
    for vp in p.get("viewports", []):
        pt_ft = round(_pt_per_unit(vp["to_page"]) * per_ft, 3)
        ratio = 1.0 / vp["scale"] if vp.get("scale") else None
        ratio_txt = f"1:{ratio:.4g}" if ratio else "unknown ratio"
        agree = [t for val, t in printed if _agrees(val, pt_ft)]
        if agree:
            check = f"; agrees with the printed label {agree[0]}"
        elif printed:
            check = ("; the sheet's printed scale labels (" + ", ".join(sorted({t for _, t in printed}))
                     + ") do not match it — the viewport is what is drawn")
        else:
            check = ""
        note = (f"exact from the drawing: viewport {vp['handle']} shows model space at "
                f"{ratio_txt} = {pt_ft:g} pt per foot; not a printed label{check}{basis}")
        views.append(ViewScale(tuple(vp["rect"]),
                               Evidence(pt_ft, f"{src}, viewport {vp['handle']}", HIGH, note, pno)))
        distinct.append(pt_ft)
    uniq = sorted({round(v, 3) for v in distinct})
    if not views:
        why = "the layout has no viewport onto model space; nothing on it is drawn to a scale"
    else:
        why = ("the sheet holds viewports at " + ", ".join(f"{v:g}" for v in uniq) +
               " pt per foot; each governs only the geometry inside it")
    return Evidence.abstain(src, why, pno), views


def _units(sidecar: dict, drawing: str) -> Optional[dict]:
    for d in sidecar.get("drawings", []):
        if d.get("name") == drawing:
            return d.get("units")
    return None


# ── what the job record keeps ─────────────────────────────────────────────────

def summary(sidecar: dict, claims: int = 0, stamped: int = 0) -> Dict[str, Any]:
    """Counts and provenance for `facts.meta["cad"]` — no drawing text."""
    recs = sidecar.get("claims", [])
    by_type: Dict[str, int] = {}
    for r in recs:
        by_type[r.get("type", "")] = by_type.get(r.get("type", ""), 0) + 1
    dims = [r for r in recs if r.get("type") == "dimension"]
    return {
        "kind": sidecar.get("kind"),
        "render_version": sidecar.get("render_version"),
        "converter": sidecar.get("converter"),
        "ezdxf": sidecar.get("ezdxf"),
        "drawings": [{k: d.get(k) for k in ("name", "release", "dxfversion", "role", "units",
                                            "audit_fixes", "viewports_repaired", "xrefs")}
                     for d in sidecar.get("drawings", [])],
        "sheets": [{"page": p["page"], "drawing": p.get("drawing"), "layout": p.get("layout"),
                    "number": p.get("number"),
                    "number_source": p.get("number_source"), "model": p.get("model"),
                    "viewports": len(p.get("viewports", [])), "text_cells": p.get("cells", 0),
                    "empty_view_share": p.get("empty_view_share", 0.0),
                    "shows_nothing": p.get("empty_view_share", 0.0) > EMPTY_VIEW_SHARE}
                   for p in sidecar.get("pages", [])],
        "layers": len(sidecar.get("layers", [])),
        "records": by_type,
        "claims": claims,
        "sources_stamped": stamped,
        "dimensions_overridden": sum(1 for d in dims if d.get("overridden")),
        "warnings": list(sidecar.get("warnings", [])),
        "seconds": sidecar.get("seconds"),
    }
