"""Field metadata for the Project Declaration — data, not UI.

This module is the single source of truth for what the questionnaire asks.  The
API serves it verbatim (`GET /api/config`), and the Angular client renders
whatever it is handed.  Adding an occupancy group, retitling a question or
softening a tolerance therefore happens here and reaches the browser with no
frontend change — and, more importantly, a code label can never drift between
two copies, because there is only one.

Both vocabularies live here side by side.  `pro_*` is the language a designer or
plans examiner uses; `simple_*` is the same question for an owner who has never
opened a code book.  They describe the same field and must stay answerable by
the same value.

`unlocks` names the rules a field enables.  The client reads it to tell someone
what answering buys them ("3 checks will abstain without these"), and it is the
honest way to motivate completion: the tool is stating what it will refuse to
guess at.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

# ── field kinds ───────────────────────────────────────────────────────────
ENUM, BOOL, NUMBER, INTEGER, TEXT = "enum", "bool", "number", "integer", "text"

# ── question groups, in the order the form shows them ─────────────────────
GROUPS = [
    ("occupancy", "Occupancy"),
    ("construction", "Construction & size"),
    ("fire", "Fire protection"),
    ("structural", "Structural"),
    ("context", "Context"),
]


@dataclass(frozen=True)
class Tolerance:
    """How far apart two numbers may be before they count as disagreeing.

    Effective tolerance is `max(abs_, rel * value)`, so a large building gets a
    proportional allowance and a small one still gets a usable floor.

    The reasoning behind each value, because a tolerance that cries wolf gets
    the tool switched off and a tolerance that is too loose finds nothing:

    * **Areas** — `max(50 sf, 2 %)`.  Drafters round gross area to the nearest
      hundred and take it off a different boundary (face of stud vs centreline)
      than the leasing plan does.  Two percent of a 15,376 sf shell is 308 sf,
      which absorbs that; 18,000 against 15,376 is 17 percent and is a real
      disagreement about what building is being permitted.
    * **Height** — `0.5 ft`.  Sheets print `26'-4"` and declarations get typed
      as `26.33`; six inches covers the rounding and any parapet-versus-eave
      quibble, while a storey is ten times that.
    * **Wind speed** — `1 mph`.  ASCE 7 contours are drawn at 5 mph intervals
      and are read off a map by eye, so a 1 mph band catches a different
      contour without flagging a different reader.
    * **Stories** — exact.  There is no such thing as half a storey in
      Table 504.4; a disagreement here changes which row governs.
    """

    abs_: float = 0.0
    rel: float = 0.0

    def limit(self, value: float) -> float:
        return max(self.abs_, self.rel * abs(float(value)))

    def to_dict(self) -> Dict[str, float]:
        return {"abs": self.abs_, "rel": self.rel}


AREA_TOL = Tolerance(abs_=50.0, rel=0.02)
HEIGHT_TOL = Tolerance(abs_=0.5)
WIND_TOL = Tolerance(abs_=1.0)


@dataclass(frozen=True)
class Field:
    key: str
    kind: str
    pro_label: str
    pro_help: str
    simple_label: str
    simple_help: str
    group: str
    choices: Optional[List[str]] = None
    choice_labels: Optional[Dict[str, str]] = None
    unit: str = ""
    unlocks: List[str] = field(default_factory=list)
    tolerance: Optional[Tolerance] = None

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["tolerance"] = self.tolerance.to_dict() if self.tolerance else None
        return d


# ── occupancy groups ──────────────────────────────────────────────────────
# Wider than fbcreview.options.OCCUPANCY_GROUPS, which listed only what the
# first test set exercised. A declaration is answered by the applicant about
# their own building, so the list has to cover the ones a small commercial
# permit actually uses.
OCCUPANCY_CHOICES = {
    "A-1": "A-1 — assembly, fixed seating (theatre)",
    "A-2": "A-2 — assembly, food and drink",
    "A-3": "A-3 — assembly, worship / recreation / amusement (studios, gyms)",
    "A-4": "A-4 — assembly, indoor sporting with spectator seating",
    "A-5": "A-5 — assembly, outdoor",
    "B": "B — business",
    "E": "E — educational",
    "F-1": "F-1 — factory, moderate hazard",
    "F-2": "F-2 — factory, low hazard",
    "I-1": "I-1 — institutional, supervised residential",
    "I-2": "I-2 — institutional, medical care",
    "M": "M — mercantile",
    "R-1": "R-1 — residential, transient (hotel)",
    "R-2": "R-2 — residential, permanent (apartments)",
    "R-3": "R-3 — residential, one and two family",
    "S-1": "S-1 — storage, moderate hazard",
    "S-2": "S-2 — storage, low hazard",
    "U": "U — utility and miscellaneous",
}

CONSTRUCTION_CHOICES = {
    "I-A": "I-A — non-combustible, protected (3 hr frame)",
    "I-B": "I-B — non-combustible, protected (2 hr frame)",
    "II-A": "II-A — non-combustible, 1 hr protected",
    "II-B": "II-B — non-combustible, unprotected",
    "III-A": "III-A — non-combustible exterior, 1 hr protected interior",
    "III-B": "III-B — non-combustible exterior, unprotected interior",
    "IV": "IV — heavy timber",
    "V-A": "V-A — any permitted material, 1 hr protected",
    "V-B": "V-B — any permitted material, unprotected",
}

SPRINKLER_CHOICES = {
    "none": "No sprinkler system",
    "nfpa13": "NFPA 13 — full system (903.3.1.1)",
    "nfpa13r": "NFPA 13R — residential up to four storeys (903.3.1.2)",
    "nfpa13d": "NFPA 13D — one and two family dwellings (903.3.1.3)",
}

SEPARATION_CHOICES = {
    "separated": "Separated occupancies — FBC-B 508.4",
    "nonseparated": "Non-separated occupancies — FBC-B 508.3",
}

EXPOSURE_CHOICES = {
    "B": "B — urban, suburban or wooded, obstructions for 1,500 ft upwind",
    "C": "C — open terrain with scattered obstructions",
    "D": "D — flat unobstructed area, or water for more than 5,000 ft",
}

RISK_CHOICES = {
    "I": "I — low hazard to human life (agricultural, minor storage)",
    "II": "II — everything not in I, III or IV",
    "III": "III — substantial hazard (assembly over 300, schools, some healthcare)",
    "IV": "IV — essential facilities (hospitals, fire, police, emergency)",
}

EDITION_CHOICES = {
    "fbc2020": "Florida Building Code, 7th Edition (2020)",
    "fbc2023": "Florida Building Code, 8th Edition (2023)",
    "fbc2026": "Florida Building Code, 9th Edition (2026)",
}


FIELDS: List[Field] = [
    # ── occupancy ─────────────────────────────────────────────────────────
    Field(
        key="occupancy_group",
        kind=ENUM,
        choices=list(OCCUPANCY_CHOICES),
        choice_labels=dict(OCCUPANCY_CHOICES),
        pro_label="Occupancy group",
        pro_help="Classification per FBC-B Chapter 3. The single most load-bearing answer here.",
        simple_label="What is the building used for?",
        simple_help=(
            "Offices and clinics are business. Shops are mercantile. Studios, gyms and "
            "places of worship are assembly. Warehouses are storage."
        ),
        group="occupancy",
        unlocks=[
            "HEIGHT_AREA.TABLE_504_HEIGHT", "HEIGHT_AREA.TABLE_504_STORIES",
            "HEIGHT_AREA.TABLE_506_AREA", "EGRESS.OCCUPANT_LOAD_COMPUTED",
            "EGRESS.COMMON_PATH", "EGRESS.TRAVEL_DISTANCE", "EGRESS.DEAD_END",
            "DECL.OCCUPANCY",
        ],
    ),
    Field(
        key="mixed_occupancy",
        kind=BOOL,
        pro_label="Mixed occupancy",
        pro_help="More than one occupancy group in the building, per FBC-B 508.",
        simple_label="Is more than one kind of use in this building?",
        simple_help=(
            "A shell with a gym, a clinic and a shop in it is mixed. One tenant doing one "
            "thing is not."
        ),
        group="occupancy",
        unlocks=["DECL.MIXED_OCCUPANCY"],
    ),
    Field(
        key="separation_method",
        kind=ENUM,
        choices=list(SEPARATION_CHOICES),
        choice_labels=dict(SEPARATION_CHOICES),
        pro_label="Separation method",
        pro_help="508.4 separated with rated assemblies, or 508.3 non-separated.",
        simple_label="Are the different uses separated by fire-rated walls?",
        simple_help="Only answer this if you said the building has more than one kind of use.",
        group="occupancy",
        unlocks=["DECL.MIXED_OCCUPANCY"],
    ),
    # ── construction and size ─────────────────────────────────────────────
    Field(
        key="construction_type",
        kind=ENUM,
        choices=list(CONSTRUCTION_CHOICES),
        choice_labels=dict(CONSTRUCTION_CHOICES),
        pro_label="Construction type",
        pro_help="Type per FBC-B Table 601.",
        simple_label="How is the building built?",
        simple_help=(
            "Steel or concrete frame, protected or unprotected; wood frame; masonry. If the "
            "structure is bare steel with no sprayed fireproofing, it is II-B."
        ),
        group="construction",
        unlocks=[
            "HEIGHT_AREA.TABLE_504_HEIGHT", "HEIGHT_AREA.TABLE_504_STORIES",
            "HEIGHT_AREA.TABLE_506_AREA", "FIRE.TABLE_601", "DECL.CONSTRUCTION_TYPE",
        ],
    ),
    Field(
        key="building_area_sf",
        kind=NUMBER,
        unit="sf",
        pro_label="Building area per storey",
        pro_help="Largest floor, gross, as Table 506.2 measures it.",
        simple_label="How big is the largest floor?",
        simple_help="Square feet, outside wall to outside wall.",
        group="construction",
        unlocks=["HEIGHT_AREA.TABLE_506_AREA", "EGRESS.OCCUPANT_LOAD_COMPUTED",
                 "XSHEET.BUILDING_AREA", "DECL.BUILDING_AREA"],
        tolerance=AREA_TOL,
    ),
    Field(
        key="total_area_sf",
        kind=NUMBER,
        unit="sf",
        pro_label="Total building area",
        pro_help="All storeys, gross.",
        simple_label="How big is the whole building?",
        simple_help="Every floor added together, in square feet.",
        group="construction",
        unlocks=["XSHEET.BUILDING_AREA", "DECL.BUILDING_AREA"],
        tolerance=AREA_TOL,
    ),
    Field(
        key="height_ft",
        kind=NUMBER,
        unit="ft",
        pro_label="Building height",
        pro_help="Grade plane to average roof, as 202 defines it.",
        simple_label="How tall is the building?",
        simple_help="Feet from the ground to the roof. A single-storey shell is usually 20 to 30.",
        group="construction",
        unlocks=["HEIGHT_AREA.TABLE_504_HEIGHT", "DECL.HEIGHT"],
        tolerance=HEIGHT_TOL,
    ),
    Field(
        key="stories",
        kind=INTEGER,
        pro_label="Storeys above grade plane",
        pro_help="Counted per 202 and Table 504.4.",
        simple_label="How many floors?",
        simple_help="Not counting a basement that is fully below ground.",
        group="construction",
        unlocks=["HEIGHT_AREA.TABLE_504_STORIES", "DECL.STORIES"],
    ),
    # ── fire protection ───────────────────────────────────────────────────
    Field(
        key="sprinkler_system",
        kind=ENUM,
        choices=list(SPRINKLER_CHOICES),
        choice_labels=dict(SPRINKLER_CHOICES),
        pro_label="Sprinkler system",
        pro_help=(
            "Which standard the system is designed to. Only 903.3.1.1 and 903.3.1.2 systems "
            "buy the Chapter 10 and Chapter 5 increases."
        ),
        simple_label="Does the building have fire sprinklers?",
        simple_help="If it does, the fire protection drawings say which standard they follow.",
        group="fire",
        unlocks=[
            "HEIGHT_AREA.TABLE_504_HEIGHT", "HEIGHT_AREA.TABLE_504_STORIES",
            "HEIGHT_AREA.TABLE_506_AREA", "EGRESS.COMMON_PATH",
            "EGRESS.TRAVEL_DISTANCE", "EGRESS.DEAD_END", "DECL.SPRINKLER",
        ],
    ),
    # ── structural / wind ─────────────────────────────────────────────────
    Field(
        key="wind_speed_mph",
        kind=NUMBER,
        unit="mph",
        pro_label="Ultimate design wind speed, Vult",
        pro_help="From the ASCE 7 map the governing edition adopts, for this parcel.",
        simple_label="What wind speed was the building designed for?",
        simple_help="The structural sheets state it in miles per hour. In Florida it is 140 to 180.",
        group="structural",
        unlocks=["STRUCT.WIND_STANDARD", "DECL.WIND_SPEED"],
        tolerance=WIND_TOL,
    ),
    Field(
        key="exposure_category",
        kind=ENUM,
        choices=list(EXPOSURE_CHOICES),
        choice_labels=dict(EXPOSURE_CHOICES),
        pro_label="Exposure category",
        pro_help="ASCE 7 26.7.3, judged from the upwind terrain in every direction.",
        simple_label="What is around the building?",
        simple_help=(
            "Built-up or wooded on all sides, open fields, or flat open ground and water. "
            "Open ground means higher wind pressure."
        ),
        group="structural",
        unlocks=["STRUCT.WIND_STANDARD", "DECL.EXPOSURE"],
    ),
    Field(
        key="risk_category",
        kind=ENUM,
        choices=list(RISK_CHOICES),
        choice_labels=dict(RISK_CHOICES),
        pro_label="Risk category",
        pro_help="FBC-B Table 1604.5.",
        simple_label="How critical is this building?",
        simple_help=(
            "Most commercial buildings are II. Schools and large assembly are III. Hospitals "
            "and emergency services are IV."
        ),
        group="structural",
        unlocks=["STRUCT.WIND_STANDARD", "XSHEET.RISK_CATEGORY", "DECL.RISK_CATEGORY"],
    ),
    # ── context ───────────────────────────────────────────────────────────
    Field(
        key="zoning",
        kind=TEXT,
        pro_label="Zoning district",
        pro_help="As the local land development code names it. Free text — it is jurisdictional.",
        simple_label="What is the property zoned?",
        simple_help="The zoning designation on the survey or the county property record.",
        group="context",
        unlocks=[],
    ),
    Field(
        key="jurisdiction",
        kind=TEXT,
        pro_label="Authority having jurisdiction",
        pro_help="The building department that will review this. Local amendments follow from it.",
        simple_label="Which city or county issues the permit?",
        simple_help="For example, Lee County, FL.",
        group="context",
        unlocks=[],
    ),
    Field(
        key="code_edition",
        kind=ENUM,
        choices=list(EDITION_CHOICES),
        choice_labels=dict(EDITION_CHOICES),
        pro_label="Code edition cited",
        pro_help="The edition the set is drawn and designed to, not the one in force today.",
        simple_label="Which edition of the code was this designed to?",
        simple_help=(
            "The cover sheet says. If the set is a few years old this is the answer that "
            "matters most — editions expire."
        ),
        group="context",
        unlocks=["CODE.EDITION_CURRENT", "STRUCT.WIND_STANDARD", "DECL.CODE_EDITION"],
    ),
]

BY_KEY: Dict[str, Field] = {f.key: f for f in FIELDS}

#: Every rule any field unlocks, deduplicated. The client divides by this to
#: say how many checks are still standing down.
ALL_UNLOCKED: List[str] = sorted({r for f in FIELDS for r in f.unlocks})


def tolerance_for(key: str) -> Optional[Tolerance]:
    f = BY_KEY.get(key)
    return f.tolerance if f else None


def choices_for(key: str) -> Optional[List[str]]:
    f = BY_KEY.get(key)
    return list(f.choices) if f and f.choices else None


def validate(values: Dict[str, Any]) -> List[str]:
    """Check a submitted declaration against this schema.

    Returns a list of human-readable problems, each naming the offending field.
    An empty list means the body is acceptable. Nothing is silently dropped or
    coerced: a value the schema does not recognise is an error, because
    accepting it quietly would mean reviewing against something the user did
    not choose.
    """
    problems: List[str] = []
    for key, value in (values or {}).items():
        f = BY_KEY.get(key)
        if f is None:
            problems.append(f"{key} is not a project declaration field.")
            continue
        if value is None:
            continue
        if f.kind == ENUM:
            if not isinstance(value, str) or value not in (f.choices or []):
                problems.append(
                    f"{key}: {value!r} is not one of {', '.join(f.choices or [])}."
                )
        elif f.kind == BOOL:
            if not isinstance(value, bool):
                problems.append(f"{key}: expected true or false, got {value!r}.")
        elif f.kind in (NUMBER, INTEGER):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                problems.append(f"{key}: expected a number, got {value!r}.")
            elif f.kind == INTEGER and float(value) != int(value):
                problems.append(f"{key}: expected a whole number, got {value!r}.")
            elif float(value) < 0:
                problems.append(f"{key}: expected a positive number, got {value!r}.")
        elif f.kind == TEXT:
            if not isinstance(value, str):
                problems.append(f"{key}: expected text, got {value!r}.")
            elif len(value) > 200:
                problems.append(f"{key}: longer than the 200 character limit.")
    return problems


def to_dicts() -> List[Dict[str, Any]]:
    """The whole schema, ready to serve. The frontend renders this."""
    return [f.to_dict() for f in FIELDS]
