"""Chapter 10 means of egress: every value in a code data block, checked
against the code table it cites."""
from __future__ import annotations
import re
from . import rule, Finding, RuleResult
from ..confidence import Abstention
from ..codes import fbc2023 as C
from ..facts import ProjectFacts
from ..declaration_schema import BY_KEY
from ..reconcile import legacy_context
from ._stated import noted, rival, stated

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


def _datum(facts, *sections):
    """The block row citing `sections`, or the same row as the layout reads it."""
    return stated(facts, *sections)


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
    # Another sheet stating a different requirement is half of what the set
    # shows (G-0 50 LF, G-1 75 LF on the Sculpted set), so it is reported here,
    # in the finding about the row, rather than as a second finding.
    other = rival(f, "egress.common_path", "required", d.sheet, d.required)
    if abs(d.required - req) > 0.5:
        also = ""
        if other is not None:
            right = abs(float(other.value) - req) <= 0.5
            also = (f" {other.sheet} states {float(other.value):g} LF"
                    + (", which is what the table requires, so the two sheets disagree."
                       if right else ", which is also not what the table requires."))
        out.findings.append(Finding(
            "H-02", "EGRESS.COMMON_PATH", "OPEN", "HIGH", "Means of egress", d.page, d.sheet,
            d.anchor or "COMMON PATH OF TRAVEL",
            f"Common path requirement understated on {d.sheet}"
            + (f" and contradicts {other.sheet}" if other is not None else ""),
            "The common path of egress travel requirement stated in the code data block, "
            "against Table 1006.2.1 for the occupancy and sprinkler status declared on this sheet.",
            noted(d, f"{d.sheet} states {d.required:g} LF required. Table 1006.2.1, Group {GROUP[0]} "
            f"{_with(SPRINKLERED)} a sprinkler system, is {req} feet. Provided is {d.provided_raw}, so there is no physical "
            f"deficiency — but the stated requirement is wrong.") + also,
            "FBC-B 1006.2.1 · Table 1006.2.1",
            f"Change {d.required:g} LF to {req} LF"
            + (f" on {d.sheet} so the sheets agree." if other is not None else "."), box=d.box))
    elif other is not None:
        # This sheet is right and another is not: the finding is about the other.
        out.findings.append(Finding(
            "H-02", "EGRESS.COMMON_PATH", "OPEN", "HIGH", "Means of egress", other.page,
            other.sheet, other.label or "COMMON PATH",
            f"Common path requirement misstated on {other.sheet} and contradicts {d.sheet}",
            "The common path of egress travel requirement stated on each sheet, against "
            "Table 1006.2.1 for the occupancy and sprinkler status of this building.",
            f"{other.sheet} states {float(other.value):g} LF; {d.sheet} states {d.required:g} LF. "
            f"Table 1006.2.1, Group {GROUP[0]} {_with(SPRINKLERED)} a sprinkler system, is "
            f"{req} feet, so {d.sheet} is right and {other.sheet} is not.",
            "FBC-B 1006.2.1 · Table 1006.2.1",
            f"Change {other.sheet} to {req} LF so the sheets agree.", box=other.box))
    else:
        out.findings.append(Finding(
            "V-CP", "EGRESS.COMMON_PATH", "PASS", "VERIFIED", "Means of egress", d.page, d.sheet,
            d.anchor or "COMMON PATH OF TRAVEL", "Common path requirement is correct",
            "The stated common path requirement against Table 1006.2.1.",
            noted(d, f"{d.required:g} LF stated, {req} ft required. Correct."),
            "FBC-B Table 1006.2.1", "None.", box=d.box))


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
        noted(d, f"Table 1017.2, Group {GROUP[0]} {_with(SPRINKLERED)} a sprinkler system = {req} feet. "
        f"Sheet states {d.required:g} LF, provided {d.provided_raw}."),
        "FBC-B Table 1017.2", "None." if ok else f"Change to {req} LF.", box=d.box))


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
        noted(d, f"1020.5 sets {C._DEADEND_BASE} feet. The 50-foot sprinklered exception covers Groups "
        f"{', '.join(sorted(C._DEADEND_50FT_GROUPS))} only — Group {GROUP[0]} is not in the list, "
        f"so {req} feet governs. Sheet states {d.required:g} LF."),
        "FBC-B 1020.5", "None." if ok else f"Change to {req} LF.", box=d.box))


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
        noted(d, f"Table 1020.3, 'any facilities not listed' = {req} inches. Sheet states "
        f"{d.required:g}\", provided {d.provided_raw}. In the 2023 FBC the width requirement sits at "
        f"1020.3 — older editions number it differently, so the citation is worth confirming and it is right."),
        "FBC-B 1020.3 · Table 1020.3", "None." if ok else f"Change to {req} in.", box=d.box))


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
            noted(d, f"{implied:.2f} in/occupant implied; {permitted} permitted. Provided {d.provided_raw}."),
            "FBC-B 1005.3.2", "None.", box=d.box))
    if implied < permitted - 0.005:
        out.findings.append(Finding(
            "M-01", "EGRESS.CAPACITY_FACTOR", "OPEN", "MEDIUM", "Means of egress", d.page, d.sheet,
            d.anchor or "EXIT WIDTH REQUIRED",
            f"{implied:.2f} in/occupant egress factor is not supported by the documents",
            "The egress width factor implied by the stated required width and occupant load, "
            "against 1005.3.2 and the conditions in its Exception 1.",
            noted(d, f"Stated {d.required:g}\" required at an occupant load of {ol:g} implies "
            f"{implied:.2f} in/occupant. 1005.3.2 Exception 1 permits 0.15 only where the building "
            f"has BOTH a sprinkler system per 903.3.1.1/903.3.1.2 AND an emergency voice/alarm "
            f"communication system per 907.5.2.2. No EVACS appears anywhere in this set. At the base "
            f"factor the requirement is {ol*C.CAPACITY_FACTOR_BASE:.2f}\", not {d.required:g}\"."),
            "FBC-B 1005.3.2 · 1005.3.2 Exc. 1",
            f"Document the EVACS per 907.5.2.2, or recompute at {C.CAPACITY_FACTOR_BASE}.",
            box=d.box))


