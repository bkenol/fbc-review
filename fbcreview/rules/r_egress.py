"""Chapter 10 means of egress: every value in a code data block, checked
against the code table it cites."""
from __future__ import annotations
from . import rule, Finding, RuleResult
from ..confidence import Abstention
from ..codes import fbc2023 as C
from ..facts import ProjectFacts
from ..declaration_schema import BY_KEY
from ..reconcile import legacy_context

#: Said when a Chapter 10 table needs the occupancy or sprinkler status and
#: neither the drawings nor the declaration give it. The rules used to assume
#: Group A-3, sprinklered, instead; see `reconcile.legacy_context`.
_ABSENT = "not stated on the drawings and not answered in the project declaration"


def _ctx(f, out, rule_id, group=True, sprinklers=True):
    """(group, sprinklered), or None after recording why the rule stood down."""
    GROUP, SPRINKLERED = legacy_context(f)
    missing = []
    if group and not GROUP:
        missing.append(BY_KEY["occupancy_group"].pro_label)
    if sprinklers and SPRINKLERED is None:
        missing.append(BY_KEY["sprinkler_system"].pro_label)
    if missing:
        out.abstentions.append(Abstention(rule_id, _ABSENT, detail="; ".join(missing)))
        return None
    return GROUP, SPRINKLERED


def _no_row(out, rule_id, table, group):
    out.abstentions.append(Abstention(
        rule_id, f"{table} row not carried in this build's corpus",
        detail=f"Group {group}."))


def _with(sprinklered):
    return "with" if sprinklered else "without"


def _datum(facts, section):
    return facts.datum(section)


@rule("EGRESS.COMMON_PATH")
def common_path(f: ProjectFacts, out: RuleResult):
    d = _datum(f, "1006.2.1")
    if not d or d.required is None:
        out.abstentions.append(Abstention("EGRESS.COMMON_PATH",
            "no code datum citing 1006.2.1 with a parseable required value"))
        return
    ctx = _ctx(f, out, "EGRESS.COMMON_PATH")
    if ctx is None:
        return
    GROUP, SPRINKLERED = ctx
    req = C.common_path_ft(GROUP, SPRINKLERED)
    if req is None:
        _no_row(out, "EGRESS.COMMON_PATH", "Table 1006.2.1", GROUP)
        return
    if abs(d.required - req) > 0.5:
        out.findings.append(Finding(
            "H-02", "EGRESS.COMMON_PATH", "OPEN", "HIGH", "Means of egress", d.page, d.sheet,
            d.anchor or "COMMON PATH OF TRAVEL",
            f"Common path requirement understated on {d.sheet}",
            "The common path of egress travel requirement stated in the code data block, "
            "against Table 1006.2.1 for the occupancy and sprinkler status declared on this sheet.",
            f"{d.sheet} states {d.required:g} LF required. Table 1006.2.1, Group {GROUP[0]} "
            f"{_with(SPRINKLERED)} a sprinkler system, is {req} feet. Provided is {d.provided_raw}, so there is no physical "
            f"deficiency — but the stated requirement is wrong.",
            "FBC-B 1006.2.1 · Table 1006.2.1",
            f"Change {d.required:g} LF to {req} LF."))
    else:
        out.findings.append(Finding(
            "V-CP", "EGRESS.COMMON_PATH", "PASS", "VERIFIED", "Means of egress", d.page, d.sheet,
            d.anchor or "COMMON PATH OF TRAVEL", "Common path requirement is correct",
            "The stated common path requirement against Table 1006.2.1.",
            f"{d.required:g} LF stated, {req} ft required. Correct.",
            "FBC-B Table 1006.2.1", "None."))


