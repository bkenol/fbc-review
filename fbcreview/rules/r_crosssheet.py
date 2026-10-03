"""Cross-sheet consistency — the class of error no single-sheet review finds."""
from __future__ import annotations
from . import rule, Finding, RuleResult
from ..confidence import Abstention
from ..codes import fbc2023 as C
from ..facts import ProjectFacts
from ..reconcile import basis_of, declared_value, source_label


def _claim(f: ProjectFacts, field: str):
    """The fact store's best claim for a field, or None."""
    store = getattr(f, "store", None)
    r = store.resolve(field) if store is not None else None
    return r.best if r is not None else None


def _home(f: ProjectFacts, claim, default_anchor: str):
    """(page, sheet, anchor) for a finding about a stated value.

    Where the value is printed, when the store read it there — the sheet that
    states it, not whichever sheet happens to come first. A value with no
    located claim lands on the first sheet, where the register still carries it.
    """
    if claim is not None:
        return claim.page, claim.sheet or f.sheet_code(claim.page), claim.label or default_anchor
    return 0, f.sheet_code(0), default_anchor


def _area_pair(f: ProjectFacts):
    """The two areas to compare, what to call each, and what they rest on.

    The drawings are the first source: the building area as stated, against
    the total a table of spaces adds up to. Each is labelled with the sheet it
    is printed on — on the Sculpted set G-0 and the G-1 occupancy tables, which
    is how this finding has always read there.

    A declared area stands in for one the sheets do not state in readable text —
    which is the whole point of the declaration — but then the labels move with
    it. A finding must never attribute to a sheet a number that sheet does not
    carry, and must not claim a declared basis for something the sheet confirms
    either.
    """
    # The area a sheet states for the building — as a storey area, or failing
    # that as a total (`TOTAL BUILDING AREA: 15,376 SF` on ITEC's G-2) — against
    # the total a table of spaces adds up to.
    ca = _claim(f, "building_area_sf") or _claim(f, "total_area_sf")
    cb = _claim(f, "area.tabulated_total_sf")
    a = float(ca.value) if ca is not None else f.meta.get("area_g0_sf")
    b = float(cb.value) if cb is not None else f.meta.get("area_g1_sf")
    la = ca.sheet if ca is not None else "G-0"
    if cb is not None:
        tables = "occupancy tables" if "OCCUP" in " ".join(
            [cb.heading, *cb.context]).upper() else "area tabulation"
        lb = f"the {cb.sheet} {tables}"
    else:
        lb = "the G-1 occupancy tables"
    drawn = {la, lb}
    if a is None and declared_value(f, "building_area_sf") is not None:
        a = float(declared_value(f, "building_area_sf"))
        la = source_label(f, "building_area_sf", "largest floor")
    if b is None and declared_value(f, "total_area_sf") is not None:
        b = float(declared_value(f, "total_area_sf"))
        lb = source_label(f, "total_area_sf", "whole building")
    drawn_only = {la, lb} == drawn
    keys = [k for k, label in (("building_area_sf", la), ("total_area_sf", lb))
            if label not in drawn]
    return a, b, la, lb, drawn_only, (basis_of(f, *keys) if keys else "drawings"), ca


