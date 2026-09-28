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


# ── the result reviewer ──────────────────────────────────────────────────────
# What the reviewer is allowed to say after the rules have run. Like the reading
# schema, its shape is the guardrail: it can ask for named sheets to be read
# again for named catalog facts, and it can leave a note for the audit record.
# There is no field for a finding, a severity, a status or a verdict on
# compliance, so there is no way for its output to add, remove, edit or re-rank
# anything the rules decided.

class RereadRequest(BaseModel):
    """One sheet to read again, for facts the review should have found on it."""

    page: int = Field(description="The sheet's page number in the set, 1-based, as listed "
                                  "in the review packet.")
    fields: List[str] = Field(description="Catalog keys to look for on that sheet, e.g. "
                                          "'occupant_load'. Only keys from the catalog count.")
    hint: str = Field(default="", description="Where on the sheet, or in what words, the value "
                                              "appears to be printed. One or two sentences.")


class ResultReview(BaseModel):
    """The reviewer's check of one pass: re-reads to try, and notes for the record."""

    meets_request: bool = Field(description="True when no re-read of any sheet would make this "
                                            "review more complete or more faithful to the set "
                                            "and to what the user asked for.")
    rereads: List[RereadRequest] = Field(
        default_factory=list,
        description="Sheets the readers appear to have missed or misread a catalog fact on. "
                    "Empty when meets_request is true.")
    notes: List[str] = Field(
        default_factory=list,
        description="Concerns a re-read cannot fix, one sentence each, for the audit record.")


#: The only keys each reviewer model may carry. Held by a test, like
#: `ALLOWED_KEYS`: a field added here widens what a model can say about a result.
REVIEW_ALLOWED_KEYS = frozenset({"meets_request", "rereads", "notes"})
REREAD_ALLOWED_KEYS = frozenset({"page", "fields", "hint"})
