"""The wire contract.

Every request and response body the service exposes is declared here as an
explicit Pydantic model. Nothing returns a bare dict and nothing is typed
`Any`, because `web/src/app/api/` is generated from the resulting
`/openapi.json` — a field renamed here must become a TypeScript compile error
over there, not a runtime `undefined`.
"""
from __future__ import annotations

import datetime as dt
from enum import StrEnum
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

JobState = Literal["queued", "running", "done", "error"]
Severity = Literal["CRITICAL", "HIGH", "MEDIUM", "LOW", "VERIFIED", "MEASURED"]
MinSeverity = Literal["CRITICAL", "HIGH", "MEDIUM", "LOW"]
FindingStatus = Literal["OPEN", "PASS", "CONFLICT"]
FindingScenario = Literal["both", "as_drawn", "as_declared"]
FindingBasis = Literal["drawings", "declaration", "both"]
ReconciledState = Literal[
    "CORROBORATED", "CONFLICT", "DECLARED_ONLY", "DRAWN_ONLY", "UNKNOWN"
]
DeclarationFieldKind = Literal["enum", "bool", "number", "integer", "text"]
ReviewMode = Literal["standard", "training"]
FeedbackPolarity = Literal["good", "defect"]
FeedbackState = Literal["new", "accepted", "rejected", "actioned"]


# The four closed sets below are `StrEnum` rather than `Literal`, and the
# distinction is not stylistic. Pydantic publishes a `Literal` inline at each
# field, and openapi-generator then emits a *separate nominal enum per
# property* — so `Markup.kind` and `MarkupRequest.kind`, generated from one
# definition here, become two TypeScript types that will not assign to each
# other. The client is then forced into casts at exactly the points where the
# same value crosses from a response into the next request.
#
# A `StrEnum` becomes a named `$ref` component instead, and both properties
# point at the one generated type. These four are the ones that cross a model
# boundary; the plain `Literal`s above and elsewhere appear on one field each
# and have no such problem.
#
# `StrEnum` members are real `str` instances, so comparison, JSON encoding and
# Firestore storage are unchanged.
class FeedbackSubject(StrEnum):
    FINDING = "finding"
    COVERAGE = "coverage"
    ABSTENTION = "abstention"
    SWEEP = "sweep"


class Disposition(StrEnum):
    CONFIRMATION = "confirmation"
    AUTO_TUNABLE = "auto_tunable"
    NEEDS_COMPONENT = "needs_component"
    ESCALATE = "escalate"


class MarkupKind(StrEnum):
    HIGHLIGHT = "highlight"
    BOX = "box"
    CLOUD = "cloud"
    ARROW = "arrow"
    STRIKEOUT = "strikeout"
    FREEHAND = "freehand"
    TEXT = "text"
    NOTE = "note"


class Decision(StrEnum):
    ACCEPT = "accept"
    REJECT = "reject"
    ACTION = "action"

