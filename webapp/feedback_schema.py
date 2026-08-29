"""What a reviewer may say about a finding — data, not UI.

This module is the single source of truth for the feedback vocabulary.  The API
serves it verbatim (`GET /api/config`) and the Angular client renders whatever
it is handed, exactly as it does for `fbcreview.declaration_schema`.  A verdict
added here reaches the browser with no frontend change, and no label can drift
between two copies because there is only one.

**Why a taxonomy rather than a comment box.**  "This finding is wrong" is not
actionable: it does not say whether the code section was misquoted, the drawing
misread, the severity overstated, or the rule simply inapplicable to this
building — and those four have nothing in common.  The first is a corpus
correction, the second an extraction bug, the third a knob, the fourth a
scoping decision.  A free-text box makes a person invent that breakdown every
time and makes the triage guess at it afterwards.  Asking the question in the
shape of the answer is what lets `webapp.triage` decide deterministically.

**The aspects are chosen to be separable.**  Every one of them can be right
while the others are wrong: a finding can cite the correct section, read the
drawing correctly, land on the right sheet, and still be at the wrong severity.
Aspects that cannot vary independently would collect noise, because a person
answering seven questions about one finding will answer them consistently
whether or not that consistency is real.

**Each aspect carries its own praise verdict.**  The first choice in every list
is the "this was good" answer, and it is a real signal rather than an opt-out:
`conclusion=correct` on a rule that another user disputed is exactly the
evidence that stops one dissent from moving the ramp.

`knob` names the calibration lever a verdict implies, and it is the whole
mechanism behind the triage split.  A verdict whose `knob` is `None` is one the
overlay cannot express — which is not a gap to be papered over but the honest
statement that the fix is code, not configuration.  See
`webapp.calibration.KNOBS` for what the overlay can actually do.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

# ── what a piece of feedback is about ─────────────────────────────────────
#: Feedback on a finding the reviewer produced.
SUBJECT_FINDING = "finding"
#: Feedback on something the reviewer did *not* produce, anchored to a markup
#: the user drew on the sheet. The overlay can never invent a finding, so this
#: subject always means new code — which is why it is a separate subject and
#: not a seventh aspect.
SUBJECT_COVERAGE = "coverage"

SUBJECTS = (SUBJECT_FINDING, SUBJECT_COVERAGE)

# ── verdict polarity ──────────────────────────────────────────────────────
GOOD, DEFECT = "good", "defect"

# ── where a defect has to be fixed ────────────────────────────────────────
#: Expressible as a calibration knob. The overlay can carry this.
TUNABLE = "tunable"
#: Needs engine work — extraction, a new rule, the renderer. No knob reaches it.
COMPONENT = "component"
#: Needs a person. Code citations live here unconditionally: changing what
#: section a finding cites is a correctness claim about the law, and no amount
#: of user agreement makes it safe to apply automatically.
JUDGEMENT = "judgement"


@dataclass(frozen=True)
class Verdict:
    """One answer to one aspect.

    `knob` is the calibration lever this verdict argues for, and `delta` how far
    it argues for moving it. Both are `None` on a verdict whose remedy is not a
    knob at all — which is most of them, deliberately.
    """

    key: str
    label: str
    help: str
    polarity: str = DEFECT
    remedy: str = JUDGEMENT
    knob: Optional[str] = None
    delta: Any = None

    def to_dict(self) -> Dict[str, Any]:
        out = asdict(self)
        # The client renders and submits; it has no business knowing which
        # lever an answer moves, and publishing that would invite a UI that
        # lets people pick the outcome instead of describing what they saw.
        for internal in ("knob", "delta", "remedy"):
            out.pop(internal)
        return out


@dataclass(frozen=True)
class Aspect:
    """One separable dimension of review quality."""

    key: str
    label: str
    help: str
    subject: str
    verdicts: Tuple[Verdict, ...]
    #: Answering is never compulsory. A person who only noticed the severity
    #: was wrong should say that and nothing else — forcing six answers to
    #: register one buys five guesses.
    required: bool = False

    def verdict(self, key: str) -> Optional[Verdict]:
        for v in self.verdicts:
            if v.key == key:
                return v
        return None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "help": self.help,
            "subject": self.subject,
            "required": self.required,
            "verdicts": [v.to_dict() for v in self.verdicts],
        }


# ── the aspects ───────────────────────────────────────────────────────────
# Ordered as a person reads a finding: was it right, how much did it matter,
# does the citation hold, was the drawing read correctly, is it in the right
# place, can it be acted on.

CONCLUSION = Aspect(
    key="conclusion",
    label="The call",
    help="Was this finding right about the set?",
    subject=SUBJECT_FINDING,
    verdicts=(
        Verdict(
            "correct", "Right — this is a real issue",
            "The set does have the problem this describes.",
            polarity=GOOD, remedy=TUNABLE, knob="confirmations", delta=1,
        ),
        Verdict(
            "false_positive", "Wrong — the set complies",
            "The drawings satisfy this requirement and the finding should not have "
            "been raised.",
            remedy=TUNABLE, knob="severity_shift", delta=-1,
        ),
        Verdict(
            "not_applicable", "Does not apply to this project",
            "The requirement is real but does not govern this occupancy, scope or "
            "construction type.",
            remedy=TUNABLE, knob="scope_occupancy", delta="exclude",
        ),
        Verdict(
            "right_wrong_reason", "Right answer, wrong reasoning",
            "There is an issue here, but not for the reason given. The wording has "
            "to change, which the overlay cannot do.",
            remedy=JUDGEMENT,
        ),
        Verdict(
            "duplicate", "Duplicate of another finding",
            "The same problem is already reported elsewhere in this review.",
            remedy=COMPONENT,
        ),
    ),
)

SEVERITY = Aspect(
    key="severity",
    label="How much it matters",
    help="Is this at the right level on the ramp?",
    subject=SUBJECT_FINDING,
    verdicts=(
        Verdict(
            "right", "Right level", "The severity matches the consequence.",
            polarity=GOOD, remedy=TUNABLE, knob="confirmations", delta=1,
        ),
        Verdict(
            "overstated", "Overstated", "Real, but less serious than reported.",
            remedy=TUNABLE, knob="severity_shift", delta=-1,
        ),
        Verdict(
            "understated", "Understated", "More serious than reported.",
            remedy=TUNABLE, knob="severity_shift", delta=1,
        ),
        Verdict(
            "not_a_finding", "Not worth reporting at all",
            "Correct, but noise — it should not appear in the register.",
            remedy=TUNABLE, knob="enabled", delta=False,
        ),
    ),
)

CITATION = Aspect(
    key="citation",
    label="The code reference",
    help="Does the cited section say what the finding claims?",
    subject=SUBJECT_FINDING,
    verdicts=(
        Verdict(
            "right", "Correct section", "The citation supports the finding.",
            polarity=GOOD, remedy=TUNABLE, knob="confirmations", delta=1,
        ),
        # Every defect below is JUDGEMENT and none carries a knob. This is
        # deliberate and is not an oversight: the code corpus is the moat
        # (ARCHITECTURE.md §4) and a citation is a claim about the law. No
        # volume of user agreement may edit one without a person deciding.
        Verdict(
            "wrong_section", "Wrong section",
            "A different section governs this. Say which in the comment.",
            remedy=JUDGEMENT,
        ),
        Verdict(
            "wrong_edition", "Wrong code edition",
            "The section is quoted from an edition this set was not drawn to.",
            remedy=JUDGEMENT,
        ),
        Verdict(
            "exception_missed", "An exception applies",
            "The section is right but an exception relieves it here.",
            remedy=JUDGEMENT,
        ),
        Verdict(
            "no_citation", "No section cited",
            "The finding asserts a requirement without naming where it comes from.",
            remedy=JUDGEMENT,
        ),
    ),
)

EVIDENCE = Aspect(
    key="evidence",
    label="What it read off the drawing",
    help="Are the values the finding quotes actually what the sheet says?",
    subject=SUBJECT_FINDING,
    verdicts=(
        Verdict(
            "right", "Read correctly", "The quoted values match the sheet.",
            polarity=GOOD, remedy=TUNABLE, knob="confirmations", delta=1,
        ),
        # Extraction is upstream of every knob: an overlay applied to a finished
        # findings list cannot re-read a table. All of these are engine work.
        Verdict(
            "misread_value", "Misread a value",
            "A number or label was picked up wrong.",
            remedy=COMPONENT,
        ),
        Verdict(
            "misread_table", "Misread a schedule or code block",
            "Rows, columns or headers were associated wrongly.",
            remedy=COMPONENT,
        ),
        Verdict(
            "wrong_scale", "Measured at the wrong scale",
            "A measured distance is off because the drawing scale was resolved wrongly.",
            remedy=COMPONENT,
        ),
        Verdict(
            "superseded", "Read from a superseded revision",
            "The value was taken from a sheet the set later revises.",
            remedy=COMPONENT,
        ),
    ),
)

LOCATION = Aspect(
    key="location",
    label="Where it landed",
    help="Is the marker on the right sheet, in the right place?",
    subject=SUBJECT_FINDING,
    verdicts=(
        Verdict(
            "right", "Right place", "The marker points at what the finding is about.",
            polarity=GOOD, remedy=TUNABLE, knob="confirmations", delta=1,
        ),
        Verdict(
            "wrong_anchor", "Wrong spot on the sheet",
            "Right sheet, but the marker is not on the thing being discussed.",
            remedy=COMPONENT,
        ),
        Verdict(
            "wrong_sheet", "Wrong sheet",
            "This belongs on a different sheet of the set.",
            remedy=COMPONENT,
        ),
    ),
)

CLARITY = Aspect(
    key="clarity",
    label="Whether it can be acted on",
    help="Could the design professional fix the set from this wording alone?",
    subject=SUBJECT_FINDING,
    verdicts=(
        Verdict(
            "clear", "Clear and actionable",
            "It says what is wrong and what to do about it.",
            polarity=GOOD, remedy=TUNABLE, knob="confirmations", delta=1,
        ),
        Verdict(
            "ambiguous", "Ambiguous", "It is not clear what is being claimed.",
            remedy=JUDGEMENT,
        ),
        Verdict(
            "no_action", "Says what is wrong but not what to do",
            "The correction is left to be inferred.",
            remedy=JUDGEMENT,
        ),
        Verdict(
            "too_technical", "Unreadable for the audience",
            "Correct, but pitched past whoever has to act on it.",
            remedy=JUDGEMENT,
        ),
    ),
)

# ── coverage: what the review missed ──────────────────────────────────────
# Anchored to a markup rather than to a finding, because there is no finding to
# anchor it to. Every verdict here is COMPONENT: the overlay may re-rank,
# re-level and suppress, and it may never invent a finding out of nothing.
# Anything a person had to notice for us is, by definition, a rule that does
# not exist yet or an input that was not extracted.
GAP = Aspect(
    key="gap",
    label="What was missed",
    help="What should this review have caught here and did not?",
    subject=SUBJECT_COVERAGE,
    required=True,
    verdicts=(
        Verdict(
            "missed_violation", "A code violation went unreported",
            "The set breaks a requirement here and nothing was raised.",
            remedy=COMPONENT,
        ),
        Verdict(
            "missed_check", "A check that should exist does not",
            "Nothing in the corpus covers this requirement at all.",
            remedy=COMPONENT,
        ),
        Verdict(
            "avoidable_abstention", "It abstained, but the data is right here",
            "The reviewer said it could not tell, and the sheet plainly states it.",
            remedy=COMPONENT,
        ),
        Verdict(
            "sheet_not_reviewed", "This sheet was not reviewed",
            "The sheet produced no findings and no abstentions.",
            remedy=COMPONENT,
        ),
    ),
)

ASPECTS: Tuple[Aspect, ...] = (
    CONCLUSION, SEVERITY, CITATION, EVIDENCE, LOCATION, CLARITY, GAP,
)

_BY_KEY: Dict[str, Aspect] = {a.key: a for a in ASPECTS}


# ── markup ────────────────────────────────────────────────────────────────
#: The shapes a person may draw on a sheet. Deliberately few: each one has to
#: mean something distinct to whoever reads the feedback afterwards, and a
#: palette of twelve tools produces twelve ways to say the same thing.
MARKUP_KINDS: Tuple[Tuple[str, str, str], ...] = (
    ("highlight", "Highlight", "A translucent wash over text or a table row."),
    ("box", "Box", "A rectangle around a region of the drawing."),
    ("arrow", "Arrow", "Points at one thing from somewhere with room to write."),
    ("strikeout", "Strike out", "A line through something that should not be there."),
    ("freehand", "Freehand", "A drawn line, for anything the other four cannot frame."),
    ("note", "Note", "A pin with no geometry, for a comment about the sheet itself."),
)


# ── serialisation ─────────────────────────────────────────────────────────
def aspects_for(subject: str) -> List[Aspect]:
    return [a for a in ASPECTS if a.subject == subject]


def to_dicts() -> List[Dict[str, Any]]:
    """The whole taxonomy, in the order the form should show it."""
    return [a.to_dict() for a in ASPECTS]


def markup_kinds() -> List[Dict[str, str]]:
    return [{"key": k, "label": l, "help": h} for k, l, h in MARKUP_KINDS]


def lookup(aspect: str, verdict: str) -> Optional[Verdict]:
    found = _BY_KEY.get(aspect)
    return found.verdict(verdict) if found else None


def validate(subject: str, answers: Dict[str, str]) -> List[str]:
    """Check submitted answers against the taxonomy.

    Returns prose problems, empty when the submission is sound.  Nothing is
    coerced and nothing is dropped: an aspect this build does not know is a 400
    naming it, because silently discarding an answer would record feedback the
    person did not give.
    """
    problems: List[str] = []

    if subject not in SUBJECTS:
        return [f"{subject!r} is not a feedback subject."]

    allowed = {a.key: a for a in aspects_for(subject)}

    for key, value in answers.items():
        aspect = allowed.get(key)
        if aspect is None:
            known = ", ".join(sorted(allowed))
            problems.append(f"{key!r} is not an aspect of {subject} feedback. Known: {known}.")
            continue
        if not isinstance(value, str) or aspect.verdict(value) is None:
            choices = ", ".join(v.key for v in aspect.verdicts)
            problems.append(f"{value!r} is not a verdict for {key!r}. Choices: {choices}.")

    for aspect in allowed.values():
        if aspect.required and aspect.key not in answers:
            problems.append(f"{aspect.key!r} must be answered.")

    return problems
