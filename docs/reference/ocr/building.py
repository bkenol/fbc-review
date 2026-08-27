"""Harvest the code-analysis scalars out of form rows.

`formblocks.form_rows` gives label/value pairs and the citation banner each one
sits under.  This module turns those into typed `Evidence`, which is what the
rules consume.  It is deliberately conservative: a value it cannot parse
confidently is left as `None`, and the rule that wanted it abstains.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Sequence

from ..confidence import Evidence, HIGH, LOW, MEDIUM
from ..facts import BuildingData, OccupantRow
from .formblocks import FormRow, find_value

_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")
_GROUP = re.compile(r"\b([ABEFHIMRSU])\s*-\s*([1-5])\b")
_CTYPE = re.compile(r"\bTYPE\s+(I{1,3}|IV|V)\s*-?\s*([AB])\b", re.I)
_FTIN = re.compile(r"(\d+)\s*'\s*-?\s*(\d+)?")

# The occupancy word on a code block is prose, not a group letter. This is the
# mapping from what drafters actually type to the FBC group.
_OCCUPANCY_WORDS = {
    "BUSINESS": "B", "OFFICE": "B", "PROFESSIONAL": "B",
    "MERCANTILE": "M", "RETAIL": "M",
    "STORAGE": "S-1", "WAREHOUSE": "S-2",
    "FACTORY": "F-1", "INDUSTRIAL": "F-1",
    "ASSEMBLY": "A-3", "EDUCATIONAL": "E",
    "RESIDENTIAL": "R-2", "INSTITUTIONAL": "I-2", "UTILITY": "U",
}


def _num(s: str) -> Optional[float]:
    m = _NUM.search(s or "")
    return float(m.group().replace(",", "")) if m else None


def _nums(s: str) -> List[float]:
    return [float(m.replace(",", "")) for m in _NUM.findall(s or "")]


def _ev(value, row: FormRow, sheet: str, conf: str, note: str = "") -> Evidence:
    return Evidence(value, f"{sheet} · {row.label}", conf, note, row_page(row))


def row_page(row: FormRow) -> Optional[int]:
    return getattr(row, "page", None)


def occupancy_group(text: str) -> Optional[str]:
    t = (text or "").upper()
    m = _GROUP.search(t)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    for word, group in _OCCUPANCY_WORDS.items():
        if word in t:
            return group
    if re.fullmatch(r"\s*[ABEFHIMRSU]\s*", t):
        return t.strip()
    return None


def construction_type(text: str) -> Optional[str]:
    m = _CTYPE.search(text or "")
    if not m:
        return None
    return f"{m.group(1).upper()}-{m.group(2).upper()}"


def sprinkler_system(text: str) -> Optional[str]:
    t = (text or "").upper()
    if "13R" in t.replace(" ", ""):
        return "nfpa13r"
    if "13D" in t.replace(" ", ""):
        return "nfpa13d"
    if "NFPA" in t and "13" in t:
        return "nfpa13"
    if t.startswith("NO") or "NOT SPRINK" in t:
        return "none"
    if t.startswith("YES"):
        return "nfpa13"
    return None


def is_sprinklered(system: Optional[str]) -> Optional[bool]:
    if system is None:
        return None
    return system != "none"


def harvest(rows: Sequence[FormRow], sheet: str, page: int,
            conf: str = MEDIUM, edition: Optional[str] = None) -> BuildingData:
    b = BuildingData()
    for r in rows:
        setattr(r, "page", page)

    def put(field: str, row: Optional[FormRow], value, note: str = ""):
        if row is None or value is None:
            return
        if getattr(b, field) is not None:
            return                                     # first hit wins
        setattr(b, field, Evidence(value, f"{sheet} · {row.label}", conf, note, page))

    r = find_value(rows, "OCCUPANCY")
    if r:
        put("occupancy_raw", r, r.value)
        put("occupancy_group", r, occupancy_group(r.value),
            f"read from '{r.value}'")

    r = find_value(rows, "OCCUPANCY SEPARATION RATING")
    if r:
        put("separation_note", r, r.value)

    r = find_value(rows, "MIXED OCCUPANCY")
    if r:
        v = r.value.strip().upper()
        put("mixed_occupancy", r, True if v.startswith("Y") else
            (False if v.startswith("N") else None))

    r = find_value(rows, "CONSTRUCTION TYPE")
    if r:
        put("construction_type", r, construction_type(r.value))

    r = find_value(rows, "FIRE SPRINKLER SYSTEM") or find_value(rows, "SPRINKLER SYSTEM")
    if r:
        put("sprinkler_system", r, sprinkler_system(r.value))

    r = find_value(rows, "SQUARE FOOTAGE PER FLOOR")
    if r:
        ns = _nums(r.value)
        if ns:
            put("area_per_floor_sf", r, ns[0])
        if len(ns) > 1:
            put("area_allowable_sf", r, ns[-1], "allowable column")

    r = find_value(rows, "TOTAL SQUARE FOOTAGE")
    if r:
        ns = _nums(r.value)
        if ns:
            put("total_area_sf", r, ns[0])
        if len(ns) > 1:
            put("total_allowable_sf", r, ns[-1], "allowable column")

    r = find_value(rows, "STORIES")
    if r:
        ns = _nums(r.value)
        if ns:
            put("stories", r, int(ns[0]))
    if b.stories is None:
        # "FIRE SPRINKLER STATUS FOR AREA: 1 STORY" is how this office records
        # the story count, because Table 506.2's S1 column depends on it.
        r = find_value(rows, "SPRINKLER STATUS FOR AREA")
        if r and re.search(r"\bSTOR(Y|IES)\b", r.value, re.I):
            ns = _nums(r.value)
            if ns:
                put("stories", r, int(ns[0]))

    # Height only counts when it is the BUILDING height. Sheets are full of
    # rows labelled HEIGHT — door height, wall height, parapet height, mounting
    # height — and taking the first one gives a confident wrong answer.
    for r in rows:
        if "HEIGHT" not in r.key or not r.value:
            continue
        banner = (r.banner or "").upper()
        if not ("BUILDING" in r.key or "504" in banner or "ALLOWABLE HEIGHT" in banner):
            continue
        m = _FTIN.search(r.value)
        if m:
            put("height_ft", r, float(m.group(1)) + (float(m.group(2) or 0) / 12.0))
        else:
            put("height_ft", r, _num(r.value))
        break

    r = find_value(rows, "WIND")
    if r:
        put("wind_speed_mph", r, _num(r.value))
    r = find_value(rows, "EXPOSURE")
    if r:
        m = re.search(r"\b([BCD])\b", r.value.upper())
        put("exposure", r, m.group(1) if m else None)
    r = find_value(rows, "RISK CATEGORY")
    if r:
        m = re.search(r"\b(I{1,3}V?|IV)\b", r.value.upper())
        put("risk_category", r, m.group(1) if m else None)

    r = find_value(rows, "TOTAL OCCUPANT LOAD") or find_value(rows, "OCCUPANT LOAD")
    if r:
        put("occupant_load_stated", r, _num(r.value))

    r = find_value(rows, "FINISHED FLOOR")
    if r:
        put("finished_floor", r, r.value)
    r = find_value(rows, "FLOOD ZONE")
    if r:
        put("flood_zone", r, r.value)

    if edition and b.code_edition is None:
        b.code_edition = Evidence(edition, f"{sheet} · code banner", conf,
                                  "read from the table banners", page)
    return b


def merge(into: BuildingData, other: BuildingData) -> BuildingData:
    """First non-empty wins, so the general sheets take precedence over a
    repeat of the same value on a discipline sheet."""
    for f in vars(into):
        if getattr(into, f) is None and getattr(other, f) is not None:
            setattr(into, f, getattr(other, f))
    return into


# ── occupant load tables ─────────────────────────────────────────────────────

_SPACE = re.compile(r"^(UNIT|SUITE|ROOM|SPACE|TENANT)\s+([A-Z0-9\-]+)$", re.I)


def occupant_rows(rows: Sequence[FormRow], sheet: str, page: int,
                  source: str = "ocr") -> List[OccupantRow]:
    """Rows of a `DESCRIPTION | AREA | USE | FACTOR | LOAD` table.

    The factor column is the point of this: on a set where it was filled in
    with something other than a Table 1004.5 value, every occupant load
    downstream is wrong, and nothing else on the sheet reveals it.
    """
    out: List[OccupantRow] = []
    seen = set()
    for r in rows:
        if not _SPACE.match(r.label.strip()):
            continue
        ns = _nums(r.value)
        if len(ns) < 2:
            continue
        area = ns[0]
        factor = ns[-1] if len(ns) >= 2 else None
        load = None
        if len(ns) >= 3:
            factor, load = ns[-2], ns[-1]
        use = " ".join(w for w in re.findall(r"[A-Za-z][A-Za-z/\-]+", r.value)
                       if w.upper() not in ("SF", "SQ", "FT"))
        # A merged cell produces rows whose "use" is the same token repeated —
        # the artefact of two columns being read as one. Reject them rather than
        # letting a phantom space into the occupant load.
        toks = [t.upper() for t in re.findall(r"[A-Za-z]+", r.value)]
        if toks and max(toks.count(t) for t in set(toks)) >= 3:
            continue
        key = (r.label.strip().upper(), area)
        if key in seen:
            continue
        seen.add(key)
        out.append(OccupantRow(name=r.label.strip(), area_sf=area, use=use.strip(),
                               factor=factor, load=load, sheet=sheet, page=page,
                               source=source,
                               rect=tuple(round(v, 1) for v in r.rect)))
    return out
