"""Why a rule stood down, and whether that is anybody's fault.

A rule that abstains is already reported — `fbcreview` never lets "not checked"
look like "checked and passed", and the register lists every abstention with the
reason the rule gave.  What the register does *not* say is whether the
abstention was **right**.

Those are very different situations wearing the same words:

* `DECL.HEIGHT — neither the drawings nor the declaration state this` on a set
  whose code-analysis sheet genuinely omits the height.  The rule was correct
  to stand down and there is nothing to fix.
* The same line on a set whose G-002 prints `HEIGHT: 25'-4"` in a table that was
  pasted in as a **picture**.  The value is on the sheet, in ink, and nothing
  read it.  That is an extraction defect, and it is the single most common
  reason a real permit set comes back with twenty abstentions.

Only the second is a problem, and only the second should be proposable as work.
Telling them apart is what this module is for.

## What it does and does not claim

It classifies the *reason string*, not the drawing.  That is a deliberate limit:
this module cannot see the sheet, so it never asserts that a value is printed
somewhere.  What it can say is which **class** of failure a reason belongs to,
what would have to be true for the abstention to be wrong, and what the operator
could do to find out.  The reviewer confirms or denies it — which is exactly the
shape the feedback taxonomy already wants (`webapp.feedback_schema.ABSTENTION`).

The one inference it does draw is corroborated rather than guessed: when a
review reports sheets that paste part of the drawing in as an image and the
rebuild was never run, an abstention for want of a value stops being
"the set does not say" and becomes "we did not read the part of the set that
would have said".  Both facts come off the same job record.  See `diagnose`.

## Why this lives in `webapp/` and not in the engine

The engine records what happened.  Deciding whether what happened was
acceptable, and routing the remedy, is product judgement layered on top — the
same split as `webapp.triage`, which decides where a complaint about a *finding*
has to be fixed.  Keeping it here also means a reason string can be
reclassified, and its guidance reworded, without touching the rule corpus.

Measured against the ITEC Alico Park set (35 sheets, 31 rules, 25 abstentions),
this classifies 24 of the 25 and leaves one unknown rather than guessing.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

# ── the classes ───────────────────────────────────────────────────────────
#: A value the rule needed was not pulled off the set.  The commonest class by
#: far, and the only one where the sheet may well state the answer.
EXTRACTION = "extraction"
#: The drawing's geometry could not be identified — no CAD layer of that name,
#: no scale, no line work.  An extraction failure too, but one whose remedy is
#: layer naming or a vector plot rather than a better parser.
GEOMETRY = "geometry"
#: The code data this build carries has no row for the case.  A corpus gap:
#: real work, and never automatic — the corpus is the moat.
CORPUS = "corpus"
#: Neither the drawings nor the declaration state it, and this build has no
#: reason to believe otherwise.  The abstention was correct.
ABSENT = "absent"
#: Switched off in the review options.  Working as asked.
OPTION = "option"
#: The rule raised.  An engine bug, always.
ERROR = "error"
#: No pattern matched.  Said plainly rather than filed under a guess.
UNKNOWN = "unknown"

KINDS = (EXTRACTION, GEOMETRY, CORPUS, ABSENT, OPTION, ERROR, UNKNOWN)


@dataclass(frozen=True)
class Kind:
    """One class of abstention, and what a person should do about it."""

    key: str
    label: str
    #: What this class means, for the register.
    help: str
    #: What would fix it, written for whoever is reading the review.
    guidance: str
    #: Whether offering "propose a fix" makes sense.  False where there is
    #: nothing to propose: the abstention was right, or the operator asked for
    #: it.
    proposable: bool

    def to_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "help": self.help,
            "guidance": self.guidance,
            "proposable": self.proposable,
        }


KIND_CATALOGUE: Tuple[Kind, ...] = (
    Kind(
        EXTRACTION,
        "Not read off the set",
        "A value the rule needed was not pulled out of the drawings.",
        "If this value is printed on a sheet, the extractor missed it and that is "
        "a defect worth reporting. Check the sheet it should be on before you "
        "propose anything — a value that genuinely is not there is not a bug.",
        True,
    ),
    Kind(
        GEOMETRY,
        "Drawing geometry not identified",
        "The linework the rule measures could not be found or scaled.",
        "Geometric rules select by CAD layer name. A set plotted without layers, "
        "or with this office's own naming, gives them nothing to select. Say what "
        "the layer is actually called and it becomes a mapping rather than a "
        "rewrite.",
        True,
    ),
    Kind(
        CORPUS,
        "Not in this build's code data",
        "The row or table the rule needed is not carried in this build.",
        "The code corpus is transcribed by hand and checked, so a missing row is "
        "added deliberately rather than inferred. Report it with the table and "
        "the case, and it becomes a corpus entry.",
        True,
    ),
    Kind(
        ABSENT,
        "Not stated anywhere",
        "Neither the drawings nor the declaration carry this value.",
        "The rule was right to stand down. Answer the question in the project "
        "declaration, or state it on the sheet, and the check runs. Report it "
        "only if the value is in fact printed somewhere.",
        False,
    ),
    Kind(
        OPTION,
        "Switched off",
        "This check was disabled in the review options.",
        "Turn the option back on and re-run. Nothing is wrong.",
        False,
    ),
    Kind(
        ERROR,
        "The rule failed",
        "The rule raised rather than deciding.",
        "This is an engine bug and should be reported as one. The review is "
        "otherwise unaffected — a rule that raises is recorded as an abstention "
        "rather than taking the run down.",
        True,
    ),
    Kind(
        UNKNOWN,
        "Unclassified",
        "This build has no classification for that reason.",
        "Report it. An unclassified reason is a gap in this list, not a "
        "judgement that the abstention was fine.",
        True,
    ),
)

_BY_KEY: Dict[str, Kind] = {k.key: k for k in KIND_CATALOGUE}


# ── reason matching ───────────────────────────────────────────────────────
# Ordered, first match wins.  Written against the reason strings the corpus
# actually emits; `tests/test_abstentions.py` asserts every one of them is
# covered, so a new reason in the engine fails a test here rather than quietly
# landing in UNKNOWN.
_PATTERNS: Tuple[Tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"rule raised"), ERROR),
    (re.compile(r"switched off in the review options"), OPTION),
    # Corpus gaps name the table they could not find a row in.
    (re.compile(r"not carried in this build'?s corpus"), CORPUS),
    (re.compile(r"has no single gross factor"), CORPUS),
    (re.compile(r"no asce 7 edition is recorded"), CORPUS),
    (re.compile(r"carries no effective date"), CORPUS),
    # Geometry: layers, scale, line work.
    (re.compile(r"no cad layer matching"), GEOMETRY),
    (re.compile(r"carries no line geometry"), GEOMETRY),
    (re.compile(r"scale unresolved"), GEOMETRY),
    # "Nobody stated it" — the honest reading, and the one `diagnose` may
    # revise when the set turns out to carry unread pasted tables.
    (re.compile(r"neither the drawings nor the declaration state this"), ABSENT),
    (re.compile(r"not stated on the drawings and not answered"), ABSENT),
    # Everything else the corpus says is some flavour of "we could not get it".
    (re.compile(r"not extracted"), EXTRACTION),
    (re.compile(r"no parseable"), EXTRACTION),
    (re.compile(r"no code datum"), EXTRACTION),
    (re.compile(r"schedule extracted|schedule found"), EXTRACTION),
    (re.compile(r"no load calculation table found"), EXTRACTION),
    (re.compile(r"no outside-air column"), EXTRACTION),
    (re.compile(r"not found (in|on)"), EXTRACTION),
    (re.compile(r"row not found"), EXTRACTION),
    (re.compile(r"no area to apply"), EXTRACTION),
    (re.compile(r"reporting floor"), EXTRACTION),
    (re.compile(r"field not reconciled"), EXTRACTION),
    (re.compile(r"no sheets to identify"), EXTRACTION),
)


def classify(reason: str) -> str:
    """Which class of abstention this reason is, on its own.

    Context-free by design: `diagnose` is where the job's own facts are allowed
    to revise the answer, and keeping the two apart means this function is a
    pure lookup that can be tested against every string the corpus emits.
    """
    text = (reason or "").strip().lower()
    if not text:
        return UNKNOWN
    for pattern, kind in _PATTERNS:
        if pattern.search(text):
            return kind
    return UNKNOWN


def kind(key: str) -> Kind:
    return _BY_KEY.get(key, _BY_KEY[UNKNOWN])


def catalogue() -> List[Dict[str, Any]]:
    """The whole classification, for `GET /api/config`."""
    return [k.to_dict() for k in KIND_CATALOGUE]


# ── the corroborated inference ────────────────────────────────────────────
#: How many abstentions for want of a value it takes before "the set does not
#: say" stops being a plausible account of all of them at once.  One rule
#: standing down is ordinary; a dozen standing down on a set that carries unread
#: pasted tables is a pattern.
_PATTERN_FLOOR = 3


@dataclass(frozen=True)
class Diagnosis:
    """A root cause that would account for several abstentions at once.

    Never a certainty and never phrased as one.  Each carries the abstentions it
    would explain, so the reader can check the claim against the sheets rather
    than take it on trust.
    """

    key: str
    headline: str
    detail: str
    #: What to do about it, in the imperative.
    action: str
    #: Whether the client can offer to re-run the review to test the theory.
    rerun: bool
    #: Rule ids this would account for.
    rules: Tuple[str, ...]
    #: Sheets to look at, 1-based as the viewer numbers them.
    sheets: Tuple[int, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "headline": self.headline,
            "detail": self.detail,
            "action": self.action,
            "rerun": self.rerun,
            "rules": list(self.rules),
            "sheets": list(self.sheets),
        }


def _plural(n: int, one: str, many: str) -> str:
    return one if n == 1 else many


def classify_all(
    abstentions: Iterable[Dict[str, Any]],
    *,
    unread_pasted_tables: bool = False,
) -> List[Dict[str, Any]]:
    """Classify a job's abstentions, in place-ish (a new list of new dicts).

    `unread_pasted_tables` is the one piece of context allowed to change an
    answer.  When the set has sheets that paste part of the drawing in as an
    image and the rebuild was not run, "neither the drawings nor the declaration
    state this" is no longer a statement about the drawings — it is a statement
    about the part of the drawings that was read.  Those become `extraction`,
    which is the class that is proposable.

    Nothing else is revised, and the original reason string is never rewritten:
    the register still shows what the rule said.
    """
    out: List[Dict[str, Any]] = []
    for record in abstentions:
        row = dict(record)
        found = classify(str(row.get("reason", "")))
        if found == ABSENT and unread_pasted_tables:
            found = EXTRACTION
        row["kind"] = found
        row["proposable"] = kind(found).proposable
        out.append(row)
    return out


def diagnose(
    abstentions: Sequence[Dict[str, Any]],
    *,
    region_pages: Sequence[int] = (),
    raster_pages: Sequence[int] = (),
    converted: bool = False,
    cad_layers: int = 0,
    measured_off: bool = False,
) -> List[Dict[str, Any]]:
    """Root causes that would account for several abstentions at once.

    Every claim here is drawn from two facts already on the job record — what
    the rules said, and what `webapp.pdfkind` found in the file — and both are
    named in the text so the reader can check it.  Nothing is inferred from the
    drawing itself, because nothing here has seen the drawing.

    Pages are reported 1-based.  `pdfkind` counts from zero and the viewer counts
    from one; a diagnosis that sends somebody to the wrong sheet is worse than
    no diagnosis.
    """
    classified = classify_all(
        abstentions,
        unread_pasted_tables=bool(region_pages) and not converted,
    )
    by_kind: Dict[str, List[str]] = {k: [] for k in KINDS}
    for row in classified:
        by_kind[row["kind"]].append(str(row.get("rule", "")))

    out: List[Diagnosis] = []

    # ── the big one: a code table that is a picture ────────────────────────
    starved = by_kind[EXTRACTION]
    if region_pages and not converted and len(starved) >= _PATTERN_FLOOR:
        sheets = tuple(p + 1 for p in sorted(set(region_pages)))
        out.append(Diagnosis(
            key="pasted_code_table",
            headline=(
                f"{len(starved)} rules stood down for want of a value, and "
                f"{len(sheets)} {_plural(len(sheets), 'sheet pastes', 'sheets paste')} "
                "part of the drawing in as a picture."
            ),
            detail=(
                "Those sheets are proper vector, so nothing looks wrong — but an "
                "image of a code-analysis table is pixels, and no text came out of "
                f"it. Sheet {', '.join(str(s) for s in sheets)} "
                f"{_plural(len(sheets), 'is', 'are')} where to look. If the "
                "occupancy, construction type, height, storeys or areas these "
                "rules wanted are printed in one of those tables, this is why they "
                "were not checked — not because the set is silent about them."
            ),
            action=(
                "Re-run with the pasted sheets rebuilt. That OCRs them and puts "
                "their text back where the extractors can read it."
            ),
            rerun=True,
            rules=tuple(sorted(starved)),
            sheets=sheets,
        ))

    # ── a set that is images all the way down ──────────────────────────────
    if raster_pages and not converted:
        sheets = tuple(p + 1 for p in sorted(set(raster_pages)))
        out.append(Diagnosis(
            key="scanned_sheets",
            headline=(
                f"{len(sheets)} {_plural(len(sheets), 'sheet is', 'sheets are')} "
                "wholly an image."
            ),
            detail=(
                f"Nothing on sheet {', '.join(str(s) for s in sheets)} was read at "
                "all — not the schedules, not the code data, not the title block. "
                "Any rule that keys on those sheets had nothing to work from."
            ),
            action="Re-run with the scanned sheets rebuilt, or supply a set plotted from CAD.",
            rerun=True,
            rules=(),
            sheets=sheets,
        ))

    # ── geometry with no layers to select ──────────────────────────────────
    geometric = by_kind[GEOMETRY]
    if geometric and not measured_off:
        out.append(Diagnosis(
            key="no_layers",
            headline=(
                f"{len(geometric)} geometric "
                f"{_plural(len(geometric), 'rule', 'rules')} could not identify the "
                "linework"
                + (" — this set carries no CAD layers at all." if not cad_layers else ".")
            ),
            detail=(
                "Geometric rules select paths by CAD layer name rather than by "
                "guessing which run of lines is an egress path. A plot that "
                "flattened its layers, or one that names them to this office's own "
                "standard, gives them nothing to match. Measuring the longest line "
                "on the sheet instead would very often measure the title block and "
                "report it as a travel distance, which is worse than not measuring."
            ),
            action=(
                "Say what the layer is actually called in your standard. A layer-name "
                "mapping is a small change; inferring the semantics is not."
            ),
            rerun=False,
            rules=tuple(sorted(geometric)),
        ))

    # ── corpus gaps ────────────────────────────────────────────────────────
    gaps = by_kind[CORPUS]
    if gaps:
        out.append(Diagnosis(
            key="corpus_gap",
            headline=(
                f"{len(gaps)} {_plural(len(gaps), 'rule', 'rules')} wanted code data "
                "this build does not carry."
            ),
            detail=(
                "The code corpus is transcribed and checked by hand rather than "
                "scraped, which is why it is trusted and why it has edges. A rule "
                "that cannot find its row abstains rather than interpolating one."
            ),
            action="Report the table and the case. Adding the row is deliberate work.",
            rerun=False,
            rules=tuple(sorted(gaps)),
        ))

    # ── rules that raised ──────────────────────────────────────────────────
    raised = by_kind[ERROR]
    if raised:
        out.append(Diagnosis(
            key="rule_error",
            headline=(
                f"{len(raised)} {_plural(len(raised), 'rule', 'rules')} failed rather "
                "than deciding."
            ),
            detail=(
                "A rule that raises is recorded as an abstention so it cannot take "
                "the run down with it. It is still a bug."
            ),
            action="Report it. Nothing about the set will fix this one.",
            rerun=False,
            rules=tuple(sorted(raised)),
        ))

    return [d.to_dict() for d in out]


def prefill(reason: str, *, unread_pasted_tables: bool = False) -> Optional[str]:
    """The verdict this abstention argues for, before a person confirms it.

    Used to open the proposal form on the answer the classification implies
    rather than on an empty list.  It is a suggestion the reviewer must accept:
    the form submits what they chose, never this.

    `None` where the classification implies nothing worth pre-selecting — which
    is deliberate for `absent`, because pre-selecting "the data is right here"
    on a set that genuinely does not state it would be putting words in the
    reviewer's mouth about the one case where the abstention was correct.
    """
    found = classify(reason)
    if found == ABSENT and unread_pasted_tables:
        return "data_in_image"
    return {
        EXTRACTION: "data_on_sheet",
        GEOMETRY: "layer_named_differently",
        CORPUS: "corpus_missing",
        ERROR: "rule_failed",
    }.get(found)
