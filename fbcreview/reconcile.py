"""Reconciliation — the declaration and the drawings, side by side.

A permit set states its code data on the general sheets.  A user who fills in
the Project Declaration states the same data directly.  Neither is authoritative
over the other: the drawings are what will be submitted, and the declaration is
what the applicant believes is being submitted.  When those two differ, the
difference *is* the finding.

Every comparable field lands in exactly one of five states:

============== ================================ ==================================
State          Meaning                          Consequence
============== ================================ ==================================
CORROBORATED   declared and drawn agree         evidence is upgraded to HIGH — two
                                                independent sources, which is more
                                                than either alone
CONFLICT       declared and drawn disagree      a finding, and the whole rule set
                                                is evaluated under both readings
DECLARED_ONLY  drawings silent, user answered   rules run, and every finding that
                                                rests on the value says so
DRAWN_ONLY     user skipped, drawings readable  exactly as before this module
UNKNOWN        neither                          abstain, exactly as before
============== ================================ ==================================

A blank field is not permission to guess.  `UNKNOWN` abstains, and no default is
ever substituted for an unanswered question.
"""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field as dc_field, replace
from typing import Any, Dict, List, Optional, Tuple

from .confidence import Evidence, HIGH, MEDIUM, LOW
from .declaration import ProjectDeclaration
from . import declaration_schema as S
from .facts import ProjectFacts

# ── states ────────────────────────────────────────────────────────────────
CORROBORATED = "CORROBORATED"
CONFLICT = "CONFLICT"
DECLARED_ONLY = "DECLARED_ONLY"
DRAWN_ONLY = "DRAWN_ONLY"
UNKNOWN = "UNKNOWN"

STATES = (CORROBORATED, CONFLICT, DECLARED_ONLY, DRAWN_ONLY, UNKNOWN)

#: `Evidence.source` for anything the user typed. Reusing the existing Evidence
#: primitive rather than inventing a parallel provenance type is deliberate —
#: rules already know how to read confidence and a note off it.
DECLARATION_SOURCE = "declaration"

# ── scenarios ─────────────────────────────────────────────────────────────
AS_DRAWN = "as_drawn"
AS_DECLARED = "as_declared"
BOTH = "both"

# ── bases, for the wording on a finding card ──────────────────────────────
FROM_DRAWINGS = "drawings"
FROM_DECLARATION = "declaration"
FROM_BOTH = "both"


# ══════════════════════════════════════════════════════════════════════════
# Normalisation
#
# Most false conflicts come from formatting, not from disagreement. A tool that
# cries wolf on `II-B` versus `Type IIB` is switched off within a day, so the
# normaliser is a pure function and is table-tested.
# ══════════════════════════════════════════════════════════════════════════
_FT_IN = re.compile(r"""(\d+(?:\.\d+)?)\s*'\s*[-–]?\s*(\d+(?:\.\d+)?)?\s*(?:"|''|”)?""")
_NUMBER = re.compile(r"-?\d[\d,]*(?:\.\d+)?")
_ROMAN = re.compile(r"^(IV|III|II|I|V)\s*[-–\s]?\s*([AB])?$")

_TRUE_WORDS = {"YES", "Y", "TRUE", "T", "1", "PROVIDED", "REQUIRED"}
_FALSE_WORDS = {"NO", "N", "FALSE", "F", "0", "NONE", "NOT REQUIRED", "N/A", "NA"}

#: Long-form occupancy names as drafters write them on a code data block.
_OCCUPANCY_WORDS = {
    "BUSINESS": "B", "OFFICE": "B", "PROFESSIONAL": "B",
    "MERCANTILE": "M", "RETAIL": "M",
    "ASSEMBLY": "A", "EDUCATIONAL": "E", "FACTORY": "F", "INDUSTRIAL": "F",
    "STORAGE": "S", "WAREHOUSE": "S", "RESIDENTIAL": "R",
    "INSTITUTIONAL": "I", "UTILITY": "U", "HAZARDOUS": "H",
}

# Between them these two cover every edition `fbcreview.codes.editions` carries a
# row for. The structured extractor (`extract.formblocks.edition_from`) reads the
# same set off the code data block; a set states its edition once, so which of the
# two readers happens to see it must not decide whether the rule can answer. A
# missing entry here does not degrade gracefully — the sweep captures `6TH
# EDITION` off the sheet, fails to normalise it, drops it, and the rule abstains
# with "the set does not state this" about a line printed on G-001.

