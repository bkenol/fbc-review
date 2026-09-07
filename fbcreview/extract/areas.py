"""Areas a sheet tabulates rather than states.

`reconcile._PATTERNS` reads an area when a sheet writes one down as a value —
`BUILDING AREA: 15,376 SF`. A great many sets never write that line. They print
a table of spaces instead, and the building's area is the sum of it:

    OCCUPANCY CALCULATION
    LOBBY/RECEPTION      400 SQ. FT.
    STUDIO AREA        1,300 SQ. FT.

Reported through Refine analysis on an MEP-only submittal (feedback
`feac59646e6c`), where `DECL.BUILDING_AREA` stood down saying *"neither the
drawings nor the declaration state this"*. That reason was false: the drawings
state the components of it, on the sheet, in text. The reviewer's note was
"Should be able to calculate building area".

**What this module does not do.** The sum of a room-area table is not a gross
building area. It is the sum of the spaces somebody chose to list, net of walls,
chases and any circulation they left out, and Table 506.2 and Table 1004.5 both
want gross. So nothing here becomes `building_area_sf`; `AreaTally` is reported
to the user as arithmetic they can check and asked to be confirmed, and it never
becomes the drawn half of a declared-against-drawn comparison. Estimating is
welcome, laundering an estimate into a stated fact is not.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

#: Headings that introduce a table of areas. A lexicon rather than one literal:
#: the offices whose sets we have seen call it an occupancy calculation, an area
#: calculation, an area tabulation and a room schedule, and all four mean the
#: same table. Matched as a whole line, so a sentence mentioning "area
#: calculations are shown on M.001" does not open one.
_HEADINGS = (
    r"OCCUPAN(?:CY|T)\s+(?:LOAD\s+)?CALCULATIONS?",
    r"AREA\s+CALCULATIONS?",
    r"AREA\s+TABULATIONS?",
    r"AREA\s+(?:SUMMARY|SCHEDULE|BREAKDOWN|ANALYSIS)",
    r"ROOM\s+(?:AREA|SCHEDULE)S?",
    r"SQUARE\s+FOOTAGE(?:\s+(?:SUMMARY|SCHEDULE|BREAKDOWN))?",
    r"GROSS\s+(?:FLOOR\s+)?AREAS?",
    r"TENANT\s+AREAS?",
)
_HEADING = re.compile(r"^\s*(" + "|".join(_HEADINGS) + r")\s*[:\-]?\s*$", re.I)

#: `LOBBY/RECEPTION   400 SQ. FT.` — a name, then a number, then an area unit.
#: The unit is required. Without it the pattern reads an occupant count, a room
#: number or a CFM figure as an area, which is how a table of the wrong kind
#: turns into a confident wrong answer.
_ROW = re.compile(
    r"^\s*(?P<label>.*?[A-Za-z][^\d]*?)\s+"
    r"(?P<n>\d[\d,]*(?:\.\d+)?)\s*"
    r"(?:SQ\.?\s*FT\.?|SQ\.?\s*F\.?|S\.\s*F\.|SQFT|SF)\.?\s*$",
    re.I)

_TOTAL = re.compile(r"^\s*(?:SUB[\s\-]?)?TOTALS?\b", re.I)

#: A room smaller than this is a dimension misread as an area; a single space
#: larger than this is a whole campus. Both are the pattern matching something
#: that is not a room.
MIN_SF, MAX_SF = 5.0, 2_000_000.0

#: How far past the heading to keep looking, and how many non-rows end the run.
MAX_SCAN, MAX_GAP = 60, 3

#: A stated total further than this from the sum of the rows means the table was
#: not read correctly — a column was missed, or two tables ran together.
TOTAL_TOLERANCE = 0.02


@dataclass(frozen=True)
class AreaRow:
    label: str
    sf: float


@dataclass(frozen=True)
class AreaTally:
    """A table of areas found on one sheet, and its arithmetic."""
    heading: str
    page: int
    sheet: str
    rows: Tuple[AreaRow, ...]
    stated_total: Optional[float] = None

    @property
    def summed_sf(self) -> float:
        return round(sum(r.sf for r in self.rows), 2)

    @property
    def reliable(self) -> bool:
        """Does the table's own total agree with the sum of its rows?

        A table that states a total is checking our arithmetic for us. When the
        two disagree we did not read the table we think we read, and the honest
        answer is to report nothing rather than a number we cannot defend.
        """
        if self.stated_total is None:
            return True
        if self.stated_total <= 0:
            return False
        return abs(self.summed_sf - self.stated_total) <= TOTAL_TOLERANCE * self.stated_total

    @property
    def total_sf(self) -> float:
        """The number to report: the table's own total when it states one."""
        return self.stated_total if self.stated_total is not None else self.summed_sf

    def as_meta(self) -> Dict[str, object]:
        """A plain dict, for `ProjectFacts.meta`, which stays JSON-shaped."""
        return {
            "heading": self.heading, "page": self.page, "sheet": self.sheet,
            "rows": [{"label": r.label, "sf": r.sf} for r in self.rows],
            "stated_total": self.stated_total,
            "summed_sf": self.summed_sf,
            "total_sf": self.total_sf,
        }


def _number(raw: str) -> Optional[float]:
    try:
        return float(raw.replace(",", ""))
    except ValueError:
        return None


def tally_on_page(text: str, page: int, sheet: str) -> Optional[AreaTally]:
    """The first area table on one page's text, or None."""
    lines = (text or "").splitlines()
    for i, line in enumerate(lines):
        head = _HEADING.match(line)
        if not head:
            continue

        rows: List[AreaRow] = []
        stated: Optional[float] = None
        gap = 0
        for raw in lines[i + 1:i + 1 + MAX_SCAN]:
            if not raw.strip():
                gap += 1
                if rows and gap >= MAX_GAP:
                    break
                continue
            # A second heading ends the first table rather than extending it.
            if _HEADING.match(raw):
                break

            m = _ROW.match(raw)
            if not m:
                gap += 1
                if rows and gap >= MAX_GAP:
                    break
                continue

            gap = 0
            value = _number(m.group("n"))
            if value is None or not (MIN_SF <= value <= MAX_SF):
                continue
            label = " ".join(m.group("label").split())
            if _TOTAL.match(label):
                # Keep the first stated total; a table with several is a table
                # we are misreading, and `reliable` will say so.
                if stated is None:
                    stated = value
                continue
            rows.append(AreaRow(label, value))

        # One row is a note, not a table. Two is a building.
        if len(rows) >= 2:
            return AreaTally(head.group(1).upper(), page, sheet, tuple(rows), stated)
    return None


def find_tally(text_by_page: Dict[int, str], sheets: Sequence) -> Optional[AreaTally]:
    """The set's area table, general sheets first.

    General sheets are preferred for the same reason `reconcile._sheet_hit`
    prefers them — a code-data sheet is the authority for what the set claims.
    Unlike the reads scoped *only* to the general series, this one carries on to
    the rest of the set when there is no general sheet, because a submittal with
    no G series is exactly the case that most needs it.
    """
    ordered = sorted(sheets, key=lambda s: (not (s.code or "")[:1].upper() == "G",
                                            s.index))
    for s in ordered:
        found = tally_on_page(text_by_page.get(s.index) or "", s.index, s.code)
        if found is not None and found.reliable:
            return found
    return None