@rule("XSHEET.BUILDING_AREA")
def area_agreement(f: ProjectFacts, out: RuleResult):
    a, b, la, lb, drawn_only, basis, ca = _area_pair(f)
    page, sheet, _ = _home(f, ca, "")
    other = lb.split()[1] if lb.startswith("the ") else lb
    # "The general sheets" only where both are: ITEC compares G-2 with A-1.
    general = la[:1].upper() == "G" and other[:1].upper() == "G"
    if a is None or b is None:
        # Naming the general sheets is only honest when the set has some. A
        # single-discipline submittal has none, the two reads in `pipeline.py`
        # never execute, and reporting "not found on both general sheets" would
        # describe a search that did not happen — the same fault as letting
        # "not checked" read as "checked and passed".
        out.abstentions.append(Abstention(
            "XSHEET.BUILDING_AREA",
            "building area not found on both general sheets"
            if f.meta.get("has_general_sheets", True) else
            "this submittal carries no general sheets, so neither area was read "
            "from one; declare the building area to have this checked"))
        return
    diff = abs(a - b)
    if diff > 1:
        out.findings.append(Finding(
            "M-04", "XSHEET.BUILDING_AREA", "OPEN", "MEDIUM", "Occupancy", page, sheet,
            f"{a:,.0f} SF",
            "Building area disagrees between sheets" if drawn_only else
            "The largest floor and the whole building do not agree",
            "The building area stated on each general sheet, compared, and the basis stated for each."
            if general else
            "The building area stated on each sheet that states it, compared, and the basis stated "
            "for each.",
            (f"{la} states {a:,.0f} SF; {lb} total {b:,.0f} SF. Difference "
             if drawn_only else
             f"The largest floor is {a:,.0f} SF ({la}); the whole building is {b:,.0f} SF "
             f"({lb}). Difference ") +
            f"{diff:,.0f} SF ({diff/max(a,b)*100:.1f} percent). Most likely gross versus net, but "
            f"Table 1004.5 factors are basis-specific, so the basis has to be explicit or the "
            f"occupant load cannot be independently checked.",
            "FBC-B 1004.5 · FBC-EBC 601.2",
            "Label both areas with their basis (gross or net).", basis=basis,
            box=ca.box if ca is not None else None))
    else:
        out.findings.append(Finding(
            "V-AR", "XSHEET.BUILDING_AREA", "PASS", "VERIFIED", "Occupancy", page, sheet,
            f"{a:,.0f} SF",
            ("Building area agrees across the general sheets" if general else
             "Building area agrees between the sheets that state it") if drawn_only else
            "The largest floor and the whole building agree",
            "Building area on each general sheet, compared." if general else
            "Building area on each sheet that states it, compared.",
            (f"{la} and {other} both give {a:,.0f} SF."
             if drawn_only else
             f"The largest floor and the whole building are both {a:,.0f} SF "
             f"({la}), which is what a single-storey building should show."),
            "FBC-B 1004.5", "None.", basis=basis, box=ca.box if ca is not None else None))


@rule("XSHEET.RISK_CATEGORY")
def risk_category(f: ProjectFacts, out: RuleResult):
    from .r_occupancy import computed_load
    rc = f.meta.get("risk_category") or declared_value(f, "risk_category")
    # A load recomputed from Table 1004.5 is a legitimate second source when the
    # sheets do not state one; the sheet's own figure still wins when it exists,
    # and the recomputation is only reached for on a set someone declared an
    # occupancy for.
    ol = f.meta.get("occupant_load") or computed_load(f, declared_only=True)
    if not rc or not ol:
        out.abstentions.append(Abstention("XSHEET.RISK_CATEGORY",
                                          "risk category or occupant load not extracted"))
        return
    rc_claim = _claim(f, "risk_category") if f.meta.get("risk_category") else None
    page, sheet, anchor = _home(f, rc_claim, "RISK CATEGORY")
    rc_box = rc_claim.box if rc_claim is not None else None
    if rc.strip().upper() in ("III", "3") and ol <= C.RISK_III_ASSEMBLY_OL:
        out.findings.append(Finding(
            "M-03", "XSHEET.RISK_CATEGORY", "OPEN", "MEDIUM", "Structural / Occupancy", page, sheet,
            anchor,
            "Risk Category III does not match the stated occupant load",
            "The assigned risk category against Table 1604.5 and the occupant load stated on the "
            "same sheet.",
            f"Risk Category {rc} is assigned. Table 1604.5 sets RC III at a primary assembly "
            f"occupancy with an occupant load greater than {C.RISK_III_ASSEMBLY_OL}. The stated "
            f"occupant load is {ol:g}. RC III is conservative — it raises design wind pressures — "
            f"but it is inconsistent with the occupant load on the same sheet.",
            "FBC-B Table 1604.5", "Confirm Risk Category II, or state the basis for III.",
            box=rc_box))
    else:
        out.findings.append(Finding(
            "V-RC", "XSHEET.RISK_CATEGORY", "PASS", "VERIFIED", "Structural / Occupancy", page, sheet,
            anchor, "Risk category is consistent with the occupant load",
            "Assigned risk category against Table 1604.5.",
            f"RC {rc} at an occupant load of {ol:g}.", "FBC-B Table 1604.5", "None.", box=rc_box))


#: Disagreements a field's own rule reports inside its finding about that row.
FOLDED = {("egress.common_path", "required")}


