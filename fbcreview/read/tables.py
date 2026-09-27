"""A schedule's rows, re-read inside the schedule's own box.

`extract.schedules.find_schedule` finds a table by running `find_tables()` over
the whole page. That is the right way to *find* one, and on some sheets the
wrong way to *read* one: M-1's Outdoor Air Calculations comes back with
Reception and Mat Studio merged into a single row (`164 994`, `10 40`, ...),
because the whole-page pass lets the row separators of neighbouring linework
decide where rows begin. The same call clipped to the table's own box returns
one row per room. Nothing is inferred here; the cells are the sheet's.
"""
from __future__ import annotations

import re
from typing import List, Optional

import pymupdf

from ..facts import Schedule, VentilationRow

_NUM = re.compile(r"^-?\d[\d,]*\.?\d*$")


def reread(doc: pymupdf.Document, schedule: Schedule) -> Optional[List[List[str]]]:
    """The schedule's cells, from a table extraction clipped to its box."""
    if schedule.bbox is None:
        return None
    clip = pymupdf.Rect(schedule.bbox)
    tables = doc[schedule.page].find_tables(clip=clip).tables
    if not tables:
        return None
    best = max(tables, key=lambda t: abs(pymupdf.Rect(t.bbox) & clip))
    return [[" ".join((c or "").split()) for c in row] for row in best.extract()]


def _num(cell: str) -> Optional[float]:
    text = (cell or "").replace(",", "").strip()
    return float(text) if _NUM.match(text) else None


def _column(headers: List[str], *tests) -> Optional[int]:
    for i, h in enumerate(headers):
        u = h.upper()
        if all(t(u) for t in tests):
            return i
    return None


def _header_and_data(cells: List[List[str]], schedule: Schedule):
    """(upper-cased headers, data rows), or None when no header can be placed."""
    head = next((i for i, row in enumerate(cells[:4])
                 if any(c.upper() in ("ROOM", "ZONE", "SPACE") for c in row)
                 and any(c.upper().startswith("AREA") for c in row)), None)
    if head is not None:
        return [c.upper() for c in cells[head]], cells[head + 1:]
    if schedule.columns and len(cells[0]) == len(schedule.columns):
        # A clip on the table's own edge can leave the header row out; the
        # whole-page read already recovered it, cell for cell.
        return [c.upper() for c in schedule.columns], cells
    return None


def ventilation_total(doc: pymupdf.Document, schedule: Schedule):
    """(area, persons, CFM) from the table's TOTAL row, or None.

    The whole-page read used to supply these only when it merged the TOTAL
    row's cells into one; on a table it reads cleanly, the numbers are in their
    own columns and this is where they are.
    """
    cells = reread(doc, schedule)
    split = _header_and_data(cells, schedule) if cells else None
    if split is None:
        return None
    h, data = split
    area = _column(h, lambda u: u.startswith("AREA"), lambda u: "OUTDOOR" not in u)
    persons = _column(h, lambda u: "PERSONS" in u or u == "PEOPLE", lambda u: "CFM" not in u)
    total = _column(h, lambda u: "TOTAL" in u, lambda u: "CFM" in u)
    for row in data:
        if row and row[0].strip().upper().startswith("TOTAL") and total is not None:
            def at(i):
                return _num(row[i]) if i is not None and i < len(row) else None
            if at(total) is not None:
                return at(area), at(persons), at(total)
    return None


def ventilation_rows(doc: pymupdf.Document, schedule: Schedule) -> Optional[List[VentilationRow]]:
    """One row per room of an outdoor-air calculation table, or None.

    None when the table cannot be read as one — no header row naming a room
    and an area, or no numbers under it — so the caller can say so rather
    than hand a rule a partial table.
    """
    cells = reread(doc, schedule)
    if not cells:
        return None
    split = _header_and_data(cells, schedule)
    if split is None:
        return None
    h, data = split
    room = _column(h, lambda u: u in ("ROOM", "ZONE", "SPACE"))
    area = _column(h, lambda u: u.startswith("AREA"), lambda u: "OUTDOOR" not in u)
    dens = _column(h, lambda u: "DENSITY" in u or "#/1000" in u)
    rp = _column(h, lambda u: "CFM/PERSON" in u or re.search(r"\bRP\b", u) is not None)
    ra = _column(h, lambda u: "CFM/SQFT" in u or "CFM/SF" in u or re.search(r"\bRA\b", u) is not None)
    persons = _column(h, lambda u: "PERSONS" in u or u == "PEOPLE", lambda u: "CFM" not in u)
    total = _column(h, lambda u: "TOTAL" in u, lambda u: "CFM" in u)
    if room is None or area is None or total is None:
        return None

    def at(row, i):
        return _num(row[i]) if i is not None and i < len(row) else None

    rows: List[VentilationRow] = []
    for row in data:
        name = row[room].strip() if room < len(row) else ""
        if not name or name.upper().startswith(("TOTAL", "NOTE")):
            break
        rows.append(VentilationRow(name, at(row, area), at(row, dens), at(row, rp),
                                   at(row, ra), at(row, persons), at(row, total)))
    return rows or None