def render_knob_value(value: object) -> str:
    """A lever's value, rendered for a person.

    Knob values are genuinely polymorphic — a bool, a step count, a severity, a
    list of occupancy groups — and `openapi-generator` renders an `anyOf` union
    as an empty named interface, which is worse than untyped: nothing can be
    assigned to it at all. Since the client only ever *displays* these (approval
    is all-or-nothing on the proposal the server already holds, and the raw
    value is read back from storage, never from a request body), the wire
    carries the rendering and the store keeps the value.
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return "on" if value else "off"
    if isinstance(value, (list, tuple)):
        return ", ".join(str(v) for v in value)
    return str(value)
SheetKind = Literal["vector", "hybrid", "raster", "blank"]
DocumentKind = Literal["vector", "mixed", "raster", "blank"]


# ── errors ────────────────────────────────────────────────────────────────
class ErrorDetail(BaseModel):
    code: str = Field(description="Stable machine-readable code, e.g. `rate_limited`.")
    message: str = Field(description="Prose safe to show a person.")


class ErrorResponse(BaseModel):
    """The only shape any failure takes."""

    error: ErrorDetail


# ── the project declaration ───────────────────────────────────────────────
class ProjectDeclaration(BaseModel):
    """What the applicant says the building is, answered before uploading.

    Every field is optional and `null` means unanswered. There is deliberately
    no "unknown" sentinel: a rule that receives `null` abstains, and it can
    never mistake a placeholder for an answer.

    Mirrors `fbcreview.declaration.ProjectDeclaration`. Values are validated
    against `fbcreview.declaration_schema` before a review starts, so an enum
    value this build does not know is a 400 naming the field rather than a
    silently dropped answer.
    """

    model_config = ConfigDict(extra="forbid")

    occupancy_group: Optional[str] = None
    mixed_occupancy: Optional[bool] = None
    separation_method: Optional[str] = None
    construction_type: Optional[str] = None
    building_area_sf: Optional[float] = Field(default=None, ge=0)
    total_area_sf: Optional[float] = Field(default=None, ge=0)
    height_ft: Optional[float] = Field(default=None, ge=0)
    stories: Optional[int] = Field(default=None, ge=0)
    sprinkler_system: Optional[str] = None
    wind_speed_mph: Optional[float] = Field(default=None, ge=0)
    exposure_category: Optional[str] = None
    risk_category: Optional[str] = None
    zoning: Optional[str] = Field(default=None, max_length=200)
    jurisdiction: Optional[str] = Field(default=None, max_length=200)
    code_edition: Optional[str] = None


class DeclarationTolerance(BaseModel):
    """How far apart two numbers may be before they count as disagreeing.

    Effective tolerance is `max(abs, rel * value)`. Rounding on a drawing is not
    a conflict; a different governing row is.
    """

    abs: float
    rel: float


class DeclarationField(BaseModel):
    """One question, in both vocabularies.

    The client renders whatever this describes and must not carry a copy of any
    label, help text or enum value: a code label that exists in two places will
    drift, and a wrong code label is a liability rather than a typo.
    """

    key: str
    kind: DeclarationFieldKind
    pro_label: str
    pro_help: str
    simple_label: str
    simple_help: str
    group: str
    choices: Optional[List[str]] = None
    choice_labels: Optional[Dict[str, str]] = None
    unit: str = ""
    unlocks: List[str] = Field(
        default_factory=list,
        description="Rule ids this answer enables. The client divides by these to say "
                    "how many checks will stand down without it.",
    )
    tolerance: Optional[DeclarationTolerance] = None


class DeclarationGroup(BaseModel):
    key: str
    label: str


class ReconciledField(BaseModel):
    """One field, with what each source said about it and how they compare."""

    field: str
    state: ReconciledState
    declared: Optional[str] = Field(default=None, description="Rendered for display.")
    drawn: Optional[str] = None
    drawn_source: Optional[str] = Field(
        default=None, description="The sheet the drawn value was read from."
    )
    note: str = ""


class DeclarationReport(BaseModel):
    """What was submitted and what the drawings said back. The audit trail."""

    declaration: ProjectDeclaration
    answered: int
    total_fields: int
    fields: List[ReconciledField]


# ── config ────────────────────────────────────────────────────────────────
class OccupancyGroup(BaseModel):
    id: str = Field(description="Value submitted with a review, e.g. `A-3`.")
    label: str


class Edition(BaseModel):
    id: str
    label: str
    available: bool = Field(description="False editions are listed and disabled, not hidden.")


class MailStatus(BaseModel):
    configured: bool
    status: str


class ReviewOptions(BaseModel):
    """The review parameters. Mirrors `fbcreview.options.ReviewOptions`."""

    model_config = ConfigDict(extra="forbid")

    edition: str = "fbc2023"
    occupancy_group: str = Field(
        default="A-3",
        description=(
            "Deprecated. Occupancy group is a fact about the building, not a review "
            "setting, and belongs in the project declaration. Sent explicitly, it seeds "
            "`declaration.occupancy_group` when the declaration does not state one; "
            "left at its default it is ignored and nothing is assumed."
        ),
    )
    sprinklered: bool = Field(
        default=True,
        description=(
            "Deprecated, as `occupancy_group`. Sent explicitly it seeds "
            "`declaration.sprinkler_system` as an NFPA 13 system or none."
        ),
    )
    mode: ReviewMode = Field(
        default="standard",
        description=(
            "`standard` reviews against the approved calibration profile, which is "
            "what every applicant sees. `training` reviews against the caller's own "
            "candidate profile and opens the feedback surface, so a reviewer can see "
            "the effect of their own accepted feedback without it reaching anyone "
            "else. Rejected with a 400 when training mode is off on this deployment."
        ),
    )
    min_severity: MinSeverity = "LOW"
    include_verified: bool = True
    include_measured: bool = True
    project_name: str = Field(default="", max_length=200)
    notes: str = Field(default="", max_length=2000)
    convert_raster: bool = Field(
        default=False,
        description=(
            "Rebuild scanned sheets before reviewing: OCR to recover a text layer, "
            "and trace the linework into real vector paths. Off by default because "
            "it turns a two-second review into a multi-minute one. Recovers text, "
            "not meaning — traced lines carry no CAD layer names, so geometric "
            "rules still abstain."
        ),
    )
    email_to: List[str] = Field(
        default_factory=list,
        description="Inert unless SMTP is configured on the server. Not surfaced in the web client.",
    )


# ── the feedback taxonomy ─────────────────────────────────────────────────
class FeedbackVerdict(BaseModel):
    """One answer to one aspect, as the form should offer it.

    Deliberately carries no hint of which calibration lever it moves. The client
    describes what a person saw; `webapp.triage` decides what that implies. A UI
    that showed the lever would invite people to pick the outcome they want
    instead of the observation they made.
    """

    key: str
    label: str
    help: str
    polarity: FeedbackPolarity = Field(
        description="`good` verdicts are real signal, not an opt-out: they are what "
                    "stops one dissent from moving a rule fifty people corroborated."
    )


class FeedbackAspect(BaseModel):
    """One separable dimension of review quality."""

    key: str
    label: str
    help: str
    subject: FeedbackSubject
    required: bool
    verdicts: List[FeedbackVerdict]


class MarkupKindInfo(BaseModel):
    # The union rather than a bare `str`: the client picks a tool from this list
    # and hands the key straight back on `MarkupRequest.kind`, so anything wider
    # here makes that round trip untyped at exactly the point it matters.
    key: MarkupKind
    label: str
    help: str


class MarkupColourInfo(BaseModel):
    """What a markup colour means.

    Served rather than hard-coded in the client for the same reason the
    taxonomy is: these are semantic categories a reader sorts by, not
    decoration, and there must be one definition of what purple means.
    """

    key: str
    label: str
    hex: str = Field(description="The swatch, for the palette and the overlay.")
    help: str


class AbstentionKindInfo(BaseModel):
    """One class of abstention, and what to do about it.

    See `webapp.abstentions`. The client renders what it is handed: it carries
    no copy of a reason string and makes no judgement of its own about whether
    a rule was right to stand down.
    """

    key: str
    label: str
    help: str
    guidance: str = Field(
        description="What would fix it, written for whoever is reading the review."
    )
    proposable: bool = Field(
        description="Whether offering to propose a fix makes sense for this class. "
                    "False where the abstention was correct, or the operator asked "
                    "for it."
    )


class DispositionInfo(BaseModel):
    key: Disposition
    label: str


class TrainingStatus(BaseModel):
    """Whether this deployment collects feedback, and what it can do with it."""

    enabled: bool = Field(description="False reproduces the service exactly as it was.")
    is_owner: bool = Field(
        description="Whether the caller may read the queue and promote a profile. "
                    "Checked server-side on every admin route regardless; this is "
                    "only so the client knows whether to draw the link."
    )
    assist: str = Field(description="Whether free-text comments get summarised, and how.")
    github: str = Field(description="Whether escalations can become issues, and where.")
    profile_version: int = Field(description="The calibration version production uses.")
    profile_label: str = ""
    calibrated_rules: int = Field(
        default=0, description="How many rules the active profile changes."
    )


# ── calibration ───────────────────────────────────────────────────────────
class CalibrationKnob(BaseModel):
    """One lever, published so the admin console hard-codes no list."""

    name: str
    kind: Literal["bool", "int", "float", "severity", "strings"]
    help: str
    bounds: Optional[List[float]] = None
    choices: Optional[List[str]] = None


class RuleCalibration(BaseModel):
    rule_id: str
    enabled: bool = True
    severity_shift: int = 0
    severity_cap: Optional[MinSeverity] = None
    severity_floor: Optional[MinSeverity] = None
    scope_occupancy: List[str] = Field(default_factory=list)
    scope_basis: List[str] = Field(default_factory=list)
    drop_verified: bool = False
    weight: float = 1.0
    confirmations: int = Field(
        default=0, description="Reviewers who said this rule got something right."
    )
    note: str = ""


class CalibrationProfile(BaseModel):
    """A versioned set of rule calibrations. Versions are immutable."""

    profile_id: str
    version: int
    scope: str
    owner_uid: str = ""
    label: str
    edition: str
    rules: Dict[str, RuleCalibration] = Field(default_factory=dict)
    note: str = ""
    derived_from: Optional[int] = None
    created_at: Optional[dt.datetime] = None
    created_by: str = ""


class CalibrationChange(BaseModel):
    """One lever moved on one rule, with why."""

    rule_id: str
    knob: str
    value: str = Field(
        default="", description="The value this change sets, rendered for display."
    )
    reason: str = ""
    note: str = ""

    @field_validator("value", mode="before")
    @classmethod
    def _render(cls, value: object) -> str:
        return render_knob_value(value)


class CalibrationAdjustment(BaseModel):
    """One finding this profile actually changed."""

    fid: str
    rule_id: str
    knob: str
    before: str
    after: str
    detail: str = ""


class CalibrationReport(BaseModel):
    """What the overlay did to a review. Part of the audit trail.

    Recorded whether or not anything moved: a review that says "profile v4,
    nothing adjusted" is a different statement from a review that never
    mentions calibration at all.
    """

    profile_id: str
    profile_version: int
    profile_label: str
    adjusted: int
    suppressed: int
    adjustments: List[CalibrationAdjustment] = Field(default_factory=list)


class CalibrationDiffRow(BaseModel):
    rule_id: str
    knob: str
    before: str = ""
    after: str = ""
    help: str = ""

    @field_validator("before", "after", mode="before")
    @classmethod
    def _render(cls, value: object) -> str:
        return render_knob_value(value)


class CalibrationView(BaseModel):
    """The admin console's calibration screen."""

    active: CalibrationProfile
    versions: List[CalibrationProfile]
    knobs: List[CalibrationKnob]
    pending: int = Field(description="Approved-and-not-yet-promoted proposals. Always 0 "
                                     "today: accepting a proposal promotes it.")


