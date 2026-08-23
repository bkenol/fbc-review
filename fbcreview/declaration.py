"""The Project Declaration — what the applicant says the building is.

A permit set states its own code data on the general sheets, and the engine
reads it there.  This module carries the *other* source: the twelve answers a
user gives before uploading.

The point is not to search for less.  It is to have **two independent sources**
for the same fact, so that agreement raises confidence, disagreement becomes a
finding, and neither source silently substitutes for the other.  See
`fbcreview/reconcile.py`, which is where the two are put side by side.

Every field is `Optional` and `None` is the only representation of "unanswered".
There is deliberately no sentinel string: a rule that receives `None` abstains,
and a rule can never mistake a placeholder for an answer.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from typing import Any, Dict, Optional


@dataclass
class ProjectDeclaration:
    """Twelve optional facts about the building, as the applicant states them.

    Field order follows the questionnaire groups in `declaration_schema.FIELDS`
    so that the dataclass and the form read the same way.
    """

    # ── occupancy ─────────────────────────────────────────────────────────
    occupancy_group: Optional[str] = None        # "A-3", "B", "M", "S-1", ...
    mixed_occupancy: Optional[bool] = None
    separation_method: Optional[str] = None      # "separated" (508.4) | "nonseparated" (508.3)

    # ── construction and size ─────────────────────────────────────────────
    construction_type: Optional[str] = None      # "I-A" ... "V-B"
    building_area_sf: Optional[float] = None     # largest floor, gross
    total_area_sf: Optional[float] = None
    height_ft: Optional[float] = None
    stories: Optional[int] = None

    # ── fire protection ───────────────────────────────────────────────────
    sprinkler_system: Optional[str] = None       # "none" | "nfpa13" | "nfpa13r" | "nfpa13d"

    # ── structural / wind ─────────────────────────────────────────────────
    wind_speed_mph: Optional[float] = None       # Vult, ultimate
    exposure_category: Optional[str] = None      # "B" | "C" | "D"
    risk_category: Optional[str] = None          # "I" | "II" | "III" | "IV"

    # ── context ───────────────────────────────────────────────────────────
    zoning: Optional[str] = None                 # free text, jurisdiction-specific
    jurisdiction: Optional[str] = None           # "Lee County, FL"
    code_edition: Optional[str] = None           # "fbc2020" | "fbc2023" | "fbc2026"

    # ── derived views ─────────────────────────────────────────────────────
    @property
    def sprinklered(self) -> Optional[bool]:
        """The boolean the Chapter 10 tables actually key on.

        `None` stays `None` — an unanswered sprinkler question is not "no".
        NFPA 13D is a dwelling-unit system and does not buy the 903.3.1.1 /
        903.3.1.2 allowances the egress tables grant, so it reads as False
        here rather than as a system the code would credit.
        """
        if self.sprinkler_system is None:
            return None
        return self.sprinkler_system.strip().lower() in ("nfpa13", "nfpa13r")

    def answered(self) -> Dict[str, Any]:
        """Only the fields the user actually answered."""
        return {f.name: getattr(self, f.name)
                for f in fields(self) if getattr(self, f.name) is not None}

    def answered_count(self) -> int:
        return len(self.answered())

    def is_empty(self) -> bool:
        return not self.answered()

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: Optional[Dict[str, Any]]) -> "ProjectDeclaration":
        """Build from a dict, ignoring keys this build does not know.

        Unknown keys are dropped rather than raised on: the API validates the
        wire body against the published schema before it ever reaches here, and
        a stored job record written by an older build must still load.
        """
        if not raw:
            return cls()
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in raw.items() if k in known and v is not None})


FIELD_NAMES = tuple(f.name for f in fields(ProjectDeclaration))
