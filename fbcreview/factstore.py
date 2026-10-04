"""The fact store: every reading of every fact, and what the set says once they are weighed.

Before this module a fact was read by whichever extractor got to it first — the
occupant load by four of them, sprinkler status by three — and reached a rule as
a bare number in `ProjectFacts.meta` with no record of where it came from
(`docs/ENGINE-TEARDOWN.md` §10.5). Here every reading is a `Claim` carrying the
sheet, the box, the text as printed and what read it, and a fact is *resolved*
from its claims in one place, by one policy.

The policy, in order:

1. Claims are grouped by value, using the same notion of "the same answer" the
   declaration uses (`reconcile.agree`): `A` and `A-3` agree, `II-B` and
   `Type IIB` agree, 1,436 and 1,436.0 agree.
2. The group with the strongest label match wins, then the one stated on more
   sheets, then the one stated more times, then the one on a general sheet.
3. Within the winning group the most specific value is the answer — `A-3`
   rather than `A`.
4. Two readers agreeing (the deterministic reader and the AI reader), or two
   sheets agreeing, is HIGH confidence. One is MEDIUM.
5. Any other group with a strong claim is kept as a **rival**. A fact with a
   rival is a disagreement the set contains, and a rule reports it; the store
   never quietly picks one side and drops the other.

There is no default anywhere in this module. A fact nobody stated resolves to
`None`, and a rule that needs it abstains.
"""
from __future__ import annotations

import re

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .confidence import HIGH, MEDIUM, Evidence

STATED, TABULATED, COMPUTED, MEASURED = "stated", "tabulated", "computed", "measured"

#: What read a claim. `ai` claims are only ever added after grounding. `cad`
#: claims come from the drawing itself — a block attribute whose name says what
#: its value is (`fbcreview/cad`), on a set uploaded as DWG or DXF.
PAIR, LINE, TABLE, LEGACY, AI, CAD = "pair", "line", "table", "legacy", "ai", "cad"

#: A rival group needs at least this label strength to count as a disagreement.
RIVAL_SCORE = 0.9

Box = Tuple[float, float, float, float]


@dataclass
class Claim:
    """One reading of one fact, and everything needed to find it on the sheet."""
    field: str
    value: Any
    raw: str                    # the text as printed — the quote
    page: int                   # 0-based page index
    sheet: str                  # the sheet number, as printed
    box: Optional[Box]          # unrotated page coordinates
    method: str                 # PAIR | LINE | TABLE | LEGACY | AI | CAD
    shape: str = ""             # inline | row | stacked | table | line
    basis: str = STATED
    confidence: str = MEDIUM
    label: str = ""
    context: Tuple[str, ...] = ()
    heading: str = ""
    role: str = ""              # "required" | "provided" | "" — the audit rows
    score: float = 1.0
    note: str = ""
    #: On a set read from a drawing: the drawing entities the claim was read
    #: from, as `dxf:<handle>` tokens joined by "+". Two claims that share an
    #: entity are one reading however many readers found it — the PDF the
    #: readers see was drawn from the same TEXT or ATTRIB. Empty everywhere else.
    source: str = ""
    #: The layout (sheet tab) and layer the reading sits on, when it came from a
    #: drawing. Provenance only.
    layout: str = ""
    layer: str = ""

    def as_meta(self) -> Dict[str, Any]:
        return {
            "field": self.field, "value": self.value, "raw": self.raw,
            "page": self.page, "sheet": self.sheet,
            "box": [round(v, 1) for v in self.box] if self.box else None,
            "method": self.method, "shape": self.shape, "basis": self.basis,
            "confidence": self.confidence, "label": self.label,
            "context": list(self.context), "heading": self.heading,
            "role": self.role, "score": round(self.score, 2), "note": self.note,
            **({"source": self.source, "layout": self.layout, "layer": self.layer}
               if self.source else {}),
        }

    def where(self) -> str:
        return f"{self.sheet}" if self.sheet else f"page {self.page + 1}"


