"""The field catalog: what the rules need to know, and every way a sheet says it.

This is the vocabulary `reconcile._PATTERNS` tried to be with fifteen regexes.
It is data, in the same hand-reviewed style as `fbcreview/codes/` — committed,
diffable, tested, never learned or fetched — and it serves both readers: the
deterministic reader matches layout pairs against it, and the AI reader is told
to look for exactly these fields, so the two answer the same questions and can
be checked against each other.

A spec says, for one fact:

* `labels` — the phrasings that name it, strongest first. Matched against the
  pair's label with its punctuation and cited section stripped: whole phrase
  first (score 1.0), then as a contained phrase (0.8).
* `disqualify` — words that, in the label, mean "not this fact". The safety
  mechanism, and load-bearing: `SITE AREA` is not the building area and
  `OCCUPANT LOAD FACTOR` is not the occupant load.
* `context_disqualify` / `context_require` — the same test against the headings
  the label sits under. `OCCUPANT LOAD: 35` under `EXIT DISCHARGE 1` is one
  exit's share, not the building's.
* `parse` — how the value is read, by name, from `fbcreview.read.parse`.
* `roles` — for the egress audit rows, which column is the code requirement the
  drafter stated and which is what the design provides.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Tuple

#: Words in a label that mark the value as the limit the drafter says the code
#: sets, rather than what the design provides.
LIMIT_WORDS = ("MAX", "MAXIMUM", "MIN", "MINIMUM", "ALLOWABLE", "REQUIRED", "LIMIT")
PROVIDED_WORDS = ("PROVIDED", "ACTUAL", "PROPOSED", "DESIGN")


@dataclass(frozen=True)
class FieldSpec:
    key: str
    labels: Tuple[str, ...]
    parse: str
    description: str
    disqualify: Tuple[str, ...] = ()
    context_disqualify: Tuple[str, ...] = ()
    context_require: Tuple[str, ...] = ()
    sections: Tuple[str, ...] = ()
    #: The ProjectDeclaration field this fact is the drawn side of, if any.
    declaration: str = ""
    #: True for the audit rows that print a requirement and a provided value.
    roles: bool = False
    #: A value must carry one of these units to count (`170 MPH`, `1,436 SF`).
    units: Tuple[str, ...] = ()


_AREA_NOT_BUILDING = ("SITE", "LOT", "PARCEL", "PARKING", "IMPERVIOUS", "PERVIOUS",
                      "LANDSCAPE", "OPEN SPACE", "WORK", "ALLOWABLE", "SMOKE", "REFUGE",
                      "DRAINAGE", "FLOOD", "RETENTION", "DETENTION", "POND", "SERVICE",
                      "PER OCCUPANT", "PER PERSON", "STORAGE", "FIRE", "OUTDOOR", "ROOF",
                      "LANDING", "LEASABLE", "RENTABLE")

FIELDS: Tuple[FieldSpec, ...] = (
    # ── what the building is ──────────────────────────────────────────────
    FieldSpec(
        "occupancy_group",
        ("OCCUPANCY", "OCCUPANCY GROUP", "OCCUPANCY CLASSIFICATION", "USE GROUP",
         "USE AND OCCUPANCY CLASSIFICATION", "USE AND OCCUPANCY", "OCCUPANCY TYPE",
         "OCCUPANCY CLASS", "CLASSIFICATION OF OCCUPANCY"),
        "occupancy", "the building's occupancy classification (Group A-3, B, M …)",
        disqualify=("LOAD", "SEPARATION", "MIXED", "DAILY", "CONTENT", "CERTIFICATE",
                    "PERMIT", "SENSOR", "SENSORS", "LEGEND", "ASSEMBLY OCCUPANCY"),
        declaration="occupancy_group"),
    FieldSpec(
        "mixed_occupancy", ("MIXED OCCUPANCY", "MIXED OCCUPANCIES", "MIXED USE OCCUPANCY"),
        "bool", "whether the building is a mixed occupancy (yes/no)",
        declaration="mixed_occupancy"),
    FieldSpec(
        "construction_type",
        ("TYPE OF CONSTRUCTION", "CONSTRUCTION TYPE", "CONSTRUCTION CLASSIFICATION",
         "BUILDING CONSTRUCTION TYPE", "CONSTRUCTION"),
        "construction", "construction type per Chapter 6 (I-A … V-B)",
        disqualify=("PHASE", "DOCUMENTS", "DEBRIS", "NOTES", "SCHEDULE", "MANAGER",
                    "DURING", "JOINT", "PERMIT"),
        declaration="construction_type"),
    FieldSpec(
        "sprinkler_system",
        ("FIRE SPRINKLERS", "SPRINKLER SYSTEM", "FIRE SPRINKLER SYSTEM",
         "AUTOMATIC SPRINKLER SYSTEM", "AUTOMATIC SPRINKLERS", "SPRINKLERED",
         "SPRINKLERS", "SPRINKLER", "FIRE SUPPRESSION", "FIRE SUPPRESSION SYSTEM"),
        "sprinkler", "whether the building is sprinklered, and to which NFPA standard",
        disqualify=("HEAD", "HEADS", "PIPE", "PIPING", "RISER", "MAIN", "CONTRACTOR",
                    "ROOM", "VALVE", "ALARM", "DRAWINGS", "SHOP", "HANGER", "FDC",
                    "SUBMITTAL", "LAYOUT"),
        declaration="sprinkler_system"),
    FieldSpec(
        "building_area_sf",
        ("BUILDING AREA", "GROSS BUILDING AREA", "GROSS FLOOR AREA", "FLOOR AREA",
         "AREA PER FLOOR", "AREA PER STORY", "TENANT AREA", "TENANT SPACE AREA",
         "SUITE AREA", "SQUARE FOOTAGE PER FLOOR", "SQUARE FOOTAGE", "AREA"),
        "area", "the building's (or tenant space's) floor area in square feet",
        disqualify=_AREA_NOT_BUILDING + ("TOTAL",),
        units=("SF", "S.F.", "SQ", "SQFT", "SQ.FT.", "GSF", "FT2"),
        declaration="building_area_sf"),
    FieldSpec(
        "total_area_sf",
        ("TOTAL BUILDING AREA", "TOTAL AREA", "TOTAL GROSS AREA", "TOTAL FLOOR AREA",
         "TOTAL SQUARE FOOTAGE", "GROSS AREA", "AGGREGATE AREA"),
        "area", "the whole building's total floor area in square feet",
        disqualify=_AREA_NOT_BUILDING,
        units=("SF", "S.F.", "SQ", "SQFT", "SQ.FT.", "GSF", "FT2"),
        declaration="total_area_sf"),
    FieldSpec(
        "height_ft",
        ("BUILDING HEIGHT", "BUILDING HEIGHT ABOVE GRADE PLANE", "MEAN ROOF HEIGHT",
         "OVERALL BUILDING HEIGHT", "HEIGHT"),
        "feet", "building height above grade plane",
        disqualify=("CEILING", "DOOR", "COUNTER", "MOUNTING", "SIGN", "FENCE", "WALL",
                    "RAILING", "GUARD", "STEP", "RISER", "HANDRAIL", "ALLOWABLE", "SEAT",
                    "RIM", "GRAB", "MIRROR", "FIXTURE", "LETTER", "CHARACTER", "SHELF",
                    "PANEL", "RECEPTACLE", "SWITCH", "PARAPET", "HEADROOM", "CLEAR"),
        declaration="height_ft"),
    FieldSpec(
        "stories",
        ("STORIES", "NUMBER OF STORIES", "STORIES ABOVE GRADE", "STORIES ABOVE GRADE PLANE",
         "NO OF STORIES", "NUMBER OF FLOORS"),
        "count", "number of storeys above grade plane",
        disqualify=("ALLOWABLE",),
        declaration="stories"),
    FieldSpec(
        "wind_speed_mph",
        ("ULTIMATE DESIGN WIND SPEED", "ULTIMATE WIND SPEED", "BASIC WIND SPEED",
         "DESIGN WIND SPEED", "WIND SPEED", "VULT", "V ULT", "ULTIMATE"),
        "wind", "ultimate design wind speed Vult, in mph",
        disqualify=("NOMINAL", "VASD"),
        units=("MPH",),
        declaration="wind_speed_mph"),
    FieldSpec(
        "exposure_category",
        ("EXPOSURE CATEGORY", "WIND EXPOSURE CATEGORY", "WIND EXPOSURE", "EXPOSURE"),
        "letter", "ASCE 7 wind exposure category (B, C or D)",
        disqualify=("FIRE", "SEPARATION"),
        declaration="exposure_category"),
    FieldSpec(
        "risk_category",
        ("RISK CATEGORY", "BUILDING RISK CATEGORY", "RISK CAT", "OCCUPANCY CATEGORY"),
        "roman", "Table 1604.5 risk category (I–IV)",
        declaration="risk_category"),
    FieldSpec(
        "code_edition",
        ("CODE EDITION", "BUILDING CODE EDITION", "APPLICABLE BUILDING CODES",
         "APPLICABLE CODES", "APPLICABLE CODE", "BUILDING CODE", "GOVERNING CODE"),
        "edition", "the Florida Building Code edition the set is designed to "
                   "(e.g. \"2023 FLORIDA BUILDING CODE 8TH EDITION\")",
        declaration="code_edition"),
    FieldSpec(
        "zoning", ("ZONING", "ZONING DISTRICT", "ZONING CLASSIFICATION"),
        "text", "zoning district", disqualify=("FLOOD",), declaration="zoning"),
    FieldSpec(
        "flood_zone", ("FLOOD ZONE", "FEMA FLOOD ZONE"),
        "text", "FEMA flood zone"),
    FieldSpec(
        "classification_of_work",
        ("CLASSIFICATION OF WORK", "WORK CLASSIFICATION", "LEVEL OF ALTERATION",
         "ALTERATION LEVEL", "SCOPE CLASSIFICATION"),
        "text", "FBC Existing Building classification of work (Alteration Level I/II/III …)"),
    FieldSpec(
        "internal_pressure_gcpi",
        ("INTERNAL PRESSURE COEFFICIENTS", "INTERNAL PRESSURE COEFFICIENT", "GCPI",
         "INTERNAL PRESSURE"),
        "text", "internal pressure coefficient GCpi and enclosure classification"),

    # ── the building's own occupant count and egress design ───────────────
    FieldSpec(
        "occupant_load",
        ("OCCUPANT LOAD", "TOTAL OCCUPANT LOAD", "BUILDING OCCUPANT LOAD",
         "CALCULATED OCCUPANT LOAD", "OCCUPANT LOAD TOTAL", "TOTAL OCCUPANTS",
         "ASSEMBLY OCCUPANCY"),
        "count", "the building's total occupant load (persons)",
        disqualify=("FACTOR", "PER", "DENSITY", "CAPACITY", "SIGN", "POSTING", "DAILY",
                    "LIMITED", "DISCHARGE", "EXIT"),
        context_disqualify=("DISCHARGE", "STAIR", "EXIT ACCESS", "DOOR"),
    ),
    FieldSpec(
        "egress_width_factor",
        ("EGRESS WIDTH FACTOR", "EGRESS CAPACITY FACTOR", "MEANS OF EGRESS CAPACITY FACTOR",
         "CAPACITY FACTOR", "WIDTH FACTOR"),
        "factor", "the means-of-egress capacity factor used, inches per occupant",
        context_disqualify=("DISCHARGE",),
    ),

    # ── the code-analysis audit rows: a stated requirement and what is provided
    FieldSpec(
        "egress.travel_distance",
        ("MAX TRAVEL DISTANCE", "MAXIMUM TRAVEL DISTANCE", "EXIT ACCESS TRAVEL DISTANCE",
         "TRAVEL DISTANCE"),
        "feet", "exit access travel distance: stated limit and provided",
        disqualify=("FIRE EXTINGUISHER", "EXTINGUISHER", "PATH 1", "PATH 2", "PATH"),
        sections=("1017.2", "1017"), roles=True),
    FieldSpec(
        "egress.common_path",
        ("COMMON PATH OF TRAVEL", "COMMON PATH OF EGRESS TRAVEL", "MAX COMMON PATH",
         "MAXIMUM COMMON PATH", "COMMON PATH"),
        "feet", "common path of egress travel: stated limit and provided",
        sections=("1006.2.1",), roles=True),
    FieldSpec(
        "egress.dead_end",
        ("DEAD END CORRIDOR", "DEAD END CORRIDORS", "MAX DEAD END", "DEAD END"),
        "feet", "dead-end corridor length: stated limit and provided",
        sections=("1020.5", "1020.4"), roles=True),
    FieldSpec(
        "egress.corridor_width",
        ("MIN CORRIDOR WIDTH", "MINIMUM CORRIDOR WIDTH", "CORRIDOR WIDTH"),
        "inches", "corridor width: stated minimum and provided",
        sections=("1020.3", "1020.2"), roles=True),
    FieldSpec(
        "egress.exits",
        ("NUMBER OF EXITS", "NO OF EXITS", "EXITS", "NUMBER REQUIRED", "NUMBER PROVIDED"),
        "count", "number of exits: required and provided",
        context_require=("EXIT", "EGRESS"),
        context_disqualify=("DISCHARGE", "PLUMBING", "FIXTURE"),
        sections=("1006.3.2", "1006.3.3", "1006.2"), roles=True),
    FieldSpec(
        "egress.width",
        ("EXIT WIDTH REQUIRED", "EXIT WIDTH PROVIDED", "EGRESS WIDTH REQUIRED",
         "EGRESS WIDTH PROVIDED", "EXIT WIDTH", "EGRESS WIDTH", "WIDTH REQUIRED",
         "WIDTH PROVIDED"),
        "inches", "total egress width: required and provided",
        context_require=("EXIT", "EGRESS", "WIDTH", "TRAVEL"),
        context_disqualify=("DISCHARGE",),
        sections=("1005.3.2", "1005.3", "1005.1"), roles=True),
    FieldSpec(
        "egress.door_clear_width",
        ("CLEAR OPENING WIDTH", "DOOR CLEAR WIDTH", "CLEAR WIDTH", "MIN CLEAR OPENING"),
        "inches", "door clear opening width: stated minimum and provided",
        sections=("1010.1.1",), roles=True),
)

BY_KEY: Dict[str, FieldSpec] = {f.key: f for f in FIELDS}

#: The catalog fields that are the drawn side of a declaration question.
DECLARATION_FIELDS: Dict[str, FieldSpec] = {f.declaration: f for f in FIELDS if f.declaration}
