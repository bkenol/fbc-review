"""A stated requirement: the code data block's row, or the same row read off the layout.

The egress rules were written against `facts.datum(section)` — a row of a code
data block keyed by the section number printed beside it. That key is the most
stable token on a code block when it is there, and it is often not there: the
Sculpted G-0 prints `NUMBER OF EXITS | 2 | 2` with no citation, so
EGRESS.EXIT_COUNT abstained with "exit count row not found" while the row sat
on the sheet in plain text.

`stated()` keeps the old key first — a set whose block reader found the row is
reviewed exactly as before — and falls back to the fact store's reading of the
same row: the catalog field for that section, its `required` and `provided`
claims, and the sheet, page and label they were printed at. Nothing is
inferred. A value only the AI reader located still says so, in the finding's
own words (`noted`), because a rule's reader cannot otherwise tell — and so does
a value read only from a drawing's own block attribute (method `cad`), whose
field name is the drawing's and is not printed on the sheet.
"""
from __future__ import annotations

from typing import Optional

from ..facts import CodeDatum, ProjectFacts

#: The catalog field that states each section's row (`fbcreview/read/catalog.py`).
FIELD_FOR = {
    "1005.3.2": "egress.width",
    "1006.2.1": "egress.common_path",
    "1006.3.2": "egress.exits",
    "1006.3.3": "egress.exits",
    "1010.1.1": "egress.door_clear_width",
    "1017.2": "egress.travel_distance",
    "1020.3": "egress.corridor_width",
    "1020.5": "egress.dead_end",
}

_UNIT = {"feet": "ft", "inches": "in", "count": ""}


def _printed(claim) -> str:
    """The value as printed: the claim's raw text without its label."""
    raw, label = claim.raw or "", claim.label or ""
    if label and raw.upper().startswith(label.upper()):
        return raw[len(label):].strip()
    return raw


def stated(f: ProjectFacts, *sections: str) -> Optional[CodeDatum]:
    """The first section's block row, else the store's reading of that row."""
    for section in sections:
        d = f.datum(section)
        if d is not None:
            return d
    store = getattr(f, "store", None)
    if store is None:
        return None
    for section in sections:
        key = FIELD_FOR.get(section)
        if key is None:
            continue
        req, prov = store.resolve(key, "required"), store.resolve(key, "provided")
        if req is None and prov is None:
            continue
        home = (req or prov).best
        from ..read.catalog import BY_KEY
        unit = _UNIT.get(BY_KEY[key].parse, "") if key in BY_KEY else ""
        only = {m for r in (req, prov) if r is not None and len(r.methods) == 1
                for m in r.methods}
        notes = [n for m, n in (("ai", f"read by AI and verified on {home.sheet}"),
                                ("cad", f"read from the drawing's own attribute on {home.sheet}"))
                 if m in only]
        return CodeDatum(
            section=section, label=home.label,
            required_raw=_printed(req.best) if req else "",
            provided_raw=_printed(prov.best) if prov else "",
            required=float(req.value) if req else None,
            provided=float(prov.value) if prov else None,
            unit=unit, sheet=home.sheet, page=home.page, anchor=home.label, box=home.box,
            note="; ".join(notes))
    return None


def noted(d: Optional[CodeDatum], text: str) -> str:
    """`text`, with the datum's provenance note appended when it has one.

    A block row and a deterministic layout reading carry no note, so every
    finding built from one reads exactly as it did before.
    """
    note = getattr(d, "note", "") if d is not None else ""
    return f"{text} ({note}.)" if note else text


def rival(f: ProjectFacts, key: str, role: str, sheet: str, value: float):
    """A claim on another sheet that states a different value for the same row.

    The fact store keeps disagreements instead of resolving them away; a rule
    that checks one sheet's statement uses this to say the other sheet says
    something else, rather than report half of what the set shows.
    """
    store = getattr(f, "store", None)
    r = store.resolve(key, role) if store is not None else None
    if r is None:
        return None
    for group in [r.claims, *r.rivals]:
        for c in group:
            if c.sheet != sheet and isinstance(c.value, (int, float)) \
                    and abs(float(c.value) - float(value)) > 0.5:
                return c
    return None
