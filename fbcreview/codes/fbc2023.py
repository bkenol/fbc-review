"""A structured slice of the 2023 Florida Building Code, 8th Edition.

This is the part that is DATA, not code, and it is the real moat: every
requirement is a row with a section number, an applicability predicate and a
value.  Rules never hard-code a number; they look it up here, so a new code
edition is a new data file rather than a new release.

Only the sections exercised by the Sculpted Hot Pilates test set are populated.
The shape is what matters — see ARCHITECTURE.md §4.
"""
from __future__ import annotations
from typing import Optional

EDITION = "2023 Florida Building Code, 8th Edition (IBC 2021 base)"

# ── Table 1006.2.1 — common path of egress travel ─────────────────────────────
_COMMON_PATH = {              # (occupancy group, sprinklered) -> feet
    ("A", True): 75, ("A", False): 75,
    ("E", True): 75, ("E", False): 75,
    ("M", True): 75, ("M", False): 75,
    ("B", True): 100, ("B", False): 75,
}

# ── Table 1017.2 — exit access travel distance ────────────────────────────────
_TRAVEL = {("A", True): 250, ("A", False): 200,
           ("B", True): 300, ("B", False): 200,
           ("M", True): 250, ("M", False): 200}

# ── 1020.5 — dead ends. The 50-ft sprinklered exception lists the groups it
#    covers, and Group A is NOT among them. Encoding the LIST rather than a
#    boolean is what stops the classic "sprinklered so 50 ft" error.
_DEADEND_BASE = 20
_DEADEND_50FT_GROUPS = {"B", "E", "F", "I-1", "M", "R-1", "R-2", "S", "U"}

# ── Table 1020.3 — corridor width ─────────────────────────────────────────────
_CORRIDOR_DEFAULT_IN = 44

# ── 1010.1.1 — door clear width / height ──────────────────────────────────────
DOOR_CLEAR_WIDTH_IN = 32
DOOR_CLEAR_HEIGHT_IN = 80
DOOR_CLOSET_EXEMPT_SF = 10        # storage closets < 10 sq ft

# ── 1005.3.2 — egress capacity factor ─────────────────────────────────────────
CAPACITY_FACTOR_BASE = 0.20        # in. per occupant, other than stairways
CAPACITY_FACTOR_SPRINKLER_EVACS = 0.15
CAPACITY_FACTOR_EXC1_REQUIRES = ("903.3.1.1 or 903.3.1.2 sprinkler system",
                                 "907.5.2.2 emergency voice/alarm communication system")

# ── Table 1006.3.2 — minimum number of exits ──────────────────────────────────
def exits_required(occupant_load: float) -> int:
    if occupant_load <= 500:
        return 2
    if occupant_load <= 1000:
        return 3
    return 4

# ── Table 1604.5 — risk category ──────────────────────────────────────────────
RISK_III_ASSEMBLY_OL = 300         # RC III: assembly with OL > 300
# ── 1003.2 — ceiling height ───────────────────────────────────────────────────
MIN_CEILING_FT = 7.5
# ── 1004.9 — assembly occupant load posting ───────────────────────────────────
POSTING_REQUIRED_FOR = {"A", "A-1", "A-2", "A-3", "A-4", "A-5"}

# ── Table 2902.1 — minimum number of required plumbing facilities ─────────────
# Transcribed by hand from 2023 FBC-B Table 2902.1 (UpCodes, "2023 FBC - Building,
# 8th edition", Chapter 29), checked 2026-09-27. Only the rows a set in hand has
# needed: a set on any other row abstains and names the table, and the row is
# added then, deliberately. Figures are occupants per fixture; `None` is the
# table's dash (none required).
PLUMBING_ROWS = (
    {
        "group": "A-3",
        "description": "Auditoriums without permanent seating, art galleries, exhibition "
                       "halls, museums, lecture halls, libraries, arcades and gymnasiums",
        "wc": (125, 65),            # (male, female)
        "lav": (200, 200),
        "drinking_fountain": 500,
        "service_sinks": 1,
    },
)
# 2902.1.1 — "To determine the occupant load of each sex, the total occupant load
# shall be divided in half" (Exception 1: approved statistical data), and
# "fractional numbers resulting from applying the fixture ratios of Table 2902.1
# shall be rounded up to the next whole number."
PLUMBING_SPLIT = 0.5
# 2902.1.2 — fixtures in single-user toilet rooms count toward the total; 2902.2
# Exception 5 — such rooms need not be designated by sex.


