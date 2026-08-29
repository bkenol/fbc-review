"""The feedback-evaluation model.

Separate from the reviewer, and deliberately a different kind of thing.  The
reviewer decides whether a building complies; this decides **where a complaint
about the reviewer has to be fixed**.  It runs after a review has finished, on
the feedback path, and nothing in it is reachable from `run_review`.

## The three answers

| Disposition | Meaning | Who acts |
| --- | --- | --- |
| `auto_tunable` | Every defect maps to a lever `webapp.calibration` already has | proposal queued, owner approves in one click |
| `needs_component` | The fix is upstream of every lever — extraction, a new rule, the renderer | owner, as engineering work |
| `escalate` | Judgement: a code citation, contested evidence, or prose nobody can route | owner, by reading it |

A fourth outcome, `confirmation`, exists for feedback that reports no defect at
all.  It is not a fallback for "we could not tell" — that is `escalate`.  It is
the case where somebody read a finding and said it was right, which is real
evidence and is recorded as such: a rule fifty people have corroborated should
not be re-levelled because the fifty-first disagreed once.

## Why this is decidable rather than a matter of opinion

The split is not a judgement call the triage makes.  It falls directly out of
what the overlay can express, which is a closed list
(`webapp.calibration.KNOBS`).  Every verdict in `webapp.feedback_schema`
declares its remedy at the point the question is written, so triage is a fold
over the answers rather than an inference from them.  Add a lever to the
overlay and some verdicts become tunable; add a verdict whose fix is code and
it is `needs_component` from the moment it exists.

## The two conservative rules

1. **Citations never auto-tune.**  Any defect touching the cited section, the
   code edition or an exception goes to a person, whatever else the submission
   says and however many people agree.  The code corpus is the moat
   (ARCHITECTURE.md §4) and a wrong citation is a liability, not a knob.
2. **The assist may only escalate.**  `webapp.assist` reads the free-text
   comment, and its opinion can raise a disposition up the ladder and never
   lower it.  Comments are untrusted browser input; the worst a hostile one can
   achieve is to have itself read by a human.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from webapp import assist, feedback_schema
from webapp.calibration import (CONFIRMATION_KNOB, KNOBS, MAX_SHIFT,
                                CalibrationProfile, ProfileChange)

log = logging.getLogger("fbc.triage")

# ── the ladder, least to most human involvement ───────────────────────────
CONFIRMATION = "confirmation"
AUTO_TUNABLE = "auto_tunable"
NEEDS_COMPONENT = "needs_component"
ESCALATE = "escalate"

DISPOSITIONS = (CONFIRMATION, AUTO_TUNABLE, NEEDS_COMPONENT, ESCALATE)

#: Position on the ladder. Used only to take maxima — nothing ever moves down.
_RANK = {d: i for i, d in enumerate(DISPOSITIONS)}

#: Confirmations on a rule past which one dissenting voice stops being enough
#: to propose a change on its own. The feedback is still recorded and still
#: reaches the owner; it just arrives as a judgement call rather than as a
#: one-click knob.
CONTESTED_AT = 3

DISPOSITION_LABELS = {
    CONFIRMATION: "Confirmed — no defect reported",
    AUTO_TUNABLE: "Can be addressed by calibration",
    NEEDS_COMPONENT: "Needs a new component",
    ESCALATE: "Needs your judgement",
}


@dataclass
class TriageVerdict:
    """Where a piece of feedback has to be fixed, and why."""

    disposition: str
    rationale: str
    #: The calibration diff this feedback argues for. Empty unless the
    #: disposition is `auto_tunable` or `confirmation`.
    changes: List[ProfileChange] = field(default_factory=list)
    #: Machine-readable reasons, for filtering the queue and for tests.
    signals: List[str] = field(default_factory=list)
    #: What `webapp.assist` made of the comment, when there was one and it was
    #: configured. Advisory; recorded so the owner can see what it said.
    assist: Optional[Dict[str, Any]] = None

    @property
    def label(self) -> str:
        return DISPOSITION_LABELS.get(self.disposition, self.disposition)

    @property
    def actionable(self) -> bool:
        """True when this needs the owner to do something."""
        return self.disposition != CONFIRMATION

    def to_dict(self) -> Dict[str, Any]:
        return {
            "disposition": self.disposition,
            "label": self.label,
            "rationale": self.rationale,
            "changes": [c.to_dict() for c in self.changes],
            "signals": list(self.signals),
            "assist": self.assist,
        }


def _raise_to(current: str, candidate: str) -> str:
    """Move up the ladder, never down."""
    return candidate if _RANK[candidate] > _RANK[current] else current


def triage(
    *,
    subject: str,
    answers: Dict[str, str],
    rule_id: str = "",
    comment: str = "",
    occupancy_group: str = "",
    finding: Optional[Dict[str, Any]] = None,
    profile: Optional[CalibrationProfile] = None,
    use_assist: bool = True,
) -> TriageVerdict:
    """Decide where this feedback has to be fixed.

    Deterministic given its inputs, except for the optional comment assist —
    which can only raise the disposition, so an unavailable assist never turns
    an escalation into an auto-tune.
    """
    signals: List[str] = []
    changes: List[ProfileChange] = []
    notes: List[str] = []

    resolved = [
        (aspect_key, feedback_schema.lookup(aspect_key, verdict_key))
        for aspect_key, verdict_key in sorted(answers.items())
    ]
    known = [(a, v) for a, v in resolved if v is not None]
    defects = [(a, v) for a, v in known if v.polarity == feedback_schema.DEFECT]
    praise = [(a, v) for a, v in known if v.polarity == feedback_schema.GOOD]

    # ── no defect reported ────────────────────────────────────────────────
    if not defects:
        disposition = CONFIRMATION
        if praise and rule_id:
            changes.append(ProfileChange(
                rule_id, CONFIRMATION_KNOB, len(praise),
                reason="confirmed by a reviewer",
            ))
            signals.append("confirmed")
            notes.append(
                f"{len(praise)} aspect{'s' if len(praise) != 1 else ''} confirmed as correct"
            )
        else:
            signals.append("empty")
            notes.append("nothing was reported")

    # ── defects, routed by the remedy each verdict declares ───────────────
    else:
        remedies = {v.remedy for _, v in defects}
        # Name every defect, not only the one that decided the route: the owner
        # reads this line in the queue, and a submission that reported three
        # things must not arrive looking like it reported one.
        notes.append(
            "reported — " + ", ".join(f"{a}: {v.label.lower()}" for a, v in defects)
        )

        if feedback_schema.JUDGEMENT in remedies:
            disposition = ESCALATE
            judged = [a for a, v in defects if v.remedy == feedback_schema.JUDGEMENT]
            signals.append("judgement")
            if "citation" in judged:
                signals.append("citation")
                notes.append(
                    "the cited code section is disputed, which is never applied "
                    "automatically"
                )
            other = [a for a in judged if a != "citation"]
            if other:
                notes.append(f"{', '.join(other)} needs a person to decide")

        elif feedback_schema.COMPONENT in remedies:
            disposition = NEEDS_COMPONENT
            broken = [a for a, v in defects if v.remedy == feedback_schema.COMPONENT]
            signals.append("component")
            notes.append(
                f"{', '.join(broken)} sits upstream of every calibration lever — "
                "no knob reaches it"
            )

        else:
            disposition = AUTO_TUNABLE
            signals.append("tunable")
            for aspect_key, verdict in defects:
                if not verdict.knob or verdict.knob not in KNOBS:
                    # A tunable verdict with no lever is a contradiction in the
                    # taxonomy rather than a routing decision. Refuse to guess.
                    disposition = ESCALATE
                    signals.append("no_lever")
                    notes.append(
                        f"{aspect_key} is marked tunable but names no lever — "
                        "this is a taxonomy bug, not a calibration"
                    )
                    continue

                raw = _raw_target(verdict, rule_id, profile, occupancy_group)
                value = KNOBS[verdict.knob].clamp(raw)
                if raw != value:
                    # The clamp bit, so the proposal is not what was asked for.
                    # Queue the clamped value and say a person should look:
                    # silently applying something weaker than requested would
                    # read as agreement.
                    disposition = _raise_to(disposition, ESCALATE)
                    signals.append("out_of_bounds")
                    notes.append(
                        f"{aspect_key} argues for {verdict.knob}={raw}, past the "
                        f"limit this lever allows — clamped to {value}"
                    )

                if verdict.knob == "scope_occupancy" and not occupancy_group:
                    # "It does not apply to this project" needs to know what
                    # this project is. Without a declared occupancy group there
                    # is nothing to scope out, and inventing one would silence
                    # the rule far more widely than anybody asked.
                    disposition = _raise_to(disposition, ESCALATE)
                    signals.append("no_occupancy")
                    notes.append(
                        "the review declared no occupancy group, so there is "
                        "nothing to scope this rule out of"
                    )
                    continue

                changes.append(ProfileChange(
                    rule_id or (finding or {}).get("rule_id", ""),
                    verdict.knob,
                    value,
                    reason=f"{aspect_key}: {verdict.label.lower()}",
                ))

    # ── conservative overrides ────────────────────────────────────────────
    if disposition == AUTO_TUNABLE and rule_id and profile is not None:
        current = profile.for_rule(rule_id)

        if current.confirmations >= CONTESTED_AT:
            disposition = _raise_to(disposition, ESCALATE)
            signals.append("contested")
            notes.append(
                f"{current.confirmations} reviewers have confirmed this rule; one "
                "dissent should not move it on its own"
            )

        for change in changes:
            knob = KNOBS.get(change.knob)
            if knob is None:
                continue
            settled = getattr(current, change.knob)
            if settled != knob.default and settled != change.value:
                # Somebody already approved a value for this lever. Moving it
                # again is a decision about that decision, not a fresh one.
                disposition = _raise_to(disposition, ESCALATE)
                signals.append("recalibrates")
                notes.append(
                    f"{change.knob} is already calibrated to {settled!r} on this "
                    f"rule; this asks for {change.value!r}"
                )

    # ── the free-text comment ─────────────────────────────────────────────
    opinion = None
    if comment.strip():
        signals.append("has_comment")
        if use_assist:
            opinion = assist.read_comment(comment, finding=finding, answers=answers)

        if opinion is None:
            # Prose nobody has read cannot be routed. That is an escalation and
            # not a shrug: the alternative is quietly discarding the only part
            # of the submission that was not a dropdown.
            disposition = _raise_to(disposition, ESCALATE)
            signals.append("unread_comment")
            notes.append("a written comment came with this and needs reading")
        else:
            notes.append(f"comment: {opinion.summary}")
            if opinion.names_a_code_section:
                disposition = _raise_to(disposition, ESCALATE)
                signals.append("comment_cites_code")
                notes.append("the comment argues about a code section")
            suggested = feedback_schema.lookup(opinion.aspect, opinion.verdict)
            if suggested is not None and suggested.polarity == feedback_schema.DEFECT:
                mapped = {
                    feedback_schema.TUNABLE: AUTO_TUNABLE,
                    feedback_schema.COMPONENT: NEEDS_COMPONENT,
                    feedback_schema.JUDGEMENT: ESCALATE,
                }.get(suggested.remedy, ESCALATE)
                if _RANK[mapped] > _RANK[disposition]:
                    signals.append("assist_raised")
                    notes.append(
                        f"the comment reads as {opinion.aspect}: "
                        f"{suggested.label.lower()}, which the answers did not say"
                    )
                # Only ever upward. A comment cannot argue a submission down
                # into a knob the structured answers did not justify.
                disposition = _raise_to(disposition, mapped)
            if opinion.confidence == "low":
                disposition = _raise_to(disposition, ESCALATE)
                signals.append("assist_unsure")

    # A proposal is only meaningful for the two dispositions that carry one.
    if disposition in (NEEDS_COMPONENT, ESCALATE):
        changes = [c for c in changes if c.knob == CONFIRMATION_KNOB]

    return TriageVerdict(
        disposition=disposition,
        rationale="; ".join(notes) or "nothing to report",
        changes=changes,
        signals=sorted(set(signals)),
        assist=(opinion.model_dump() if opinion is not None else None),
    )


def _raw_target(verdict, rule_id: str, profile: Optional[CalibrationProfile],
                occupancy_group: str = "") -> Any:
    """The value a verdict argues the lever should take, **before clamping**.

    Deltas are resolved against the profile in force so a proposal is an
    absolute setting, never an increment. A queued proposal that meant "one step
    quieter than whatever is current" would mean something different by the time
    it was approved, which is exactly the kind of drift that makes an audit
    trail worthless.

    Returned unclamped on purpose: the caller compares this with the clamped
    value to notice that somebody asked for more than the lever allows, which is
    a thing to tell a person about rather than to round off silently.
    """
    current = profile.for_rule(rule_id) if (profile and rule_id) else None

    if verdict.knob == "severity_shift":
        base = current.severity_shift if current else 0
        return base + int(verdict.delta or 0)

    if verdict.knob == "scope_occupancy":
        # The taxonomy only says "exclude the group this project is"; which
        # group that is comes from the review.
        groups = set(current.scope_occupancy) if current else set()
        if occupancy_group:
            groups.add(occupancy_group)
        return sorted(groups)

    return verdict.delta
