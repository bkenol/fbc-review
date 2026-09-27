"""Ceiling-height tags on a reflected ceiling plan.

The convention is two stacked text entities — what the height is measured to,
and the height:

    A.F.F.              B/O BAR JOIST
    +10'-0"             +14'-8"

— or, less often, one line: `+10'-0" A.F.F.`. Only sheets whose title says they
are a ceiling plan are read, and only whole tags: a note such as `BOTTOM OF
SIGNAGE AT 10'-0" A.F.F.` is a mounting height, not a ceiling, and is never a
match because the height is not alone on its line.
"""
from __future__ import annotations

import re
from typing import Dict, Iterable, List

from ..facts import CeilingTag, Sheet
from ..layout import PageLayout, union
from .parse import parse

_REFERENCE = re.compile(
    r"^(?:A\.?\s?F\.?\s?F\.?|CLG\.?|CEILING|"
    r"(?:B\.?\s?O\.?|B/O|BOTTOM\s+OF)\s+(?:BAR\s+|STEEL\s+)?(?:JOISTS?|STRUCTURE|DECK))$")
_HEIGHT = re.compile(r"^\+?\s*\d{1,2}\s*'\s*-?\s*\d{1,2}(?:\s+\d/\d)?\s*\"?$")
_ONE_LINE = re.compile(r"^(\+\s*\d{1,2}\s*'\s*-?\s*\d{1,2}\s*\"?)\s+(A\.?\s?F\.?\s?F\.?)$")


def ceiling_sheets(sheets: Iterable[Sheet]) -> List[Sheet]:
    """Sheets whose title says they are a reflected ceiling plan."""
    return [s for s in sheets
            if "CEILING" in (s.title or "").upper() or (s.title or "").upper().startswith("RCP")]


def ceiling_tags(layouts: Dict[int, PageLayout], sheets: Iterable[Sheet]) -> List[CeilingTag]:
    out: List[CeilingTag] = []
    for sheet in ceiling_sheets(sheets):
        layout = layouts.get(sheet.index)
        if layout is None:
            continue
        segs = layout.segments
        for s in segs:
            text = s.text.strip().upper()
            one = _ONE_LINE.match(text)
            if one:
                height = parse("feet", one.group(1))
                if height is not None:
                    out.append(CeilingTag(height, one.group(1), one.group(2), sheet.index,
                                          sheet.code, s.box))
                continue
            if not _HEIGHT.match(text):
                continue
            # The reference sits directly above or below the height, in the
            # same column: within about a line and a half, overlapping in x.
            for r in segs:
                if r is s or not _REFERENCE.match(r.text.strip().upper()):
                    continue
                gap = min(abs(r.box[1] - s.box[3]), abs(s.box[1] - r.box[3]))
                overlap = min(r.box[2], s.box[2]) - max(r.box[0], s.box[0])
                if gap <= 1.6 * max(s.h, r.h) and overlap > 0:
                    height = parse("feet", text)
                    if height is not None:
                        out.append(CeilingTag(height, s.text.strip(), r.text.strip(),
                                              sheet.index, sheet.code, union([s.box, r.box])))
                    break
    return out
