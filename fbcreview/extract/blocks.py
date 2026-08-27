"""Anchor-and-region extraction for UNRULED code-data blocks.

Ruled schedules come out of `find_tables`.  Code data blocks — the boxes on G-0
and G-1 that read `MAX TRAVEL DISTANCE (1017.2): 250 LF / 69'-4"` — usually have
no cell ruling, so they need positional clustering instead.

The key design decision: rows are keyed by the CITED SECTION NUMBER, not by the
label text.  Labels wrap, get abbreviated and vary by drafter; the parenthesised
section number is the most stable token on any code data block and is exactly
what the rule engine wants to join on.
"""
from __future__ import annotations
import re
from typing import Dict, List, Optional, Tuple
import pymupdf
from ..facts import CodeDatum

#: A cited code section. Two spellings are in use and a set picks one: the
#: parenthesised "(1006.2.1)" the egress blocks use, and the spelled-out
#: "SECTION 302" / "TABLE 602" that ITEC's code-compliance block uses
#: throughout. Keyed on the citation, because the citation is what a rule
#: matches on — a row without one is prose and is skipped either way.
_SECTION = re.compile(
    r"\((\d{3,4}(?:\.\d+)*)\)"
    r"|\b(?:SECTION|TABLE)\s+(\d{3,4}(?:\.\d+)*)\b",
    re.I,
)
_FEET_IN = re.compile(r"(\d+)\s*'\s*-\s*(\d+(?:\s+\d+/\d+)?)\s*\"")
_LF      = re.compile(r"(\d[\d,]*\.?\d*)\s*(LF|FEET|FT)\b", re.I)
# 1 3/4"   ·   3/4"   ·   32"   ·   10.50"
_INCHES  = re.compile(r"(?:(\d+)\s+)?(?:(\d+)\s*/\s*(\d+)|(\d+(?:\.\d+)?))\s*\"")
_PLAIN   = re.compile(r"^\s*(\d[\d,]*\.?\d*)\s*$")


def to_feet(raw: str) -> Optional[float]:
    raw = (raw or "").strip()
    m = _FEET_IN.search(raw)
    if m:
        inches = m.group(2).strip()
        if " " in inches:
            a, b = inches.split(); n, d = b.split("/")
            inch = float(a) + float(n) / float(d)
        else:
            inch = float(inches)
        return float(m.group(1)) + inch / 12.0
    m = _LF.search(raw)
    if m:
        return float(m.group(1).replace(",", ""))
    return None


def to_inches(raw: str) -> Optional[float]:
    raw = (raw or "").strip()
    ft = _FEET_IN.search(raw)
    if ft:
        v = to_feet(raw)
        return v * 12.0 if v is not None else None
    m = _INCHES.search(raw)
    if m:
        whole, num, den, dec = m.groups()
        v = float(whole) if whole else 0.0
        if num and den:
            v += float(num) / float(den)
        elif dec:
            v = float(dec) if not whole else v + float(dec)
        return v
    return None


def to_number(raw: str) -> Optional[float]:
    m = _PLAIN.match((raw or "").strip())
    return float(m.group(1).replace(",", "")) if m else None


def _rows(page: pymupdf.Page, region: pymupdf.Rect, band: float = 5.0
          ) -> List[Tuple[float, List[Tuple[float, str]]]]:
    """Cluster words into visual rows by y, preserving x for column order."""
    buckets: Dict[int, List[Tuple[float, str]]] = {}
    for x0, y0, x1, y1, txt, *_ in page.get_text("words"):
        if pymupdf.Rect(x0, y0, x1, y1).intersects(region):
            buckets.setdefault(int(round(y0 / band)), []).append((x0, txt))
    return [(k * band, sorted(v)) for k, v in sorted(buckets.items())]


#: The search window under a block header, as a fraction of the sheet. These
#: were fixed point sizes — 620 x 260 with a 130pt left pad — measured on a
#: 1224 x 792 sheet. A 36" x 24" sheet plots at 2592 x 1728, where the same
#: block occupies about 1493 x 655, so a fixed window cropped it to a corner
#: and the rows below the cut were simply never seen. The ratios below are
#: those original numbers over that original sheet, so a 1224 x 792 sheet gets
#: byte-identical bounds and a larger sheet gets a proportionally larger window.
_WINDOW_W, _WINDOW_H, _WINDOW_PAD = 620 / 1224, 260 / 792, 130 / 1224


