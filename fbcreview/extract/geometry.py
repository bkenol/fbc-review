"""Measuring real distances off the drawing's own vector geometry."""
from __future__ import annotations
from typing import List, Optional, Sequence, Tuple
import math
import pymupdf
from ..confidence import Evidence, HIGH


def paths_on_layer(doc: pymupdf.Document, pno: int, layer_substring: str) -> List[dict]:
    s = layer_substring.lower()
    return [p for p in doc[pno].get_drawings()
            if p.get("layer") and s in p["layer"].lower()]


def polyline_length_ft(points: Sequence[Tuple[float, float]], pt_per_ft: float) -> float:
    total = 0.0
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        total += math.hypot(x1 - x0, y1 - y0)
    return total / pt_per_ft


def measure(points: Sequence[Tuple[float, float]], scale: Evidence, what: str) -> Evidence:
    """Measure a traced path, or abstain if the page scale was not resolved."""
    if not scale:
        return Evidence.abstain(f"measure:{what}",
                                f"page scale unresolved — {scale.note}", scale.page)
    ft = polyline_length_ft(points, scale.value)
    return Evidence(round(ft, 2), f"traced geometry @ {scale.value} pt/ft", HIGH,
                    f"{what}: {ft:.2f} ft", scale.page)
