"""Review options — everything the web form lets a user choose.

These were hard-coded constants in the rule modules. They are now explicit
inputs, because occupancy group and sprinkler status change the answer to most
of Chapter 10 and a reviewer must be able to state them.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict, field
from typing import List

SEVERITY_ORDER = ["CRITICAL", "HIGH", "MEDIUM", "LOW"]

EDITIONS = {
    "fbc2023": "2023 Florida Building Code, 8th Edition (IBC 2021 base)",
    # 9th Edition takes effect 31 Dec 2026 — corpus not yet populated.
    "fbc2026": "2026 Florida Building Code, 9th Edition — not yet available",
}

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
    occupancy_group: str = "A-3"
    sprinklered: bool = True
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

    def edition_label(self) -> str:
        return EDITIONS.get(self.edition, self.edition)

    def to_dict(self) -> dict:
        return asdict(self)
