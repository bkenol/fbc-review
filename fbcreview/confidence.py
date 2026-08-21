"""Confidence and abstention primitives.

The single most important property of an automated code reviewer is that it
knows when it does not know.  Every extracted value carries where it came from
and how much to trust it; a rule that depends on an ABSTAIN value does not fire
at all rather than firing on a guess.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Optional

HIGH, MEDIUM, LOW = "high", "medium", "low"


@dataclass
class Evidence:
    """A value plus its provenance. `value is None` means we abstained."""
    value: Any
    source: str                      # "A-2 door schedule row 4", "page 2 /Measure + scale label"
    confidence: str = HIGH
    note: str = ""
    page: Optional[int] = None

    def __bool__(self) -> bool:
        return self.value is not None

    @classmethod
    def abstain(cls, source: str, note: str, page: Optional[int] = None) -> "Evidence":
        return cls(None, source, LOW, note, page)


@dataclass
class Abstention:
    """Recorded whenever a rule declined to fire. These are reported, not hidden —
    'not checked' must never be indistinguishable from 'checked and passed'."""
    rule_id: str
    reason: str
    page: Optional[int] = None
    detail: str = ""
