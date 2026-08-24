"""What the drawings already say, offered back as answers to the declaration.

The declaration questionnaire asks the applicant for a dozen facts about the
building. The engine already reads most of those facts off the set itself —
that is the whole basis of the declared-versus-drawn reconciliation — so asking
a designer to type in a number that is printed on G-0 is asking them to
transcribe their own drawing.

This module turns `fbcreview.reconcile.drawn_declaration()` into something a
form control can hold: a string per field, plus where it came from. Nothing
here decides anything. The suggestion is offered, the applicant confirms or
overrides it, and what they submit is what gets declared — which matters,
because the declaration is an assertion by a person and the reconciliation is
only worth running if the two sides are genuinely independent. A silently
auto-accepted value would make every field agree with itself.

No model call, in keeping with the rest of the review path: this is the same
pure-Python parse the review runs, stopped after the facts are built.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from fbcreview import declaration_schema as schema
from fbcreview.pipeline import build_facts
from fbcreview.reconcile import drawn_declaration


@dataclass(frozen=True)
class Suggestion:
    """One field the drawings answer, ready for the form."""

    key: str
    value: str
    source: str
    confidence: str
    note: str
    page: Optional[int]


def control_value(field: schema.Field, value: Any) -> Optional[str]:
    """Render an engine value as the string that field's control holds.

    Returns None when the value cannot be offered honestly — an enum the served
    choice list does not contain, or something that will not convert. Dropping a
    suggestion is always safe; offering one the select cannot hold is not, and
    silently coercing it would put a value in front of the applicant that the
    drawings do not actually state.
    """
    if value is None:
        return None

    if field.kind == "bool":
        return "true" if bool(value) else "false"

    if field.kind == "enum":
        text = str(value).strip()
        choices = field.choices or []
        if choices and text not in choices:
            return None
        return text or None

    if field.kind in ("number", "integer"):
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        if field.kind == "integer":
            return str(int(round(number)))
        # 4180.0 is a building area, not a measurement to four decimal places.
        return str(int(number)) if number == int(number) else f"{number:g}"

    text = str(value).strip()
    return text or None


def suggestions_from_facts(facts: Any) -> List[Suggestion]:
    """Everything the drawings state, in the served field order."""
    drawn: Dict[str, Any] = drawn_declaration(facts)
    out: List[Suggestion] = []

    for field in schema.FIELDS:
        evidence = drawn.get(field.key)
        if evidence is None or evidence.value is None:
            continue
        rendered = control_value(field, evidence.value)
        if rendered is None:
            continue
        out.append(
            Suggestion(
                key=field.key,
                value=rendered,
                source=evidence.source,
                confidence=evidence.confidence,
                note=evidence.note,
                page=evidence.page,
            )
        )
    return out


def read(path: str) -> List[Suggestion]:
    """Parse a permit set and return what it states about the declaration."""
    return suggestions_from_facts(build_facts(path))
