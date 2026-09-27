"""Ruled schedule extraction via PyMuPDF's table finder, plus row repair.

`find_tables` recovers the door schedule, RTU schedule, panel schedule and load
calculations from the test set cleanly.  Run over the whole page it does NOT
always recover the outdoor-air table cleanly: two source rows collapse into one
cell (`'RECEPTION MAT STUDIO' | '164 994'`).  The same call clipped to the
table's own box separates them, which is what `fbcreview/read/tables.py` does
with the `bbox` recorded here; `split_merged_row` is the fallback for a table
that re-read cannot parse.
"""
from __future__ import annotations
import re
from typing import List, Optional
import pymupdf
from ..facts import Schedule, ScheduleRow


def _clean(c) -> str:
    return re.sub(r"\s+", " ", (c or "")).strip()


def find_schedule(doc: pymupdf.Document, pno: int, title_contains: str,
                  sheet: str, name: Optional[str] = None) -> Optional[Schedule]:
    """`name` is what the schedule is filed under, when that differs from the
    title searched for. A set that prints "DOOR SCHEDULE" and one that prints
    "DOOR AND FRAME SCHEDULE" carry the same table, and the rules ask for it
    by one name — so the title is how it is found and `name` is how it is
    stored. Omitted, the two are the same, which is the old behaviour.
    """
    up = title_contains.upper()
    page_area = abs(doc[pno].rect.get_area())
    cands = []
    for t in doc[pno].find_tables().tables:
        ex = t.extract()
        if not ex:
            continue
        blob = " ".join(_clean(c) for r in ex[:2] for c in r).upper()
        if up not in blob:
            continue
        area = abs(pymupdf.Rect(t.bbox).get_area())
        if area > 0.30 * page_area:      # the page frame, not a schedule
            continue
        cands.append((area, t, ex))
    cands.sort(key=lambda c: c[0])
    for _area, t, ex in cands[:1]:
        # header = first row whose cells are mostly non-empty short labels
        hdr_i = 0
        for i, r in enumerate(ex[:3]):
            filled = [c for c in r if _clean(c)]
            if len(filled) >= max(2, len(r) // 2):
                hdr_i = i
                break
        cols = [_clean(c) for c in ex[hdr_i]]
        cols, spilled = split_merged_header(cols)
        rows: List[ScheduleRow] = []
        if spilled:
            rows.append(ScheduleRow(spilled[0], {c or f"col{i}": v
                                                 for i, (c, v) in enumerate(zip(cols, spilled))}))
        for r in ex[hdr_i + 1:]:
            cells = [_clean(c) for c in r]
            if not any(cells):
                continue
            mark = cells[0]
            rows.append(ScheduleRow(mark, {c or f"col{i}": v
                                           for i, (c, v) in enumerate(zip(cols, cells))}))
        return Schedule(name or title_contains.upper(), sheet, pno, cols, rows,
                        title_contains, bbox=tuple(t.bbox))
    return None


_NUMPAIR = re.compile(r"^(-?[\d,.]+)\s+(-?[\d,.]+)$")


def split_merged_row(row: ScheduleRow) -> Optional[List[ScheduleRow]]:
    """Repair the common 'two source rows merged into one cell' failure.

    Fires only when EVERY populated cell splits into the same number of parts —
    otherwise the alignment is a guess and we return None so the caller abstains.
    """
    parts_per_cell = {}
    for k, v in row.fields.items():
        if not v:
            continue
        toks = v.split()
        parts_per_cell[k] = toks
    counts = {len(v) for v in parts_per_cell.values()}
    if len(counts) != 1:
        return None
    n = counts.pop()
    if n < 2:
        return None
    out = []
    for i in range(n):
        out.append(ScheduleRow(parts_per_cell[list(parts_per_cell)[0]][i],
                               {k: v[i] for k, v in parts_per_cell.items()}))
    return out


_DATAISH = re.compile(r"^(\d+\s*'|\d+(\.\d+)?\s*\"|\d+[A-Z]?$|EXISTING$|[A-Z]$|\d+\s+\d+/\d+)")


def split_merged_header(cols: List[str]):
    """Repair 'header row and first data row merged into one row'.

    Each cell becomes LABEL + DATA where DATA is the trailing run of tokens that
    look like values. Only applied when at least half the populated cells split
    the same way — otherwise the alignment is a guess and we leave it alone,
    losing one row rather than inventing one.
    """
    labels, data, splits = [], [], 0
    for c in cols:
        toks = c.split()
        cut = len(toks)
        for i, t in enumerate(toks):
            if i and _DATAISH.match(t.upper()):
                cut = i
                break
        lab = " ".join(toks[:cut]).strip()
        val = " ".join(toks[cut:]).strip()
        labels.append(lab or c)
        data.append(val)
        if val:
            splits += 1
    populated = sum(1 for c in cols if c.strip())
    if populated and splits >= populated / 2:
        return labels, data
    return cols, None
