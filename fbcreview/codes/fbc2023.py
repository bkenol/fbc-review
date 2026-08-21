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
