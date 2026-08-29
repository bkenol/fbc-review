"""The calibration overlay — what feedback is allowed to change.

There is no weight file in this product and there is not about to be one.  The
review is a pure function of the drawings and the code corpus (ARCHITECTURE.md
§2, `CLAUDE.md`), and that is the claim being sold.  So "training" here does not
mean gradient descent on an opaque parameter vector.  It means a **versioned,
diffable, reversible set of levers applied to a finished findings list**, every
one of which a person can read and argue with.

That is a weaker mechanism than a learned model and the weakness is the point:
a plan review is a liability-bearing document, and "the model decided" is not a
defence.  Every adjustment this module makes is recorded on the review it
changed, with the profile version that caused it.

## What the overlay can do

Re-level, re-order, scope and suppress.  That is the whole list, and it is
bounded by construction:

| Lever | Effect |
| --- | --- |
| `enabled` | stop reporting this rule at all |
| `severity_shift` | move it up or down the actionable ramp, within ±2 |
| `severity_cap` / `severity_floor` | clamp it, whatever the shift says |
| `scope_occupancy` | stop reporting it for named occupancy groups |
| `scope_basis` | stop reporting findings that rest only on declared input |
| `drop_verified` | stop printing this rule's passes in the verified register |
| `weight` | order it earlier or later among findings of equal severity |

## What the overlay categorically cannot do

**It cannot invent a finding.**  It runs downstream of the corpus, over a list
that is already decided, so a check that does not exist cannot be conjured by
turning a knob.  It cannot re-read a table, re-resolve a scale, move a marker,
change a citation or rewrite a sentence either — all of those happen upstream
of it or inside the code corpus.

This is not a limitation to be worked around later.  It is the boundary that
makes the triage in `webapp.triage` decidable: feedback that maps onto a lever
above is configuration, and feedback that does not is engineering.  The moment
the overlay gains the power to fabricate a finding, that distinction collapses
and so does the audit trail.

## Suppression is recorded as an abstention

`CLAUDE.md` and `README.md` both put it first: "not checked" must never be
indistinguishable from "checked and passed".  A rule switched off by
calibration has not passed, so `apply` emits an `Abstention` naming the profile
that silenced it.  A reader of the register can always tell the difference
between a clean sheet and a quiet one.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from fbcreview.confidence import Abstention
from fbcreview.rules import Finding

#: The actionable ramp, most severe first. `VERIFIED` and `MEASURED` are
#: registers rather than levels and are deliberately absent: a shift must never
#: be able to promote a passing check into a violation, which is precisely the
#: kind of "learning" that would make this product dangerous.
RAMP: Tuple[str, ...] = ("CRITICAL", "HIGH", "MEDIUM", "LOW")

#: Not a lever. Counts of users who said a rule got something right, kept so a
#: single dissenting voice cannot move a rule that fifty people corroborated.
#: Read by `webapp.triage`; never applied to a finding.
CONFIRMATION_KNOB = "confirmations"

MAX_SHIFT = 2


@dataclass(frozen=True)
class Knob:
    """One lever, and the bounds nothing may move it past.

    Bounds are enforced here rather than at the call site so that a proposal
    built from feedback, a value typed into the admin console and a profile
    loaded out of Firestore are all clamped by the same code.
    """

    name: str
    kind: str
    default: Any
    help: str
    bounds: Optional[Tuple[float, float]] = None
    choices: Optional[Tuple[str, ...]] = None

    def clamp(self, value: Any) -> Any:
        if value is None:
            return self.default
        if self.kind == "bool":
            return bool(value)
        if self.kind == "int":
            lo, hi = self.bounds or (-MAX_SHIFT, MAX_SHIFT)
            try:
                return int(max(lo, min(hi, int(value))))
            except (TypeError, ValueError):
                return self.default
        if self.kind == "float":
            lo, hi = self.bounds or (0.0, 1.0)
            try:
                return float(max(lo, min(hi, float(value))))
            except (TypeError, ValueError):
                return self.default
        if self.kind == "severity":
            return value if value in RAMP else self.default
        if self.kind == "strings":
            if not isinstance(value, (list, tuple, set)):
                return self.default
            return sorted({str(v) for v in value if str(v)})
        return value


KNOBS: Dict[str, Knob] = {
    "enabled": Knob(
        "enabled", "bool", True,
        "Report this rule's findings at all. Off emits an abstention naming the profile.",
    ),
    "severity_shift": Knob(
        "severity_shift", "int", 0,
        "Steps to move findings along the actionable ramp. Negative is less severe.",
        bounds=(-MAX_SHIFT, MAX_SHIFT),
    ),
    "severity_cap": Knob(
        "severity_cap", "severity", None,
        "Never report this rule above this level, whatever the shift says.",
        choices=RAMP,
    ),
    "severity_floor": Knob(
        "severity_floor", "severity", None,
        "Never report this rule below this level.",
        choices=RAMP,
    ),
    "scope_occupancy": Knob(
        "scope_occupancy", "strings", [],
        "Occupancy groups this rule does not govern. Findings are suppressed when "
        "the reviewed building is one of them.",
    ),
    "scope_basis": Knob(
        "scope_basis", "strings", [],
        "Bases to suppress — e.g. `declaration` drops findings that rest only on "
        "what the applicant asserted, with no drawing evidence.",
    ),
    "drop_verified": Knob(
        "drop_verified", "bool", False,
        "Keep this rule's passes out of the verified register. The check still runs.",
    ),
    "weight": Knob(
        "weight", "float", 1.0,
        "Ordering only, among findings of equal severity. Never changes a level.",
        bounds=(0.1, 5.0),
    ),
}


@dataclass
class RuleCalibration:
    """Every lever, for one rule."""

    rule_id: str
    enabled: bool = True
    severity_shift: int = 0
    severity_cap: Optional[str] = None
    severity_floor: Optional[str] = None
    scope_occupancy: List[str] = field(default_factory=list)
    scope_basis: List[str] = field(default_factory=list)
    drop_verified: bool = False
    weight: float = 1.0
    #: How many users have confirmed this rule got something right. Evidence for
    #: the triage, never applied to a finding.
    confirmations: int = 0
    #: Free prose recording why this rule is calibrated the way it is. Written
    #: by the promotion, not by a user.
    note: str = ""

    def is_default(self) -> bool:
        """True when this entry changes nothing, so it can be dropped."""
        blank = RuleCalibration(self.rule_id)
        mine = dataclasses.asdict(self)
        theirs = dataclasses.asdict(blank)
        for ignored in ("confirmations", "note"):
            mine.pop(ignored), theirs.pop(ignored)
        return mine == theirs

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RuleCalibration":
        rule_id = str(data.get("rule_id") or "")
        out = cls(rule_id=rule_id)
        for name, knob in KNOBS.items():
            setattr(out, name, knob.clamp(data.get(name)))
        try:
            out.confirmations = max(0, int(data.get("confirmations") or 0))
        except (TypeError, ValueError):
            out.confirmations = 0
        out.note = str(data.get("note") or "")
        return out


#: Profile scopes. `global` is what production reviews against; `candidate` is
#: one user's training sandbox, which is the only place feedback lands
#: immediately.
GLOBAL, CANDIDATE = "global", "candidate"

#: The identifier of the profile production uses. A promotion writes a new
#: version and repoints this.
ACTIVE_ID = "active"


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


@dataclass
class CalibrationProfile:
    """A named, versioned set of rule calibrations.

    Versions are never edited in place. Promotion writes a new version and moves
    the active pointer, so any review can name the exact profile version it was
    produced under and that version can still be read back afterwards.
    """

    profile_id: str = ACTIVE_ID
    version: int = 0
    scope: str = GLOBAL
    owner_uid: str = ""
    label: str = "Uncalibrated"
    edition: str = "fbc2023"
    rules: Dict[str, RuleCalibration] = field(default_factory=dict)
    note: str = ""
    derived_from: Optional[int] = None
    created_at: Optional[dt.datetime] = None
    created_by: str = ""

    # ── lookup ────────────────────────────────────────────────────────────
    def for_rule(self, rule_id: str) -> RuleCalibration:
        return self.rules.get(rule_id) or RuleCalibration(rule_id)

    def touched(self) -> List[str]:
        """Rules this profile actually changes, in id order."""
        return sorted(r for r, c in self.rules.items() if not c.is_default())

    def is_empty(self) -> bool:
        return not self.touched()

    # ── mutation, always producing a new version ──────────────────────────
    def with_changes(
        self,
        changes: Sequence["ProfileChange"],
        *,
        label: str = "",
        note: str = "",
        created_by: str = "",
        profile_id: Optional[str] = None,
        scope: Optional[str] = None,
        owner_uid: Optional[str] = None,
    ) -> "CalibrationProfile":
        """A copy with `changes` applied, one version on.

        Never mutates the receiver. A profile that has been used to produce a
        review has to stay readable exactly as it was, or the review's audit
        trail points at something that no longer exists.
        """
        rules = {rid: RuleCalibration.from_dict(c.to_dict()) for rid, c in self.rules.items()}

        for change in changes:
            current = rules.get(change.rule_id) or RuleCalibration(change.rule_id)
            knob = KNOBS.get(change.knob)
            if knob is None:
                if change.knob == CONFIRMATION_KNOB:
                    current.confirmations = max(
                        0, current.confirmations + int(change.value or 0)
                    )
                    rules[change.rule_id] = current
                continue
            setattr(current, change.knob, knob.clamp(change.value))
            if change.note:
                current.note = change.note
            rules[change.rule_id] = current

        return CalibrationProfile(
            profile_id=profile_id or self.profile_id,
            version=self.version + 1,
            scope=scope or self.scope,
            owner_uid=self.owner_uid if owner_uid is None else owner_uid,
            label=label or self.label,
            edition=self.edition,
            rules=rules,
            note=note,
            derived_from=self.version,
            created_at=utcnow(),
            created_by=created_by,
        )

    # ── serialisation ─────────────────────────────────────────────────────
    def to_dict(self) -> Dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "version": self.version,
            "scope": self.scope,
            "owner_uid": self.owner_uid,
            "label": self.label,
            "edition": self.edition,
            "rules": {r: c.to_dict() for r, c in sorted(self.rules.items())},
            "note": self.note,
            "derived_from": self.derived_from,
            "created_at": self.created_at,
            "created_by": self.created_by,
        }

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "CalibrationProfile":
        if not data:
            return cls()
        raw = data.get("rules") or {}
        rules = {
            str(rid): RuleCalibration.from_dict({**entry, "rule_id": rid})
            for rid, entry in raw.items()
            if isinstance(entry, dict)
        }
        return cls(
            profile_id=str(data.get("profile_id") or ACTIVE_ID),
            version=int(data.get("version") or 0),
            scope=str(data.get("scope") or GLOBAL),
            owner_uid=str(data.get("owner_uid") or ""),
            label=str(data.get("label") or "Uncalibrated"),
            edition=str(data.get("edition") or "fbc2023"),
            rules=rules,
            note=str(data.get("note") or ""),
            derived_from=data.get("derived_from"),
            created_at=data.get("created_at"),
            created_by=str(data.get("created_by") or ""),
        )


@dataclass
class ProfileChange:
    """One lever moved on one rule, with why."""

    rule_id: str
    knob: str
    value: Any
    reason: str = ""
    note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "knob": self.knob,
            "value": self.value,
            "reason": self.reason,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ProfileChange":
        return cls(
            rule_id=str(data.get("rule_id") or ""),
            knob=str(data.get("knob") or ""),
            value=data.get("value"),
            reason=str(data.get("reason") or ""),
            note=str(data.get("note") or ""),
        )


@dataclass
class Adjustment:
    """One finding, changed. Written onto the review it changed."""

    fid: str
    rule_id: str
    knob: str
    before: str
    after: str
    detail: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)


@dataclass
class CalibrationResult:
    findings: List[Finding]
    adjustments: List[Adjustment]
    abstentions: List[Abstention]
    profile_id: str
    profile_version: int
    profile_label: str

    def to_report(self) -> Dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "profile_version": self.profile_version,
            "profile_label": self.profile_label,
            "adjusted": len(self.adjustments),
            "suppressed": sum(1 for a in self.adjustments if a.knob in _SUPPRESSING),
            "adjustments": [a.to_dict() for a in self.adjustments],
        }


_SUPPRESSING = ("enabled", "scope_occupancy", "scope_basis", "drop_verified")


def shift_severity(severity: str, steps: int) -> str:
    """Move a severity along the actionable ramp, saturating at both ends.

    `VERIFIED` and `MEASURED` are returned untouched. They are not points on
    this ramp, and a shift that could turn a passing check into a CRITICAL
    would let accumulated opinion manufacture a violation — which is the one
    thing calibration must never be able to do.
    """
    if severity not in RAMP or not steps:
        return severity
    index = RAMP.index(severity)
    # Negative steps mean *less* severe, which is further down the list.
    return RAMP[max(0, min(len(RAMP) - 1, index - steps))]


def _clamp_between(severity: str, floor: Optional[str], cap: Optional[str]) -> str:
    if severity not in RAMP:
        return severity
    index = RAMP.index(severity)
    if cap in RAMP:
        index = max(index, RAMP.index(cap))
    if floor in RAMP:
        index = min(index, RAMP.index(floor))
    return RAMP[index]


def apply(
    findings: Iterable[Finding],
    profile: Optional[CalibrationProfile],
    *,
    occupancy_group: str = "",
) -> CalibrationResult:
    """Apply a profile to a finished findings list. Pure; no I/O.

    Returns the adjusted list, the audit trail of what moved, and an abstention
    for every rule the profile silenced.
    """
    findings = list(findings)
    if profile is None or profile.is_empty():
        return CalibrationResult(
            findings=findings,
            adjustments=[],
            abstentions=[],
            profile_id=(profile.profile_id if profile else ACTIVE_ID),
            profile_version=(profile.version if profile else 0),
            profile_label=(profile.label if profile else "Uncalibrated"),
        )

    kept: List[Tuple[float, Finding]] = []
    adjustments: List[Adjustment] = []
    silenced: Dict[str, str] = {}

    for finding in findings:
        cal = profile.for_rule(finding.rule_id)

        # ── suppression, in the order a reader would ask about it ─────────
        if not cal.enabled:
            silenced[finding.rule_id] = "switched off in this calibration profile"
            adjustments.append(Adjustment(
                finding.fid, finding.rule_id, "enabled", finding.severity, "suppressed",
                cal.note or "rule disabled by calibration",
            ))
            continue

        if occupancy_group and occupancy_group in cal.scope_occupancy:
            silenced[finding.rule_id] = (
                f"scoped out of occupancy group {occupancy_group} by this profile"
            )
            adjustments.append(Adjustment(
                finding.fid, finding.rule_id, "scope_occupancy",
                finding.severity, "suppressed",
                f"does not govern occupancy group {occupancy_group}",
            ))
            continue

        if finding.basis in cal.scope_basis:
            adjustments.append(Adjustment(
                finding.fid, finding.rule_id, "scope_basis",
                finding.severity, "suppressed",
                f"rests on {finding.basis} and this profile suppresses that basis",
            ))
            continue

        if cal.drop_verified and finding.severity == "VERIFIED":
            adjustments.append(Adjustment(
                finding.fid, finding.rule_id, "drop_verified", "VERIFIED", "suppressed",
                "passes from this rule are kept out of the verified register",
            ))
            continue

        # ── re-levelling ──────────────────────────────────────────────────
        severity = shift_severity(finding.severity, cal.severity_shift)
        severity = _clamp_between(severity, cal.severity_floor, cal.severity_cap)

        if severity != finding.severity:
            adjustments.append(Adjustment(
                finding.fid, finding.rule_id, "severity_shift",
                finding.severity, severity, cal.note,
            ))
            finding = dataclasses.replace(finding, severity=severity)

        kept.append((cal.weight, finding))

    # Weight orders within a severity band and never across one: a heavily
    # weighted LOW must not outrank a CRITICAL, whatever anyone has said about
    # it. Ties fall back to the engine's own ordering so the result stays
    # deterministic.
    rank = {s: i for i, s in enumerate(("CRITICAL", "HIGH", "MEDIUM", "LOW",
                                        "MEASURED", "VERIFIED"))}
    kept.sort(key=lambda pair: (
        rank.get(pair[1].severity, 9), -pair[0], pair[1].page, pair[1].fid, pair[1].scenario,
    ))

    abstentions = [
        Abstention(rule_id, "suppressed by calibration",
                   detail=f"{reason} (profile {profile.profile_id} v{profile.version})")
        for rule_id, reason in sorted(silenced.items())
    ]

    return CalibrationResult(
        findings=[f for _, f in kept],
        adjustments=adjustments,
        abstentions=abstentions,
        profile_id=profile.profile_id,
        profile_version=profile.version,
        profile_label=profile.label,
    )


def diff(before: CalibrationProfile, after: CalibrationProfile) -> List[Dict[str, Any]]:
    """What changed between two profile versions, for the admin console."""
    rows: List[Dict[str, Any]] = []
    for rule_id in sorted(set(before.rules) | set(after.rules)):
        old, new = before.for_rule(rule_id), after.for_rule(rule_id)
        for name in KNOBS:
            was, now = getattr(old, name), getattr(new, name)
            if was != now:
                rows.append({
                    "rule_id": rule_id,
                    "knob": name,
                    "before": was,
                    "after": now,
                    "help": KNOBS[name].help,
                })
    return rows


def knob_catalogue() -> List[Dict[str, Any]]:
    """The levers, as data, so the admin console renders no hard-coded list."""
    # Deliberately without each lever's default. A polymorphic value on the
    # wire generates as an untyped blob in the TypeScript client, and the
    # console does not need it: every rule's concrete values come back on
    # `RuleCalibration`, where each knob has its own real type.
    return [
        {
            "name": k.name,
            "kind": k.kind,
            "help": k.help,
            "bounds": list(k.bounds) if k.bounds else None,
            "choices": list(k.choices) if k.choices else None,
        }
        for k in KNOBS.values()
    ]
