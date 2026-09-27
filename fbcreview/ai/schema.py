"""What the AI reader is allowed to say: values it located, and where.

This schema *is* the guardrail's first line. There is no field here for a
severity, a finding, a verdict, a citation or a threshold, so there is no way
for a model's output to carry one into the review. It can only report that a
value is printed on the sheet, quote the words it is printed in, and name which
catalog fact it believes that value is — and `fbcreview.ai.grounding` checks
all three against the sheet before any of it is used.
"""
from __future__ import annotations

from typing import List, Literal

from pydantic import BaseModel, Field


class FieldReading(BaseModel):
    """One value printed on the sheet, and which fact it states."""

    field: str = Field(description="The catalog key this value states, e.g. "
                                   "'occupant_load' or 'egress.common_path'.")
    value: str = Field(description="The value exactly as printed, e.g. '70', "
                                   "'ASSEMBLY (A-3)', '250 LF', '8\\'-1\"'.")
    quote: str = Field(description="The shortest verbatim run of sheet text that contains "
                                   "both the label and the value, exactly as printed.")
    role: Literal["", "required", "provided"] = Field(
        default="",
        description="For the egress audit fields only: 'required' when the value is the "
                    "limit the sheet says the code sets, 'provided' when it is what the "
                    "design provides. Empty for every other field.")


class SheetReading(BaseModel):
    """Everything one sheet states that the catalog asks about."""

    sheet_number: str = Field(default="", description="The sheet number in the title block.")
    fields: List[FieldReading] = Field(default_factory=list)


#: The only keys a `FieldReading` may carry. Held by a test, so a field added
#: here — a severity, a verdict — fails the build rather than widening what a
#: model can say.
ALLOWED_KEYS = frozenset({"field", "value", "quote", "role"})
