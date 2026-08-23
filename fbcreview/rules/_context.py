"""Shared helpers for rules that read the Project Declaration.

Every rule in this family follows the same shape: ask `reconcile.building()` for
the facts it needs, stand down if any of them is `None`, and record on the
finding whether the answer rested on the drawings, the declaration, or both.

Nothing here decides anything. It exists so that "abstain, and say exactly which
answer was missing" is one line rather than six, because a rule that is tedious
to write honestly gets written dishonestly.
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from ..confidence import Abstention
from ..declaration_schema import BY_KEY
from ..facts import ProjectFacts
from ..reconcile import (FROM_BOTH, FROM_DECLARATION, FROM_DRAWINGS, Resolved,
                         basis_of, building)


def need(f: ProjectFacts, out, rule_id: str, *keys: str) -> Optional[Dict[str, Any]]:
    """Every value in `keys`, or `None` after recording why the rule stood down.

    The abstention names the missing questions in the words the questionnaire
    used, so the reason reads as something a person can act on rather than as a
    field name from the fact model.
    """
    values: Dict[str, Any] = {}
    missing = []
    for key in keys:
        r = building(f, key)
        if r is None or r.value is None:
            missing.append(BY_KEY[key].pro_label if key in BY_KEY else key)
        else:
            values[key] = r.value
    if missing:
        out.abstentions.append(Abstention(
            rule_id,
            "not stated on the drawings and not answered in the project declaration",
            detail="; ".join(missing)))
        return None
    return values


def where(f: ProjectFacts, *keys: str) -> Tuple[int, str]:
    """The page and sheet to hang a finding on.

    The sheet that stated one of the inputs, when the drawings stated any of
    them. A wholly declaration-derived finding has no home on the drawing, so it
    lands on the first sheet — the register carries it either way, and a marker
    that cannot be placed is simply not placed.
    """
    for key in keys:
        r = building(f, key)
        if r is not None and r.evidence.page is not None:
            return r.evidence.page, f.sheet_code(r.evidence.page)
    return 0, f.sheet_code(0)


def basis(f: ProjectFacts, *keys: str) -> str:
    return basis_of(f, *keys)


def source_phrase(f: ProjectFacts, key: str) -> str:
    """How to name where one value came from, inside a finding's prose."""
    r = building(f, key)
    if r is None:
        return "not stated"
    if r.basis == FROM_DECLARATION:
        return "from your project declaration"
    if r.basis == FROM_BOTH:
        return f"declared, and stated on {r.evidence.source.split(' + ')[0]}"
    return f"stated on {r.evidence.source}"


def show(value: Any) -> str:
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float) and value == int(value):
        return f"{int(value):,}"
    if isinstance(value, float):
        return f"{value:,.2f}"
    if isinstance(value, int):
        return f"{value:,}"
    return str(value)


def feet_inches(feet: float) -> str:
    whole = int(feet)
    inches = round((feet - whole) * 12)
    if inches == 12:
        whole, inches = whole + 1, 0
    return f"{whole}'-{inches}\""
