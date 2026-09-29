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
# What the reviewer may say after the rules have run (`CLAUDE.md`, rule 7, as
# amended by the owner on 2026-09-28): sheets to read again, direct edits to
# the findings, and notes for the audit record. Every edit is validated before
# it is applied (`fbcreview.ai.review.apply_edits`): an edit that adds or
# withdraws a finding, or changes a severity or status, must quote text that is
# printed on the sheet, and every applied edit is labelled on the finding.

class RereadRequest(BaseModel):
    """One sheet to read again, for facts the review should have found on it."""

    page: int = Field(description="The sheet's page number in the set, 1-based, as listed "
                                  "in the review packet.")
    fields: List[str] = Field(description="Catalog keys to look for on that sheet, e.g. "
                                          "'occupant_load'. Only keys from the catalog count.")
    hint: str = Field(default="", description="Where on the sheet, or in what words, the value "
                                              "appears to be printed. One or two sentences.")


class FindingEdit(BaseModel):
    """One direct change to the review's findings."""

    op: Literal["revise", "add", "withdraw"] = Field(
        description="'revise' changes an existing finding, 'add' raises one the rules missed, "
                    "'withdraw' removes one the sheets or the request show is wrong.")
    key: str = Field(default="", description="For revise and withdraw: the finding's key as "
                                             "listed in the packet.")
    severity: Literal["", "CRITICAL", "HIGH", "MEDIUM", "LOW", "VERIFIED"] = Field(
        default="", description="The new severity. Empty leaves it as it is.")
    status: Literal["", "OPEN", "PASS"] = Field(
        default="", description="OPEN for a problem to act on, PASS for a check that passed. "
                                "Empty leaves it as it is.")
    title: str = Field(default="", description="New one-line title. Empty leaves it.")
    result: str = Field(default="", description="New statement of what was found. Empty "
                                                "leaves it.")
    remedy: str = Field(default="", description="What the applicant should do. Empty leaves it.")
    code: str = Field(default="", description="Code section cited, e.g. 'FBC-B 1010.1.1'. "
                                              "Empty leaves it.")
    page: int = Field(default=0, description="1-based page the quote is printed on. Required "
                                             "for add; for revise and withdraw, defaults to the "
                                             "finding's own page.")
    quote: str = Field(default="", description="The shortest verbatim run of the sheet's text "
                                               "that supports this edit, exactly as printed. "
                                               "Required to add or withdraw a finding or to "
                                               "change a severity or status.")
    reason: str = Field(description="Why, in one or two sentences. Shown on the finding.")


class ResultReview(BaseModel):
    """The reviewer's check of one pass: re-reads, direct edits, and notes."""

    meets_request: bool = Field(description="True when, after the edits in this answer, the "
                                            "review is complete, faithful to the set and to "
                                            "what the user asked for.")
    rereads: List[RereadRequest] = Field(
        default_factory=list,
        description="Sheets the readers appear to have missed or misread a catalog fact on.")
    edits: List[FindingEdit] = Field(
        default_factory=list,
        description="Direct changes to the findings, applied in order.")
    notes: List[str] = Field(
        default_factory=list,
        description="Anything else worth keeping for the audit record, one sentence each.")


#: The keys each reviewer model may carry. Held by a test, like `ALLOWED_KEYS`,
#: so widening what a model can say about a result is a deliberate change.
REVIEW_ALLOWED_KEYS = frozenset({"meets_request", "rereads", "edits", "notes"})
REREAD_ALLOWED_KEYS = frozenset({"page", "fields", "hint"})
EDIT_ALLOWED_KEYS = frozenset({"op", "key", "severity", "status", "title", "result", "remedy",
                               "code", "page", "quote", "reason"})