#: How a finding names a fact. Audit rows are named with their role, because
#: "the common path agrees" and "the common path requirement agrees" are
#: different claims — and only one of them is true of the Sculpted set.
_NAMES = {
    "egress.travel_distance": "travel distance",
    "egress.common_path": "common path of egress travel",
    "egress.dead_end": "dead-end limit",
    "egress.corridor_width": "corridor width",
    "egress.exits": "number of exits",
    "egress.width": "egress width",
    "egress.door_clear_width": "door clear width",
    "occupancy_group": "occupancy group",
    "sprinkler_system": "sprinkler system",
    "occupant_load": "occupant load",
    "construction_type": "construction type",
    "building_area_sf": "building area",
    "total_area_sf": "total building area",
}


def _name(key: str, role: str = "") -> str:
    from ..read.catalog import BY_KEY
    spec = BY_KEY.get(key)
    base = _NAMES.get(key) or (spec.labels[0].lower() if spec is not None and spec.labels else key)
    return f"{base}, {role}" if role else base


def _shown(c) -> str:
    raw, label = (c.raw or "").strip(), (c.label or "").strip()
    return raw[len(label):].strip(" :") if label and raw.upper().startswith(label.upper()) else raw


@rule("XSHEET.STATED_CONFLICT")
def stated_conflict(f: ProjectFacts, out: RuleResult):
    """The same fact stated differently on two sheets.

    Every value the fact store read on more than one sheet is compared, with
    formatting differences already absorbed (`ALTERATION - LEVEL II` and `II`
    are one answer). What remains is a disagreement the set itself contains —
    independent of any code table, and a reason for a plans examiner to look
    harder at both sheets.
    """
    store = getattr(f, "store", None)
    if store is None:
        out.abstentions.append(Abstention("XSHEET.STATED_CONFLICT", "stated values not extracted"))
        return
    folded = [r for r in store.conflicts() if (r.field, r.role) in FOLDED]
    conflicts = [r for r in store.conflicts() if (r.field, r.role) not in FOLDED]
    shared = []
    for key, role in store.keys():
        r = store.resolve(key, role)
        # Sheets that each state it: one drawing entity seen on two sheets
        # cannot disagree with itself, so it was never compared, and a pass on
        # it would be "not checked" dressed as "checked" (measured: V-XS PASS
        # for one model-space note shown through two viewports).
        if r is not None and len(r.stating_sheets) > 1 and not r.conflict:
            shared.append((key, role, r))
    for i, r in enumerate(conflicts):
        best = r.best
        others = [g[0] for g in r.rivals if g and g[0].sheet != best.sheet] or \
                 [g[0] for g in r.rivals if g]
        if not others:
            continue
        o = others[0]
        name = _name(r.field, r.role)
        out.findings.append(Finding(
            "M-XS" if i == 0 else f"M-XS{chr(ord('b') + i - 1)}", "XSHEET.STATED_CONFLICT",
            "OPEN", "MEDIUM", "General", best.page, best.sheet, best.label or best.raw,
            f"{best.sheet} and {o.sheet} state a different {name}" if o.sheet != best.sheet
            else f"{best.sheet} states the {name} two different ways",
            "Every value stated on more than one sheet, compared across the sheets that state it.",
            f"{best.sheet} states \"{_shown(best)}\" ({best.label}); {o.sheet} states "
            f"\"{_shown(o)}\" ({o.label}). One set, two answers — whichever is right, the other "
            f"sheet is what a plans examiner will circle.",
            "FBC-B 107.2.1",
            f"Decide which {name} is right and make every sheet that states it agree.",
            box=best.box))
    if shared and not conflicts:
        listed = sorted({f"{_name(k, role)} ({', '.join(r.stating_sheets)})"
                         for k, role, r in shared})
        first = shared[0][2].best
        out.findings.append(Finding(
            "V-XS", "XSHEET.STATED_CONFLICT", "PASS", "VERIFIED", "General",
            first.page, first.sheet, first.label or first.raw,
            "Other values stated on more than one sheet agree" if folded else
            "Values stated on more than one sheet agree",
            "Every value stated on more than one sheet, compared across the sheets that state it.",
            f"{len(listed)} value{'s' if len(listed) != 1 else ''} stated on more than one sheet, "
            f"all in agreement: " + "; ".join(listed) + "."
            + "".join(f" One value is not in agreement — the {_name(r.field, r.role)} — and "
                      f"that disagreement is reported with its own code check." for r in folded),
            "FBC-B 107.2.1", "None.", box=first.box))
    elif not shared and not conflicts:
        out.abstentions.append(Abstention(
            "XSHEET.STATED_CONFLICT",
            "no value is stated on more than one sheet, so there is nothing to compare"))