@rule("EGRESS.EXIT_COUNT")
def exit_count(f: ProjectFacts, out: RuleResult):
    from .r_occupancy import computed_load
    d = _datum(f, "1006.3.2", "1006.3.3")
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
        noted(d, f"Occupant load {ol:g} requires {req} exits; {prov:g} provided."),
        "FBC-B Table 1006.3.2", "None." if ok else f"Provide {req} exits.", box=d.box))


# ══ one capacity factor per analysis ═══════════════════════════════════════
def _requirement_factor(f: ProjectFacts):
    """(factor, how the set states it, page, sheet) the required width was computed with.

    A factor the set states outright wins; otherwise the one implied by the
    stated required width and the stated occupant load.
    """
    store = getattr(f, "store", None)
    r = store.resolve("egress_width_factor") if store is not None else None
    if r is not None:
        return float(r.value), f"stated on {r.best.sheet}", r.best.page, r.best.sheet
    d = _datum(f, "1005.3.2")
    ol = f.meta.get("occupant_load")
    if d is not None and d.required and ol:
        return (d.required / ol, f"{d.required:g}\" required for {ol:g} occupants on {d.sheet}",
                d.page, d.sheet)
    return None, "", 0, ""


@rule("EGRESS.FACTOR_CONSISTENCY")
def factor_consistency(f: ProjectFacts, out: RuleResult):
    """The factor each exit was rated at, against the one the requirement uses.

    1005.3.2 gives one factor for a building — 0.2 in/occupant, or 0.15 under
    Exception 1 — and an analysis that rates its exits at one and computes its
    required width at the other has not decided which applies.
    """
    rated = [x for x in f.discharges if x.width_provided_in and x.capacity]
    if not rated:
        out.abstentions.append(Abstention(
            "EGRESS.FACTOR_CONSISTENCY",
            "exit widths and the occupants each exit is rated for not extracted"))
        return
    used, how, _page, _sheet = _requirement_factor(f)
    if used is None:
        out.abstentions.append(Abstention(
            "EGRESS.FACTOR_CONSISTENCY", "required-width capacity factor not extracted"))
        return
    per_exit = [(x, x.width_provided_in / x.capacity) for x in rated]
    lines = "; ".join(f"{x.name.title()} ({x.sheet}): {x.width_provided_in:g}\" rated for "
                      f"{x.capacity:g} occupants = {k:.2f} in/occupant" for x, k in per_exit)
    first = rated[0]
    differing = [k for _x, k in per_exit if abs(k - used) > 0.005]
    if differing:
        stricter = all(k > used for k in differing)
        out.findings.append(Finding(
            "M-02", "EGRESS.FACTOR_CONSISTENCY", "OPEN", "MEDIUM", "Means of egress",
            first.page, first.sheet, first.anchor,
            "Two different capacity factors used in one analysis",
            "The capacity factor each exit was rated at, against the factor the required "
            "egress width was computed with.",
            f"{lines}. The required width uses {used:.2f} in/occupant ({how}). "
            + ("The exits were rated the conservative way and the requirement computed the "
               "permissive way. " if stricter else "")
            + "1005.3.2 supports one factor for the building, not both in one analysis.",
            "FBC-B 1005.3.2",
            "Decide which factor 1005.3.2 supports for this building — see the egress capacity "
            "factor check — and apply it uniformly across the analysis.", box=first.box))
    else:
        out.findings.append(Finding(
            "V-UF", "EGRESS.FACTOR_CONSISTENCY", "PASS", "VERIFIED", "Means of egress",
            first.page, first.sheet, first.anchor,
            "One capacity factor is used throughout the egress analysis",
            "The capacity factor each exit was rated at, against the factor the required "
            "egress width was computed with.",
            f"{lines}. The required width uses the same {used:.2f} in/occupant ({how}).",
            "FBC-B 1005.3.2", "None.", box=first.box))