#: How a sheet names the edition itself. An ordinal is a statement *about the
#: building code*, so it is read before any year.
_EDITION_ORDINALS = {
    "6TH": "fbc2017", "SIXTH": "fbc2017",
    "7TH": "fbc2020", "SEVENTH": "fbc2020",
    "8TH": "fbc2023", "EIGHTH": "fbc2023",
    "9TH": "fbc2026", "NINTH": "fbc2026",
}

#: The same editions by year. Weaker evidence: a permit set lists several codes
#: on one line — `FBC 7TH EDITION (2020)` beside `NEC 2017` — and a bare year
#: may belong to any of them.
_EDITION_YEARS = {
    "2017": "fbc2017", "2020": "fbc2020",
    "2023": "fbc2023", "2026": "fbc2026",
}

#: Sprinkler standards, canonicalised. `YES` is what a drawing says when it
#: confirms a system without naming the standard — enough to know a system
#: exists, not enough to know which allowances it buys.
_SPRINKLER_PRESENT = {"NFPA13", "NFPA13R", "NFPA13D", "YES"}
_SPRINKLER_ABSENT = {"NONE"}


def norm_bool(value: Any) -> Optional[bool]:
    """`True`, `"Yes"`, `"y"` and `"TRUE"` are the same answer."""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().upper()
    if text in _TRUE_WORDS:
        return True
    if text in _FALSE_WORDS:
        return False
    return None


