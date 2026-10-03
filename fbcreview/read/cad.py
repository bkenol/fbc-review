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
from ..confidence import HIGH, MEDIUM, Evidence
from ..extract.scale import _LABEL, _agrees, label_value
from ..facts import ViewScale
from ..factstore import CAD, RIVAL_SCORE, Claim
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


def _prefix(sidecar: dict, page: dict) -> str:
    """What makes a handle unique across the upload: handles are numbered per
    drawing, so two sheet files made from one template give the same note the
    same handle (measured: "the same drawing entity is also shown on" a sheet
    that states it itself). With more than one drawing, a token carries the
    drawing's position in the sidecar — not its name, which may hold the `+`
    or `:` the token is built with. With one, it is the bare handle."""
    names = [d.get("name", "") for d in (sidecar or {}).get("drawings", [])]
    if len(names) < 2:
        return ""
    d = page.get("drawing", "")
    return f"{names.index(d)}/" if d in names else ""


def attribute_claims(sidecar: dict, codes: Dict[int, str]) -> List[Claim]:
    """Catalog facts the drawing's block attributes state."""
    out: List[Claim] = []
    page_info = pages(sidecar)
    for rec in sidecar.get("claims", []):
        if rec.get("type") != "attribute" or not rec.get("box"):
            continue
        # A tag describes what it tags. Room 101's OCCUPANT_LOAD = 45, seen
        # through a viewport, beat the printed TOTAL OCCUPANT LOAD 600 and
        # turned "600 requires 3 exits; 2 provided" into a verified pass
        # (measured). An attribute seen through a viewport, or on a block that
        # repeats in model space, is not a statement about the building; what
        # it prints is still on the sheet for the layout reader, with its own
        # printed label or none.
        if rec.get("viewport") or rec.get("repeated"):
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
            # A tag's name is never printed, so it never outranks a printed
            # label: at RIVAL_SCORE it can still raise a conflict, never win one.
            c.score = min(c.score, RIVAL_SCORE)
            c.source = (f"dxf:{_prefix(sidecar, page_info.get(pno, {}))}"
                        f"{rec.get('source') or rec.get('handle', '')}")
            c.layout = layout
            c.layer = rec.get("layer", "")
            c.note = (f"the drawing's {rec.get('kind', 'ATTRIB')} '{label}' — the field's "
                      "name is the drawing's, not printed on the sheet")
            out.append(c)
    return out


def _covered(claim: Box, cell: Box, slack: float = 0.5) -> bool:
    """Is most of a text cell inside a claim's box, or most of the claim
    inside the cell?

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
    inter = (x1 - x0) * (y1 - y0)
    area = max(cell[2] - cell[0], 0.1) * max(cell[3] - cell[1], 0.1)
    # Or most of the claim inside the cell: the AI quotes the shortest run
    # holding label and value — `OCCUPANT LOAD: 70` of a longer note — and that
    # box covers under half of the note's cell. Unstamped, it passed for a
    # second, independent reader of the very text the layout reader read
    # (measured: HIGH, "two independent readers agree"). A one-row claim's
    # thin reach into the next row of a tight stack is still neither.
    own = max(claim[2] - claim[0], 0.1) * max(claim[3] - claim[1], 0.1)
    return inter >= 0.5 * area or inter >= 0.5 * own


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
            pre = _prefix(sidecar, info[c.page])
            c.source = "+".join(f"dxf:{pre}{h}" for h in sorted(handles))
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
    src = f"{sheet}: layout '{p.get('layout', '')}'"
    printed = _labels(layout)
    if not p.get("model") and not p.get("viewports"):
        # Drawn on paper, through no viewport. What is on it is at whatever
        # scale its label prints — the drawing defines none — so a printed
        # label governs, through the PDF path's own resolver (measured: a
        # life-safety plan at a printed 1/4" = 1'-0" lost its 18 pt/ft and
        # its egress measure to "nothing on it is drawn to a scale").
        if printed:
            return None
        return Evidence.abstain(
            src, "the layout has no viewport onto model space and prints no scale label, so "
                 "nothing on it has a scale to convert at", pno), []
    units = _units(sidecar, p.get("drawing", ""))
    if units is None or units.get("inches_per_unit") is None:
        why = "the drawing declares no units, so its coordinates cannot be converted to feet"
        return Evidence.abstain(src, why, pno), [
            ViewScale(tuple(v["rect"]), Evidence.abstain(f"{src}, viewport {v['handle']}", why, pno))
            for v in p.get("viewports", [])]
    per_ft = 12.0 / float(units["inches_per_unit"])
    stated_units = units.get("basis") == "stated"
    basis = ("" if stated_units
             else f"; units {units.get('name')} inferred — {units.get('note', '')}")
    # Exact only when the drawing states its units. Inferred units make the
    # ratio exact and the feet an inference, and an inference is not graded
    # as a stated fact (the PDF path drops an unconfirmed label the same way).
    conf = HIGH if stated_units else MEDIUM
    exact = "exact from the drawing" if stated_units else \
        "from the drawing's coordinates, its units inferred"

    if p.get("model"):
        v = round(_pt_per_unit(p["to_page"]) * per_ft, 3)
        ev = Evidence(v, f"{src} (model space)", conf,
                      f"{exact}: model space fitted to the sheet at {v:g} pt per "
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
        note = (f"{exact}: viewport {vp['handle']} shows model space at "
                f"{ratio_txt} = {pt_ft:g} pt per foot; not a printed label{check}{basis}")
        views.append(ViewScale(tuple(vp["rect"]),
                               Evidence(pt_ft, f"{src}, viewport {vp['handle']}", conf, note, pno)))
        distinct.append(pt_ft)
    uniq = sorted({round(v, 3) for v in distinct})
    if not views:
        why = "none of the layout's viewports onto model space could be read"
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
