"""Label/value extraction from code-analysis blocks, vector or raster.

`blocks.code_data_block` handles the common Florida layout where every row
carries a parenthesised section number — `MAX TRAVEL DISTANCE (1017.2): 250 LF`.
That is the best case and the rule corpus keys on it.

Plenty of offices do not draw it that way.  Phoenix Associates' sheets put the
citation in a BANNER above each table — `TABLES 504.3, 504.4 & 506.2 < > FLORIDA
BUILDING CODE 7TH EDITION` — and then list bare `LABEL: value` rows underneath.
The citation is still there and still the most stable token on the sheet; it is
just one level up.  This module reads that shape.

It works from a word list rather than from a page, so the same code serves a
vector sheet and an OCR'd raster sheet with no branch.

Three things `blocks.labelled_values` cannot do and this can:

* **Word values.** `labelled_values` takes the value to be the trailing run of
  numeric / `YES` / `NO` / dimensional tokens, so it returns nothing at all for
  `OCCUPANCY: BUSINESS`, `CONSTRUCTION TYPE: TYPE II-B NON-COMBUSTIBLE
  NON-RATED` or `MIXED OCCUPANCY: MULTIPLE - SEPARATED PER TABLE 508.4` — which
  on this kind of block is most of the rows.
* **Two pairs on one visual row.** `OCCUPANCY: BUSINESS   MIXED OCCUPANCY? NO`
  is one row and two answers.  See `split_pairs`.
* **The banner citation.** A row inherits the section its table is drawn under,
  so provenance survives even though the drafter cited once instead of per row.

Nothing here invents a value.  A label whose answer could not be read comes back
with an empty value and `unanswered=True`, which is what lets a rule abstain
with a precise reason instead of going quiet — or, worse, inferring the answer
from the label text.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import pymupdf

from ..confidence import HIGH
from .blocks import block_region

#: (x0, y0, x1, y1, text, strip, line, word). Field 5 is the COLUMN STRIP —
#: which of the side-by-side tables on the sheet this word belongs to — where
#: PyMuPDF puts its own block number, which is not useful here. `column_strips`
#: below fills it for a page; the raster path fills it from the vertical rules
#: it finds while segmenting the region.
Word = Tuple[float, float, float, float, str, int, int, int]

# "TABLE 602", "TABLES 504.3, 504.4 & 506.2", "SECTION 506 & 507", "SECTION 302"
_BANNER = re.compile(
    r"\b(?:TABLES?|SECTIONS?)\s+((?:\d{3,4}(?:\.\d+)*)(?:\s*(?:,|&|AND)\s*\d{3,4}(?:\.\d+)*)*)",
    re.I)
_EDITION = re.compile(r"\b(\d)(?:ST|ND|RD|TH)\s+EDITION\b", re.I)
_LEADING_JUNK = re.compile(r"^[\|\[\]\{\}<>_\-—–\.,;:\s]+")
_TRAILING_JUNK = re.compile(r"[\|\[\]\{\}<>_\s]+$")

#: What ends a label. A colon is the usual mark; drafters write `MIXED
#: OCCUPANCY?` with a question mark instead, and mean the same thing.
_MARKS = (":", "?")


def clean(s: str) -> str:
    """Strip the punctuation OCR hallucinates around ruled cell borders.

    Tesseract reads a table's vertical rule as `|`, `[`, `]` or `I` roughly as
    often as it ignores it, so `[BUSINESS` and `BUSINESS` are the same value and
    a comparison that does not know that will report a phantom conflict.
    """
    s = _LEADING_JUNK.sub("", s or "")
    s = _TRAILING_JUNK.sub("", s)
    return s.strip()


@dataclass
class FormRow:
    label: str
    value: str
    y: float
    strip: int
    rect: pymupdf.Rect
    confidence: str = HIGH
    banner: str = ""                 # nearest citation banner above this row
    sections: List[str] = field(default_factory=list)
    unanswered: bool = False         # the sheet poses this label but no value was read
    sheet: str = ""
    page: int = 0

    @property
    def key(self) -> str:
        return re.sub(r"[^A-Z0-9 ]", "", self.label.upper()).strip()

    def as_meta(self) -> dict:
        """A JSON-safe record of the row, for `facts.meta`.

        Every field a reader needs to find this value on the sheet in thirty
        seconds: which sheet, which page, where on it, what the drafter cited
        above it, and how far to trust the reading.
        """
        return {
            "label": self.label,
            "value": self.value,
            "key": self.key,
            "sheet": self.sheet,
            "page": self.page,
            "rect": [round(v, 2) for v in tuple(self.rect)],
            "confidence": self.confidence,
            "banner": self.banner,
            "sections": list(self.sections),
            "unanswered": self.unanswered,
        }


# ── column strips ────────────────────────────────────────────────────────────
#
# Clustering rows on y alone is wrong whenever two tables are printed side by
# side, which on a code-data sheet is the normal case rather than the exception:
# the left table's row and the right table's row share a y and merge into one
# line of nonsense, and the left table's banner is then inherited by the right
# table's rows.
#
# The separator is a vertical gap that runs the full height of the block with no
# word crossing it. That much is easy. The trap is that a plain `LABEL   value`
# table has one of those too — the gutter between the label column and the value
# column — and splitting there puts every label in one strip and every value in
# another, so no pair is ever assembled again.
#
# What tells the two apart is what sits to the right of the gap: a second table
# asks its own questions, a value column does not. So a gap becomes a strip
# boundary only when the words between it and the next candidate gap carry their
# own label marks. Working left to right, on a sheet drawn as
# `[L-labels | L-values] [R-labels | R-values]`:
#
#     gap after L-labels  -> next segment is L-values, no marks  -> not a strip
#     gap after L-values  -> next segment is R-labels, marks     -> STRIP
#     gap after R-labels  -> next segment is R-values, no marks  -> not a strip
#
# which is the answer. A banner spanning its own table helps rather than hurts:
# it crosses that table's internal gutters and stops them being candidates at
# all, while leaving the gap between two tables clear.

#: A gap narrower than this many text heights is spacing inside one table, not a
#: separator between two. Deliberately low: a candidate is already a gap no word
#: in the whole block crosses, and the guard above — not the width — is what
#: decides, so a low floor only offers more real gutters to test.
_STRIP_GAP_EMS = 1.5

#: A strip has to be a table, not a stray token that drifted right of the gutter.
_STRIP_MIN_WORDS = 2


def _marked(words: Sequence[Word]) -> bool:
    """Does this run of words pose a question — i.e. carry its own labels?"""
    return any(w[4].rstrip().endswith(_MARKS) for w in words)


def column_strips(words: Sequence[Word], gap_ems: float = _STRIP_GAP_EMS
                  ) -> List[Word]:
    """Return `words` with field 5 rewritten to a column-strip index.

    Left to right, starting at 0. Words that were never separated by an accepted
    gap all land in strip 0, which is the single-table case and the safe default:
    `split_pairs` still recovers every pair on a merged row, only the banner
    attribution loses precision.
    """
    ws = sorted(words, key=lambda w: w[0])
    if len(ws) < 2:
        return [(w[0], w[1], w[2], w[3], w[4], 0, w[6], w[7]) for w in ws]

    heights = sorted(w[3] - w[1] for w in ws)
    em = heights[len(heights) // 2] or 1.0
    floor = gap_ems * em

    # Candidate gaps: x ranges no word overlaps anywhere in the block.
    candidates: List[Tuple[float, int]] = []      # (gap start x, index of first word right of it)
    running = ws[0][2]
    for i in range(1, len(ws)):
        if ws[i][0] - running > floor:
            candidates.append((running, i))
        running = max(running, ws[i][2])

    accepted: List[int] = []
    for n, (_x, i) in enumerate(candidates):
        end = candidates[n + 1][1] if n + 1 < len(candidates) else len(ws)
        segment = ws[i:end]
        if len(segment) >= _STRIP_MIN_WORDS and _marked(segment):
            accepted.append(i)

    out: List[Word] = []
    strip = 0
    boundaries = set(accepted)
    for i, w in enumerate(ws):
        if i in boundaries:
            strip += 1
        out.append((w[0], w[1], w[2], w[3], w[4], strip, w[6], w[7]))
    return out


# ── row assembly ─────────────────────────────────────────────────────────────

def cluster_rows(words: Sequence[Word], band: float = 6.0
                 ) -> List[Tuple[int, float, List[Word]]]:
    """Group words into visual rows, keyed by (strip, y).

    Clustering on y alone is wrong whenever two tables are printed side by side,
    which on a code-data sheet is the normal case rather than the exception.
    """
    buckets: Dict[Tuple[int, int], List[Word]] = {}
    for w in words:
        buckets.setdefault((int(w[5]), int(round(w[1] / band))), []).append(w)
    out = []
    for (strip, yb), ws in sorted(buckets.items()):
        out.append((strip, yb * band, sorted(ws, key=lambda t: t[0])))
    return out


def split_label_value(row: Sequence[Word], min_gap_ratio: float = 1.8
                      ) -> Optional[Tuple[str, str]]:
    """Split a row into label and value at its widest horizontal gap.

    More robust than "the value is the trailing numeric run", which fails on
    `OCCUPANCY: BUSINESS`, `CONSTRUCTION TYPE: TYPE II-B NON-COMBUSTIBLE` and
    every other row whose value is words rather than a number — which on this
    kind of block is most of them.
    """
    if len(row) < 2:
        return None
    gaps = []
    for i in range(len(row) - 1):
        gaps.append((row[i + 1][0] - row[i][2], i))
    gaps.sort(reverse=True)
    widest, at = gaps[0]
    if widest <= 0:
        return None

    # A colon is a stronger signal than whitespace when both are present.
    for i, w in enumerate(row[:-1]):
        if w[4].rstrip().endswith(":"):
            at = i
            break
    else:
        median = sorted(g for g, _ in gaps)[len(gaps) // 2] if gaps else 0.0
        if median > 0 and widest < min_gap_ratio * median:
            return None

    label = clean(" ".join(w[4] for w in row[:at + 1]).rstrip(":"))
    value = clean(" ".join(w[4] for w in row[at + 1:]))
    if not label or not value:
        return None
    return label, value


# ── banners ──────────────────────────────────────────────────────────────────

def banners(rows: Iterable[Tuple[int, float, List[Word]]]
            ) -> Dict[int, List[Tuple[float, str, List[str]]]]:
    """Citation banners per strip, as (y, text, [sections]), sorted by y."""
    out: Dict[int, List[Tuple[float, str, List[str]]]] = {}
    for strip, y, ws in rows:
        line = " ".join(w[4] for w in ws)
        m = _BANNER.search(line)
        if not m:
            continue
        secs = re.findall(r"\d{3,4}(?:\.\d+)*", m.group(1))
        out.setdefault(strip, []).append((y, line.strip(), secs))
    for k in out:
        out[k].sort()
    return out


def banner_for(bans: Dict[int, List[Tuple[float, str, List[str]]]],
               strip: int, y: float) -> Tuple[str, List[str]]:
    best: Tuple[str, List[str]] = ("", [])
    for by, text, secs in bans.get(strip, []):
        if by <= y:
            best = (text, secs)
        else:
            break
    return best


# ── the public entry point ───────────────────────────────────────────────────

def split_pairs(row: Sequence[Word]) -> List[Tuple[str, str, int, int]]:
    """Split a row into EVERY `label: value` pair it contains.

    A code-analysis table is often two columns of label/value printed inside one
    ruled box, so a single visual row carries two pairs:

        OCCUPANCY:  BUSINESS        MIXED OCCUPANCY?  NO

    Taking only the widest gap yields `OCCUPANCY = BUSINESS MIXED OCCUPANCY?`,
    which loses the second answer and corrupts the first. Colons (and the `?`
    that drafters use in place of one) mark where each label ends; the widest
    gap between two consecutive markers is where the previous value ends and the
    next label begins.

    Returns (label, value, first_word_index, last_word_index).
    """
    if len(row) < 2:
        return []
    # Include a mark on the LAST token. A label whose answer could not be read
    # is not the same as a label that is not there: the sheet asks
    # "MIXED OCCUPANCY?" and the answer sits in a column the OCR lost. Emitting
    # it with an empty value lets the rule abstain with a precise reason instead
    # of going silent — or, worse, inferring an answer from the label text.
    marks = [i for i, w in enumerate(row)
             if w[4].rstrip().endswith(_MARKS)]
    if not marks:
        lv = split_label_value(row)
        return [(lv[0], lv[1], 0, len(row) - 1)] if lv else []

    out: List[Tuple[str, str, int, int]] = []
    starts = [0]
    for a, b in zip(marks, marks[1:]):
        best, at = -1.0, a + 1
        for i in range(a + 1, b):
            gap = row[i + 1][0] - row[i][2]
            if gap > best:
                best, at = gap, i
        starts.append(at + 1)
    ends = starts[1:] + [len(row)]

    for mark, s0, e0 in zip(marks, starts, ends):
        label = clean(" ".join(w[4] for w in row[s0:mark + 1]).rstrip(":?"))
        value = clean(" ".join(w[4] for w in row[mark + 1:e0]))
        if label and len(label) >= 3 and len(value) <= 120:
            out.append((label, value, s0, max(s0, e0 - 1)))
    return out


def form_rows(words: Sequence[Word], confidence: str = HIGH,
              band: float = 6.0, sheet: str = "", page: int = 0) -> List[FormRow]:
    rows = cluster_rows(words, band)
    bans = banners(rows)
    # A banner is a citation, not an answer. Left in, it splits at its own widest
    # gap and enters the record as a label/value pair — on a two-table sheet,
    # literally one banner labelled with the other.
    is_banner = {(strip, y) for strip, ys in bans.items() for y, _t, _s in ys}
    out: List[FormRow] = []
    for strip, y, ws in rows:
        if (strip, y) in is_banner:
            continue
        text, secs = banner_for(bans, strip, y)
        for label, value, i0, i1 in split_pairs(ws):
            seg = ws[i0:i1 + 1] or ws
            out.append(FormRow(
                label=label, value=value, y=y, strip=strip,
                rect=pymupdf.Rect(min(w[0] for w in seg), min(w[1] for w in seg),
                                  max(w[2] for w in seg), max(w[3] for w in seg)),
                confidence=confidence, banner=text, sections=secs,
                unanswered=not value, sheet=sheet, page=page))
    return out


def form_block(doc: pymupdf.Document, pno: int, header: str, sheet: str = "",
               confidence: str = HIGH, width: Optional[float] = None,
               height: Optional[float] = None, left_pad: Optional[float] = None,
               ) -> List[FormRow]:
    """Every `label: value` pair printed under `header` on page `pno`.

    The window is the one `blocks.code_data_block` already reads — same region,
    different parser — so a sheet that carries both shapes is read once by each
    and neither has to guess where the other stopped.
    """
    page = doc[pno]
    region = block_region(page, header, width=width, height=height,
                          left_pad=left_pad, anchor="lowest")
    if region is None:
        return []
    words = [w for w in page.get_text("words")
             if pymupdf.Rect(w[0], w[1], w[2], w[3]).intersects(region)]
    if not words:
        return []
    return form_rows(column_strips(words), confidence=confidence,
                     sheet=sheet, page=pno)


def edition_from(words: Sequence[Word]) -> Optional[str]:
    """`FLORIDA BUILDING CODE 7TH EDITION` -> "fbc2020"."""
    text = " ".join(w[4] for w in words).upper()
    if "FLORIDA BUILDING CODE" not in text:
        return None
    m = _EDITION.search(text)
    if not m:
        return None
    return {"6": "fbc2017", "7": "fbc2020", "8": "fbc2023", "9": "fbc2026"}.get(m.group(1))


def _needles(needles: Sequence[str]) -> List[str]:
    return [re.sub(r"[^A-Z0-9 ]", "", n.upper()).strip() for n in needles]


def find_value(rows: Sequence[FormRow], *needles: str,
               without: Sequence[str] = ()) -> Optional[FormRow]:
    """First row whose label contains every needle. Needles are matched against
    the punctuation-stripped upper-case key, so `OCCUPANCY:` and `OCCUPANCY`
    both hit.

    `without` excludes rows whose key carries any of those words. A code block
    prints `OCCUPANT LOAD FACTOR: 150` beside `TOTAL OCCUPANT LOAD: 152` and
    both keys contain `OCCUPANT LOAD`; reading the factor as the load is how a
    plausible wrong number gets into a review.
    """
    want, veto = _needles(needles), _needles(without)
    for r in rows:
        if not r.value:
            continue
        # Needles match as substrings — `OCCUPANT LOAD` has to hit
        # `TOTAL OCCUPANT LOAD`. A veto matches whole words, so excluding
        # `PER` rejects `OCCUPANT LOAD PER FLOOR` without also rejecting a
        # label that merely spells one somewhere inside a longer word.
        if veto and set(r.key.split()) & set(veto):
            continue
        if all(w in r.key for w in want):
            return r
    return None


def poses(rows: Sequence[FormRow], *needles: str) -> bool:
    """True when the sheet asks this question at all, answered or not."""
    want = _needles(needles)
    return any(all(w in r.key for w in want) for r in rows)