def norm_number(value: Any) -> Optional[float]:
    """A number, however it was written.

    `26'-4"` and `26.33` are the same height; `15,376 SF` and `15376` are the
    same area. Feet-and-inches is tried first because `26'-4"` also contains a
    bare `26` and a bare `4`.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    m = _FT_IN.search(text)
    if m and m.group(1) and ("'" in text):
        feet = float(m.group(1))
        inches = float(m.group(2)) if m.group(2) else 0.0
        return feet + inches / 12.0
    m = _NUMBER.search(text.replace(",", ""))
    return float(m.group()) if m else None


def norm_occupancy(value: Any) -> Optional[str]:
    """`Group B`, `BUSINESS`, `b` and `B` are the same classification.

    A bare letter and a lettered subgroup are kept distinct — `A` is not `A-3`
    for Table 1004.5 purposes — but a long-form word maps to its letter, which
    is all a code data block that says `OCCUPANCY: BUSINESS` gives you.
    """
    if value is None:
        return None
    text = re.sub(r"\s+", " ", str(value).strip().upper())
    text = re.sub(r"^(?:USE\s+)?(?:GROUP|OCCUPANCY|CLASSIFICATION)\s*[:\-]?\s*", "", text)
    text = text.strip(" .:,;/")
    if not text:
        return None
    for word, letter in _OCCUPANCY_WORDS.items():
        if text.startswith(word):
            # "BUSINESS GROUP B" carries the letter too; prefer the letter.
            m = re.search(r"\b([ABEFHIMRSU])\s*-?\s*(\d)?\b\s*$", text)
            if m:
                return f"{m.group(1)}-{m.group(2)}" if m.group(2) else m.group(1)
            return letter
    m = re.match(r"^([ABEFHIMRSU])\s*[-–]?\s*(\d)?$", text)
    if m:
        return f"{m.group(1)}-{m.group(2)}" if m.group(2) else m.group(1)
    return text


def norm_construction(value: Any) -> Optional[str]:
    """`II-B`, `IIB`, `Type II-B`, `TYPE 2B` and `type ii-b` are one answer."""
    if value is None:
        return None
    text = re.sub(r"\s+", " ", str(value).strip().upper())
    text = re.sub(r"^(?:TYPE\s+OF\s+CONSTRUCTION|CONSTRUCTION\s+TYPE|TYPE)\s*[:\-]?\s*", "", text)
    text = text.strip(" .:,;")
    # Arabic numerals happen: "2B" is Type II-B on plenty of sheets.
    arabic = re.match(r"^([1-5])\s*[-–\s]?\s*([AB])?$", text)
    if arabic:
        roman = ["I", "II", "III", "IV", "V"][int(arabic.group(1)) - 1]
        return f"{roman}-{arabic.group(2)}" if arabic.group(2) else roman
    m = _ROMAN.match(text.replace("-", "").replace(" ", "")) or _ROMAN.match(text)
    if m:
        return f"{m.group(1)}-{m.group(2)}" if m.group(2) else m.group(1)
    return text


def norm_roman(value: Any) -> Optional[str]:
    """Risk category: `III`, `3` and `iii` are the same row of Table 1604.5."""
    if value is None:
        return None
    text = str(value).strip().upper().strip(" .:,;")
    text = re.sub(r"^(?:RISK\s+)?CATEGORY\s*[:\-]?\s*", "", text)
    if text in ("1", "2", "3", "4"):
        return ["I", "II", "III", "IV"][int(text) - 1]
    return text or None


def norm_sprinkler(value: Any) -> Optional[str]:
    """Canonical sprinkler answer: a named standard, `YES`, or `NONE`."""
    if value is None:
        return None
    if isinstance(value, bool):
        return "YES" if value else "NONE"
    text = re.sub(r"[\s.\-]", "", str(value).strip().upper())
    if text in ("NFPA13", "NFPA13R", "NFPA13D"):
        return text
    if "13R" in text:
        return "NFPA13R"
    if "13D" in text:
        return "NFPA13D"
    if "NFPA13" in text or "903311" in text or "903312" in text:
        return "NFPA13"
    b = norm_bool(text)
    if b is True:
        return "YES"
    if b is False:
        return "NONE"
    return None


def norm_letter(value: Any) -> Optional[str]:
    """Exposure category — one letter, however it is decorated."""
    if value is None:
        return None
    text = str(value).strip().upper()
    text = re.sub(r"^(?:EXPOSURE\s*)?(?:CATEGORY|CAT)?\s*[:\-]?\s*", "", text)
    m = re.match(r"^([A-D])\b", text.strip())
    return m.group(1) if m else None


def norm_edition(value: Any) -> Optional[str]:
    """`fbc2020`, `7th Edition`, `FBC 2020` all name the same code edition."""
    if value is None:
        return None
    text = str(value).strip().upper()
    m = re.search(r"FBC\s*(20\d\d)", text) or re.match(r"^(20\d\d)$", text)
    if m:
        # An edition the corpus has no row for still resolves to a key, so the
        # rule can abstain on "no effective date for that edition" rather than
        # on "the set does not state this". Those are different answers.
        return _EDITION_YEARS.get(m.group(1), f"fbc{m.group(1)}")
    # Ordinals before years, and never in dictionary order: which key a dict
    # happens to yield first is not a reason to prefer one reading of a sheet.
    for lexicon in (_EDITION_ORDINALS, _EDITION_YEARS):
        for word, edition in lexicon.items():
            if re.search(rf"\b{word}\b", text):
                return edition
    return None


def norm_separation(value: Any) -> Optional[str]:
    """508.3 non-separated versus 508.4 separated."""
    if value is None:
        return None
    text = re.sub(r"[\s\-_]", "", str(value).strip().upper())
    if "508.3" in str(value) or text.startswith("NON") or "NONSEPARATED" in text:
        return "nonseparated"
    if "508.4" in str(value) or "SEPARATED" in text:
        return "separated"
    return None


def norm_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = re.sub(r"\s+", " ", str(value).strip().upper()).strip(" .,;:")
    return text or None


def norm_place(value: Any) -> Optional[str]:
    """A jurisdiction, however it is punctuated and abbreviated.

    `Lee County, FL` and `LEE COUNTY, FLORIDA` are the same authority. Getting
    this wrong reports a conflict about a comma, and a tool that reports a
    conflict about a comma is switched off before it reports a real one.
    """
    if value is None:
        return None
    text = re.sub(r"\s+", " ", str(value).strip().upper())
    text = re.sub(r"\bFLORIDA\b", "FL", text)
    text = re.sub(r"[.,;]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


#: Per-field normaliser. Keyed by declaration field name so there is exactly one
#: place that decides what "the same answer" means for each question.
NORMALISERS = {
    "occupancy_group": norm_occupancy,
    "mixed_occupancy": norm_bool,
    "separation_method": norm_separation,
    "construction_type": norm_construction,
    "building_area_sf": norm_number,
    "total_area_sf": norm_number,
    "height_ft": norm_number,
    "stories": norm_number,
    "sprinkler_system": norm_sprinkler,
    "wind_speed_mph": norm_number,
    "exposure_category": norm_letter,
    "risk_category": norm_roman,
    "zoning": norm_text,
    "jurisdiction": norm_place,
    "code_edition": norm_edition,
}


def normalise(key: str, value: Any) -> Any:
    """Canonical form of `value` for field `key`. Pure, and tested as such."""
    fn = NORMALISERS.get(key, norm_text)
    return fn(value)


def agree(key: str, declared: Any, drawn: Any) -> bool:
    """Do these two answers say the same thing about field `key`?

    Rounding on a drawing is not a disagreement; a different governing row is.
    """
    a, b = normalise(key, declared), normalise(key, drawn)
    if a is None or b is None:
        return True                       # nothing to disagree about
    if key == "sprinkler_system":
        return _sprinkler_agree(a, b)
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        tol = S.tolerance_for(key)
        if tol is None:
            return float(a) == float(b)   # stories: exact, there is no half storey
        return abs(float(a) - float(b)) <= tol.limit(max(abs(a), abs(b)))
    if key == "occupancy_group":
        # `B` from a code block that says BUSINESS does not contradict a
        # declared `B`; nor does a declared `A-3` contradict a drawn `A`.
        return a == b or a.startswith(f"{b}-") or b.startswith(f"{a}-")
    return a == b


def _sprinkler_agree(a: str, b: str) -> bool:
    """A drawing that says only `YES` cannot contradict a named standard.

    It can contradict `none`, and two *named* standards can contradict each
    other — 13R does not buy what 13 buys.
    """
    a_present, b_present = a in _SPRINKLER_PRESENT, b in _SPRINKLER_PRESENT
    if a_present != b_present:
        return False
    if a == "YES" or b == "YES":
        return True
    return a == b


# ══════════════════════════════════════════════════════════════════════════
# What the drawings say
# ══════════════════════════════════════════════════════════════════════════
def _sheet_hit(facts: ProjectFacts, pattern: re.Pattern) -> Optional[Tuple[str, int, re.Match]]:
    """First page whose text matches, with the sheet code that names it.

    General sheets are searched first: a code data block on G-002 is the
    authority for what the set claims, and a stray mention on a detail sheet is
    not.
    """
    order = sorted(facts.text_by_page, key=lambda p: (facts.sheet_code(p)[:1] != "G", p))
    for page in order:
        m = pattern.search(facts.text_by_page.get(page) or "")
        if m:
            return facts.sheet_code(page), page, m
    return None


# A code-analysis block states its values two ways, and both are in use.
#
# One punctuates — `BUILDING HEIGHT: 26'-4"` — and every pattern below that
# requires `[:=]` is reading that form. The other tabulates: the label is a row
# heading and the value sits in a column beside it, with nothing but whitespace
# between them. ITEC's G-002 is entirely the second kind:
#
#     PROPOSED  FBC ALLOWABLE*
#     HEIGHT                              26'-4"    75 FT.
#     STORIES                             1         4
#     SQUARE FOOTAGE PER FLOOR (MAXIMUM)  15,376    92,000
#
# The tabular patterns take the FIRST value on the row, which is the proposed
# one. That ordering is the whole point: the second column is the code limit,
# and reading it as the building would compare the limit against itself and
# report every set as compliant. The patterns bound how far they will look
# ahead for that first number, so a row whose value did not survive OCR runs
# past its own limit and matches nothing rather than borrowing the next row's.
# The gap may contain newlines: each recovered cell is written as its own run,
# so `get_text()` returns the row as "LABEL\n15,376\n92,000" rather than as one
# line, and a pattern that stopped at the line break would never reach a value.
#
# Punctuated forms are listed first, and the sweep stops at the first hit, so an
# explicit statement always beats a positional read.
_PATTERNS: Dict[str, List[str]] = {
    "occupancy_group": [
        # The value ends at a slash or a comma as often as at a line break:
        # `OCCUPANCY: BUSINESS / OFFICE, PROFESSIONAL SERVICES` is one row.
        r"\bOCCUPANCY(?:\s+GROUP|\s+CLASSIFICATION)?\s*[:=]\s*([A-Za-z][A-Za-z\-\s]{0,24}?)\s*(?:[/,;]|\s{2,}|[\r\n]|$)",
        r"\bUSE\s+AND\s+OCCUPANCY\s+CLASSIFICATION\s*[:=]\s*([A-Za-z][A-Za-z\-\s]{0,24}?)\s*(?:[/,;]|\s{2,}|[\r\n]|$)",
        r"\bGROUP\s+([ABEFHIMRSU]\s*-?\s*\d?)\b",
    ],
    "mixed_occupancy": [r"\bMIXED\s+OCCUPANC(?:Y|IES)\s*\??\s*[:=]?\s*(YES|NO)\b"],
    "separation_method": [
        r"\b(NON[\s\-]?SEPARATED)\b",
        r"\bSEPARAT(?:ED|ION)[^\r\n]{0,60}?\b(?:PER\s+)?TABLE\s+(508\.4)\b",
        r"\bTABLE\s+(508\.[34])\b",
    ],
    "construction_type": [
        r"\bCONSTRUCTION\s+TYPE\s*[:=]\s*([IV]{1,3}\s*-?\s*[AB]?|[1-5]\s*-?\s*[AB]?)\b",
        r"\bTYPE\s+OF\s+CONSTRUCTION\s*[:=]\s*([IV]{1,3}\s*-?\s*[AB]?|[1-5]\s*-?\s*[AB]?)\b",
        r"\bTYPE\s+([IV]{1,3}\s*-\s*[AB])\b",
    ],
    "building_area_sf": [
        r"\bBUILDING\s+AREA\s*[:=]\s*([\d,]+)\s*(?:SF|S\.F\.|SQ)",
        r"\bAREA\s+PER\s+(?:FLOOR|STOR[EY]{1,2})\s*[:=]\s*([\d,]+)",
        # Tabular. See the note on the tabular forms below.
        r"\bSQUARE\s+FOOTAGE\s+PER\s+(?:FLOOR|STOR[EY]{1,2})[^\d]{0,24}([\d][\d,]{2,})",
    ],
    "total_area_sf": [
        r"\bTOTAL\s+(?:BUILDING\s+)?AREA\s*[:=]\s*([\d,]+)\s*(?:SF|S\.F\.|SQ)",
        r"\bGROSS\s+(?:BUILDING\s+)?AREA\s*[:=]\s*([\d,]+)\s*(?:SF|S\.F\.|SQ)",
        r"\bTOTAL\s+SQUARE\s+FOOTAGE[^\d]{0,24}([\d][\d,]{2,})",
    ],
    "height_ft": [
        r"\bBUILDING\s+HEIGHT\s*[:=]\s*(\d+\s*'\s*-?\s*\d*\s*\"?|\d+(?:\.\d+)?\s*(?:FT|FEET))",
        r"\bMEAN\s+ROOF\s+HEIGHT\s*[:=]\s*(\d+\s*'\s*-?\s*\d*\s*\"?|\d+(?:\.\d+)?)",
        r"\bHEIGHT\s+(\d+\s*'\s*-?\s*\d*\s*\"?)",
    ],
    "stories": [
        r"\b(?:NUMBER\s+OF\s+)?STOR(?:IES|EYS)\s*[:=]\s*(\d+)\b",
        # The tabular row, ahead of the loose form below. ITEC states its
        # storey count only in a column beside the STORIES heading, and the
        # loose pattern was answering from "FSPK 1 STORY" in the sprinkler
        # status row instead — right on that set by luck, and wrong on any
        # multi-storey building carrying the same boilerplate.
        r"\bSTOR(?:IES|EYS)\s+(\d+)\b",
        r"\b(\d+)\s+STOR(?:Y|IES|EY|EYS)\b",
    ],
    "sprinkler_system": [
        r"\bSPRINKLER[^\r\n]{0,40}?[:=]\s*(NFPA\s*13[RD]?|YES|NO|NONE)\b",
        r"\b(NFPA\s*13[RD]?)\b",
        r"\bFULLY\s+SPRINKLERED\b()",
    ],
    "wind_speed_mph": [
        r"\bV\s*(?:ULT|ULTIMATE)?\s*[:=]\s*(\d{2,3})\s*MPH",
        r"\bULTIMATE\s+(?:DESIGN\s+)?WIND\s+SPEED\s*[:=]\s*(\d{2,3})",
        r"\bWIND\s+SPEED\s*[:=]\s*(\d{2,3})\s*MPH",
    ],
    "exposure_category": [r"\bEXPOSURE\s*(?:CATEGORY)?\s*[:=]?\s*([B-D])\b"],
    "risk_category": [r"\bRISK\s*CATEGORY\s*[:=]?\s*(I{1,3}V?|IV|[1-4])\b"],
    "zoning": [r"\bZONING\s*[:=]\s*([^\r\n]{1,40}?)(?:\s{2,}|[\r\n]|$)"],
    "jurisdiction": [
        r"\b([A-Z][A-Za-z\.\s]{2,28}?\s+COUNTY,?\s+(?:FL|FLORIDA))\b",
        r"\bJURISDICTION\s*[:=]\s*([^\r\n]{1,40}?)(?:\s{2,}|[\r\n]|$)",
    ],
    "code_edition": [
        r"\bFLORIDA\s+BUILDING\s+CODE[^\r\n]{0,40}?\b(\d(?:ST|ND|RD|TH)\s+EDITION)",
        r"\b(\d(?:ST|ND|RD|TH)\s+EDITION)[^\r\n]{0,30}?\((\d{4})\)",
        r"\bFLORIDA\s+BUILDING\s+CODE[,\s]+(20\d\d)\b",
    ],
}


def drawn_declaration(facts: ProjectFacts) -> Dict[str, Evidence]:
    """Everything the drawings themselves say about the declaration's fields.

    Only live text is read; nothing here reaches into `fbcreview/extract/`, and
    a set whose code data block is a pasted picture yields very little — which
    is the honest answer for that set, and exactly the case the declaration
    exists to cover.
    """
    out: Dict[str, Evidence] = {}

    # Values the pipeline already extracted are preferred: they came off a
    # structured block rather than a text sweep.
    meta = facts.meta
    if meta.get("area_g0_sf") is not None:
        out["building_area_sf"] = Evidence(
            float(meta["area_g0_sf"]), "G-0 project data", MEDIUM,
            "building area stated on the general sheet")
    if meta.get("area_g1_sf") is not None:
        out["total_area_sf"] = Evidence(
            float(meta["area_g1_sf"]), "G-1 occupancy tables", MEDIUM,
            "sum of the occupancy tables")
    if meta.get("risk_category"):
        out["risk_category"] = Evidence(
            norm_roman(meta["risk_category"]), "G-0 project data", MEDIUM, "")
    if meta.get("sprinklered") is not None:
        out["sprinkler_system"] = Evidence(
            "YES" if meta["sprinklered"] else "NONE", "G-1 building code analysis",
            MEDIUM, "stated as a yes/no, so the standard is not established")

    for key, patterns in _PATTERNS.items():
        if key in out:
            continue
        for raw in patterns:
            hit = _sheet_hit(facts, re.compile(raw, re.I))
            if not hit:
                continue
            sheet, page, m = hit
            captured = next((g for g in m.groups() if g), m.group(0))
            value = normalise(key, captured)
            if value is None:
                continue
            out[key] = Evidence(value, sheet, MEDIUM,
                                f"read from the live text of {sheet}", page)
            break

    _apply_separation_implication(facts, out)
    return out


def _apply_separation_implication(facts: ProjectFacts, out: Dict[str, Evidence]) -> None:
    """`Table 508.4` on a sheet is an assertion that the building is mixed.

    508.4 is the *separated occupancies* method and it exists only for a
    building with more than one occupancy group in it. A set that cites it
    while its own summary row reads `MIXED OCCUPANCY? NO` is contradicting
    itself, and the citation is the harder evidence: a drafter edits a summary
    row, they do not casually cite a table by number.
    """
    sep = out.get("separation_method")
    if not sep or sep.value != "separated":
        return
    stated = out.get("mixed_occupancy")
    note = (f"{sep.source} cites Table 508.4, which applies only to a "
            f"mixed-occupancy building")
    if stated is not None and stated.value is False:
        note += "; the same sheet's summary row reads MIXED OCCUPANCY? NO"
    out["mixed_occupancy"] = Evidence(True, sep.source, MEDIUM, note, sep.page)


# ══════════════════════════════════════════════════════════════════════════
# The reconciliation itself
# ══════════════════════════════════════════════════════════════════════════
@dataclass
class Reconciled:
    field: str
    declared: Optional[Evidence]      # source == "declaration"
    drawn: Optional[Evidence]         # source == the sheet code it was read from
    state: str

    @property
    def declared_value(self) -> Any:
        return self.declared.value if self.declared else None

    @property
    def drawn_value(self) -> Any:
        return self.drawn.value if self.drawn else None

    def evidence(self, scenario: str = AS_DRAWN) -> Optional[Evidence]:
        """The evidence a rule should use under one reading of the set.

        CORROBORATED upgrades to HIGH and records both sources — that upgrade is
        the reward for answering, and it is meant to be visible in the register.
        """
        if self.state == CORROBORATED:
            assert self.declared and self.drawn
            return Evidence(
                self.drawn.value, f"{self.drawn.source} + declaration", HIGH,
                f"declared as {_show(self.declared.value)} and stated on "
                f"{self.drawn.source} as {_show(self.drawn.value)} — two independent sources",
                self.drawn.page)
        if self.state == CONFLICT:
            return self.declared if scenario == AS_DECLARED else self.drawn
        if self.state == DECLARED_ONLY:
            return self.declared
        if self.state == DRAWN_ONLY:
            return self.drawn
        return None

    def basis(self) -> str:
        if self.state == CORROBORATED:
            return FROM_BOTH
        if self.state == DECLARED_ONLY:
            return FROM_DECLARATION
        return FROM_DRAWINGS


def _show(value: Any) -> str:
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float) and value == int(value):
        return f"{int(value):,}"
    if isinstance(value, float):
        return f"{value:,.2f}"
    if isinstance(value, int):
        return f"{value:,}"
    return str(value)


@dataclass
class Resolved:
    """One building fact as a rule sees it, under one scenario."""
    key: str
    value: Any
    evidence: Evidence
    state: str
    basis: str


@dataclass
class ReconciledFacts:
    facts: ProjectFacts
    declaration: ProjectDeclaration
    fields: Dict[str, Reconciled] = dc_field(default_factory=dict)

    def conflicts(self) -> List[Reconciled]:
        return [r for r in self.fields.values() if r.state == CONFLICT]

    def material_conflicts(self) -> List[Reconciled]:
        """Conflicts on a field some rule actually consumes.

        Zoning and jurisdiction are free text that no rule reads. A
        disagreement there is worth printing on the declaration page, but it
        cannot change a single finding — so it must not double the work.
        """
        return [r for r in self.conflicts()
                if (S.BY_KEY[r.field].unlocks if r.field in S.BY_KEY else [])]

    def has_conflict(self) -> bool:
        return bool(self.material_conflicts())

    def state_of(self, key: str) -> str:
        r = self.fields.get(key)
        return r.state if r else UNKNOWN

    # ── the two readings ──────────────────────────────────────────────────
    def as_drawn(self) -> ProjectFacts:
        """The set as submitted. This is what the AHJ reviews."""
        return self._view(AS_DRAWN)

    def as_declared(self) -> ProjectFacts:
        """The set as the applicant described it."""
        return self._view(AS_DECLARED)

    def _view(self, scenario: str) -> ProjectFacts:
        resolved: Dict[str, Resolved] = {}
        for key, rec in self.fields.items():
            ev = rec.evidence(scenario)
            if ev is None or ev.value is None:
                continue
            resolved[key] = Resolved(key, ev.value, ev, rec.state, rec.basis())
        meta = dict(self.facts.meta)
        meta["building"] = resolved
        meta["scenario"] = scenario
        meta["declaration"] = self.declaration
        meta["reconciled"] = self
        return replace(self.facts, meta=meta)


def reconcile(facts: ProjectFacts,
              declaration: Optional[ProjectDeclaration] = None) -> ReconciledFacts:
    """Put the declaration and the drawings side by side, field by field."""
    declaration = declaration or ProjectDeclaration()
    drawn = drawn_declaration(facts)
    fields: Dict[str, Reconciled] = {}

    for key in S.BY_KEY:
        raw_declared = getattr(declaration, key, None)
        declared_ev = None
        if raw_declared is not None:
            declared_ev = Evidence(normalise(key, raw_declared), DECLARATION_SOURCE,
                                   MEDIUM, "stated in the project declaration")
        drawn_ev = drawn.get(key)
        if drawn_ev is not None and drawn_ev.value is None:
            drawn_ev = None

        if declared_ev and drawn_ev:
            state = CORROBORATED if agree(key, declared_ev.value, drawn_ev.value) else CONFLICT
        elif declared_ev:
            state = DECLARED_ONLY
        elif drawn_ev:
            state = DRAWN_ONLY
        else:
            state = UNKNOWN
        fields[key] = Reconciled(key, declared_ev, drawn_ev, state)

    return ReconciledFacts(facts=facts, declaration=declaration, fields=fields)


# ══════════════════════════════════════════════════════════════════════════
# What a rule reads
# ══════════════════════════════════════════════════════════════════════════
def building(facts: ProjectFacts, key: str) -> Optional[Resolved]:
    """The resolved building fact `key`, or `None` if neither source states it.

    `None` means abstain. There is no default here on purpose: a blank field is
    not permission to guess.
    """
    return (facts.meta.get("building") or {}).get(key)


def value_of(facts: ProjectFacts, key: str) -> Any:
    """The resolved value from whichever source has it. For new rules."""
    r = building(facts, key)
    return r.value if r else None


#: States in which the declaration had something to say about a field.
PARTICIPATING = (CORROBORATED, CONFLICT, DECLARED_ONLY)


def declared_value(facts: ProjectFacts, key: str) -> Any:
    """The resolved value, but only where the declaration participates.

    This is what the rules that predate the declaration use for their fallbacks.
    The distinction matters twice over:

    * A set submitted with no declaration must review exactly as it did before
      this feature existed — for every set, not only the one in the regression
      fixture. Letting a text sweep feed an old rule an input it never had would
      change what that rule reports on work nobody declared anything about,
      which is a behaviour change dressed up as a refactor.
    * `XSHEET.BUILDING_AREA` compares the area stated on two *different* general
      sheets. Handing it two readings of the same row would produce a confident
      PASS that compared a number with itself.
    """
    r = building(facts, key)
    if r is None or r.state not in PARTICIPATING:
        return None
    return r.value


def source_label(facts: ProjectFacts, key: str, what: str) -> str:
    """How to name a declaration-sourced value in a finding's prose.

    Never "your declaration" for something the drawings said: the markup may not
    attribute to one source what came from the other, in either direction.
    """
    r = building(facts, key)
    if r is None:
        return "not stated"
    if r.basis == FROM_DECLARATION:
        return f"your declaration ({what})"
    if r.basis == FROM_BOTH:
        rf = facts.meta.get("reconciled")
        rec = rf.fields.get(key) if rf else None
        if rec is not None and rec.drawn is not None:
            return f"your declaration, confirmed on {rec.drawn.source}"
        return f"your declaration ({what})"
    return r.evidence.source


def scenario_of(facts: ProjectFacts) -> str:
    return facts.meta.get("scenario", AS_DRAWN)


def basis_of(facts: ProjectFacts, *keys: str) -> str:
    """The weakest basis among the facts a finding rests on.

    A finding that leans on one declared-only value rests on user input however
    many drawn values sit beside it, and the card has to say so — the markup
    must never attribute to the drawings something the drawings do not say.
    """
    bases = [r.basis for r in (building(facts, k) for k in keys) if r]
    if any(b == FROM_DECLARATION for b in bases):
        return FROM_DECLARATION
    if bases and all(b == FROM_BOTH for b in bases):
        return FROM_BOTH
    return FROM_DRAWINGS


def legacy_context(facts: ProjectFacts) -> Tuple[str, bool]:
    """Occupancy group and sprinkler status for the rules that predate this module.

    Those rules shipped with a fallback — `A-3`, and whatever the general sheets'
    building-code-analysis block said about sprinklers, defaulting to yes — and
    the existing regression set depends on it.  It is preserved verbatim rather
    than quietly turned into an abstention, so a set submitted with no
    declaration reviews exactly as it did before this feature.

    The reconciled value is used **only when the declaration participates** —
    CORROBORATED, CONFLICT or DECLARED_ONLY.  A value the text sweep found on
    its own (DRAWN_ONLY) is deliberately not fed to these rules: that would
    change what they report on a set nobody declared anything about, which is
    a behaviour change dressed up as a refactor.

    New rules do not use this at all.  They read `building()` and stand down
    when it returns `None`.
    """
    participating = (CORROBORATED, CONFLICT, DECLARED_ONLY)

    r = building(facts, "occupancy_group")
    if r is not None and r.state in participating:
        group = str(r.value)
    else:
        group = str(facts.meta.get("occupancy_group") or "A-3")

    s = building(facts, "sprinkler_system")
    if s is not None and s.state in participating:
        sprinklered = s.value in _SPRINKLER_PRESENT
    else:
        sprinklered = bool(facts.meta.get("sprinklered", True))
    return group, sprinklered