def code_data_block(doc: pymupdf.Document, pno: int, header: str, sheet: str,
                    width: Optional[float] = None, height: Optional[float] = None,
                    left_pad: Optional[float] = None) -> List[CodeDatum]:
    """Extract every `LABEL (section): required provided` row under `header`."""
    page = doc[pno]
    hits = page.search_for(header)
    if not hits:
        return []
    if width is None:
        width = page.rect.width * _WINDOW_W
    if height is None:
        height = page.rect.height * _WINDOW_H
    if left_pad is None:
        left_pad = page.rect.width * _WINDOW_PAD
    a = max(hits, key=lambda r: r.y0)          # the data block, not a TOC mention
    region = pymupdf.Rect(a.x0 - left_pad, a.y0, a.x0 + width, a.y0 + height)
    out: List[CodeDatum] = []
    for _y, row in _rows(page, region):
        line = " ".join(t for _x, t in row)
        m = _SECTION.search(line)
        if not m:
            continue
        section = m.group(1) or m.group(2)
        label = line[:m.start()].strip(" :")
        tail = line[m.end():].strip(" :")
        # required / provided split: the two value groups after the citation
        toks = tail.split()
        req, prov = "", ""
        if toks:
            # values are 1-2 tokens each ("250 LF", "69'-4\"", "44\"", "2")
            def take(i):
                if i >= len(toks):
                    return "", i
                v = toks[i]
                if i + 1 < len(toks) and toks[i + 1].upper() in ("LF", "FT", "FEET", "IN"):
                    return f"{v} {toks[i+1]}", i + 2
                return v, i + 1
            req, i = take(0)
            prov, _ = take(i)
        # A code-analysis row states a value. Prose that happens to cite a
        # section does not, and a block header search over a whole sheet finds
        # plenty of it — "...clearances required by SECTION 604 for the..."
        # yields required="for", provided="the" if it is taken at face value.
        # Requiring one side to parse as a number or a dimension is what keeps
        # a sentence from entering the record as a measurement. A row that
        # cannot be read is left out, and the rule that wanted it abstains,
        # which is the honest outcome — never a guessed value.
        if not any(fn(v) is not None
                   for v in (req, prov)
                   for fn in (to_number, to_inches, to_feet)):
            continue
        out.append(CodeDatum(section=section, label=label, required_raw=req,
                             provided_raw=prov, sheet=sheet, page=pno, anchor=label or header))
    return out


def normalise(d: CodeDatum) -> CodeDatum:
    """Attach numeric values and a unit, chosen by what the raw text looks like."""
    for unit, fn in (("ft", to_feet), ("in", to_inches), ("count", to_number)):
        r, p = fn(d.required_raw), fn(d.provided_raw)
        if r is not None or p is not None:
            d.required, d.provided, d.unit = r, p, unit
            return d
    return d


def labelled_values(doc: pymupdf.Document, pno: int, header: str,
                    width: Optional[float] = None, height: Optional[float] = None,
                    left_pad: Optional[float] = None) -> Dict[str, str]:
    """`LABEL  value` rows under a block header, for blocks that carry no
    section citations (BUILDING CODE ANALYSIS, PROJECT DATA and friends)."""
    page = doc[pno]
    hits = page.search_for(header)
    if not hits:
        return {}
    if width is None:
        width = page.rect.width * (340 / 1224)
    if height is None:
        height = page.rect.height * (300 / 792)
    if left_pad is None:
        left_pad = page.rect.width * (12 / 1224)
    a = hits[0]
    region = pymupdf.Rect(a.x0 - left_pad, a.y0, a.x0 + width, a.y0 + height)
    out: Dict[str, str] = {}
    for _y, row in _rows(page, region):
        toks = [t for _x, t in row]
        if len(toks) < 2:
            continue
        # value = trailing run of tokens that are numeric / dimensional
        cut = len(toks)
        for i in range(len(toks) - 1, -1, -1):
            if re.match(r'^[\d.,]+["\']?$|^(LF|FT|IN|YES|NO|N/A)$', toks[i].upper()):
                cut = i
            else:
                break
        if cut == len(toks) or cut == 0:
            continue
        out[" ".join(toks[:cut]).strip(" :.")] = " ".join(toks[cut:]).strip()
    return out
