"""Review options — how the review should behave and what it should report.

Deliberately *not* facts about the building. Occupancy group and sprinkler
status used to live here, and they were always in the wrong place: they are
things the building is, not things the reviewer chose. They now belong to
`fbcreview.declaration.ProjectDeclaration`, which is reconciled against what the
drawings say rather than silently overriding it.

What is left is output and behaviour: which corpus to review against, how far
down the severity ramp to report, and whether to show the verified and measured
registers.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict, field
from typing import List, Optional

SEVERITY_ORDER = ["CRITICAL", "HIGH", "MEDIUM", "LOW"]

EDITIONS = {
    # The 7th Edition is listed because sets are still drawn to it and the
    # reviewer has to be able to say so — see CODE.EDITION_CURRENT. It is not a
    # corpus this build reviews *against*; `available` on the wire says which.
    "fbc2020": "2020 Florida Building Code, 7th Edition — superseded",
    "fbc2023": "2023 Florida Building Code, 8th Edition (IBC 2021 base)",
    # 9th Edition takes effect 31 Dec 2026 — corpus not yet populated.
    "fbc2026": "2026 Florida Building Code, 9th Edition — not yet available",
}

#: Editions this build carries a rule corpus for.
AVAILABLE_EDITIONS = ("fbc2023",)

# Kept for the review-options wire model, whose `occupancy_group` field is now a
# deprecated bridge into the declaration. The declaration's own, wider list is
# in `fbcreview.declaration_schema.OCCUPANCY_CHOICES`.
OCCUPANCY_GROUPS = [
    ("A-2", "A-2 — assembly, food and drink"),
    ("A-3", "A-3 — assembly, worship/recreation/amusement (studios, gyms)"),
    ("B",   "B — business"),
    ("M",   "M — mercantile"),
    ("E",   "E — educational"),
]


@dataclass
class ReviewOptions:
    edition: str = "fbc2023"
    min_severity: str = "LOW"          # report findings at or above this level
    include_verified: bool = True      # the green markers — v5's differentiator
    include_measured: bool = True      # traced geometry on the life-safety sheet
    project_name: str = ""
    email_to: List[str] = field(default_factory=list)
    notes: str = ""

    def severity_allowed(self, sev: str) -> bool:
        if sev in ("VERIFIED",):
            return self.include_verified
        if sev in ("MEASURED",):
            return self.include_measured
        if sev not in SEVERITY_ORDER:
            return True
        return SEVERITY_ORDER.index(sev) <= SEVERITY_ORDER.index(self.min_severity)

    def effective_edition(self, declaration=None) -> str:
        """Which edition this review is against.

        The declaration wins when it states one: the applicant knows what the
        set was drawn to, and the review option is a fallback for a submission
        that carries no declaration at all.
        """
        declared = getattr(declaration, "code_edition", None)
        return declared or self.edition

    def edition_label(self, declaration=None) -> str:
        e = self.effective_edition(declaration)
        return EDITIONS.get(e, e)

    def to_dict(self) -> dict:
        return asdict(self)
