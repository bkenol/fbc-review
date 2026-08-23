"""The wire contract.

Every request and response body the service exposes is declared here as an
explicit Pydantic model. Nothing returns a bare dict and nothing is typed
`Any`, because `web/src/app/api/` is generated from the resulting
`/openapi.json` — a field renamed here must become a TypeScript compile error
over there, not a runtime `undefined`.
"""
from __future__ import annotations

import datetime as dt
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

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


# ── findings ──────────────────────────────────────────────────────────────
class Finding(BaseModel):
    """One rule outcome. Mirrors `fbcreview.rules.Finding`."""

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


class Abstention(BaseModel):
    """A rule that declined to run. Never the same thing as a rule that passed."""

    rule: str
    reason: str
    detail: str = ""


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
    expires_at: dt.datetime


class ReviewAccepted(BaseModel):
    id: str


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
    bytes: int
    pages: Optional[int] = None
    source: Optional[SourceProfile] = Field(
        default=None, description="What kind of PDF was uploaded, measured at admission."
    )
    summary: Optional[Summary] = None
    conversion: Optional[ConversionReport] = Field(
        default=None, description="Present when scanned sheets were rebuilt."
    )
    downloads: Optional[Downloads] = Field(
        default=None, description="Present only while state is `done`."
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