# ══ ceiling height ═════════════════════════════════════════════════════════
@rule("EGRESS.CEILING_HEIGHT")
def ceiling_height(f: ProjectFacts, out: RuleResult):
    """Every ceiling-height tag on the reflected ceiling plan against 1003.2."""
    from ..read.tags import ceiling_sheets
    from ._context import feet_inches
    rcp = ceiling_sheets(f.sheets)
    if not rcp:
        out.abstentions.append(Abstention(
            "EGRESS.CEILING_HEIGHT", "reflected ceiling plan not found in this set"))
        return
    if not f.ceilings:
        out.abstentions.append(Abstention(
            "EGRESS.CEILING_HEIGHT", "ceiling heights not extracted from the reflected ceiling plan",
            detail=", ".join(s.code for s in rcp)))
        return
    minimum = C.MIN_CEILING_FT
    low = min(f.ceilings, key=lambda t: t.height_ft)
    groups = {}
    for t in f.ceilings:
        groups.setdefault(t.reference.upper(), []).append(t)
    described = []
    for ref, tags in groups.items():
        lo, hi = min(t.height_ft for t in tags), max(t.height_ft for t in tags)
        span = feet_inches(lo) if lo == hi else f"{feet_inches(lo)} to {feet_inches(hi)}"
        described.append(f"{span} {ref} ({len(tags)} tag{'s' if len(tags) != 1 else ''})")
    sheets = ", ".join(sorted({t.sheet for t in f.ceilings}))
    summary = f"{sheets} tags {len(f.ceilings)} ceilings: {'; '.join(described)}."
    if low.height_ft + 1e-6 >= minimum:
        title_sheet = next((s for s in rcp if s.index == low.page), rcp[0])
        out.findings.append(Finding(
            "V-17", "EGRESS.CEILING_HEIGHT", "PASS", "VERIFIED", "Means of egress",
            low.page, low.sheet, title_sheet.title.upper() or low.raw,
            f"Ceiling heights clear the {feet_inches(minimum)} minimum",
            "Every ceiling-height tag on the reflected ceiling plan against the minimum ceiling "
            "height for a means of egress.",
            f"{summary} The lowest is {feet_inches(low.height_ft)}; FBC-B 1003.2 requires "
            f"{feet_inches(minimum)}. Clear by {feet_inches(low.height_ft - minimum)}.",
            "FBC-B 1003.2", "None."))
    else:
        out.findings.append(Finding(
            "M-CH", "EGRESS.CEILING_HEIGHT", "OPEN", "MEDIUM", "Means of egress",
            low.page, low.sheet, low.raw,
            f"A ceiling is tagged {feet_inches(low.height_ft)}, under the "
            f"{feet_inches(minimum)} means-of-egress minimum",
            "Every ceiling-height tag on the reflected ceiling plan against the minimum ceiling "
            "height for a means of egress.",
            f"{summary} The lowest, {low.raw} {low.reference}, is under the "
            f"{feet_inches(minimum)} FBC-B 1003.2 requires wherever the space is part of the "
            f"means of egress.",
            "FBC-B 1003.2",
            "Raise the ceiling, or confirm the space is outside the means of egress and cite "
            "the minimum that governs it.", box=low.box))