def common_path_ft(group: str, sprinklered: bool) -> Optional[int]:
    return _COMMON_PATH.get((group.split("-")[0].upper(), bool(sprinklered)))


def travel_distance_ft(group: str, sprinklered: bool) -> Optional[int]:
    return _TRAVEL.get((group.split("-")[0].upper(), bool(sprinklered)))


def dead_end_ft(group: str, sprinklered: bool) -> int:
    g = group.upper()
    if sprinklered and (g in _DEADEND_50FT_GROUPS or g.split("-")[0] in _DEADEND_50FT_GROUPS):
        return 50
    return _DEADEND_BASE


def corridor_width_in(_group: str) -> int:
    return _CORRIDOR_DEFAULT_IN


def capacity_factor(sprinklered: bool, evacs: bool) -> float:
    return (CAPACITY_FACTOR_SPRINKLER_EVACS
            if (sprinklered and evacs) else CAPACITY_FACTOR_BASE)


def door_clear_width_from_leaf(leaf_in: float, thickness_in: float = 1.75,
                               stop_in: float = 0.125) -> float:
    """Clear width at 90 degrees = leaf - thickness - stop. 1010.1.1 measures
    face-of-door to stop, so the leaf itself eats its own thickness."""
    return leaf_in - thickness_in - stop_in


# ══════════════════════════════════════════════════════════════════════════
# Chapter 5 — general building heights and areas
#
# These tables are what a Project Declaration unlocks: every one of them is
# arithmetic against a lookup, needs no drawing parsing at all, and therefore
# runs on a set whose code-analysis block is a pasted picture.
#
# Only the cells this build is confident of are populated. A missing cell
# returns None and the rule abstains — the corpus never guesses a threshold,
# because a wrong allowable area is worse than no allowable area.
# ══════════════════════════════════════════════════════════════════════════
from typing import Dict, Optional, Tuple           # noqa: E402  (data block)

UNLIMITED = float("inf")

CONSTRUCTION_TYPES = ("I-A", "I-B", "II-A", "II-B", "III-A", "III-B", "IV", "V-A", "V-B")

# ── Table 504.3 — allowable building height in feet above grade plane ────────
#
# The heights below are the row Table 504.3 gives for Groups A, B, E, F, M, S
# and U. Groups H, I and R are deliberately NOT covered: their rows differ, and
# encoding the GROUP LIST the value covers rather than a blanket number is the
# same discipline as `_DEADEND_50FT_GROUPS` above — it is what stops a rule
# being quietly wrong for the one occupancy it was not written against.
_HEIGHT_GROUPS = {"A", "B", "E", "F", "M", "S", "U"}
_HEIGHT_FT: Dict[str, Tuple[float, float]] = {      # type -> (non-sprinklered, sprinklered)
    "I-A":   (UNLIMITED, UNLIMITED),
    "I-B":   (160, 180),
    "II-A":  (65, 85),
    "II-B":  (55, 75),
    "III-A": (65, 85),
    "III-B": (55, 75),
    "IV":    (65, 85),
    "V-A":   (50, 70),
    "V-B":   (40, 60),
}

# ── Table 504.4 — allowable number of storeys above grade plane ──────────────
# Keyed (group, type) -> (non-sprinklered, sprinklered). Populated for the
# groups this build reviews; anything else abstains.
_STORIES: Dict[Tuple[str, str], Tuple[float, float]] = {}
for _g, _rows in {
    "B":   {"I-A": (UNLIMITED, UNLIMITED), "I-B": (11, 12), "II-A": (5, 6), "II-B": (3, 4),
            "III-A": (5, 6), "III-B": (3, 4), "IV": (5, 6), "V-A": (3, 4), "V-B": (2, 3)},
    "A-3": {"I-A": (UNLIMITED, UNLIMITED), "I-B": (11, 12), "II-A": (3, 4), "II-B": (2, 3),
            "III-A": (3, 4), "III-B": (2, 3), "IV": (3, 4), "V-A": (2, 3), "V-B": (1, 2)},
    "M":   {"I-A": (UNLIMITED, UNLIMITED), "I-B": (11, 12), "II-A": (4, 5), "II-B": (2, 3),
            "III-A": (4, 5), "III-B": (2, 3), "IV": (4, 5), "V-A": (3, 4), "V-B": (1, 2)},
    "S-1": {"I-A": (UNLIMITED, UNLIMITED), "I-B": (11, 12), "II-A": (4, 5), "II-B": (2, 3),
            "III-A": (3, 4), "III-B": (2, 3), "IV": (4, 5), "V-A": (3, 4), "V-B": (1, 2)},
}.items():
    for _t, _v in _rows.items():
        _STORIES[(_g, _t)] = _v

