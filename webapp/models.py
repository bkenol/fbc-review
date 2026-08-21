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
FindingStatus = Literal["OPEN", "PASS"]
SheetKind = Literal["vector", "hybrid", "raster", "blank"]
DocumentKind = Literal["vector", "mixed", "raster", "blank"]


# ── errors ────────────────────────────────────────────────────────────────
class ErrorDetail(BaseModel):
    code: str = Field(description="Stable machine-readable code, e.g. `rate_limited`.")
    message: str = Field(description="Prose safe to show a person.")


class ErrorResponse(BaseModel):
    """The only shape any failure takes."""

    error: ErrorDetail


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
    occupancy_group: str = "A-3"
    sprinklered: bool = True
    min_severity: MinSeverity = "LOW"
    include_verified: bool = True
    include_measured: bool = True
    project_name: str = Field(default="", max_length=200)
    notes: str = Field(default="", max_length=2000)
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
    abstentions: List[Abstention]
    rules_run: int
    scale_pages: int
    pdf_bytes: int
    pdf_name: str
    findings_count: int


# ── what kind of PDF was uploaded ─────────────────────────────────────────
class SheetProfile(BaseModel):
    """Measured, not guessed: path count, live character count and how much of
    the page is covered by raster images."""

    page: int
    kind: SheetKind
    vector_paths: int
    live_chars: int
    image_count: int
    image_coverage: float = Field(ge=0.0, le=1.0)
    reason: str


class SourceProfile(BaseModel):
    """Whether this set is readable, and which sheets are not.

    A scanned sheet produces no findings, and no findings reads as a clean
    sheet. Publishing this lets the client say "not checked" where the truth is
    "could not be read".
    """

    kind: DocumentKind
    cad_layers: int
    reviewable_pages: int
    raster_pages: List[int]
    summary: str
    sheets: List[SheetProfile]


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
    bytes: int
    pages: Optional[int] = None
    source: Optional[SourceProfile] = Field(
        default=None, description="What kind of PDF was uploaded, measured at admission."
    )
    summary: Optional[Summary] = None
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
    summary: Summary
    findings: List[Finding]


# ── health ────────────────────────────────────────────────────────────────
class Health(BaseModel):
    """Deliberately cheap: no Firestore or Cloud Storage call. Cloud Run's
    startup probe hits this."""

    ok: bool
    service: str
    version: str
