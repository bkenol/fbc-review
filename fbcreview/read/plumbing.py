"""A plumbing fixture calculation, read from the block that states it.

The Sculpted G-1 is typical of how a small tenant set shows its Table 2902.1
work:

    PLUMBING COUNTS
    ASSEMBLY OCCUPANCY: 70
    WC:            1/125 MALE, 1/65 FEMALE
    LAV:           1/ 200
    DF:            1/500
    SERVICE SINK:  1
    ...
    TOTAL REQUIRED:      TOTAL PROVIDED
    MALE
    1 WC                 1 UNISEX WC
    ...

The ratio lines are read as lines. The two count columns are told apart by
where their headers sit, because a line read across the block would put
`1 WC` and `1 UNISEX WC` into one sentence.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional

from ..facts import PlumbingCount
from ..layout import PageLayout

_HEAD = re.compile(r"^PLUMBING\s+(?:FIXTURE\s+)?(?:COUNTS?|CALCULATIONS?|FIXTURES|"
                   r"FIXTURE\s+COUNT|REQUIREMENTS)\b")
_RATIO = re.compile(r"^(WC|W\.C\.|WATER\s+CLOSETS?|LAVS?|LAVATOR(?:Y|IES)|DF|D\.F\.|"
                    r"DRINKING\s+FOUNTAINS?)\s*:\s*(.+)$")
_ONE_PER = re.compile(r"1\s*/\s*(\d[\d,]*)\s*(MALE|FEMALE)?")
_SINK = re.compile(r"^SERVICE\s+SINKS?\s*:\s*(\d+)\b")
_LOAD = re.compile(r"\b(?:OCCUPANCY|OCCUPANT\s+LOAD|OCCUPANTS)\s*:\s*(\d[\d,]*)\b")
_COUNT = re.compile(r"^(\d+)\s+(UNISEX\s+)?(WC|W\.C\.|WATER\s+CLOSETS?|LAVS?|LAVATOR(?:Y|IES)|DF|"
                    r"D\.F\.|DRINKING\s+FOUNTAINS?|SERVICE\s+SINKS?|MOP\s+SINKS?|URINALS?)\b")


def _kind(word: str) -> str:
    w = word.upper().replace(".", "").strip()
    if w.startswith(("WC", "WATER CLOSET")):
        return "wc"
    if w.startswith("LAV"):
        return "lav"
    if w.startswith(("DF", "DRINKING")):
        return "drinking_fountain"
    if w.startswith(("SERVICE", "MOP")):
        return "service_sink"
    return "urinal"


def _lines(segs) -> List[str]:
    """Segments joined into visual lines, top to bottom, left to right."""
    rows: List[list] = []
    for s in sorted(segs, key=lambda s: (s.box[1], s.box[0])):
        centre = (s.box[1] + s.box[3]) / 2.0
        for row in rows:
            if abs(row[0] - centre) <= 0.6 * s.h:
                row[1].append(s)
                break
        else:
            rows.append([centre, [s]])
    return [" ".join(x.text.strip() for x in sorted(r[1], key=lambda x: x.box[0])) for r in rows]


def plumbing_count(layouts: Dict[int, PageLayout], codes: Dict[int, str]) -> Optional[PlumbingCount]:
    """The first fixture calculation the set states, or None."""
    for page, layout in sorted(layouts.items()):
        head = next((s for s in layout.segments if _HEAD.match(s.text.strip().upper())), None)
        if head is None:
            continue
        x0, x1 = head.box[0] - 120, head.box[2] + 260
        y0, y1 = head.box[1], head.box[1] + 45 * max(head.h, 1.0)
        block = [s for s in layout.segments if s is not head and x0 <= s.box[0] <= x1
                 and y0 < s.box[1] <= y1]
        out = PlumbingCount(None, page=page, sheet=codes.get(page, f"p{page + 1}"),
                            anchor=head.text.strip(), box=head.box)
        cols = {}
        for s in block:
            t = s.text.strip().upper()
            if t.startswith("TOTAL REQUIRED"):
                cols["required"] = s.box[0]
            elif t.startswith("TOTAL PROVIDED"):
                cols["provided"] = s.box[0]
        top_of_counts = min((s.box[1] for s in block
                             if s.text.strip().upper().startswith(("TOTAL REQUIRED",
                                                                   "TOTAL PROVIDED"))),
                            default=y1)
        for line in _lines([s for s in block if s.box[1] < top_of_counts]):
            u = line.upper()
            m = _LOAD.search(u)
            if m and out.occupant_load is None:
                out.occupant_load = float(m.group(1).replace(",", ""))
            m = _SINK.match(u)
            if m:
                out.service_sinks = float(m.group(1))
                continue
            m = _RATIO.match(u)
            if not m:
                continue
            per = {sex: float(n.replace(",", "")) for n, sex in _ONE_PER.findall(m.group(2))
                   for sex in [sex or "ALL"]}
            male = per.get("MALE", per.get("ALL"))
            female = per.get("FEMALE", per.get("ALL"))
            out.ratios[_kind(m.group(1))] = (male, female)
        if len(cols) == 2:
            for s in block:
                if s.box[1] <= top_of_counts:
                    continue
                m = _COUNT.match(s.text.strip().upper())
                if not m:
                    continue
                side = min(cols, key=lambda k: abs(cols[k] - s.box[0]))
                bucket = out.required if side == "required" else out.provided
                k = _kind(m.group(3))
                bucket[k] = bucket.get(k, 0.0) + float(m.group(1))
                if side == "provided" and m.group(2):
                    out.unisex = True
        if out.ratios or out.provided:
            return out
    return None