@dataclass
class Resolution:
    field: str
    role: str
    value: Any
    claims: List[Claim]
    confidence: str
    rivals: List[List[Claim]] = field(default_factory=list)

    @property
    def best(self) -> Claim:
        return self.claims[0]

    @property
    def conflict(self) -> bool:
        return bool(self.rivals)

    @property
    def methods(self) -> List[str]:
        return sorted({c.method for c in self.claims})

    @property
    def sheets(self) -> List[str]:
        seen: List[str] = []
        for c in self.claims:
            if c.sheet not in seen:
                seen.append(c.sheet)
        return seen

    def evidence(self) -> Evidence:
        """The resolved value as the `Evidence` the rest of the engine speaks."""
        b = self.best
        how = {PAIR: "read from", LINE: "read from", TABLE: "read from the table on",
               LEGACY: "read from", AI: "read by AI and verified on",
               CAD: f"read from the drawing's {b.label or 'attribute'} field, printed on"
               }.get(b.method, "read from")
        stated, shown = self._other_sheets()
        also = f"; also stated on {', '.join(stated)}" if stated else ""
        if shown:
            # one model-space note seen through viewports on several layouts is
            # one statement, wherever it shows
            also += f"; the same drawing entity is also shown on {', '.join(shown)}"
        agreed = (" — two independent readers agree"
                  if len(self.methods) > 1 and independent(self.claims) else "")
        note = f"'{b.raw}' {how} {b.where()}{also}{agreed}"
        return Evidence(self.value, b.where(), self.confidence, note, b.page)

    @property
    def stating_sheets(self) -> List[str]:
        """Sheets that state the value: the best claim's, and every other that
        states it again. A sheet that only shows the best claim's own drawing
        entity is not one — one note seen through viewports on two sheets is
        one statement, and agreeing with itself is not two sheets agreeing.
        On a PDF, where claims carry no source, this is every sheet."""
        return [self.best.sheet] + self._other_sheets()[0]

    def _other_sheets(self) -> Tuple[List[str], List[str]]:
        """Sheets other than the best claim's, split into those that state the
        value again and those that only show the best claim's own drawing
        entity again. Claims read off a PDF carry no source, so for them every
        other sheet is a second statement, as it always was."""
        b = self.best
        mine = set(b.source.split("+")) if b.source else set()
        stated: List[str] = []
        shown: List[str] = []
        for s in self.sheets:
            if s == b.sheet:
                continue
            on = [c for c in self.claims if c.sheet == s]
            same = bool(mine) and all(c.source and mine & set(c.source.split("+")) for c in on)
            (shown if same else stated).append(s)
        return stated, shown

    def as_meta(self) -> Dict[str, Any]:
        return {
            "field": self.field, "role": self.role, "value": self.value,
            "confidence": self.confidence, "sheets": self.sheets, "methods": self.methods,
            "claims": [c.as_meta() for c in self.claims],
            "rivals": [[c.as_meta() for c in group] for group in self.rivals],
        }


def _same(field_key: str, a: Any, b: Any) -> bool:
    from .read.catalog import BY_KEY
    from .reconcile import agree
    spec = BY_KEY.get(field_key)
    if isinstance(a, str) and isinstance(b, str) and _written_out(a, b):
        return True
    if spec is not None and spec.declaration:
        return agree(spec.declaration, a, b) and _specific_agree(a, b)
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(float(a) - float(b)) <= max(0.01, 0.005 * max(abs(a), abs(b)))
    return str(a).strip().upper() == str(b).strip().upper()


_WORD = re.compile(r"[A-Z0-9]+(?:[-./][A-Z0-9]+)*")


def _written_out(a: str, b: str) -> bool:
    """One text value is the other written more fully.

    G-0 prints `CLASSIFICATION OF WORK: ALTERATION - LEVEL II` and, a line
    below, `ALTERATION - LEVEL: II`; both are one answer. The shorter value's
    words must appear, whole and in order, as a run inside the longer's — so
    `LEVEL II` never matches `LEVEL III`, and `A-3` never matches `A-2`.
    """
    ta, tb = _WORD.findall(a.upper()), _WORD.findall(b.upper())
    if not ta or not tb:
        return False
    short, long_ = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    n = len(short)
    return any(long_[i:i + n] == short for i in range(len(long_) - n + 1))


def _specific_agree(a: Any, b: Any) -> bool:
    """`A-3` and `A-2` both "agree" with `A`, but not with each other."""
    if isinstance(a, str) and isinstance(b, str) and "-" in a and "-" in b:
        return a.upper() == b.upper()
    return True


def _specificity(value: Any) -> int:
    return len(str(value)) if value is not None else 0


def _tokens(c: Claim) -> set:
    """What a claim was read from. See `independent`."""
    if c.source:
        return set(c.source.split("+"))
    return {f"{c.page}|{c.method}"}