# ── markup ────────────────────────────────────────────────────────────────
class MarkupGeometry(BaseModel):
    """Where a markup sits, in PDF user space.

    PDF points, not screen pixels, and not a fraction of the page. The viewer
    zooms and the window resizes; the drawing does not. Storing device
    coordinates would make every markup wrong at a different zoom level, and
    storing fractions would lose precision on a 24x36 sheet where a door tag is
    a few points across.
    """

    model_config = ConfigDict(extra="forbid")

    x0: float = 0
    y0: float = 0
    x1: float = 0
    y1: float = 0
    points: List[List[float]] = Field(
        default_factory=list,
        description="Freehand and arrow paths, as [[x, y], ...] in the same space.",
        max_length=2000,
    )


class MarkupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page: int = Field(ge=1)
    kind: MarkupKind
    geometry: MarkupGeometry
    comment: str = Field(default="", max_length=4000)
    colour: str = Field(default="", max_length=16)
    sheet: str = Field(default="", max_length=40)
    finding_fid: str = Field(
        default="", max_length=64,
        description="The finding this markup is about, when it is about one.",
    )


class Markup(BaseModel):
    id: str
    job_id: str
    page: int
    sheet: str = ""
    kind: MarkupKind
    geometry: MarkupGeometry
    comment: str = ""
    colour: str = ""
    finding_fid: str = ""
    created_at: dt.datetime