# ══ assembly occupant-load posting ═════════════════════════════════════════
_POSTING = re.compile(
    r"OCCUPANT\s+LOAD\s+(?:SIGN|PLACARD|POSTING)|"
    r"(?:MAX(?:IMUM)?\.?\s+)?OCCUPAN(?:CY|T\s+LOAD)\s+(?:SIGN|PLACARD)|"
    r"POST(?:ED)?\s+(?:THE\s+)?(?:MAX(?:IMUM)?\.?\s+)?OCCUPAN(?:CY|T\s+LOAD)|"
    r"OCCUPAN(?:CY|T\s+LOAD)\s+(?:SHALL\s+BE\s+)?POSTED|"
    r"\b1004\.9\b", re.I)


@rule("EGRESS.OCCUPANT_LOAD_POSTING")
def occupant_load_posting(f: ProjectFacts, out: RuleResult):
    """1004.9: an assembly space posts its occupant load. Searched in every sheet's text."""
    ctx = _ctx(f, out, "EGRESS.OCCUPANT_LOAD_POSTING", sprinklers=False)
    if ctx is None:
        return
    GROUP, _ = ctx
    if GROUP not in C.POSTING_REQUIRED_FOR and GROUP.split("-")[0] not in C.POSTING_REQUIRED_FOR:
        out.abstentions.append(Abstention(
            "EGRESS.OCCUPANT_LOAD_POSTING",
            "the occupant-load posting of 1004.9 applies only to assembly occupancies",
            detail=f"Group {GROUP}."))
        return
    unread = [p for p in sorted(f.text_by_page) if len((f.text_by_page[p] or "").strip()) < 20]
    if unread:
        out.abstentions.append(Abstention(
            "EGRESS.OCCUPANT_LOAD_POSTING",
            "sheet text not extracted on every sheet, so an absent posting cannot be established",
            detail=", ".join(f.sheet_code(p) for p in unread)))
        return
    hit = next(((p, m) for p, t in sorted(f.text_by_page.items())
                for m in [_POSTING.search(t or "")] if m), None)
    store = getattr(f, "store", None)
    r = store.resolve("occupant_load") if store is not None else None
    page, sheet = (r.best.page, r.best.sheet) if r is not None else (0, f.sheet_code(0))
    anchor = r.best.label if r is not None else "OCCUPANT LOAD"
    ol = f.meta.get("occupant_load")
    load = f" with a stated occupant load of {ol:g}" if ol else ""
    if hit:
        p, m = hit
        out.findings.append(Finding(
            "V-OLP", "EGRESS.OCCUPANT_LOAD_POSTING", "PASS", "VERIFIED", "Means of egress",
            p, f.sheet_code(p), m.group(0),
            "The assembly occupant-load posting is called for",
            "Every sheet in the set for the occupant-load sign 1004.9 requires in an assembly "
            "space.",
            f"Group {GROUP}{load}. {f.sheet_code(p)} carries \"{m.group(0).strip()}\". 1004.9 "
            f"requires the occupant load of an assembly space posted near its main exit or exit "
            f"access doorway, on an approved, legible, permanent sign.",
            "FBC-B 1004.9", "None."))
    else:
        out.findings.append(Finding(
            "M-05", "EGRESS.OCCUPANT_LOAD_POSTING", "OPEN", "MEDIUM", "Means of egress",
            page, sheet, anchor,
            "Assembly occupant-load posting is not shown anywhere in the set",
            "Every sheet in the set for the occupant-load sign 1004.9 requires in an assembly "
            "space.",
            f"Group {GROUP}{load}. 1004.9 requires every room or space that is an assembly "
            f"occupancy to have its occupant load posted in a conspicuous place near the main "
            f"exit or exit access doorway, on an approved, legible, permanent sign. No sheet's "
            f"text mentions an occupant-load sign or posting ({len(f.text_by_page)} sheets "
            f"searched). A sign drawn only as a symbol, with no text anywhere in the set, would "
            f"not be seen by this check.",
            "FBC-B 1004.9",
            "Show the sign location near the main exit access doorway and note it as a "
            "permanent, approved sign.", box=r.best.box if r is not None else None))
