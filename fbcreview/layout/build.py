"""Building a page's layout: words → segments → pairs.

Measured on the real Sculpted set before any threshold here was chosen:

* PyMuPDF's (block, line) grouping is the drafter's text entity. `OCCUPANT
  LOAD` and `70` on G-1 are two entities; `TYPE OF CONSTRUCTION:` and `III-B`
  on G-0 are two entities; each grid cell is its own. So a segment starts as one
  PyMuPDF line.
* Inside one PyMuPDF line the gap between words is 0.24 of the text height at
  the median and 0.28 at the 99th percentile (12,995 gaps). `JOIN_EMS` sits
  well clear of that: a wider gap is a column break, not a space.

Everything below is geometry and a small vocabulary of what a value looks like.
Nothing here knows what an occupant load is — that is `fbcreview.read`.
"""
from __future__ import annotations

import re
from bisect import bisect_left, bisect_right
from collections import defaultdict
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

import pymupdf

from .model import (INLINE, ROW, STACKED, Box, PageLayout, Pair, Segment, Word,
                    union)

#: A gap wider than this many text heights inside a line is a column break.
JOIN_EMS = 0.6
#: Two segments are on one line when their vertical extents overlap by at least
#: this share of the smaller height.
SAME_LINE = 0.5
#: A row's next value sits within this share of the page width of the last one.
ROW_REACH = 0.10
#: A stacked value starts within this many text heights below its label…
STACK_EMS = 2.6
#: …and within this many heights of the label's left edge (or overlaps it).
ALIGN_EMS = 1.5
#: A value runs on to the next line when that line sits within this many
#: heights below — `ULTIMATE: 170 MPH` / `NOMINAL: 132 MPH` under one label.
CONT_EMS = 0.8
#: How far above a label to look for the heading that governs it, in heights.
CONTEXT_EMS = 7.0
#: A block title is at least this much taller than the text it heads.
TITLE_RATIO = 1.4

# ── what a value looks like ─────────────────────────────────────────────────
_NUMERIC = re.compile(r"""^[±+\-]?\d[\d,]*(?:\.\d+)?(?:["'”’]|%)?$""")
_DIMENSION = re.compile(r"""^\d+\s*'\s*-?\s*\d*(?:\s+\d+/\d+)?\s*"?$|^\d+/\d+"?$|^\d+'-\d+"?$""")
_UNITS = {"SF", "S.F.", "SQ", "SQ.", "FT", "FT.", "FEET", "LF", "IN", "IN.", "MPH", "CFM",
          "PSF", "KVA", "A", "AMPS", "V", "%", "GSF", "NSF", "OCC", "PERSONS", "PEOPLE",
          "DOOR(S)", "DOORS", "EXITS", "(DOORS)", "(GROSS)", "(NET)", "(ENCLOSED)",
          "HR", "HOUR", "HOURS", "STORY", "STORIES"}
#: Word answers a code block prints without a number. Kept short on purpose:
#: a plain label only takes a value that is a number or one of these, so a
#: neighbouring table's first column is never read as an answer.
VALUE_WORDS = {
    "YES", "NO", "NONE", "N/A", "NA", "N.A.", "----", "---", "--", "-",
    "SPRINKLERED", "NON-SPRINKLERED", "UNSPRINKLERED", "FULLY", "PARTIAL", "PARTIALLY",
    "ASSEMBLY", "BUSINESS", "MERCANTILE", "EDUCATIONAL", "FACTORY", "INDUSTRIAL",
    "STORAGE", "RESIDENTIAL", "INSTITUTIONAL", "UTILITY", "HAZARDOUS",
    "ENCLOSED", "PARTIALLY ENCLOSED", "OPEN",
    "I", "II", "III", "IV", "V", "B", "C", "D",
    "SEPARATED", "NON-SEPARATED", "NONSEPARATED",
    # The function-of-space column of an occupant-load table (Table 1004.5).
    "ACCESSORY", "OFFICE", "RETAIL", "EXERCISE", "CLASSROOM", "KITCHEN",
    "WAREHOUSE", "CIRCULATION",
}
_TYPE = re.compile(r"^(?:TYPE\s+)?(?:I{1,3}|IV|V)\s*-?\s*[AB]?(?:\s*\(.*\))?$", re.I)
_GROUP = re.compile(r"^(?:GROUP\s+)?[ABEFHIMRSU](?:\s*-\s*\d)?$|^\([ABEFHIMRSU]-?\d?\)$", re.I)