class MarkupList(BaseModel):
    markups: List[Markup]


class MarkupExport(BaseModel):
    """A whole annotated pass, in one document.

    What a reviewer takes away at the end of a session and what the owner reads
    when one is submitted. Deliberately the same payload for both: an export
    somebody can inspect before they send it is an export they will trust, and
    two different shapes would drift.

    Geometry is carried per markup because it is what makes an annotation
    checkable — "sheet 12, this box, this comment" can be found again. It is in
    PDF points, the same space the viewer draws in.
    """

    job_id: str
    filename: str = ""
    project_name: str = ""
    exported_at: dt.datetime
    exported_by: str = Field(default="", description="Empty on an open-access deployment.")
    markups: List[Markup]
    counts: Dict[str, int] = Field(
        default_factory=dict, description="How many of each kind, for the summary line."
    )
    commented: int = Field(default=0, description="How many carry a written comment.")
    sheets: List[int] = Field(
        default_factory=list, description="Sheets carrying at least one annotation."
    )
    #: Plain text, generated server-side. A markup pass is read by a person, and
    #: a person handed 40 JSON objects reads none of them. Same content as
    #: `markups`, ordered by sheet.
    text: str = ""


class SweepRequest(BaseModel):
    """Hand a marked-up pass over for review.

    Carries no markup: the server already holds every annotation on this job for
    this user, and letting the client post its own list would let it submit a
    pass that never existed. `answers` and `comment` are the same shape every
    other submission uses, validated against the `sweep` aspect.
    """

    model_config = ConfigDict(extra="forbid")

    answers: Dict[str, str] = Field(default_factory=dict)
    comment: str = Field(default="", max_length=4000)


# ── feedback ──────────────────────────────────────────────────────────────
class FeedbackRequest(BaseModel):
    """One piece of feedback about one thing.

    `answers` is the structured part and is what gets triaged. `comment` is the
    residue — everything the taxonomy did not ask about — and a submission that
    carries one always reaches a person, because prose nobody has read cannot be
    routed deterministically.
    """

    model_config = ConfigDict(extra="forbid")

    subject: FeedbackSubject = FeedbackSubject.FINDING
    finding_fid: str = Field(default="", max_length=64)
    answers: Dict[str, str] = Field(
        default_factory=dict,
        description="Aspect key to verdict key, validated against the taxonomy "
                    "`/api/config` publishes. An unknown key is a 400 naming it.",
    )
    comment: str = Field(default="", max_length=4000)
    markup_id: str = Field(default="", max_length=64)
    rule_id: str = Field(
        default="", max_length=64,
        description=(
            "The rule an abstention-subject submission is about. Checked against "
            "the abstentions this review actually recorded — a rule id the review "
            "did not stand down on is a 404, so the anchor cannot be invented by "
            "the client any more than a finding id can."
        ),
    )


class AssistOpinion(BaseModel):
    """What `webapp.assist` made of a free-text comment.

    Recorded so the owner can see what was said about their user's words, and
    typed so the client can show it. Advisory in the strict sense: the triage
    may raise a disposition on the strength of it and may never lower one.
    """

    summary: str
    aspect: str = ""
    verdict: str = ""
    names_a_code_section: bool = False
    confidence: Literal["low", "medium", "high"] = "low"
    rationale: str = ""


class TriageResult(BaseModel):
    """Where this feedback has to be fixed, and why."""

    disposition: Disposition
    label: str
    rationale: str
    changes: List[CalibrationChange] = Field(default_factory=list)
    signals: List[str] = Field(default_factory=list)
    assist: Optional[AssistOpinion] = Field(
        default=None,
        description="What the comment assist made of the free text, when there was "
                    "some and it was configured. Advisory — it may raise a "
                    "disposition and never lower one.",
    )


