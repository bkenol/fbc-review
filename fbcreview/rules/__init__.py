"""The rule engine.

A rule is a pure function of ProjectFacts -> list[Finding].  No model, no
network, no I/O.  Every rule declares the sections it enforces and must either
produce a Finding (OPEN, PASS or CONFLICT) or record an Abstention explaining
why it could not run.  Silence is never allowed: "not checked" must be
distinguishable from "checked and passed", which is the whole point of the
verified register.

Rules read building facts through `fbcreview.reconcile.building()`, which hands
back the declaration and the drawings already reconciled.  When those two
sources disagree the whole corpus is run **twice** — see `run_all` — and each
finding records which reading produced it.
"""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Any, Callable, Dict, List, Optional
from ..confidence import Abstention
from ..facts import ProjectFacts

SEVERITIES = ("CRITICAL", "HIGH", "MEDIUM", "LOW", "VERIFIED", "MEASURED")

#: `CONFLICT` is a status, not a severity: it says where the finding came from
#: (the two sources disagree), while severity still says how much it matters.
STATUSES = ("OPEN", "PASS", "CONFLICT")

#: Statuses that represent something the applicant has to act on.
ACTIONABLE = ("OPEN", "CONFLICT")


@dataclass
class Finding:
    fid: str
    rule_id: str
    status: str                  # "OPEN" | "PASS" | "CONFLICT"
    severity: str
    discipline: str
    page: int
    sheet: str
    anchor: str                  # text to box on the drawing
    title: str
    checked: str
    result: str
    code: str
    action: str = ""
    body: str = ""
    hit: int = 0
    #: Which reading of the set produced this. "both" is the common case — most
    #: rules never touch a field the two sources disagree about.
    scenario: str = "both"
    #: What the finding rests on: "drawings", "declaration" or "both". A finding
    #: resting on user input must say so; the markup may never attribute to the
    #: drawings something the drawings do not state.
    basis: str = "drawings"

    def to_dict(self) -> dict:
        return asdict(self)

    def same_content(self, other: "Finding") -> bool:
        """Equal in everything except which scenario produced it."""
        a, b = asdict(self), asdict(other)
        a.pop("scenario"), b.pop("scenario")
        return a == b


@dataclass
class RuleResult:
    findings: List[Finding] = field(default_factory=list)
    abstentions: List[Abstention] = field(default_factory=list)
    #: The `ReconciledFacts` this run was evaluated against, for the register
    #: and the audit trail. `None` when the engine was driven without one.
    reconciled: Optional[Any] = None


_REGISTRY: Dict[str, Callable[[ProjectFacts, RuleResult], None]] = {}


def rule(rule_id: str):
    def deco(fn):
        _REGISTRY[rule_id] = fn
        fn.rule_id = rule_id
        return fn
    return deco


def _run_once(facts: ProjectFacts) -> RuleResult:
    res = RuleResult()
    for rid, fn in sorted(_REGISTRY.items()):
        try:
            fn(facts, res)
        except Exception as exc:                       # a broken rule must not
            res.abstentions.append(Abstention(         # take the run down
                rid, "rule raised", detail=f"{type(exc).__name__}: {exc}"))
    return res


def _merge_scenarios(drawn: List[Finding], declared: List[Finding]) -> List[Finding]:
    """Fold the two evaluations into one register.

    Deduplicated on `(rule_id, sheet, anchor, fid)`.  The `fid` is part of the
    key because several rules legitimately emit more than one finding under a
    single anchor — every under-width door in a schedule shares the sheet and
    the anchor — and collapsing those would silently drop findings.  Where a key
    repeats within one run, the two runs' entries are paired in order, which is
    deterministic because the corpus is.

    A finding that came out identical either way is reported **once**, as
    `both`.  Get this wrong and every register doubles in length.
    """
    from collections import defaultdict

    def key(f: Finding):
        return (f.rule_id, f.sheet, f.anchor, f.fid)

    left: Dict[Any, List[Finding]] = defaultdict(list)
    right: Dict[Any, List[Finding]] = defaultdict(list)
    for f in drawn:
        left[key(f)].append(f)
    for f in declared:
        right[key(f)].append(f)

    out: List[Finding] = []
    for k in list(left) + [k for k in right if k not in left]:
        a, b = left.get(k, []), right.get(k, [])
        for i in range(max(len(a), len(b))):
            fa = a[i] if i < len(a) else None
            fb = b[i] if i < len(b) else None
            if fa and fb:
                if fa.same_content(fb):
                    fa.scenario = "both"
                    out.append(fa)
                else:
                    fa.scenario = "as_drawn"
                    fb.scenario = "as_declared"
                    out += [fa, fb]
            elif fa:
                fa.scenario = "as_drawn"
                out.append(fa)
            elif fb:
                fb.scenario = "as_declared"
                out.append(fb)
    return out


def _merge_abstentions(*runs: List[Abstention]) -> List[Abstention]:
    seen, out = set(), []
    for run in runs:
        for a in run:
            k = (a.rule_id, a.reason, a.page, a.detail)
            if k in seen:
                continue
            seen.add(k)
            out.append(a)
    return out


def run_all(facts: ProjectFacts, options=None, declaration=None) -> RuleResult:
    """Run every registered rule against the reconciled set.

    `options` is a ReviewOptions; when given it is published on facts.meta so
    rules can read the review context, and it filters the finished list by the
    severity floor the user chose.  `declaration` is a ProjectDeclaration; when
    it disagrees with the drawings the corpus is evaluated **twice** — once
    against `as_drawn()`, once against `as_declared()`.

    **Exactly two scenarios. Never a combinatorial product.**  Five conflicting
    fields produce two runs, not thirty-two: one in which every declared value
    wins and one in which every drawn value wins.  Anyone tempted to "improve"
    this into a matrix should note that the matrix answers a question nobody
    asked — a permit set is submitted as a whole, and the two things worth
    comparing are the set as drawn and the set as described.
    """
    from ..reconcile import reconcile

    if options is not None:
        facts.meta["options"] = options

    rf = reconcile(facts, declaration)
    drawn_view = rf.as_drawn()

    if not rf.has_conflict():
        res = _run_once(drawn_view)
    else:
        a = _run_once(drawn_view)
        b = _run_once(rf.as_declared())
        res = RuleResult(findings=_merge_scenarios(a.findings, b.findings),
                         abstentions=_merge_abstentions(a.abstentions, b.abstentions))

    res.reconciled = rf
    if options is not None:
        res.findings = [f for f in res.findings if options.severity_allowed(f.severity)]
    order = {s: i for i, s in enumerate(SEVERITIES)}
    res.findings.sort(key=lambda f: (order.get(f.severity, 9), f.page, f.fid, f.scenario))
    return res


def registered() -> List[str]:
    return sorted(_REGISTRY)
