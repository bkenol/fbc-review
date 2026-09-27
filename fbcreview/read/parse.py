"""Reading a printed value into the form a rule compares.

One parser per kind of value, named by the catalog. Every one returns `None`
when the text does not say the thing — a parser that cannot fail is the bug the
old `re.sub("[^0-9.]")` readers had, turning `TYPE II-B` into `2`.

These are also the grounding verifier's parsers: an AI-proposed value is only
accepted when the same function, run over the quote the model gave, produces
the same value. So a reading and its check can never disagree about what
"the same value" means.
"""
from __future__ import annotations

import re
from typing import Any, Callable, Dict, Optional

from ..extract.blocks import to_inches
from ..reconcile import (norm_bool, norm_construction, norm_edition, norm_letter,
                         norm_number, norm_occupancy, norm_roman, norm_text)

_NUM = re.compile(r"(?<![\d.])(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d+))?")
_GROUP = re.compile(r"\b([ABEFHIMRSU])\s*-\s*(\d)\b")
_BARE_GROUP = re.compile(r"(?:\bGROUP\s+|\()([ABEFHIMRSU])\b")
_TYPE = re.compile(r"\b(IV|V|I{1,3})\s*-?\s*([AB])\b")
_TYPE_ARABIC = re.compile(r"\bTYPE\s+([1-5])\s*-?\s*([AB])\b")


def _first_number(text: str) -> Optional[float]:
    m = _NUM.search((text or "").replace(" ,", ","))
    if not m:
        return None
    whole = m.group(1).replace(",", "")
    return float(f"{whole}.{m.group(2)}" if m.group(2) else whole)


def occupancy(text: str) -> Optional[str]:
    """`ASSEMBLY (A-3)` → `A-3`; `GROUP A` → `A`; `BUSINESS / OFFICE` → `B`.

    The explicit group code wins over the word, because it is more specific:
    `ASSEMBLY` is any of A-1 … A-5 and Table 1004.5 cares which.
    """
    up = (text or "").upper()
    m = _GROUP.search(up)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    m = _BARE_GROUP.search(up)
    if m:
        return m.group(1)
    value = norm_occupancy(up)
    if value and re.fullmatch(r"[ABEFHIMRSU](?:-\d)?", value):
        return value
    return None


def construction(text: str) -> Optional[str]:
    up = (text or "").upper()
    m = _TYPE.search(up)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    m = _TYPE_ARABIC.search(up)
    if m:
        return norm_construction(f"{m.group(1)}{m.group(2)}")
    value = norm_construction(up)
    return value if value and re.fullmatch(r"(?:IV|V|I{1,3})(?:-[AB])?", value) else None


def sprinkler(text: str) -> Optional[str]:
    """`SPRINKLERED`, `YES`, `FULLY SPRINKLERED` → YES; a named NFPA standard is
    kept; `NON-SPRINKLERED`, `NO`, `NONE` → NONE. Anything else is not an answer."""
    up = re.sub(r"\s+", " ", (text or "").upper()).strip()
    if not up:
        return None
    if re.search(r"\b(NON[\s-]?SPRINKLERED|UNSPRINKLERED|NOT SPRINKLERED|NO SPRINKLER)", up):
        return "NONE"
    compact = re.sub(r"[\s.\-]", "", up)
    if "13R" in compact:
        return "NFPA13R"
    if "13D" in compact:
        return "NFPA13D"
    if "NFPA13" in compact or "903311" in compact or "903312" in compact:
        return "NFPA13"
    if re.search(r"\bSPRINKLERED\b|\bFULLY\b", up):
        return "YES"
    b = norm_bool(up.split()[0].strip(",;"))
    if b is True:
        return "YES"
    if b is False:
        return "NONE"
    return None


def area(text: str) -> Optional[float]:
    value = _first_number(text)
    return value if value and value >= 20 else None


def feet(text: str) -> Optional[float]:
    t = (text or "").strip().upper()
    if not t or not any(ch.isdigit() for ch in t):
        return None
    if "'" in t or re.search(r"\b(LF|FT|FEET)\b", t):
        return norm_number(t)
    if '"' in t:
        inches = to_inches(t)
        return round(inches / 12.0, 3) if inches is not None else None
    return _first_number(t)


def inches(text: str) -> Optional[float]:
    t = (text or "").strip()
    if not t or not any(ch.isdigit() for ch in t):
        return None
    value = to_inches(t)
    if value is None:
        value = _first_number(t)
    return value


def count(text: str) -> Optional[float]:
    m = re.search(r"(?<![\d.,])(\d{1,5})(?![\d.,]*\d)", (text or "").replace(",", ""))
    if not m:
        return None
    return float(m.group(1))


def factor(text: str) -> Optional[float]:
    """An egress capacity factor: `0.15" (DOORS)`, `0.2 IN/OCC`."""
    m = re.search(r"(?<!\d)(0?\.\d+)", text or "")
    if not m:
        return None
    value = float(m.group(1))
    return value if 0.05 <= value <= 0.5 else None


def wind(text: str) -> Optional[float]:
    """Vult. `ULTIMATE: 170 MPH  NOMINAL: 132 MPH` is 170 — never the nominal."""
    up = (text or "").upper()
    m = re.search(r"(?:ULT(?:IMATE)?|VULT|V\s*ULT)\s*[:=]?\s*(\d{2,3})", up)
    if m:
        value = float(m.group(1))
    else:
        if "NOMINAL" in up or "VASD" in up:
            return None
        m = re.search(r"(\d{2,3})\s*MPH", up) or re.fullmatch(r"\s*(\d{2,3})\s*", up)
        if not m:
            return None
        value = float(m.group(1))
    return value if 85 <= value <= 250 else None


def roman(text: str) -> Optional[str]:
    value = norm_roman((text or "").strip().split("(")[0].strip())
    return value if value in ("I", "II", "III", "IV") else None


def letter(text: str) -> Optional[str]:
    return norm_letter(text)


def boolean(text: str) -> Optional[bool]:
    return norm_bool((text or "").strip().split()[0] if (text or "").strip() else None)


def edition(text: str) -> Optional[str]:
    """The Florida Building Code edition a line cites, or None.

    Only a line that names the FBC counts: `2020 NATIONAL ELECTRIC CODE` is a
    year and a code, but not this one.
    """
    up = (text or "").upper()
    if not re.search(r"\bFLORIDA\s+BUILDING\s+CODE\b|\bFBC\b|\bF\.B\.C\.", up):
        return None
    return norm_edition(up)


def text_value(text: str) -> Optional[str]:
    value = norm_text(text)
    if not value or value.strip("-– ") == "":
        return None
    return value


PARSERS: Dict[str, Callable[[str], Any]] = {
    "occupancy": occupancy, "construction": construction, "sprinkler": sprinkler,
    "area": area, "feet": feet, "inches": inches, "count": count, "factor": factor,
    "wind": wind, "roman": roman, "letter": letter, "bool": boolean,
    "edition": edition, "text": text_value,
}


def parse(kind: str, text: str) -> Any:
    fn = PARSERS.get(kind)
    return fn(text) if fn else None
