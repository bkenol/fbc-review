"""The grounding verifier: an AI proposal is used only if the sheet says it.

`CLAUDE.md`, "AI reads; rules decide", rule 2. A proposal names a catalog field,
a value and a quote. It becomes a claim only when all of this holds:

1. **The field is in the catalog**, and an audit-row field says which role.
2. **The value is inside the quote**, token for token, and parses with the same
   catalog parser the deterministic reader uses. A value that needs a unit
   (`SF`, `MPH`) has it in the quote.
3. **The quote is on the page** — every token found in the page's words, the
   nearest occurrences of each around the rarest one forming a compact cluster:
   a label and its value, not a word from each end of the sheet. Text recovered
   by OCR may miss by one character per word, on the label only; the value's
   own tokens must match exactly, because a misread digit is the error that
   matters.
4. **The label names the field** — it shares a word with the catalog's labels
   for it, or the heading it sits under does — and it trips none of the catalog's
   disqualifiers, in the label or in the headings over the located text.

Everything that fails is recorded on the store as a rejection, with the reason,
and never reaches a rule. An accepted claim scores below a deterministic exact
match, so where the two readers disagree about the same fact the deterministic
reading stands and the AI's never becomes a "disagreement in the set"; where they
agree, the fact is two independent readings and HIGH confidence.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from ..confidence import LOW, MEDIUM
from ..factstore import AI, Claim, FactStore
from ..layout import PageLayout, Word, union
from ..read.catalog import BY_KEY, FieldSpec
from ..read.deterministic import _context_ok, _has_unit, normalise_label
from ..read.parse import parse
from .readings import Readings
from .schema import FieldReading

#: A quote's words must fit in a box no bigger than this share of the sheet's
#: diagonal, or this many text heights, whichever is larger.
MAX_SPAN_DIAG = 0.15
MAX_SPAN_EMS = 45.0
#: Below a deterministic exact match (1.0), above nothing that counts as a rival.
AI_SCORE = 0.85

_QUOTES = str.maketrans({"”": '"', "“": '"', "″": '"', "’": "'", "‘": "'", "′": "'",
                         "–": "-", "—": "-"})
_STOP = {"OF", "THE", "AND", "PER", "NO", "TOTAL", "MAX", "MIN", "MAXIMUM", "MINIMUM",
         "NUMBER", "A", "AN", "TO", "FOR", "IN", "ON", "REQUIRED", "PROVIDED"}


def norm_token(t: str) -> str:
    t = (t or "").translate(_QUOTES).upper()
    t = t.strip(",;:?")
    t = t.strip("()[]")
    return t.rstrip(".") if not re.fullmatch(r"[\d.]+", t) else t


def tokens(text: str) -> List[str]:
    return [n for n in (norm_token(t) for t in (text or "").split()) if n]


def _edit1(a: str, b: str) -> bool:
    """Equal, or one substitution / insertion / deletion apart."""
    if a == b:
        return True
    if abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b)) == 1
    if len(a) > len(b):
        a, b = b, a
    i = j = diff = 0
    while i < len(a) and j < len(b):
        if a[i] != b[j]:
            diff += 1
            j += 1
            if diff > 1:
                return False
        else:
            i += 1
            j += 1
    return True


@dataclass
class Located:
    words: List[Word]
    box: Tuple[float, float, float, float]
    span: float
    fuzzy: bool


def _centre(w: Word) -> Tuple[float, float]:
    return ((w.x0 + w.x1) / 2.0, (w.y0 + w.y1) / 2.0)


def locate(quote: str, words: Sequence[Word], width: float, height: float,
           exact: Iterable[str] = ()) -> Optional[Located]:
    """Where on the page a quote is printed, or None.

    `exact` are tokens that must match without tolerance — the value's own.
    """
    qt = tokens(quote)
    if not qt:
        return None
    must = set(exact)
    by_token: Dict[str, List[Word]] = {}
    for w in words:
        for t in tokens(w.text):
            by_token.setdefault(t, []).append(w)

    candidates: Dict[str, List[Word]] = {}
    fuzzy_used = False
    for t in set(qt):
        found = list(by_token.get(t, []))
        if not found and t not in must and len(t) >= 4 and not any(ch.isdigit() for ch in t):
            found = [w for k, ws in by_token.items() if _edit1(t, k) for w in ws]
            fuzzy_used = fuzzy_used or bool(found)
        if not found:
            return None
        candidates[t] = found

    anchor = min(set(qt), key=lambda t: len(candidates[t]))
    heights = sorted(w.h for w in words) or [10.0]
    em = heights[len(heights) // 2]
    limit = max(MAX_SPAN_DIAG * math.hypot(width, height), MAX_SPAN_EMS * em)

    best: Optional[Located] = None
    for w0 in candidates[anchor][:200]:
        cx, cy = _centre(w0)
        chosen: List[Word] = [w0]
        used: Set[int] = {id(w0)}
        ok = True
        anchor_placed = False
        for t in qt:
            if t == anchor and not anchor_placed:
                anchor_placed = True                  # w0 is this token
                continue
            pool = [w for w in candidates[t] if id(w) not in used] or candidates[t]
            if not pool:
                ok = False
                break
            near = min(pool, key=lambda w: math.hypot(_centre(w)[0] - cx, _centre(w)[1] - cy))
            chosen.append(near)
            used.add(id(near))
        if not ok:
            continue
        box = union([w.box for w in chosen])
        span = math.hypot(box[2] - box[0], box[3] - box[1])
        if best is None or span < best.span:
            best = Located(chosen, box, span, fuzzy_used)
    if best is None or best.span > limit:
        return None
    return best


def _pair_located(qt: List[str], layout: PageLayout) -> Optional[Located]:
    """The layout pair the quote reproduces, when there is one.

    The same words often occur more than once on a sheet — `OCCUPANT LOAD` heads
    a column in one table and labels a row in another, and `70` is printed four
    times on G-1 — so the tightest cluster of words is not always the one the
    quote describes. A pair whose own label and values are the quote's tokens is:
    it is the drafter's label/value, boxed where the drafter put it.
    """
    best: Optional[Tuple[int, Located]] = None
    for p in layout.pairs:
        pt = tokens(p.label) + tokens(" ".join(p.values))
        if not _subsequence(qt, pt):
            continue
        extra = len(pt) - len(qt)
        if extra > 4:
            continue
        box = p.box
        loc = Located([], box, math.hypot(box[2] - box[0], box[3] - box[1]), False)
        if best is None or extra < best[0]:
            best = (extra, loc)
    return best[1] if best else None


def _subsequence(needle: List[str], hay: List[str]) -> bool:
    it = iter(hay)
    return all(any(n == h for h in it) for n in needle)


def _head_words(spec: FieldSpec) -> Set[str]:
    words: Set[str] = set()
    for label in spec.labels:
        for w in normalise_label(label).split():
            if w not in _STOP and len(w) > 1:
                words.add(w)
    return words


def _label_part(quote_tokens: List[str], value_tokens: List[str]) -> List[str]:
    out = list(quote_tokens)
    for v in value_tokens:
        if v in out:
            out.remove(v)
    return out


def ground_field(reading: FieldReading, layout: PageLayout, sheet: str
                 ) -> Tuple[Optional[Claim], str]:
    """(claim, "") when the proposal holds up; (None, reason) when it does not."""
    spec = BY_KEY.get(reading.field)
    if spec is None:
        return None, f"'{reading.field}' is not a field the catalog asks about"
    role = reading.role if spec.roles else ""
    if spec.roles and role not in ("required", "provided"):
        return None, "an audit-row value without a role (required or provided)"

    qt, vt = tokens(reading.quote), tokens(reading.value)
    if not vt:
        return None, "empty value"
    if not _subsequence(vt, qt):
        return None, "the value is not inside the quote"
    value = parse(spec.parse, reading.value)
    if value is None and spec.parse == "edition":
        value = parse("edition", reading.quote)
    if value is None:
        return None, f"the value does not read as {spec.parse}"
    if spec.units and not _has_unit(reading.quote, spec.units):
        return None, f"the quote carries none of the units the field needs ({', '.join(spec.units)})"

    label_tokens = _label_part(qt, vt)
    label = " ".join(label_tokens)
    norm = normalise_label(label)
    if any(f" {normalise_label(d)} " in f" {norm} " for d in spec.disqualify):
        return None, "the label is one the catalog says is not this field"

    loc = _pair_located(qt, layout) or locate(reading.quote, layout.words, layout.width,
                                              layout.height, exact=vt)
    if loc is None:
        return None, "the quote was not found on the sheet"

    near = [p for p in layout.pairs if _intersects(p.box, loc.box)]
    context = [c for p in near for c in p.context]
    heading = next((p.heading for p in near if p.heading), "")
    if not _context_ok(spec, label, context, heading):
        return None, "the heading over the quote says it is not this building's value"
    heads = _head_words(spec)
    ctx_words = set(normalise_label(" ".join([*context, heading])).split())
    if not (set(norm.split()) & heads or ctx_words & heads) and spec.parse != "edition":
        return None, "nothing in the label or its heading names this field"

    return Claim(spec.key, value, reading.quote, layout.page, sheet, loc.box, AI,
                 shape="ai", confidence=LOW if loc.fuzzy else MEDIUM, label=label,
                 context=tuple(context[:2]), heading=heading, role=role, score=AI_SCORE,
                 note="matched with OCR tolerance" if loc.fuzzy else ""), ""


def _intersects(a, b) -> bool:
    return a[0] <= b[2] and b[0] <= a[2] and a[1] <= b[3] and b[1] <= a[3]


def ground_readings(readings: Readings, layouts: Dict[int, PageLayout],
                    sheet_codes: Dict[int, str], store: FactStore) -> None:
    """Every proposal in `readings`, checked against its own page."""
    accepted = rejected = 0
    for page, reading in sorted(readings.sheets.items()):
        layout = layouts.get(page)
        sheet = sheet_codes.get(page, f"p{page + 1}")
        for fr in reading.fields:
            if layout is None:
                claim, why = None, "the reading names a page the set does not have"
            else:
                claim, why = ground_field(fr, layout, sheet)
            if claim is not None:
                store.add(claim)
                accepted += 1
            else:
                store.reject({"page": page, "sheet": sheet, "field": fr.field,
                              "value": fr.value, "quote": fr.quote, "reason": why})
                rejected += 1
    readings.grounded = {"accepted": accepted, "rejected": rejected}
