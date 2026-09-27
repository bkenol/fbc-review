"""`findings.json`'s findings: each rule outcome, plus where to draw it and what it read.

`Finding.to_dict()` is the engine's record and stays exactly what it was — the
baselines in `tests/` hold it byte for byte. What the viewer needs on top is
added here, once, for the worker and `run.py --json` alike:

* `key` — unique within one review. `fid` is not: two under-width doors are two
  H-03s, and a declared-versus-drawn divergence is two findings with one fid.
  The client keys everything it draws on this.
* `rect` — where the marker goes, in pdf.js viewport space at scale 1 on the
  source page (points, origin top-left, the page's `/Rotate` applied): the
  rule's own `box` where it knows the row it is about, else the renderer's
  `search_for(anchor)` at occurrence `hit`, else where its evidence was read.
  Found on the file the engine read, so an anchor that exists only in text a
  raster rebuild recovered is still placed. `None` when it cannot be placed,
  or for a finding that exists only under the declared reading, which is
  never drawn on the sheet.
* `evidence` — the readings the rule's inputs rest on: the value, the words as
  printed, the sheet and page, and which reader found it. A value the AI reader
  located says so. Page numbers here are 0-based, like `Finding.page`; the
  client converts both in one place.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

import pymupdf

from .layout import viewer_rect

#: The fact-store fields each rule reads, for `evidence`. A rule absent here
#: reads something other than a stated value (a schedule, a table, geometry)
#: and carries no evidence list; its finding text says what it read.
_OCC, _SPR = ("occupancy_group", ""), ("sprinkler_system", "")
RULE_FIELDS: Dict[str, Tuple[Tuple[str, str], ...]] = {
    "EGRESS.COMMON_PATH": (("egress.common_path", "required"), ("egress.common_path", "provided"),
                           _OCC, _SPR),
    "EGRESS.TRAVEL_DISTANCE": (("egress.travel_distance", "required"),
                               ("egress.travel_distance", "provided"), _OCC, _SPR),
    "EGRESS.DEAD_END": (("egress.dead_end", "required"), _OCC),
    "EGRESS.CORRIDOR_WIDTH": (("egress.corridor_width", "required"),
                              ("egress.corridor_width", "provided"), _OCC),
    "EGRESS.CAPACITY_FACTOR": (("egress.width", "required"), ("egress.width", "provided"),
                               ("occupant_load", ""), _SPR),
    "EGRESS.EXIT_COUNT": (("egress.exits", "required"), ("egress.exits", "provided"),
                          ("occupant_load", "")),
    "EGRESS.FACTOR_CONSISTENCY": (("egress_width_factor", ""),),
    "EGRESS.OCCUPANT_LOAD_POSTING": (_OCC, ("occupant_load", "")),
    "DOORS.CLEAR_WIDTH_REQUIREMENT": (("egress.door_clear_width", "required"),),
    "XSHEET.RISK_CATEGORY": (("risk_category", ""), ("occupant_load", "")),
    "XSHEET.BUILDING_AREA": (("building_area_sf", ""), ("total_area_sf", ""),
                             ("area.tabulated_total_sf", "")),
    "HEIGHT_AREA.TABLE_504_HEIGHT": (("height_ft", ""), ("construction_type", ""), _OCC, _SPR),
    "HEIGHT_AREA.TABLE_504_STORIES": (("stories", ""), ("construction_type", ""), _OCC, _SPR),
    "HEIGHT_AREA.TABLE_506_AREA": (("building_area_sf", ""), ("construction_type", ""), _OCC,
                                   _SPR),
    "FIRE.TABLE_601": (("construction_type", ""),),
    "STRUCT.WIND_STANDARD": (("wind_speed_mph", ""), ("exposure_category", ""),
                             ("risk_category", ""), ("code_edition", "")),
    "CODE.EDITION_CURRENT": (("code_edition", ""),),
    "DECL.OCCUPANCY": (_OCC,),
    "DECL.CONSTRUCTION_TYPE": (("construction_type", ""),),
    "DECL.SPRINKLER": (_SPR,),
    "DECL.BUILDING_AREA": (("building_area_sf", ""), ("total_area_sf", "")),
    "DECL.HEIGHT": (("height_ft", ""),),
    "DECL.STORIES": (("stories", ""),),
    "DECL.WIND_SPEED": (("wind_speed_mph", ""),),
    "DECL.EXPOSURE": (("exposure_category", ""),),
    "DECL.RISK_CATEGORY": (("risk_category", ""),),
    "DECL.CODE_EDITION": (("code_edition", ""),),
    "DECL.MIXED_OCCUPANCY": (("mixed_occupancy", ""),),
}


def _shown(value: Any) -> str:
    """A value as a card shows it: 50, not 50.0; yes, not True."""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return f"{value:g}" if abs(value - round(value)) > 1e-9 else f"{int(round(value))}"
    return "" if value is None else str(value)


def _evidence(doc: Optional[pymupdf.Document], facts, rule_id: str) -> List[Dict[str, Any]]:
    store = getattr(facts, "store", None)
    if store is None:
        return []
    out: List[Dict[str, Any]] = []
    for key, role in RULE_FIELDS.get(rule_id, ()):
        r = store.resolve(key, role)
        if r is None:
            continue
        c = r.best
        rect = None
        if doc is not None and c.box and 0 <= c.page < doc.page_count:
            rect = viewer_rect(doc[c.page], c.box)
        ai_only = r.methods == ["ai"]
        out.append({
            "field": key, "role": role, "value": _shown(r.value), "quote": c.raw,
            "sheet": c.sheet, "page": c.page, "rect": rect, "method": c.method,
            "confidence": r.confidence, "sheets": list(r.sheets),
            "note": (f"read by AI and verified on {c.sheet}" if ai_only else
                     "read by AI and by the layout reader, in agreement"
                     if "ai" in r.methods else ""),
        })
    return out


def _keys(findings: Sequence) -> List[str]:
    """`fid`, made unique within the review: `H-03`, `H-03~2`; a declared twin is `M-04@as_declared`."""
    seen: Dict[str, int] = {}
    out = []
    for f in findings:
        base = f.fid if f.scenario in ("both", "as_drawn") else f"{f.fid}@{f.scenario}"
        seen[base] = seen.get(base, 0) + 1
        out.append(base if seen[base] == 1 else f"{base}~{seen[base]}")
    return out


def _rect(doc: Optional[pymupdf.Document], f, evidence) -> Optional[List[float]]:
    """Where the marker goes: the rule's own box, else the anchor, else the evidence.

    The same order the renderer uses for the reviewed copy, so the live viewer
    and the downloaded PDF mark the same place.
    """
    if doc is None or f.scenario == "as_declared":
        return None
    if not (0 <= f.page < doc.page_count):
        return None
    page = doc[f.page]
    if f.box:
        return viewer_rect(page, f.box)
    if f.anchor:
        hits = page.search_for(f.anchor)
        if len(hits) > (f.hit or 0):
            return viewer_rect(page, hits[f.hit or 0])
    return next((e["rect"] for e in evidence if e["page"] == f.page and e["rect"]), None)


def findings_payload(pdf_path: Optional[str], facts, findings: Sequence) -> List[Dict[str, Any]]:
    """Every finding as `findings.json` carries it. `pdf_path` is the file the engine read."""
    doc = None
    if pdf_path:
        try:
            doc = pymupdf.open(pdf_path)
        except Exception:                     # noqa: BLE001 — placement is a nicety
            doc = None
    try:
        out = []
        for f, key in zip(findings, _keys(findings)):
            d = f.to_dict()
            d["key"] = key
            d["evidence"] = _evidence(doc, facts, f.rule_id)
            d["rect"] = _rect(doc, f, d["evidence"])
            out.append(d)
        return out
    finally:
        if doc is not None:
            doc.close()