class Feedback(BaseModel):
    id: str
    job_id: str
    subject: FeedbackSubject
    finding_fid: str = ""
    rule_id: str = ""
    sheet: str = ""
    page: int = 0
    answers: Dict[str, str] = Field(default_factory=dict)
    comment: str = ""
    markup_id: str = ""
    disposition: Disposition
    rationale: str = ""
    triage: TriageResult
    state: FeedbackState
    created_at: dt.datetime
    decided_at: Optional[dt.datetime] = None
    decided_by: str = ""
    decision_note: str = ""
    issue_url: str = ""
    #: Present on the admin view only. The submitter's address is not returned
    #: to other submitters.
    email: str = ""
    filename: str = ""
    #: Snapshots taken at submission time. Stored rather than looked up later
    #: because the queue has to show what was reported even after the review's
    #: artefacts have aged out of the bucket — and because a finding re-read
    #: from a re-run review is not the finding the person was looking at.
    finding: Optional[Finding] = None
    markup: Optional[Markup] = None
    #: The whole annotated pass, on a `sweep` submission. A snapshot for the same
    #: reason the other two are: the markup can be edited or deleted afterwards,
    #: and what the owner has to read is what was handed over.
    sweep: Optional[MarkupExport] = None
    applied_to_candidate: bool = Field(
        default=False,
        description="Whether this landed in the submitter's own training profile. "
                    "Production is untouched until the owner promotes.",
    )


class FeedbackList(BaseModel):
    feedback: List[Feedback]


class FeedbackAccepted(BaseModel):
    """What the submitter is told back.

    The triage verdict is returned rather than hidden: someone who reports a
    misread table should be told immediately that it needs engine work and is
    not a knob, instead of watching nothing happen.
    """

    id: str
    triage: TriageResult
    applied_to_candidate: bool
    candidate_version: int = 0
    message: str


class DecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Decision
    note: str = Field(default="", max_length=2000)


class PromptExport(BaseModel):
    """Escalated feedback as a runnable prompt.

    Returned as a typed body rather than `text/markdown` so the generated client
    carries the type, as everything else here does.
    """

    feedback_id: str
    filename: str = Field(description="Suggested name under docs/, in the house style.")
    markdown: str


class IssueCreated(BaseModel):
    feedback_id: str
    url: str = Field(description="Empty when GitHub is not configured on this server.")
    status: str


class AdminOverview(BaseModel):
    open_feedback: int
    counts: Dict[str, int] = Field(description="Open feedback by disposition.")
    mail: MailStatus
    github: str
    assist: str
    active_version: int
    calibrated_rules: int


class ConfigResponse(BaseModel):
    occupancy_groups: List[OccupancyGroup]
    editions: List[Edition]
    severities: List[MinSeverity] = Field(description="Severity floor choices, most severe first.")
    stages: List[str] = Field(description="Ordered stage labels a running job moves through.")
    max_upload_mb: int
    max_pages: int
    retain_days: int
    rules: List[str] = Field(description="Rule ids registered in this build.")
    mail: MailStatus
    defaults: ReviewOptions
    declaration_groups: List[DeclarationGroup] = Field(
        description="Question groups, in the order the form should show them."
    )
    declaration_fields: List[DeclarationField] = Field(
        description=(
            "The whole questionnaire, as data. Adding a field or an enum value in "
            "`fbcreview/declaration_schema.py` reaches the client through here with no "
            "frontend change."
        )
    )
    declaration_unlockable: List[str] = Field(
        description="Every rule any declaration field unlocks, deduplicated."
    )
    training: TrainingStatus
    feedback_aspects: List[FeedbackAspect] = Field(
        description=(
            "The whole feedback taxonomy, as data. Adding a verdict in "
            "`webapp/feedback_schema.py` reaches the client through here with no "
            "frontend change — the same contract the declaration questionnaire has."
        )
    )
    markup_kinds: List[MarkupKindInfo]
    markup_colours: List[MarkupColourInfo] = Field(
        default_factory=list,
        description="What each markup colour means. Semantic categories a reader "
                    "sorts a submitted pass by, not a paint box.",
    )
    abstention_kinds: List[AbstentionKindInfo] = Field(
        default_factory=list,
        description="How `webapp.abstentions` classifies a rule that declined to "
                    "run, and what it says should be done about each class.",
    )
    dispositions: List[DispositionInfo]
    calibration_knobs: List[CalibrationKnob] = Field(
        description="Every lever the overlay has. The closed list this publishes is "
                    "what makes the triage split decidable rather than a judgement."
    )
    ai_reading: bool = Field(
        default=False,
        description="Whether this deployment reads sheets with the AI reader "
                    "(`FBC_AI_READING=on` with a key). Rules are pure Python either way.",
    )


# ── findings ──────────────────────────────────────────────────────────────
class FindingEvidence(BaseModel):
    """One reading a finding rests on: the value, the words as printed, and where.

    Written by `fbcreview/payload.py`. `method` says which reader found it —
    `pair`, `line` and `table` are the layout reader; `ai` is the AI sheet
    reader, whose readings are used only after the quote has been found on the
    sheet, and `note` says so in words a card can show.
    """

    field: str = Field(description="The catalog field, e.g. `egress.common_path`.")
    role: str = Field(default="", description="`required`, `provided`, or empty.")
    value: str = Field(description="The value as a card shows it.")
    quote: str = Field(description="The words the value was read from, as printed.")
    sheet: str
    page: int = Field(description="0-based, like `Finding.page`.")
    rect: Optional[List[float]] = Field(
        default=None,
        description="Where it is printed: pdf.js viewport space at scale 1 on the source page.",
    )
    method: str
    confidence: str
    sheets: List[str] = Field(default_factory=list,
                              description="Every sheet that states the same value.")
    note: str = ""