@rule("EGRESS.TRAVEL_DISTANCE")
def travel(f: ProjectFacts, out: RuleResult):
    d = _datum(f, "1017.2")
    if not d or d.required is None:
        out.abstentions.append(Abstention("EGRESS.TRAVEL_DISTANCE", "no parseable 1017.2 datum"))
        return
    ctx = _ctx(f, out, "EGRESS.TRAVEL_DISTANCE")
    if ctx is None:
        return
    GROUP, SPRINKLERED = ctx
    req = C.travel_distance_ft(GROUP, SPRINKLERED)
    if req is None:
        _no_row(out, "EGRESS.TRAVEL_DISTANCE", "Table 1017.2", GROUP)
        return
    ok = abs(d.required - req) <= 0.5
    out.findings.append(Finding(
        "V-01" if ok else "H-TD", "EGRESS.TRAVEL_DISTANCE", "PASS" if ok else "OPEN",
        "VERIFIED" if ok else "HIGH", "Means of egress", d.page, d.sheet,
        d.anchor or "MAX TRAVEL DISTANCE",
        "Travel distance limit — correct" if ok else "Travel distance limit is wrong",
        "The stated travel distance limit against Table 1017.2 for this occupancy and sprinkler status.",
        f"Table 1017.2, Group {GROUP[0]} {_with(SPRINKLERED)} a sprinkler system = {req} feet. "
        f"Sheet states {d.required:g} LF, provided {d.provided_raw}.",
        "FBC-B Table 1017.2", "None." if ok else f"Change to {req} LF."))


@rule("EGRESS.DEAD_END")
def dead_end(f: ProjectFacts, out: RuleResult):
    d = _datum(f, "1020.5")
    if not d or d.required is None:
        out.abstentions.append(Abstention("EGRESS.DEAD_END", "no parseable 1020.5 datum"))
        return
    ctx = _ctx(f, out, "EGRESS.DEAD_END")
    if ctx is None:
        return
    GROUP, SPRINKLERED = ctx
    req = C.dead_end_ft(GROUP, SPRINKLERED)
    ok = abs(d.required - req) <= 0.5
    out.findings.append(Finding(
        "V-02" if ok else "M-DE", "EGRESS.DEAD_END", "PASS" if ok else "OPEN",
        "VERIFIED" if ok else "MEDIUM", "Means of egress", d.page, d.sheet,
        d.anchor or "DEAD END CORRIDOR",
        f"Dead-end limit {req} LF — correct for Group {GROUP[0]}" if ok else "Dead-end limit is wrong",
        "The stated dead-end limit against 1020.5 and the group list in its sprinklered exception.",
        f"1020.5 sets {C._DEADEND_BASE} feet. The 50-foot sprinklered exception covers Groups "
        f"{', '.join(sorted(C._DEADEND_50FT_GROUPS))} only — Group {GROUP[0]} is not in the list, "
        f"so {req} feet governs. Sheet states {d.required:g} LF.",
        "FBC-B 1020.5", "None." if ok else f"Change to {req} LF."))


@rule("EGRESS.CORRIDOR_WIDTH")
def corridor(f: ProjectFacts, out: RuleResult):
    d = _datum(f, "1020.3")
    if not d or d.required is None:
        out.abstentions.append(Abstention("EGRESS.CORRIDOR_WIDTH", "no parseable 1020.3 datum"))
        return
    ctx = _ctx(f, out, "EGRESS.CORRIDOR_WIDTH", sprinklers=False)
    if ctx is None:
        return
    GROUP, _SPRINKLERED = ctx
    req = C.corridor_width_in(GROUP)
    ok = abs(d.required - req) <= 0.5
    out.findings.append(Finding(
        "V-03" if ok else "M-CW", "EGRESS.CORRIDOR_WIDTH", "PASS" if ok else "OPEN",
        "VERIFIED" if ok else "MEDIUM", "Means of egress", d.page, d.sheet,
        d.anchor or "MIN. CORRIDOR WIDTH",
        "Corridor width and the 1020.3 citation are both correct" if ok else "Corridor width is wrong",
        "The stated corridor width and the section number cited beside it against the 2023 FBC text.",
        f"Table 1020.3, 'any facilities not listed' = {req} inches. Sheet states "
        f"{d.required:g}\", provided {d.provided_raw}. In the 2023 FBC the width requirement sits at "
        f"1020.3 — older editions number it differently, so the citation is worth confirming and it is right.",
        "FBC-B 1020.3 · Table 1020.3", "None." if ok else f"Change to {req} in."))


