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
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import pymupdf

from ..confidence import HIGH, LOW, MEDIUM

Word = Tuple[float, float, float, float, str, int, int, int]

# "TABLE 602", "TABLES 504.3, 504.4 & 506.2", "SECTION 506 & 507", "SECTION 302"
_BANNER = re.compile(
    r"\b(?:TABLES?|SECTIONS?)\s+((?:\d{3,4}(?:\.\d+)*)(?:\s*(?:,|&|AND)\s*\d{3,4}(?:\.\d+)*)*)",
    re.I)
_EDITION = re.compile(r"\b(\d)(?:ST|ND|RD|TH)\s+EDITION\b", re.I)
_LEADING_JUNK = re.compile(r"^[\|\[\]\{\}<>_\-—–\.,;:\s]+")
_TRAILING_JUNK = re.compile(r"[\|\[\]\{\}<>_\s]+$")


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

    @property
    def key(self) -> str:
        return re.sub(r"[^A-Z0-9 ]", "", self.label.upper()).strip()


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
    best = ("", [])
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
             if w[4].rstrip().endswith((":", "?"))]
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
              band: float = 6.0) -> List[FormRow]:
    rows = cluster_rows(words, band)
    bans = banners(rows)
    out: List[FormRow] = []
    for strip, y, ws in rows:
        text, secs = banner_for(bans, strip, y)
        for label, value, i0, i1 in split_pairs(ws):
            seg = ws[i0:i1 + 1] or ws
            out.append(FormRow(
                label=label, value=value, y=y, strip=strip,
                rect=pymupdf.Rect(min(w[0] for w in seg), min(w[1] for w in seg),
                                  max(w[2] for w in seg), max(w[3] for w in seg)),
                confidence=confidence, banner=text, sections=secs,
                unanswered=not value))
    return out


def edition_from(words: Sequence[Word]) -> Optional[str]:
    """`FLORIDA BUILDING CODE 7TH EDITION` -> "fbc2020"."""
    text = " ".join(w[4] for w in words).upper()
    if "FLORIDA BUILDING CODE" not in text:
        return None
    m = _EDITION.search(text)
    if not m:
        return None
    return {"6": "fbc2017", "7": "fbc2020", "8": "fbc2023", "9": "fbc2026"}.get(m.group(1))


def find_value(rows: Sequence[FormRow], *needles: str) -> Optional[FormRow]:
    """First row whose label contains every needle. Needles are matched against
    the punctuation-stripped upper-case key, so `OCCUPANCY:` and `OCCUPANCY`
    both hit."""
    want = [re.sub(r"[^A-Z0-9 ]", "", n.upper()).strip() for n in needles]
    for r in rows:
        if r.value and all(w in r.key for w in want):
            return r
    return None


def poses(rows: Sequence[FormRow], *needles: str) -> bool:
    """True when the sheet asks this question at all, answered or not."""
    want = [re.sub(r"[^A-Z0-9 ]", "", n.upper()).strip() for n in needles]
    return any(all(w in r.key for w in want) for r in rows)