# ── Table 506.2 — allowable area factor, At, in square feet ──────────────────
# (group, type) -> (NS, S1, SM): non-sprinklered, single-storey sprinklered,
# multi-storey sprinklered — transcribed as printed in 2023 FBC-B Table 506.2
# (IBC 2021 base), checked 2026-09-27 against the UpCodes rendering of the 8th
# Edition and against ICC's 2021 IBC Heights & Areas course (EDUCODE 2024,
# Session 36/38), whose worked example takes 92,000 SF from this table for a
# single-storey sprinklered Group B, Type II-B building.
#
# The previous transcription carried S1 = 3 x NS and SM = 2 x NS in every row —
# the pre-2015 "Is = 2 / Is = 3" increase read as a multiplier — and put A-1's
# 8,500 in A-3's III-B cell. The printed table is S1 = 4 x NS and SM = 3 x NS
# throughout, and `tests/test_code_corpus.py` holds that invariant so a slip of
# that kind cannot come back unnoticed.
_AREA: Dict[Tuple[str, str], Tuple[float, float, float]] = {}
for _g, _rows in {
    "A-3": {"I-A": (UNLIMITED,) * 3, "I-B": (UNLIMITED,) * 3,
            "II-A": (15500, 62000, 46500), "II-B": (9500, 38000, 28500),
            "III-A": (14000, 56000, 42000), "III-B": (9500, 38000, 28500),
            "IV": (15000, 60000, 45000), "V-A": (11500, 46000, 34500),
            "V-B": (6000, 24000, 18000)},
    "B":   {"I-A": (UNLIMITED,) * 3, "I-B": (UNLIMITED,) * 3,
            "II-A": (37500, 150000, 112500), "II-B": (23000, 92000, 69000),
            "III-A": (28500, 114000, 85500), "III-B": (19000, 76000, 57000),
            "IV": (36000, 144000, 108000), "V-A": (18000, 72000, 54000),
            "V-B": (9000, 36000, 27000)},
    "M":   {"I-A": (UNLIMITED,) * 3, "I-B": (UNLIMITED,) * 3,
            "II-A": (21500, 86000, 64500), "II-B": (12500, 50000, 37500),
            "III-A": (18500, 74000, 55500), "III-B": (12500, 50000, 37500),
            "IV": (20500, 82000, 61500), "V-A": (14000, 56000, 42000),
            "V-B": (9000, 36000, 27000)},
    "S-1": {"I-A": (UNLIMITED,) * 3, "I-B": (48000, 192000, 144000),
            "II-A": (26000, 104000, 78000), "II-B": (17500, 70000, 52500),
            "III-A": (26000, 104000, 78000), "III-B": (17500, 70000, 52500),
            "IV": (25500, 102000, 76500), "V-A": (14000, 56000, 42000),
            "V-B": (9000, 36000, 27000)},
}.items():
    for _t, _v in _rows.items():
        _AREA[(_g, _t)] = _v

# ── Table 601 — fire-resistance rating requirements, in hours ────────────────
# `None` in the exterior non-bearing wall slot means "see Table 602", which is a
# function of fire separation distance and cannot be answered from a
# declaration alone. "HT" is heavy timber sized per 2304.11.
_TABLE_601: Dict[str, Dict[str, object]] = {
    "I-A":   {"frame": 3, "bearing_ext": 3, "bearing_int": 3, "nonbearing_ext": None,
              "nonbearing_int": 0, "floor": 2, "roof": 1.5},
    "I-B":   {"frame": 2, "bearing_ext": 2, "bearing_int": 2, "nonbearing_ext": None,
              "nonbearing_int": 0, "floor": 2, "roof": 1},
    "II-A":  {"frame": 1, "bearing_ext": 1, "bearing_int": 1, "nonbearing_ext": None,
              "nonbearing_int": 0, "floor": 1, "roof": 1},
    "II-B":  {"frame": 0, "bearing_ext": 0, "bearing_int": 0, "nonbearing_ext": None,
              "nonbearing_int": 0, "floor": 0, "roof": 0},
    "III-A": {"frame": 1, "bearing_ext": 2, "bearing_int": 1, "nonbearing_ext": None,
              "nonbearing_int": 0, "floor": 1, "roof": 1},
    "III-B": {"frame": 0, "bearing_ext": 2, "bearing_int": 0, "nonbearing_ext": None,
              "nonbearing_int": 0, "floor": 0, "roof": 0},
    "IV":    {"frame": "HT", "bearing_ext": 2, "bearing_int": "HT", "nonbearing_ext": None,
              "nonbearing_int": "HT", "floor": "HT", "roof": "HT"},
    "V-A":   {"frame": 1, "bearing_ext": 1, "bearing_int": 1, "nonbearing_ext": None,
              "nonbearing_int": 0, "floor": 1, "roof": 1},
    "V-B":   {"frame": 0, "bearing_ext": 0, "bearing_int": 0, "nonbearing_ext": None,
              "nonbearing_int": 0, "floor": 0, "roof": 0},
}