@rule("EGRESS.CAPACITY_FACTOR")
def capacity_factor(f: ProjectFacts, out: RuleResult):
    d = _datum(f, "1005.3.2")
    if not d or d.required is None:
        out.abstentions.append(Abstention("EGRESS.CAPACITY_FACTOR", "no parseable 1005.3.2 datum"))
        return
    ol = f.meta.get("occupant_load")
    if not ol:
        out.abstentions.append(Abstention("EGRESS.CAPACITY_FACTOR", "occupant load not extracted"))
        return
    ctx = _ctx(f, out, "EGRESS.CAPACITY_FACTOR", group=False)
    if ctx is None:
        return
    _GROUP, SPRINKLERED = ctx
    implied = d.required / ol
    evacs = f.text_contains("VOICE/ALARM") or f.text_contains("EVACS") or f.text_contains("907.5.2.2")
    permitted = C.capacity_factor(SPRINKLERED, evacs)
    if implied >= permitted - 0.005:
        out.findings.append(Finding(
            "V-CF", "EGRESS.CAPACITY_FACTOR", "PASS", "VERIFIED", "Means of egress", d.page, d.sheet,
            d.anchor or "EXIT WIDTH REQUIRED", "Egress width factor is supported by the documents",
            "The factor implied by the stated required width and occupant load, against 1005.3.2.",
            f"{implied:.2f} in/occupant implied; {permitted} permitted. Provided {d.provided_raw}.",
            "FBC-B 1005.3.2", "None."))
    if implied < permitted - 0.005:
        out.findings.append(Finding(
            "M-01", "EGRESS.CAPACITY_FACTOR", "OPEN", "MEDIUM", "Means of egress", d.page, d.sheet,
            d.anchor or "EXIT WIDTH REQUIRED",
            f"{implied:.2f} in/occupant egress factor is not supported by the documents",
            "The egress width factor implied by the stated required width and occupant load, "
            "against 1005.3.2 and the conditions in its Exception 1.",
            f"Stated {d.required:g}\" required at an occupant load of {ol:g} implies "
            f"{implied:.2f} in/occupant. 1005.3.2 Exception 1 permits 0.15 only where the building "
            f"has BOTH a sprinkler system per 903.3.1.1/903.3.1.2 AND an emergency voice/alarm "
            f"communication system per 907.5.2.2. No EVACS appears anywhere in this set. At the base "
            f"factor the requirement is {ol*C.CAPACITY_FACTOR_BASE:.2f}\", not {d.required:g}\".",
            "FBC-B 1005.3.2 · 1005.3.2 Exc. 1",
            f"Document the EVACS per 907.5.2.2, or recompute at {C.CAPACITY_FACTOR_BASE}."))


@rule("EGRESS.EXIT_COUNT")
def exit_count(f: ProjectFacts, out: RuleResult):
    from .r_occupancy import computed_load
    d = _datum(f, "1006.3.2") or _datum(f, "1006.3.3")
    # Table 1004.5 can produce the load when the sheets do not state one; what
    # the sheets state still wins where it is readable.
    ol = f.meta.get("occupant_load") or computed_load(f, declared_only=True)
    if not ol:
        out.abstentions.append(Abstention("EGRESS.EXIT_COUNT", "occupant load not extracted"))
        return
    req = C.exits_required(ol)
    prov = d.provided if d else None
    if prov is None:
        out.abstentions.append(Abstention("EGRESS.EXIT_COUNT",
                                          "exit count row not found in a code data block"))
        return
    ok = prov >= req
    out.findings.append(Finding(
        "V-04" if ok else "C-EX", "EGRESS.EXIT_COUNT", "PASS" if ok else "OPEN",
        "VERIFIED" if ok else "CRITICAL", "Means of egress", d.page, d.sheet,
        d.anchor or "NUMBER OF EXITS",
        "Exit count is correct for the stated occupant load" if ok else "Too few exits",
        "Exit count against Table 1006.3.2 for the stated occupant load.",
        f"Occupant load {ol:g} requires {req} exits; {prov:g} provided.",
        "FBC-B Table 1006.3.2", "None." if ok else f"Provide {req} exits."))