def _tokens(text: str) -> List[str]:
    return text.split()


def value_token(tok: str) -> bool:
    t = tok.upper().strip(",;")
    return bool(_NUMERIC.match(t) or _DIMENSION.match(t) or t in _UNITS
                or t in VALUE_WORDS or _GROUP.match(t))


def value_like(text: str) -> bool:
    """Could this segment be an answer printed against a plain label?"""
    t = text.strip()
    if not t or t.endswith(":"):
        return False
    up = t.upper()
    if up in VALUE_WORDS or _TYPE.match(up) or _GROUP.match(up):
        return True
    toks = _tokens(up)
    if any(ch.isdigit() for ch in up) and len(toks) <= 6:
        return True
    # "ASSEMBLY (A-3)", "SPRINKLERED, NFPA 13": led by a value word, short.
    return len(toks) <= 4 and toks[0].strip(",;") in VALUE_WORDS


def label_like(text: str) -> bool:
    t = text.strip().rstrip(":?").strip()
    letters = sum(ch.isalpha() for ch in t)
    if letters < 2:
        return False
    return not value_like(t) or text.strip().endswith((":", "?"))


def marked(text: str) -> bool:
    return text.rstrip().endswith((":", "?"))


# ── words and segments ──────────────────────────────────────────────────────
def page_words(page: pymupdf.Page) -> List[Word]:
    out: List[Word] = []
    for x0, y0, x1, y1, text, block, line, _n in page.get_text("words"):
        t = text.strip()
        if t:
            out.append(Word(x0, y0, x1, y1, t, (block, line)))
    return out


def _vertical_overlap(a, b) -> float:
    return min(a[3], b[3]) - max(a[1], b[1])


def _same_line(a: Segment, b: Segment) -> bool:
    return _vertical_overlap(a.box, b.box) >= SAME_LINE * min(a.h, b.h)