class Finding(BaseModel):
    """One rule outcome. Mirrors `fbcreview.rules.Finding`, plus what the viewer needs."""

    fid: str
    rule_id: str
    status: FindingStatus
    severity: Severity
    discipline: str
    page: int
    sheet: str
    anchor: str
    title: str
    checked: str
    result: str
    code: str
    action: str = ""
    body: str = ""
    hit: int = 0
    scenario: FindingScenario = Field(
        default="both",
        description=(
            "Which reading produced this. `both` means it came out the same whether the "
            "set was evaluated as drawn or as declared, which is the common case. "
            "`as_drawn` means it appeared only against the drawings; `as_declared` only "
            "against the declaration."
        ),
    )
    basis: FindingBasis = Field(
        default="drawings",
        description=(
            "What the finding rests on. `declaration` means the drawings do not state "
            "the value it depends on, and the card says so — the markup must never "
            "attribute to the drawings something the drawings do not say."
        ),
    )
    key: str = Field(
        default="",
        description=(
            "Unique within one review. `fid` is not — two under-width doors are two "
            "H-03s — so anything the client keys, tracks or selects uses this. Empty "
            "on a review written before it existed; fall back to `fid`."
        ),
    )
    rect: Optional[List[float]] = Field(
        default=None,
        description=(
            "Where to draw the marker: [x0, y0, x1, y1] in pdf.js viewport space at "
            "scale 1 on the uploaded set's page (points, origin top-left, rotation "
            "applied). Null when it could not be placed; `anchor` and `hit` remain "
            "the fallback."
        ),
    )
    evidence: List[FindingEvidence] = Field(
        default_factory=list,
        description="The readings the rule's inputs rest on, and which reader found each.",
    )


class Abstention(BaseModel):
    """A rule that declined to run. Never the same thing as a rule that passed.

    `rule`, `reason` and `detail` are what the engine recorded and are never
    rewritten. `kind` and `proposable` are `webapp.abstentions`' reading of that
    reason, added on the way out — so the register still shows what the rule
    said, and the classification sits beside it rather than in place of it.
    """

    rule: str
    reason: str
    detail: str = ""
    kind: str = Field(
        default="unknown",
        description="Which class of abstention this is. See `AbstentionKindInfo`.",
    )
    proposable: bool = Field(
        default=False,
        description="Whether this one is worth offering a proposal for. False on an "
                    "abstention that was correct — most 'the set does not state it' "
                    "abstentions are.",
    )
    unlocked_by: List[str] = Field(
        default_factory=list,
        description=(
            "Keys of the `DeclarationField`s whose answer would let this rule run. "
            "Inverted from the schema's own `unlocks`, so it cannot drift from it. "
            "Empty where no question would help — a geometric rule that could not "
            "find its linework is not waiting on a questionnaire."
        ),
    )


class AbstentionDiagnosis(BaseModel):
    """A root cause that would account for several abstentions at once.

    Drawn entirely from two things already on the job record — what the rules
    said, and what `webapp.pdfkind` found in the file. Never a certainty, and
    the text never phrases it as one: the sheets it names are the reader's way
    of checking the claim rather than taking it on trust.
    """

    key: str
    headline: str
    detail: str
    action: str = Field(description="What to do about it, in the imperative.")
    rerun: bool = Field(
        description="Whether re-running the review would test the theory. The client "
                    "offers the re-run only where this is true."
    )
    rules: List[str] = Field(
        default_factory=list, description="Rule ids this would account for."
    )
    sheets: List[int] = Field(
        default_factory=list,
        description="Sheets to look at, numbered from 1 as the viewer numbers them.",
    )


class SheetRef(BaseModel):
    """One sheet of the set, as the title block names it.

    The viewer's navigator is the reason this exists. `pages` alone gives it
    "3 of 15", which is not what anybody working through a permit set says to
    anybody else — they say M.001, or A-2. The engine already reads the sheet
    number off the title block to key its findings, so the labels are not a new
    claim about the drawings; they are the same reading, carried far enough
    forward to be navigable.

    A sheet whose number could not be read keeps its page number, exactly as
    `fbcreview.facts.ProjectFacts.sheet_code` does — `p7` rather than a guess.
    """

    page: int = Field(description="1-based, as the viewer numbers pages.")
    code: str = Field(description='Sheet number off the title block, e.g. "M.001".')
    title: str = Field(default="", description="Sheet title, where one was read.")
    discipline: str = Field(default="", description="G / A / M / E / P.")
    read: bool = Field(
        default=True,
        description="False when the sheet number could not be read and `code` is "
                    "a page number standing in for it.",
    )


class AiReadingSummary(BaseModel):
    """Counts only — never sheet text. See `fbcreview/ai/readings.py`."""

    model: str
    prompt_version: str
    sheets_read: int
    sheets_failed: int
    proposals: int = Field(description="Values the model proposed.")
    accepted: int = Field(description="Proposals found on the sheet and used.")
    rejected: int = Field(description="Proposals the sheet did not bear out; never used.")
    usage: Dict[str, int] = Field(default_factory=dict)


