"""Is the code edition this set cites still the code in force?

The effective dates live in `fbcreview/codes/editions.py`, keyed by edition, and
`today` is an input rather than something reached for inside the rule — so the
behaviour is testable on both sides of an adoption date, and December does not
quietly break it.
"""
from __future__ import annotations

import datetime as dt

from . import rule, Finding, RuleResult
from ..codes import editions as E
from ..confidence import Abstention
from ..facts import ProjectFacts
from ._context import basis, need, source_phrase, where


@rule("CODE.EDITION_CURRENT")
def edition_current(f: ProjectFacts, out: RuleResult):
    v = need(f, out, "CODE.EDITION_CURRENT", "code_edition")
    if v is None:
        return
    key = str(v["code_edition"])
    cited = E.edition(key)
    if cited is None:
        out.abstentions.append(Abstention(
            "CODE.EDITION_CURRENT", "this build carries no effective date for that edition",
            detail=key))
        return

    today = f.meta.get("as_of") or dt.date.today()
    current = E.in_force(today)
    page, sheet = where(f, "code_edition")
    b = basis(f, "code_edition")

    if current is None or current.effective <= cited.effective:
        out.findings.append(Finding(
            "V-ED", "CODE.EDITION_CURRENT", "PASS", "VERIFIED", "Administration",
            page, sheet, "FLORIDA BUILDING CODE",
            f"{cited.label} is the edition in force",
            "The code edition the set cites, against the Florida adoption schedule.",
            f"{cited.label} took effect {cited.effective.isoformat()} and is current as of "
            f"{today.isoformat()}. Cited edition {source_phrase(f, 'code_edition')}.",
            "F.A.C. 61G20 · FBC adoption schedule", "None.", basis=b))
        return

    out.findings.append(Finding(
        "C-ED", "CODE.EDITION_CURRENT", "OPEN", "CRITICAL", "Administration",
        page, sheet, "FLORIDA BUILDING CODE",
        f"Designed and cited to {cited.label}, which is superseded",
        "The code edition the set cites, against the Florida adoption schedule and the "
        "date of this review.",
        f"Every code reference in this set is {cited.ordinal} Edition ({cited.year}), "
        f"based on {cited.ibc_base}. {current.label} took effect "
        f"{current.effective.isoformat()} and is the code in force as of "
        f"{today.isoformat()}. The set was legitimately {cited.ordinal} Edition when it was "
        f"drawn — this is about what happens if it is submitted, re-submitted or revived "
        f"today, and it is a re-analysis rather than a cover-sheet edit: "
        f"{current.label} adopts {current.asce7 or 'a different referenced standard set'} "
        f"in place of {cited.asce7 or 'the earlier one'}.",
        "F.A.C. 61G20 · FBC adoption schedule · FBC-B 1609",
        "Confirm whether a live permit exists from the original submittal. If it lapsed, "
        f"update every code reference to the {current.ordinal} Edition and re-run the "
        f"analyses the changed referenced standards affect.", basis=b))