def segments_of(words: Sequence[Word]) -> List[Segment]:
    """Words grouped the way the drafter placed them."""
    groups: Dict[Tuple[int, int], List[Word]] = defaultdict(list)
    for w in words:
        groups[w.group].append(w)

    pieces: List[Segment] = []
    for ws in groups.values():
        ws = sorted(ws, key=lambda w: w.x0)
        run = [ws[0]]
        for w in ws[1:]:
            prev = run[-1]
            gap = w.x0 - prev.x1
            if gap > JOIN_EMS * min(w.h, prev.h) or _vertical_overlap(prev.box, w.box) < \
                    SAME_LINE * min(w.h, prev.h):
                pieces.append(Segment(run))
                run = [w]
            else:
                run.append(w)
        pieces.append(Segment(run))

    # Text written one word per object (OCR overlays, some plotters) comes back
    # from PyMuPDF as separate lines. Join neighbours that sit a space apart on
    # one baseline at one size — never across a column gap.
    pieces.sort(key=lambda s: (round(s.yc, 0), s.x0))
    merged: List[Segment] = []
    by_row: Dict[int, List[Segment]] = defaultdict(list)
    for s in pieces:
        key = int(s.yc // 4)
        partner = None
        for k in (key - 1, key, key + 1):
            for cand in by_row.get(k, []):
                gap = s.x0 - cand.x1
                if (-0.5 <= gap <= JOIN_EMS * min(s.h, cand.h) and _same_line(cand, s)
                        and max(s.h, cand.h) <= 1.3 * min(s.h, cand.h)):
                    partner = cand
                    break
            if partner:
                break
        if partner is not None:
            partner.words.extend(s.words)
            continue
        merged.append(s)
        by_row[key].append(s)
    for i, s in enumerate(merged):
        s.words.sort(key=lambda w: w.x0)
        s.index = i
    return merged


# ── a spatial index, so a dense sheet stays linear ──────────────────────────
class _Grid:
    CELL = 64.0

    def __init__(self, segs: Sequence[Segment]):
        self.cells: Dict[Tuple[int, int], List[Segment]] = defaultdict(list)
        for s in segs:
            for cx in range(int(s.x0 // self.CELL), int(s.x1 // self.CELL) + 1):
                for cy in range(int(s.y0 // self.CELL), int(s.y1 // self.CELL) + 1):
                    self.cells[(cx, cy)].append(s)

    def query(self, x0, y0, x1, y1) -> List[Segment]:
        seen: Set[int] = set()
        out: List[Segment] = []
        for cx in range(int(x0 // self.CELL), int(x1 // self.CELL) + 1):
            for cy in range(int(y0 // self.CELL), int(y1 // self.CELL) + 1):
                for s in self.cells.get((cx, cy), ()):
                    if s.index not in seen:
                        seen.add(s.index)
                        out.append(s)
        return out


# ── pairs ───────────────────────────────────────────────────────────────────
_SPLIT = re.compile(r"^(?P<label>.*?[A-Za-z)\]])\s*(?P<mark>[:?])(?:\s+|$)(?P<value>.*)$")


def split_inline(text: str) -> Optional[Tuple[str, str]]:
    """`LABEL: value` in one text object → (label, value). Value may be "".

    The mark has to follow a letter or a closing bracket and be followed by a
    space, so `1:2` (a slope), `12:30` and `±0.18` are never split.
    """
    m = _SPLIT.match(text.strip())
    if not m:
        return None
    label = m.group("label").strip()
    if sum(ch.isalpha() for ch in label) < 2:
        return None
    return label, m.group("value").strip()


def split_trailing(text: str) -> Optional[Tuple[str, str]]:
    """`OCCUPANT LOAD 70`, `SPRINKLERED YES` — a label and a trailing value in
    one text object with nothing but a space between them."""
    toks = _tokens(text)
    if len(toks) < 2:
        return None
    cut = len(toks)
    for i in range(len(toks) - 1, 0, -1):
        if value_token(toks[i]):
            cut = i
        else:
            break
    if cut == len(toks):
        return None
    label = " ".join(toks[:cut])
    # The value must carry something other than a unit: `TOTAL SF` is a label.
    if not any(value_token(t) and t.upper() not in _UNITS for t in toks[cut:]):
        return None
    # A label is words. `SPRINKLERED YES` has one that doubles as an answer
    # word elsewhere, and that is fine; `250 LF` has none.
    if sum(ch.isalpha() for ch in label) < 2 or any(ch.isdigit() for ch in label):
        return None
    return label, " ".join(toks[cut:])


def _right_of(seg: Segment, grid: _Grid, width: float) -> List[Segment]:
    reach = ROW_REACH * width
    cands = grid.query(seg.x1, seg.y0 - seg.h, seg.x1 + reach * 3, seg.y1 + seg.h)
    return sorted((c for c in cands if c.index != seg.index and c.x0 >= seg.x1 - 1.0
                   and _same_line(seg, c)), key=lambda c: c.x0)


def _below(seg: Segment, grid: _Grid) -> List[Segment]:
    h = seg.h
    cands = grid.query(seg.x0 - ALIGN_EMS * h, seg.y1, seg.x1 + ALIGN_EMS * h,
                       seg.y1 + STACK_EMS * h + h)
    out = []
    for c in cands:
        if c.index == seg.index or c.y0 < seg.y1 - 0.25 * h:
            continue
        if c.y0 - seg.y1 > STACK_EMS * h:
            continue
        aligned = abs(c.x0 - seg.x0) <= ALIGN_EMS * h
        overlap = min(c.x1, seg.x1) - max(c.x0, seg.x0)
        if aligned or overlap >= 0.5 * min(c.x1 - c.x0, seg.x1 - seg.x0):
            out.append(c)
    return sorted(out, key=lambda c: (c.y0, abs(c.x0 - seg.x0)))


def _bare_label(text: str) -> bool:
    """`REQUIRED:`, `OCCUPANCY:` — a label with nothing after its mark."""
    sp = split_inline(text)
    return bool(sp) and not sp[1]


def _plain_label(text: str) -> bool:
    """An unmarked label a row value may be read against.

    Short, and no digits outside a cited section: `OCCUPANT LOAD` and
    `EGRESS WIDTH FACTOR` qualify; `2023 FLORIDA BUILDING CODE 8TH EDITION -
    BUILDING` is an entry in a list of codes, not a question, and must not take
    the `III-B` printed to its right as its answer.
    """
    t = text.strip()
    if marked(t) or not label_like(t):
        return False
    bare = re.sub(r"\(\s*\d[\d.]*\s*\)", "", t)
    return len(_tokens(bare)) <= 7 and not any(ch.isdigit() for ch in bare)


def _column_header(value: Segment, grid: _Grid) -> Optional[Segment]:
    """The segment heading a value's column: walk up through the other rows'
    values to the first label above (`REQUIRED:`, `AREA`). None past a gap."""
    h = value.h
    top = value
    for _ in range(60):
        cands = grid.query(top.x0 - h, top.y0 - 4 * h, top.x1 + h, top.y0)
        above = [c for c in cands if c.y1 <= top.y0 + 0.25 * h and c.index != top.index
                 and min(c.x1, top.x1) - max(c.x0, top.x0)
                 >= 0.3 * min(c.x1 - c.x0, top.x1 - top.x0)]
        if not above:
            return None
        nearest = max(above, key=lambda c: c.y1)
        if top.y0 - nearest.y1 > 3.5 * h or nearest.h >= TITLE_RATIO * h:
            return None
        if not value_like(nearest.text) and label_like(nearest.text):
            return nearest
        top = nearest
    return None


def _row_values(label: Segment, grid: _Grid, width: float, taken: Set[int],
                first_any: bool) -> List[Segment]:
    """Values printed to a label's right on its line.

    The first must look like an answer (or, after a colon, be short text).
    Another is only taken when both sit under column headers on one header
    row — `REQUIRED:` / `PROVIDED:` — so a grid reads across and a neighbouring
    table's cells do not.
    """
    values: List[Segment] = []
    first_head: Optional[Segment] = None
    widest = 0.0
    prev = label
    for c in _right_of(label, grid, width):
        gap = c.x0 - prev.x1
        if c.index in taken or gap > ROW_REACH * width:
            break
        if _bare_label(c.text):
            break
        if not values:
            ok = value_like(c.text) or (first_any and not marked(c.text)
                                        and len(_tokens(c.text)) <= 8
                                        and gap <= 4 * label.h)
            if not ok:
                break
            values.append(c)
            first_head = _column_header(c, grid)
        else:
            if not value_like(c.text) or first_head is None:
                break
            if gap > max(2.5 * widest, 6 * label.h):
                break
            head = _column_header(c, grid)
            if head is None or abs(head.yc - first_head.yc) > 1.5 * head.h:
                break
            values.append(c)
        widest = max(widest, gap)
        prev = c
    return values


def _wrapped_label_head(seg: Segment, grid: _Grid) -> bool:
    """Is this the first line of a label wrapped onto two lines?

    `INTERNAL PRESSURE` over `COEFFICIENTS:` — a plain label line with the
    marked remainder directly beneath it. It is never the continuation of the
    value above it.
    """
    if not _plain_label(seg.text):
        return False
    for c in _below(seg, grid):
        if c.y0 - seg.y1 > CONT_EMS * seg.h:
            return False
        return _bare_label(c.text)
    return False


def _row_label_left(value: Segment, grid: _Grid, width: float) -> bool:
    """Is this value already the answer of a label printed to its left?

    A grid's column header (`REQUIRED:`) sits directly above the first value in
    its column, which is exactly where a stacked answer would sit. The row label
    to the value's left (`MAX TRAVEL DISTANCE (1017.2):`) is what says which one
    the drafter meant: a value with a row label beside it belongs to the row.
    """
    reach = ROW_REACH * width
    here = value
    for _ in range(12):
        cands = grid.query(here.x0 - reach, here.y0 - here.h, here.x0, here.y1 + here.h)
        left = [c for c in cands if c.index != here.index and c.x1 <= here.x0 + 1.0
                and _same_line(c, here) and here.x0 - c.x1 <= reach]
        if not left:
            return False
        nearest = max(left, key=lambda c: c.x1)
        # Only a marked row label (`MAX TRAVEL DISTANCE (1017.2):`) owns a
        # value against a header above it. A plain phrase to the left is as
        # likely another table's cell — G-0's abbreviation list puts `UNDER
        # COUNTER` on the baseline of `1,436 SF`, which belongs to `BUILDING
        # AREA:` directly above it.
        if _bare_label(nearest.text):
            return True
        if not value_like(nearest.text):
            return False
        here = nearest                    # another value in the same row: keep walking
    return False


def _stacked_value(label: Segment, grid: _Grid, taken: Set[int],
                   width: float) -> Optional[Segment]:
    for c in _below(label, grid):
        if c.index in taken:
            return None
        if _bare_label(c.text):
            return None                                   # the next label, not an answer
        if _row_label_left(c, grid, width):
            return None                                   # a grid value under its header
        return c
    return None


def _headings(label: Segment, grid: _Grid, in_pair: Set[int]) -> Tuple[List[str], str]:
    """Headings over a label in its column: the nearest sub-heading(s) and the
    block title. Walks up through other rows of the same block."""
    h = label.h
    context: List[str] = []
    title = ""
    top = label
    for _ in range(60):
        cands = grid.query(label.x0 - 2 * h, top.y0 - CONTEXT_EMS * h, label.x1 + 2 * h, top.y0)
        above = [c for c in cands if c.y1 <= top.y0 + 0.25 * h and c.index != top.index
                 and (abs(c.x0 - label.x0) <= 2 * h
                      or min(c.x1, label.x1) - max(c.x0, label.x0) > 0)]
        if not above:
            break
        nearest = max(above, key=lambda c: c.y1)
        if top.y0 - nearest.y1 > CONTEXT_EMS * h:
            break
        if nearest.h >= TITLE_RATIO * h and label_like(nearest.text):
            title = nearest.text.rstrip(":").strip()
            break
        if nearest.index not in in_pair and label_like(nearest.text) and len(context) < 2:
            context.append(nearest.text.rstrip(":?").strip())
        top = nearest
    return context, title


def pairs_of(segs: Sequence[Segment], page: int, width: float) -> List[Pair]:
    grid = _Grid(segs)
    pairs: List[Pair] = []
    as_value: Set[int] = set()
    as_label: Set[int] = set()
    inner_pair: Dict[int, Pair] = {}
    ordered = sorted(segs, key=lambda s: (s.y0, s.x0))

    def claim(pair: Pair, label_segs: Iterable[Segment], values: Iterable[Segment]) -> None:
        pairs.append(pair)
        for s in label_segs:
            as_label.add(s.index)
        for v in values:
            as_value.add(v.index)

    # 1. inline — label and value in one text object
    bare: List[Segment] = []
    for s in ordered:
        sp = split_inline(s.text)
        if sp:
            if sp[1]:
                p = Pair(sp[0], [sp[1]], INLINE, page, s.box, [s.box])
                claim(p, [s], [])
                inner_pair[s.index] = p
            else:
                bare.append(s)
            continue
        tr = split_trailing(s.text)
        if tr:
            p = Pair(tr[0], [tr[1]], INLINE, page, s.box, [s.box])
            claim(p, [s], [])
            inner_pair[s.index] = p

    # 2. `LABEL:` with nothing after the mark — its answer is either on the
    #    line beneath (stacked) or to its right (a grid row). Whichever sits
    #    nearer, measured in text heights, is the one the drafter meant.
    for s in bare:
        if s.index in as_value:
            continue
        taken = as_value | {x.index for x in bare if x.index != s.index}
        below = _stacked_value(s, grid, as_value, width)
        right = _row_values(s, grid, width, taken, first_any=True)
        use_stack = below is not None and (
            not right or (below.y0 - s.y1) <= (right[0].x0 - s.x1))
        label_text = s.text.rstrip(":?").strip()
        if use_stack:
            values = [below]
            last = below
            while True:
                nxt = [c for c in _below(last, grid) if c.index not in as_value
                       and c.y0 - last.y1 <= CONT_EMS * last.h
                       and abs(c.x0 - below.x0) <= ALIGN_EMS * below.h]
                if not nxt or _bare_label(nxt[0].text) or _wrapped_label_head(nxt[0], grid):
                    break
                values.append(nxt[0])
                last = nxt[0]
            label_segs = [s]
            # A label wrapped onto two lines: `INTERNAL PRESSURE` / `COEFFICIENTS:`.
            above = [c for c in grid.query(s.x0 - s.h, s.y0 - 2 * s.h, s.x1 + s.h, s.y0)
                     if c.y1 <= s.y0 + 0.25 * s.h and s.y0 - c.y1 <= CONT_EMS * s.h
                     and abs(c.x0 - s.x0) <= ALIGN_EMS * s.h and c.index not in as_value
                     and c.index not in as_label and not marked(c.text)
                     and _plain_label(c.text)]
            if above:
                head = max(above, key=lambda c: c.y1)
                label_text = f"{head.text} {label_text}"
                label_segs.insert(0, head)
            p = Pair(label_text, [v.text for v in values], STACKED, page,
                     union([x.box for x in label_segs]), [v.box for v in values])
            claim(p, label_segs, values)
            for v in values:
                # `BASIC WIND SPEED:` / `ULTIMATE: 170 MPH` — the value line is
                # an inline pair of its own, and this label is its context.
                inner = inner_pair.get(v.index)
                if inner is not None:
                    inner.context.insert(0, label_text)
        elif right:
            p = Pair(label_text, [v.text for v in right], ROW, page, s.box,
                     [v.box for v in right])
            p.headers = [_header_text(v, grid) for v in right]
            claim(p, [s], right)

    # 3. an unmarked label with its answer to the right: `OCCUPANT LOAD   70`
    for s in ordered:
        if s.index in as_value or s.index in as_label or not _plain_label(s.text):
            continue
        right = _row_values(s, grid, width, as_value | as_label, first_any=False)
        if not right:
            continue
        p = Pair(s.text.strip(), [v.text for v in right], ROW, page, s.box,
                 [v.box for v in right])
        p.headers = [_header_text(v, grid) for v in right]
        claim(p, [s], right)

    # 4. the headings each label sits under
    in_pair = as_label | as_value
    by_box = {s.box: s for s in segs}
    for p in pairs:
        seg = by_box.get(p.label_box) or _segment_at(segs, p.label_box)
        if seg is None:
            continue
        ctx, title = _headings(seg, grid, in_pair)
        for c in ctx:
            if c not in p.context:
                p.context.append(c)
        p.heading = p.heading or title
    return pairs


def _header_text(value: Segment, grid: _Grid) -> str:
    """The column header's text, with a header wrapped onto two lines joined —
    `AREA PER` / `OCCUPANT` is one header, `AREA PER OCCUPANT`."""
    head = _column_header(value, grid)
    if head is None:
        return ""
    text = head.text.rstrip(":?").strip()
    cands = grid.query(head.x0 - head.h, head.y0 - 2 * head.h, head.x1 + head.h, head.y0)
    above = [c for c in cands if c.index != head.index and c.y1 <= head.y0 + 0.25 * head.h
             and head.y0 - c.y1 <= CONT_EMS * head.h
             and min(c.x1, head.x1) - max(c.x0, head.x0) > 0
             and label_like(c.text) and not value_like(c.text)]
    if above:
        top = max(above, key=lambda c: c.y1)
        text = f"{top.text.rstrip(':?').strip()} {text}"
    return text


def _segment_at(segs: Sequence[Segment], box: Box) -> Optional[Segment]:
    for s in segs:
        if abs(s.x0 - box[0]) < 0.5 and abs(s.y1 - box[3]) < 0.5:
            return s
    for s in segs:
        if abs(s.x0 - box[0]) < 0.5 and s.y0 >= box[1] - 0.5 and s.y1 <= box[3] + 0.5:
            return s
    return None


def reading_lines(segs: Sequence[Segment]) -> List[Segment]:
    """Segments in reading order: top to bottom, left to right within a line."""
    return sorted(segs, key=lambda s: (round(s.yc / max(s.h, 1.0)), s.x0))


def page_layout(page: pymupdf.Page, words: Optional[List[Word]] = None) -> PageLayout:
    ws = words if words is not None else page_words(page)
    segs = segments_of(ws) if ws else []
    mb = page.mediabox
    pairs = pairs_of(segs, page.number, mb.width) if segs else []
    return PageLayout(page.number, mb.width, mb.height, ws, segs, pairs,
                      reading_lines(segs))