_TABLE_601_LABELS = {
    "frame": "primary structural frame", "bearing_ext": "bearing walls, exterior",
    "bearing_int": "bearing walls, interior", "nonbearing_int": "nonbearing walls, interior",
    "floor": "floor construction", "roof": "roof construction",
}

# ── Table 1004.5 — maximum floor area allowance per occupant ────────────────
# Only the *gross* factors that can be applied to a whole building area without
# a room-by-room breakdown are keyed by group here. Assembly and education are
# deliberately absent: their factors are use-specific (7 net concentrated, 15
# net at tables, 5 net standing, 20 net classroom), so a whole-floor number
# would be a fabrication. Those groups abstain and say why.
OCCUPANT_LOAD_GROSS: Dict[str, float] = {
    "B": 150,        # business areas — 150 gross
    "M": 60,         # mercantile
    "S-1": 300,      # storage, moderate hazard
    "S-2": 500,      # storage, low hazard (warehouses)
    "F-1": 100,      # factory / industrial areas
    "F-2": 100,
}

#: Use-specific factors, for the register's explanation and for a set that does
#: break its areas down by use. `net` says which basis the factor is measured on.
OCCUPANT_LOAD_USES = {
    "business": (150, "gross"),
    "mercantile": (60, "gross"),
    "exercise room": (50, "gross"),
    "assembly, concentrated (chairs only)": (7, "net"),
    "assembly, unconcentrated (tables and chairs)": (15, "net"),
    "assembly, standing space": (5, "net"),
    "classroom": (20, "net"),
    "storage, stock, shipping": (300, "gross"),
    "warehouse": (500, "gross"),
}


def _norm_type(t: str) -> str:
    return (t or "").strip().upper().replace(" ", "")


def _group_key(group: str) -> str:
    return (group or "").strip().upper()


def height_limit_ft(group: str, ctype: str, sprinklered: bool) -> Optional[float]:
    """Table 504.3. `None` when this build does not carry the row."""
    g = _group_key(group)
    if g.split("-")[0] not in _HEIGHT_GROUPS:
        return None
    row = _HEIGHT_FT.get(_norm_type(ctype))
    return None if row is None else row[1 if sprinklered else 0]


def height_groups_covered() -> set:
    return set(_HEIGHT_GROUPS)


def stories_limit(group: str, ctype: str, sprinklered: bool) -> Optional[float]:
    """Table 504.4."""
    row = _STORIES.get((_group_key(group), _norm_type(ctype)))
    return None if row is None else row[1 if sprinklered else 0]


def area_limit_sf(group: str, ctype: str, sprinklered: bool,
                  stories: Optional[int] = None) -> Optional[float]:
    """Table 506.2, allowable area factor for one storey.

    The sprinklered column depends on how many storeys there are: S1 is the
    single-storey value, SM the multi-storey one, and a set that has not said
    how many storeys it has gets the conservative SM figure.
    """
    row = _AREA.get((_group_key(group), _norm_type(ctype)))
    if row is None:
        return None
    if not sprinklered:
        return row[0]
    if stories is not None and int(stories) <= 1:
        return row[1]
    return row[2]


def table_601(ctype: str) -> Optional[Dict[str, object]]:
    return _TABLE_601.get(_norm_type(ctype))


def table_601_labels() -> Dict[str, str]:
    return dict(_TABLE_601_LABELS)


def occupant_load_factor(group: str) -> Optional[float]:
    """Table 1004.5, gross, for groups where one factor covers the whole area."""
    g = _group_key(group)
    return OCCUPANT_LOAD_GROSS.get(g) or OCCUPANT_LOAD_GROSS.get(g.split("-")[0])
