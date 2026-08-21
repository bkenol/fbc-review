"""The rule engine.

A rule is a pure function of ProjectFacts -> list[Finding].  No model, no
network, no I/O.  Every rule declares the sections it enforces and must either
produce a Finding (OPEN or PASS) or record an Abstention explaining why it could
not run.  Silence is never allowed: "not checked" must be distinguishable from
"checked and passed", which is the whole point of the verified register.
"""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Callable, Dict, List, Optional
from ..confidence import Abstention
from ..facts import ProjectFacts

SEVERITIES = ("CRITICAL", "HIGH", "MEDIUM", "LOW", "VERIFIED", "MEASURED")


@dataclass
class Finding:
    fid: str
    rule_id: str
    status: str                  # "OPEN" | "PASS"
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

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class RuleResult:
    findings: List[Finding] = field(default_factory=list)
    abstentions: List[Abstention] = field(default_factory=list)


_REGISTRY: Dict[str, Callable[[ProjectFacts, RuleResult], None]] = {}


def rule(rule_id: str):
    def deco(fn):
        _REGISTRY[rule_id] = fn
        fn.rule_id = rule_id
        return fn
    return deco


def run_all(facts: ProjectFacts, options=None) -> RuleResult:
    """Run every registered rule. `options` is a ReviewOptions; when given it is
    published on facts.meta so rules can read the occupancy context, and it
    filters the finished list by the severity floor the user chose."""
    if options is not None:
        facts.meta["options"] = options
    res = RuleResult()
    for rid, fn in sorted(_REGISTRY.items()):
        try:
            fn(facts, res)
        except Exception as exc:                       # a broken rule must not
            res.abstentions.append(Abstention(         # take the run down
                rid, "rule raised", detail=f"{type(exc).__name__}: {exc}"))
    if options is not None:
        res.findings = [f for f in res.findings if options.severity_allowed(f.severity)]
    order = {s: i for i, s in enumerate(SEVERITIES)}
    res.findings.sort(key=lambda f: (order.get(f.severity, 9), f.page, f.fid))
    return res


def registered() -> List[str]:
    return sorted(_REGISTRY)
