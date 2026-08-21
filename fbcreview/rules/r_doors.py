"""Door clear width — the rule that found H-03 in the test set."""
from __future__ import annotations
from . import rule, Finding, RuleResult
from ..confidence import Abstention
from ..codes import fbc2023 as C
from ..facts import ProjectFacts


@rule("DOORS.CLEAR_WIDTH")
def clear_width(f: ProjectFacts, out: RuleResult):
    if not f.doors:
        out.abstentions.append(Abstention("DOORS.CLEAR_WIDTH", "no door schedule extracted"))
        return
    bad = []
    for d in f.doors:
        if d.is_existing or d.width_in is None:
            continue
        clear = C.door_clear_width_from_leaf(d.width_in, d.thickness_in or 1.75)
        if clear < C.DOOR_CLEAR_WIDTH_IN:
            bad.append((d, clear))
    ref = f.doors[0]
    if bad:
        for d, clear in bad:
            bifold = "BI-FOLD" in d.remarks.upper() or d.style.strip().upper() == "B"
            out.findings.append(Finding(
                "H-03" if not bifold else "M-03b", "DOORS.CLEAR_WIDTH", "OPEN",
                "HIGH" if not bifold else "MEDIUM", "Means of egress / Accessibility",
                d.page, d.sheet, "DOOR AND FRAME SCHEDULE",
                f"Door {d.number} is a {_ftin(d.width_in)} leaf — under the "
                f"{C.DOOR_CLEAR_WIDTH_IN} in. clear width the set itself requires",
                "Every leaf width in the door schedule against the 32 in. minimum clear opening "
                "in FBC-B 1010.1.1, excluding doors marked existing.",
                f"Door {d.number} is scheduled as a new {d.style} leaf, {_ftin(d.width_in)} x "
                f"{_ftin(d.height_in)} x {d.thickness_in or 1.75:g}\". A leaf that wide produces about "
                f"{clear:.2f} in. of clear width at 90 degrees once the leaf and stop are deducted — "
                f"about {C.DOOR_CLEAR_WIDTH_IN - clear:.1f} in. short.",
                "FBC-B 1010.1.1 · FBC-A 404.2.3",
                f"Increase Door {d.number} to a 3'-0\" leaf, or demonstrate "
                f"{C.DOOR_CLEAR_WIDTH_IN} in. of net clear width with the specified hardware."
                + (f" If Closet {d.number} is under {C.DOOR_CLOSET_EXEMPT_SF} sq ft the 1010.1.1 "
                   f"storage-closet exception applies — confirm the area." if bifold else "")))
    ok = [d for d in f.doors if not d.is_existing and d.width_in
          and C.door_clear_width_from_leaf(d.width_in, d.thickness_in or 1.75)
          >= C.DOOR_CLEAR_WIDTH_IN]
    if ok:
        out.findings.append(Finding(
            "V-DW", "DOORS.CLEAR_WIDTH", "PASS", "VERIFIED", "Means of egress",
            ref.page, ref.sheet, "DOOR AND FRAME SCHEDULE",
            f"{len(ok)} of {len([d for d in f.doors if not d.is_existing])} new doors meet the "
            f"32 in. clear opening",
            "Every scheduled leaf width against FBC-B 1010.1.1.",
            "Compliant: " + ", ".join(f"door {d.number} at {_ftin(d.width_in)}" for d in ok) + ".",
            "FBC-B 1010.1.1", "None."))


def _ftin(inches):
    if inches is None:
        return "?"
    ft, rem = divmod(round(inches), 12)
    return f"{ft}'-{rem}\""
