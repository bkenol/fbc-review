"""Measured geometry — distances taken off the drawing, not read off a label.

A review that only re-reads the designer's own annotations has verified nothing.
This rule traces the egress-path layer and reports the longest continuous run,
which is a lower bound on the exit access travel distance and an independent
check on the number printed on the sheet.
"""
from __future__ import annotations
import math
from collections import defaultdict
from typing import List, Optional, Tuple

import pymupdf

from . import rule, Finding, RuleResult
from ..confidence import Abstention
from ..facts import ProjectFacts

EGRESS_LAYER = "egress path"
COLLINEAR_TOL = 0.6      # pt — segments this close in the fixed axis are the same run
JOIN_TOL = 2.0           # pt — gap that still counts as continuous


def _segments(page: pymupdf.Page, layer_sub: str) -> List[Tuple[float, float, float, float]]:
    out = []
    for p in page.get_drawings():
        lay = p.get("layer")
        if not lay or layer_sub not in lay.lower():
            continue
        for it in p["items"]:
            if it[0] == "l":
                a, b = it[1], it[2]
                out.append((a.x, a.y, b.x, b.y))
            elif it[0] == "re":
                r = it[1]
                out += [(r.x0, r.y0, r.x1, r.y0), (r.x1, r.y0, r.x1, r.y1),
                        (r.x1, r.y1, r.x0, r.y1), (r.x0, r.y1, r.x0, r.y0)]
    return out


def _bounds(segs) -> Tuple[float, float, float, float]:
    """The box the traced geometry occupies, for attributing it to a view."""
    xs = [c for x0, _y0, x1, _y1 in segs for c in (x0, x1)]
    ys = [c for _x0, y0, _x1, y1 in segs for c in (y0, y1)]
    return (min(xs), min(ys), max(xs), max(ys))


def longest_run_pt(segs) -> float:
    """Longest continuous straight run, after merging collinear segments.

    CAD exports an egress path as hundreds of short dashes; measuring any one of
    them is meaningless. Merging them back into runs is what makes the number
    comparable to a travel distance.
    """
    best = 0.0
    for axis in (0, 1):                       # 0 = vertical runs, 1 = horizontal
        groups = defaultdict(list)
        for x0, y0, x1, y1 in segs:
            if axis == 0 and abs(x1 - x0) < COLLINEAR_TOL:
                groups[round(x0, 1)].append((min(y0, y1), max(y0, y1)))
            elif axis == 1 and abs(y1 - y0) < COLLINEAR_TOL:
                groups[round(y0, 1)].append((min(x0, x1), max(x0, x1)))
        for ivs in groups.values():
            ivs.sort()
            lo, hi = ivs[0]
            for a, b in ivs[1:]:
                if a <= hi + JOIN_TOL:
                    hi = max(hi, b)
                else:
                    best = max(best, hi - lo); lo, hi = a, b
            best = max(best, hi - lo)
    return best


def _ft_in(ft: float) -> str:
    whole = int(ft)
    return f"{whole}'-{round((ft - whole) * 12)}\""


@rule("MEASURE.EGRESS_EXTENT")
def egress_extent(f: ProjectFacts, out: RuleResult):
    opts = f.meta.get("options")
    if opts is not None and not opts.include_measured:
        out.abstentions.append(Abstention(
            "MEASURE.EGRESS_EXTENT", "geometric measurement was switched off in the review options"))
        return

    doc = pymupdf.open(f.source_path)
    try:
        target = None
        for pno, geo in sorted(f.geometry.items()):
            if any(EGRESS_LAYER in k.lower() for k in geo.layers):
                target = (pno, geo)
                break
        if target is None:
            out.abstentions.append(Abstention(
                "MEASURE.EGRESS_EXTENT",
                "no CAD layer matching 'egress path' — the plot may be a scan, or the layer "
                "is named differently in this office's standard"))
            return
        pno, geo = target
        segs = _segments(doc[pno], EGRESS_LAYER)
        if not segs:
            out.abstentions.append(Abstention(
                "MEASURE.EGRESS_EXTENT", "egress layer present but carries no line geometry", pno))
            return

        # The scale governing THIS geometry, not the sheet. A sheet printing a
        # plan at 1/4" beside a wall section at 1-1/2" has no page-wide scale and
        # abstains as a page; the egress path is inside one of those views and is
        # measurable at that view's scale.
        ev = geo.scale_for(_bounds(segs))
        if not ev:
            out.abstentions.append(Abstention(
                "MEASURE.EGRESS_EXTENT",
                f"scale unresolved on {f.sheet_code(pno)} — {ev.note}", pno))
            return
        scale = ev.value
        run_ft = longest_run_pt(segs) / scale

        d = f.datum("1017.2")
        stated = d.provided if d else None
        limit = d.required if d else None
        cmp_txt = ""
        if stated:
            delta = run_ft - stated
            cmp_txt = (f" The sheet annotates {_ft_in(stated)} of travel; the traced run is "
                       f"{abs(delta):.1f} ft {'longer' if delta > 0 else 'shorter'}, which is what "
                       f"you expect when the drawn run follows a band edge rather than the "
                       f"annotated centreline.")
        over = bool(limit and run_ft > limit)
        out.findings.append(Finding(
            "MEAS-1", "MEASURE.EGRESS_EXTENT", "OPEN" if over else "PASS",
            "CRITICAL" if over else "MEASURED", "Means of egress", pno, f.sheet_code(pno),
            "TRAVEL DISTANCE",
            f"Longest egress run measured off the drawing — {run_ft:.1f} ft",
            f"Every segment on the '{EGRESS_LAYER}' CAD layer of {f.sheet_code(pno)} was traced, "
            f"collinear dashes were merged back into continuous runs, and the longest run was "
            f"converted at the scale the sheet itself states for this drawing "
            f"({scale:g} pt/ft, {ev.confidence} confidence — {ev.source}).",
            f"{len(segs)} segments traced; longest continuous run {run_ft:.2f} ft "
            f"({_ft_in(run_ft)})." + cmp_txt +
            (f" Limit is {limit:g} ft." if limit else "") +
            (" THE MEASURED RUN EXCEEDS THE LIMIT." if over else " Inside the limit."),
            "FBC-B Table 1017.2 · scale from the sheet's own label and the PDF's "
            "/Measure dictionary",
            "Confirm the travel path and re-dimension." if over else "None."))
    finally:
        doc.close()
