"""Drawing scale, determined from two independent sources and cross-checked.

Why this is not a one-liner: the ISO 32000 §12.9 route (viewport /Measure
dictionaries) is exact but ambiguous in real AutoCAD output.  The test set
carries the SAME 125 viewports on every page, at 23 different conversion
factors, most of them unit-scale artefacts of the layout.  Reverse-iterating
the /VP array as the spec suggests returns the sheet viewport (71.99 pt/ft),
which is wrong by a factor of four.

So: build a candidate set from /Measure, build a second candidate set from the
scale labels printed under each view, and intersect.  Agreement between two
independent sources is high confidence.  One source only is medium.  A page
that prints several different scales and offers no way to attribute geometry to
one of them is an ABSTAIN — geometric rules then skip that page rather than
measuring at a guessed scale.
"""
from __future__ import annotations
import re
from typing import List, Optional
import pymupdf
from ..confidence import Evidence, HIGH, MEDIUM

_VP    = re.compile(r"/Type/Viewport(.*?)/BBox\[([-\d\. ]+)\]", re.S)
_C     = re.compile(r"/X\[<</C ([\d\.eE+-]+)")
# 1/4" = 1'-0"   ·   3/8" = 1'-0"   ·   1" = 1'-0"   ·   6" = 1'-0"   ·   1 1/2" = 1'-0"
_LABEL = re.compile(r"""(\d+)?\s*(?:(\d+)\s*/\s*(\d+))?\s*"\s*=\s*1\s*'\s*-\s*0\s*\"""", re.X)

PT_PER_IN = 72.0


def measure_candidates(doc: pymupdf.Document, page: int) -> List[float]:
    """Distinct pt-per-foot values declared in the page's /VP /Measure dictionaries."""
    try:
        typ, val = doc.xref_get_key(doc[page].xref, "VP")
    except Exception:
        return []
    if typ != "array" or not val:
        return []
    out = set()
    for body, _bbox in _VP.findall(val):
        m = _C.search(body)
        if not m:
            continue
        try:
            c = float(m.group(1))
        except ValueError:
            continue
        if c > 0:
            out.add(round(1.0 / c, 3))
    return sorted(out)


def label_candidates(page_text: str) -> List[float]:
    """pt-per-foot implied by every `X" = 1'-0"` scale label printed on the sheet."""
    out = set()
    for whole, num, den in _LABEL.findall(page_text):
        inches = 0.0
        if whole:
            inches += float(whole)
        if num and den:
            inches += float(num) / float(den)
        if inches > 0:
            out.add(round(inches * PT_PER_IN, 3))
    return sorted(out)


def page_scale(doc: pymupdf.Document, page: int, page_text: str,
               tol: float = 0.05) -> Evidence:
    """Resolve one governing scale for the page, or abstain."""
    labels = label_candidates(page_text)
    meas = measure_candidates(doc, page)
    src = f"page {page+1}"

    if not labels and not meas:
        return Evidence.abstain(src, "no scale label and no /Measure viewports", page)

    if len(labels) == 1:
        lab = labels[0]
        agree = [m for m in meas if abs(m - lab) <= tol * lab / 18.0 + 0.05]
        if agree:
            return Evidence(lab, f"{src}: scale label + /Measure viewport {agree[0]}",
                            HIGH, f"label {lab} pt/ft confirmed by {len(agree)} viewport(s)", page)
        return Evidence(lab, f"{src}: printed scale label only", MEDIUM,
                        "no /Measure viewport corroborates this value", page)

    if len(labels) > 1:
        return Evidence.abstain(
            src, f"sheet prints {len(labels)} different scales {labels}; geometry cannot be "
                 f"attributed to one without view-boundary analysis", page)

    return Evidence.abstain(src, f"/Measure offers {len(meas)} candidates and the sheet prints "
                                 f"no scale label to disambiguate", page)
