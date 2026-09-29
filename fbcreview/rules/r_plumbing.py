"""Chapter 29 plumbing fixtures.

The ratios a set states, against the Table 2902.1 row they come from; the split
by sex and the round-ups of 2902.1.1, recomputed; and what is provided against
what that requires. The table lives in `codes/fbc2023.py` and carries only the
rows transcribed so far — a set on any other row abstains and names the table,
rather than be checked against a row it does not use.
"""
from __future__ import annotations

import math

from . import rule, Finding, RuleResult
from ..codes import fbc2023 as C
from ..confidence import Abstention
from ..facts import ProjectFacts
from ..reconcile import legacy_context

_NAME = {"wc": "WC", "lav": "LAV", "drinking_fountain": "DF", "service_sink": "service sink"}


def _row(group, ratios):
    """The corpus row whose ratios are the ones stated, for this building's group."""
    for row in C.PLUMBING_ROWS:
        if group and group != row["group"] and group != row["group"].split("-")[0]:
            continue
        df = ratios.get("drinking_fountain", (None, None))[0]
        if (ratios.get("wc") == row["wc"] and ratios.get("lav") == row["lav"]
                and df == row["drinking_fountain"]):
            return row
    return None


def _ratios_text(pc) -> str:
    parts = []
    wc = pc.ratios.get("wc")
    if wc:
        parts.append(f"WC 1/{wc[0]:g} male, 1/{wc[1]:g} female" if wc[0] != wc[1]
                     else f"WC 1/{wc[0]:g}")
    lav = pc.ratios.get("lav")
    if lav:
        parts.append(f"LAV 1/{lav[0]:g}" if lav[0] == lav[1]
                     else f"LAV 1/{lav[0]:g} male, 1/{lav[1]:g} female")
    df = pc.ratios.get("drinking_fountain")
    if df:
        parts.append(f"DF 1/{df[0]:g}")
    if pc.service_sinks:
        parts.append(f"{pc.service_sinks:g} service sink")
    return "; ".join(parts)


def _r2(x: float) -> str:
    """Two places, half rounded up — as a sheet prints 35/200 (0.18), not as a float does (0.17)."""
    return f"{math.floor(x * 100 + 0.5) / 100:.2f}"


def _counts(d) -> str:
    return ", ".join(f"{v:g} {_NAME.get(k, k)}" for k, v in d.items())


@rule("PLUMB.FIXTURE_COUNT")
def fixture_count(f: ProjectFacts, out: RuleResult):
    pc = f.plumbing
    if pc is None or not pc.ratios:
        out.abstentions.append(Abstention(
            "PLUMB.FIXTURE_COUNT", "plumbing fixture calculation not extracted"))
        return
    ol = pc.occupant_load or f.meta.get("occupant_load")
    if not ol:
        out.abstentions.append(Abstention(
            "PLUMB.FIXTURE_COUNT", "occupant load not extracted"))
        return
    group, _ = legacy_context(f)
    row = _row(group, pc.ratios)
    if row is None:
        out.abstentions.append(Abstention(
            "PLUMB.FIXTURE_COUNT", "Table 2902.1 row not carried in this build's corpus",
            detail=f"Group {group or 'not stated'}; the set states {_ratios_text(pc)}."))
        return

    half = ol * C.PLUMBING_SPLIT
    need = {
        "wc": math.ceil(half / row["wc"][0]) + math.ceil(half / row["wc"][1]),
        "lav": math.ceil(half / row["lav"][0]) + math.ceil(half / row["lav"][1]),
        "drinking_fountain": math.ceil(ol / row["drinking_fountain"]),
        "service_sink": row["service_sinks"],
    }
    working = (f"Occupant load {ol:g}, {half:g} per sex (2902.1.1). WC "
               f"{half:g}/{row['wc'][0]} = {_r2(half / row['wc'][0])} male and "
               f"{half:g}/{row['wc'][1]} = {_r2(half / row['wc'][1])} female; LAV "
               f"{half:g}/{row['lav'][0]} = {_r2(half / row['lav'][0])} each; DF "
               f"{ol:g}/{row['drinking_fountain']} = {_r2(ol / row['drinking_fountain'])}. "
               f"Rounded up: {_counts(need)}.")
    matches = (f"{pc.sheet} states {_ratios_text(pc)} — Table 2902.1's {row['group']} row "
               f"'{row['description']}', exactly.")
    unisex = (" The water closets and lavatories are in single-user unisex rooms, which "
              "count toward the total (2902.1.2) and need not be designated by sex (2902.2 "
              "Exception 5)." if pc.unisex else "")

    short = {k: v for k, v in need.items() if pc.provided and pc.provided.get(k, 0) < v}
    miscounted = {k: v for k, v in need.items() if k in pc.required and pc.required[k] != v}
    if short:
        out.findings.append(Finding(
            "P-01", "PLUMB.FIXTURE_COUNT", "OPEN", "HIGH", "Plumbing",
            pc.page, pc.sheet, pc.anchor,
            "Fewer plumbing fixtures provided than Table 2902.1 requires",
            "Each fixture ratio against Table 2902.1, the split and round-ups of 2902.1.1, and "
            "what is provided against what that requires.",
            f"{matches} {working} Provided: {_counts(pc.provided)}. Short: "
            + ", ".join(f"{_NAME.get(k, k)} ({pc.provided.get(k, 0):g} of {v})"
                        for k, v in short.items()) + "." + unisex,
            "FBC-B Table 2902.1 · 2902.1.1",
            "Provide the missing fixtures, or state the approved basis for fewer.", box=pc.box))
    elif miscounted:
        out.findings.append(Finding(
            "P-02", "PLUMB.FIXTURE_COUNT", "OPEN", "MEDIUM", "Plumbing",
            pc.page, pc.sheet, pc.anchor,
            "The stated plumbing fixture requirement is miscomputed",
            "Each fixture ratio against Table 2902.1, the split and round-ups of 2902.1.1, and "
            "what is provided against what that requires.",
            f"{matches} {working} The sheet states {_counts(pc.required)} required: "
            + ", ".join(f"{_NAME.get(k, k)} {pc.required[k]:g}, not {v}"
                        for k, v in miscounted.items()) + "." + unisex,
            "FBC-B Table 2902.1 · 2902.1.1",
            "Recompute the fixture count and correct the stated requirement.", box=pc.box))
    else:
        provided = (f" Provided: {_counts(pc.provided)}." if pc.provided else
                    " The calculation does not list what is provided.")
        out.findings.append(Finding(
            "V-06", "PLUMB.FIXTURE_COUNT", "PASS", "VERIFIED", "Plumbing",
            pc.page, pc.sheet, pc.anchor,
            "Every plumbing ratio, the 50/50 split and all round-ups are correct",
            "Each fixture ratio against Table 2902.1, the split and round-ups of 2902.1.1, and "
            "what is provided against what that requires.",
            f"{matches} {working}{provided}{unisex}",
            "FBC-B Table 2902.1 · 2902.1.1 · 2902.2", "None.", box=pc.box))
