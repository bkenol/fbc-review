"""The deterministic reader: layout pairs, matched against the catalog, as claims.

No regex per phrasing. A pair's label is normalised — punctuation and cited
section numbers stripped — and scored against every catalog spec: a whole-label
match is 1.0, a label that contains a multi-word catalog phrase is 0.8, and a
cited section the spec governs lifts a match to 1.0 because the section number
is the most stable token on a code block. Disqualifiers veto, in the label and in
the headings over it.

The roles of the audit rows (a stated requirement against what is provided) come
from the column header first — `REQUIRED:` / `PROVIDED:` — then from the label's
own words (`MAX. COMMON PATH`, `NUMBER PROVIDED`), then from position in a
two-value row. A single value with none of those cues is read by its form: a
feet-and-inches figure is a measured run, an `LF` figure is a limit. Anything
else gets no role and no claim, rather than a guessed one.
"""
from __future__ import annotations

import re
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from ..factstore import LINE, PAIR, TABULATED, Claim
from ..layout import PageLayout, Pair, union
from .catalog import FIELDS, LIMIT_WORDS, PROVIDED_WORDS, FieldSpec
from .parse import edition, parse

_SECTION = re.compile(r"\(\s*(\d{3,4}(?:\.\d+)*)\s*\)|\b(?:SECTION|TABLE)\s+(\d{3,4}(?:\.\d+)*)")
_PUNCT = re.compile(r"[^A-Z0-9 ]+")


def normalise_label(text: str) -> str:
    t = (text or "").upper()
    t = re.sub(r"\(\s*\d{3,4}(?:\.\d+)*\s*\)", " ", t)
    t = _PUNCT.sub(" ", t)
    return re.sub(r"\s+", " ", t).strip()


def _cited(label: str) -> List[str]:
    return [a or b for a, b in _SECTION.findall((label or "").upper())]


def _phrase_in(phrase: str, text: str) -> bool:
    return f" {phrase} " in f" {text} "


def match(spec: FieldSpec, label: str, raw_label: str) -> float:
    """How strongly a printed label names this fact — 0 when it does not."""
    norm = normalise_label(label)
    if not norm:
        return 0.0
    if any(_phrase_in(normalise_label(d), norm) for d in spec.disqualify):
        return 0.0
    score = 0.0
    for i, phrase in enumerate(spec.labels):
        p = normalise_label(phrase)
        if norm == p:
            score = max(score, 1.0 - 0.01 * i)
        elif " " in p and _phrase_in(p, norm):
            score = max(score, 0.8 - 0.01 * i)
    if score and spec.sections and any(s in spec.sections for s in _cited(raw_label)):
        score = 1.0
    return score


def _context_ok(spec: FieldSpec, label: str, context: Sequence[str], heading: str) -> bool:
    ctx = " | ".join(normalise_label(c) for c in [*context, heading] if c)
    if any(_phrase_in(normalise_label(d), ctx.replace("|", " ")) for d in spec.context_disqualify):
        return False
    if spec.context_require:
        both = f"{normalise_label(label)} {ctx.replace('|', ' ')}"
        if not any(normalise_label(r) in both.split() or _phrase_in(normalise_label(r), both)
                   for r in spec.context_require):
            # EXIT / EGRESS also match their plurals: `EXITS/ EGRESS`.
            if not any(w.startswith(normalise_label(r)) for r in spec.context_require
                       for w in both.split()):
                return False
    return True


def _has_unit(raw: str, units: Sequence[str]) -> bool:
    up = f" {raw.upper().replace(',', ' ')} "
    return any(f" {u} " in up or up.rstrip().endswith(u) or f"{u} " in up for u in units)


def role_of(label: str, header: str, i: int, n: int, raw: str) -> str:
    h = normalise_label(header).split()
    if any(w in h for w in ("REQUIRED", "REQ", "MIN", "MAX", "MINIMUM", "MAXIMUM",
                            "ALLOWABLE", "ALLOWED", "LIMIT")):
        return "required"
    if any(w in h for w in ("PROVIDED", "ACTUAL", "PROPOSED", "DESIGN", "MEASURED")):
        return "provided"
    words = normalise_label(label).split()
    if n >= 2:
        return "required" if i == 0 else ("provided" if i == 1 else "")
    if any(w in words for w in LIMIT_WORDS):
        return "required"
    if any(w in words for w in PROVIDED_WORDS):
        return "provided"
    up = raw.upper()
    if "'" in up:
        return "provided"
    if re.search(r"\b(LF|FT|FEET)\b", up):
        return "required"
    return ""