class Summary(BaseModel):
    """Counts and provenance for a finished review.

    The findings themselves are deliberately not carried here: a Firestore
    document is capped at 1 MiB and a large set's finding bodies can approach
    it. They live in `findings.json` in the bucket and are fetched from the
    signed URL.
    """

    sheets: int
    pages: int
    cad_layers: int
    annotations: int
    marked: int
    counts: Dict[str, int] = Field(description="Finding count keyed by severity.")
    open: int
    verified: int
    conflicts: int = Field(
        default=0,
        description="Findings where the declaration and the drawings disagree.",
    )
    abstentions: List[Abstention]
    rules_run: int
    scale_pages: int
    pdf_bytes: int
    pdf_name: str
    findings_count: int
    sheet_index: List[SheetRef] = Field(
        default_factory=list,
        description="The set's sheets in page order. Empty on a review run before "
                    "this was recorded, which the client treats as 'label the "
                    "sheets by page number' rather than as an error.",
    )
    ai_reading: Optional[AiReadingSummary] = Field(
        default=None,
        description="What the AI sheet reader did on this review. Null when it was off.",
    )

# ── what kind of PDF was uploaded ─────────────────────────────────────────
class RasterRegion(BaseModel):
    """A pasted image large enough to plausibly hold a table."""

    x0: float
    y0: float
    x1: float
    y1: float
    megapixels: float
    coverage: float


class SheetProfile(BaseModel):
    """Measured, not guessed: drawing-primitive count, live character count and
    how much of the page is covered by raster images."""

    page: int
    kind: SheetKind
    vector_items: int
    live_chars: int
    image_count: int
    image_coverage: float = Field(ge=0.0, le=1.0)
    reason: str
    raster_regions: List[RasterRegion] = Field(default_factory=list)


class SourceProfile(BaseModel):
    """Whether this set is readable, and which sheets are not.

    A scanned sheet produces no findings, and no findings reads as a clean
    sheet. Publishing this lets the client say "not checked" where the truth is
    "could not be read".
    """

    kind: DocumentKind
    cad_layers: int
    reviewable_pages: int
    raster_pages: List[int] = Field(
        default_factory=list, description="Sheets that are wholly images."
    )
    region_pages: List[int] = Field(
        default_factory=list,
        description=(
            "Readable sheets that nonetheless paste part of the drawing in as an "
            "image. A code-analysis table pasted that way is pixels, and needs OCR."
        ),
    )
    summary: str
    sheets: List[SheetProfile]


class ConvertedPage(BaseModel):
    page: int
    ocr_chars: int
    traced_segments: int
    seconds: float
    note: str = ""


class ConversionReport(BaseModel):
    """What the raster rebuild actually recovered.

    Reported rather than summarised away: if OCR found 40 characters on a
    sheet, the review that follows is thin and the person needs to know why.
    """

    converted_pages: List[ConvertedPage]
    ocr_used: bool
    vectorise_used: bool
    seconds: float
    traced_layer: str = Field(
        description=(
            "Optional-content group the traced linework is written to. Deliberately "
            "not named after any semantic CAD layer — tracing recovers lines, not "
            "what they mean."
        )
    )
    recovered_chars: int
    traced_segments: int


# ── jobs ──────────────────────────────────────────────────────────────────
class Downloads(BaseModel):
    """V4 signed URLs, fetched straight from Cloud Storage by the browser."""

    markup_pdf: str
    findings_json: str
    source_pdf: str = Field(
        default="",
        description=(
            "The set as uploaded. The in-app viewer renders this and draws the "
            "findings itself as an overlay, rather than rendering `markup_pdf` — "
            "otherwise every marker would be drawn twice, once burnt into the page "
            "and once interactively, and neither could be turned off."
        ),
    )
    expires_at: dt.datetime


class PrefilledField(BaseModel):
    """One declaration answer the drawings already state.

    A suggestion, never a decision. The applicant confirms or overrides it, and
    what they submit is what gets declared — the reconciliation is only worth
    running if the two sides stay independent, and a silently auto-accepted
    value would make every field agree with itself.
    """

    key: str = Field(description="A declaration field key, as `/api/config` publishes it.")
    value: str = Field(description="Rendered as the field's control holds it, never typed.")
    source: str = Field(description='Where on the set it was read, e.g. "G-0 project data".')
    confidence: str
    note: str = ""
    page: Optional[int] = None


class PrefillResponse(BaseModel):
    """What a set states about itself, read without starting a review."""

    filename: str
    pages: int
    bytes: int
    source: SourceProfile
    fields: List[PrefilledField] = Field(
        description=(
            "Only fields the drawings actually state. A set whose code data block is "
            "a pasted picture yields very few, which is the honest answer for that "
            "set and exactly the case the declaration exists to cover."
        )
    )