def independent(claims: List[Claim]) -> bool:
    """Do these agreeing claims come from more than one reading?

    The rule that has always held: two readers, or two sheets, are independent;
    one reader twice on one sheet is not. Stated as clusters — claims join a
    cluster when they share a token, and a PDF claim's token is its (page,
    method) — it is the same rule, and it extends to a set read from a drawing,
    where a claim's tokens are the drawing entities behind it. There the layout
    reader and the CAD reader finding one ATTRIB, or one model-space note seen
    through viewports on two sheets, are one reading: agreeing with yourself is
    not corroboration.

    Two clusters are independent when between them they span two sheets or two
    readers — the old rule, applied to clusters instead of claims. So two
    separate entities that one reader found on one sheet stay one reading, as
    the same words printed twice on a PDF page always were.
    """
    clusters: List[Tuple[set, set, set]] = []          # tokens, pages, methods
    for c in claims:
        tokens, pages, methods = set(_tokens(c)), {c.page}, {c.method}
        for k in [k for k in clusters if k[0] & tokens]:
            clusters.remove(k)
            tokens |= k[0]
            pages |= k[1]
            methods |= k[2]
        clusters.append((tokens, pages, methods))
    for i, (_t, pages, methods) in enumerate(clusters):
        for _u, pages2, methods2 in clusters[i + 1:]:
            if len(pages | pages2) > 1 or len(methods | methods2) > 1:
                return True
    return False


def _general(sheet: str) -> bool:
    return (sheet or "")[:1].upper() == "G"


class FactStore:
    def __init__(self) -> None:
        self._claims: Dict[Tuple[str, str], List[Claim]] = defaultdict(list)
        self._cache: Dict[Tuple[str, str], Optional[Resolution]] = {}
        self.rejected: List[Dict[str, Any]] = []

    # ── writing ────────────────────────────────────────────────────────────
    def add(self, claim: Claim) -> None:
        key = (claim.field, claim.role)
        for c in self._claims[key]:
            # On a drawn sheet two entities can print the same words. Both are
            # kept — a sheet that shows another sheet's note *and* states it
            # itself must say so — while `independent` still counts them as one
            # reader on one sheet. A source of "" (every PDF claim) compares
            # equal, so a PDF reads exactly as before.
            if (c.page == claim.page and c.method == claim.method
                    and _same(claim.field, c.value, claim.value) and c.raw == claim.raw
                    and c.source == claim.source):
                return                                    # the same reading twice
        self._claims[key].append(claim)
        self._cache.pop(key, None)

    def extend(self, claims: Iterable[Claim]) -> None:
        for c in claims:
            self.add(c)

    def each(self) -> Iterable[Claim]:
        """Every claim held, in no particular order."""
        for group in self._claims.values():
            yield from group

    def changed(self) -> None:
        """Forget resolutions — call after amending claims in place."""
        self._cache.clear()

    def reject(self, record: Dict[str, Any]) -> None:
        """An AI proposal the grounding verifier would not accept. Audit only."""
        self.rejected.append(record)

    # ── reading ────────────────────────────────────────────────────────────
    def claims(self, field_key: str, role: str = "") -> List[Claim]:
        return list(self._claims.get((field_key, role), []))

    def keys(self) -> List[Tuple[str, str]]:
        return sorted(k for k, v in self._claims.items() if v)

    def resolve(self, field_key: str, role: str = "") -> Optional[Resolution]:
        key = (field_key, role)
        if key not in self._cache:
            self._cache[key] = self._resolve(field_key, role)
        return self._cache[key]

    def value(self, field_key: str, role: str = "") -> Any:
        r = self.resolve(field_key, role)
        return r.value if r else None

    def _resolve(self, field_key: str, role: str) -> Optional[Resolution]:
        claims = self._claims.get((field_key, role)) or []
        claims = [c for c in claims if c.value is not None]
        if not claims:
            return None

        groups: List[List[Claim]] = []
        for c in sorted(claims, key=lambda c: (-c.score, c.page)):
            for g in groups:
                if _same(field_key, g[0].value, c.value):
                    g.append(c)
                    break
            else:
                groups.append([c])

        def rank(g: List[Claim]):
            return (max(c.score for c in g), len({c.page for c in g}), len(g),
                    any(_general(c.sheet) for c in g), -min(c.page for c in g))

        groups.sort(key=rank, reverse=True)
        win = groups[0]
        win.sort(key=lambda c: (-c.score, -_specificity(c.value), c.page))
        value = max(win, key=lambda c: (_specificity(c.value), c.score)).value
        rivals = [g for g in groups[1:] if max(c.score for c in g) >= RIVAL_SCORE]
        return Resolution(field_key, role, value, win, HIGH if independent(win) else MEDIUM,
                          rivals)

    def conflicts(self) -> List[Resolution]:
        out = []
        for f, role in self.keys():
            r = self.resolve(f, role)
            if r is not None and r.conflict:
                out.append(r)
        return out

    def summary(self) -> Dict[str, Any]:
        """JSON-safe, for `ProjectFacts.meta` and the job record."""
        out: Dict[str, Any] = {}
        for f, role in self.keys():
            r = self.resolve(f, role)
            if r is None:
                continue
            out[f"{f}:{role}" if role else f] = r.as_meta()
        return out