def claims_from_pair(pair: Pair, sheet: str) -> List[Claim]:
    out: List[Claim] = []
    for spec in FIELDS:
        score = match(spec, pair.label, pair.label)
        if not score or not _context_ok(spec, pair.label, pair.context, pair.heading):
            continue
        base = dict(page=pair.page, sheet=sheet, method=PAIR, shape=pair.kind,
                    label=pair.label, context=tuple(pair.context), heading=pair.heading,
                    score=score)
        if spec.roles:
            for i, raw in enumerate(pair.values):
                role = role_of(pair.label, pair.header_for(i), i, len(pair.values), raw)
                if not role:
                    continue
                value = parse(spec.parse, raw)
                if value is None:
                    continue
                box = union([pair.label_box, pair.value_boxes[i]]) \
                    if i < len(pair.value_boxes) else pair.box
                out.append(Claim(spec.key, value, f"{pair.label} {raw}".strip(),
                                 box=box, role=role, **base))
            continue
        raw = " ".join(pair.values)
        if spec.units and not _has_unit(raw, spec.units):
            continue
        value = parse(spec.parse, raw)
        if value is None:
            continue
        out.append(Claim(spec.key, value, pair.quote, box=pair.box, **base))
    return out


def tabulated_claims(pair: Pair, sheet: str) -> List[Claim]:
    """The TOTAL row of a table of spaces: its area column is a sum somebody
    wrote down (tabulated, never the gross building area), its occupant-load
    column is the load the table states."""
    if normalise_label(pair.label) not in ("TOTAL", "TOTALS", "GRAND TOTAL"):
        return []
    out: List[Claim] = []
    for i, raw in enumerate(pair.values):
        head = normalise_label(pair.header_for(i))
        box = union([pair.label_box, pair.value_boxes[i]]) if i < len(pair.value_boxes) else pair.box
        common = dict(page=pair.page, sheet=sheet, box=box, method=PAIR, shape=pair.kind,
                      label=f"TOTAL · {pair.header_for(i)}", context=tuple(pair.context),
                      heading=pair.heading)
        if head in ("AREA", "AREA SF", "SQ FT", "SF", "NET AREA", "GROSS AREA"):
            v = parse("area", raw)
            if v is not None:
                out.append(Claim("area.tabulated_total_sf", v, f"TOTAL {raw}",
                                 basis=TABULATED, score=0.9, **common))
        elif head in ("OCCUPANT LOAD", "OCCUPANTS", "LOAD", "NO OF OCCUPANTS"):
            v = parse("count", raw)
            if v is not None:
                out.append(Claim("occupant_load", v, f"TOTAL {raw}", score=0.95, **common))
    return out


def edition_claims(layout: PageLayout, sheet: str) -> List[Claim]:
    """`2023 FLORIDA BUILDING CODE 8TH EDITION - BUILDING` — the edition is
    stated in a line, not asked as a question, so it is read from lines."""
    out: List[Claim] = []
    for seg in layout.segments:
        value = edition(seg.text)
        if value:
            out.append(Claim("code_edition", value, seg.text, layout.page,
                             sheet, seg.box, LINE, shape="line", score=1.0))
    return out


def read_layouts(layouts: Dict[int, PageLayout], sheet_codes: Dict[int, str]) -> List[Claim]:
    claims: List[Claim] = []
    for pno in sorted(layouts):
        layout = layouts[pno]
        sheet = sheet_codes.get(pno, f"p{pno + 1}")
        for pair in layout.pairs:
            claims.extend(claims_from_pair(pair, sheet))
            claims.extend(tabulated_claims(pair, sheet))
        claims.extend(edition_claims(layout, sheet))
    return claims