class HistoryEntry(BaseModel):
    """One past review, as the history list shows it.

    Deliberately not the whole `Job`. A list of thirty rows does not need thirty
    sets of signed download URLs, and minting them costs a round trip each —
    the client asks for `GET /api/jobs/{id}` when a row is actually opened.
    """

    id: str
    filename: str
    state: JobState
    created_at: dt.datetime
    finished_at: Optional[dt.datetime] = None
    pages: int = 0
    project_name: str = ""
    edition: str = ""
    error: Optional[str] = None
    counts: Dict[str, int] = Field(
        default_factory=dict,
        description="Findings by severity, from the stored summary. Empty until done.",
    )
    open_findings: int = 0
    declared_fields: int = Field(
        default=0, description="How many declaration questions were answered."
    )


class HistoryResponse(BaseModel):
    """The caller's own reviews, newest first. Never anyone else's."""

    entries: List[HistoryEntry]


class ReviewAccepted(BaseModel):
    id: str


class RerunRequest(BaseModel):
    """Review the same set again, having answered more of the declaration.

    The commonest abstention on a real submittal is a rule standing down for
    want of a value that neither the drawings nor the declaration carry. Until
    this existed the only remedy was to upload the file again and re-answer the
    whole questionnaire, which is why nobody did it and the abstention stayed.

    The set itself is never re-sent: the PDF is already in the bucket and the
    admission profile is already on the record, so this costs a re-run of the
    engine and nothing else.
    """

    model_config = ConfigDict(extra="forbid")

    declaration: ProjectDeclaration = Field(
        default_factory=lambda: ProjectDeclaration(),
        description=(
            "The declaration for the new review. Merged over the original's — a "
            "field omitted here keeps whatever the first review was given, and "
            "nothing that was already answered has to be typed again."
        ),
    )
    convert_raster: Optional[bool] = Field(
        default=None,
        description=(
            "Override the rebuild-scanned-sheets option. Omitted keeps what the "
            "original review ran with."
        ),
    )


class Job(BaseModel):
    id: str
    filename: str = Field(description="Basename as uploaded. Never a path.")
    state: JobState
    stage: int = Field(ge=0, description="Index into `stages`.")
    stage_label: str
    stages: List[str]
    options: ReviewOptions
    declaration: Optional[ProjectDeclaration] = Field(
        default=None, description="What was submitted with the review, if anything."
    )
    rerun_of: Optional[str] = Field(
        default=None,
        description=(
            "The review this one re-ran, when it re-ran one. Set by "
            "`POST /api/jobs/{id}/rerun`; null on a review of a freshly uploaded "
            "set. The two are separate reviews on purpose — a review is a dated "
            "statement, and the earlier one is not edited."
        ),
    )
    bytes: int
    pages: Optional[int] = None
    source: Optional[SourceProfile] = Field(
        default=None, description="What kind of PDF was uploaded, measured at admission."
    )
    summary: Optional[Summary] = None
    conversion: Optional[ConversionReport] = Field(
        default=None, description="Present when scanned sheets were rebuilt."
    )
    calibration: Optional[CalibrationReport] = Field(
        default=None,
        description="What the calibration overlay did to this review. Present on any "
                    "finished review, including one where it changed nothing.",
    )
    downloads: Optional[Downloads] = Field(
        default=None, description="Present only while state is `done`."
    )
    diagnosis: List[AbstentionDiagnosis] = Field(
        default_factory=list,
        description=(
            "Root causes that would account for several of this review's "
            "abstentions at once. Computed on read from the abstentions and the "
            "source profile, so an old review gets one too. Empty when nothing "
            "in the record supports a claim."
        ),
    )
    error: Optional[str] = None
    error_code: Optional[str] = None
    created_at: dt.datetime
    started_at: Optional[dt.datetime] = None
    finished_at: Optional[dt.datetime] = None
    elapsed_seconds: float


# ── the findings document ─────────────────────────────────────────────────
class FindingsDocument(BaseModel):
    """The exact shape of `findings.json` in the bucket.

    No endpoint serves this — the browser fetches it from the signed URL — but
    it is published into `components.schemas` (see `server.custom_openapi`) so
    the generated TypeScript client carries the type. Without that the findings
    table would be the one part of the client typed by hand, which is precisely
    the part most likely to drift.
    """

    job_id: str
    options: ReviewOptions
    declaration: Optional[DeclarationReport] = Field(
        default=None, description="Present when a project declaration was submitted."
    )
    summary: Summary
    calibration: Optional[CalibrationReport] = Field(
        default=None, description="The profile this review ran under, and what it moved."
    )
    findings: List[Finding]


# ── health ────────────────────────────────────────────────────────────────
class Health(BaseModel):
    """Deliberately cheap: no Firestore or Cloud Storage call. Cloud Run's
    startup probe hits this."""

    ok: bool
    service: str
    version: str
    auth_required: bool = Field(
        default=True,
        description=(
            "Whether this deployment demands a Firebase ID token. False only on a "
            "deliberately open build. The client reads it here rather than "
            "inferring it, because guessing wrong in either direction is bad: "
            "guess `true` on an open deployment and the tool is unreachable; "
            "guess `false` on a real one and every request 401s behind a UI that "
            "claims you are signed in."
        ),
    )
