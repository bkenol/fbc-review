"""Code editions and when each one is in force.

Kept as data, keyed by edition, and **never** as a literal inside a rule body.
A rule that hard-codes "the 8th Edition is current" is correct until the last
week of December 2026 and wrong every day after, silently.

Florida adopts a new Building Code on a three-year cycle. The effective date is
the date the edition takes force; the edition in force on any given day is the
latest one whose effective date has passed. A set drawn to an earlier edition
was legitimate when drawn — this data says nothing about that. It says what
happens if the set is submitted, re-submitted or revived today.
"""
from __future__ import annotations

import datetime as _dt
from typing import Dict, List, Optional

#: Which ASCE 7 the edition adopts for wind. The edition change is what moves
#: the wind maps, so this is the join the structural rules need.
ASCE7 = {
    "fbc2020": "ASCE 7-16",
    "fbc2023": "ASCE 7-22",
    "fbc2026": None,          # not yet published — abstain rather than guess
}


class Edition:
    __slots__ = ("key", "ordinal", "year", "label", "effective", "ibc_base")

    def __init__(self, key: str, ordinal: str, year: int, label: str,
                 effective: _dt.date, ibc_base: str):
        self.key, self.ordinal, self.year = key, ordinal, year
        self.label, self.effective, self.ibc_base = label, effective, ibc_base

    @property
    def asce7(self) -> Optional[str]:
        return ASCE7.get(self.key)

    def __repr__(self) -> str:                      # pragma: no cover - debugging
        return f"<Edition {self.key} effective {self.effective.isoformat()}>"


#: Ordered oldest first. The 9th Edition date is the published cycle date; it is
#: "on or about" in the sense that the Commission can move it, which is another
#: reason it belongs here rather than in a rule.
EDITIONS: List[Edition] = [
    Edition("fbc2017", "6th", 2017, "2017 Florida Building Code, 6th Edition",
            _dt.date(2017, 12, 31), "IBC 2015"),
    Edition("fbc2020", "7th", 2020, "2020 Florida Building Code, 7th Edition",
            _dt.date(2020, 12, 31), "IBC 2018"),
    Edition("fbc2023", "8th", 2023, "2023 Florida Building Code, 8th Edition",
            _dt.date(2023, 12, 31), "IBC 2021"),
    Edition("fbc2026", "9th", 2026, "2026 Florida Building Code, 9th Edition",
            _dt.date(2026, 12, 31), "IBC 2024"),
]

BY_KEY: Dict[str, Edition] = {e.key: e for e in EDITIONS}


def edition(key: str) -> Optional[Edition]:
    return BY_KEY.get(key)


def in_force(on: _dt.date) -> Optional[Edition]:
    """The edition in force on `on`. The date is an argument, never `today()`
    reached for inside a rule, so the behaviour is testable in both directions."""
    passed = [e for e in EDITIONS if e.effective <= on]
    return passed[-1] if passed else None


def superseded_by(key: str, on: _dt.date) -> Optional[Edition]:
    """The edition that has replaced `key` as of `on`, or `None` if it is current
    (or not yet in force, which is a different thing and not this function's)."""
    this = BY_KEY.get(key)
    current = in_force(on)
    if this is None or current is None:
        return None
    return current if current.effective > this.effective else None
