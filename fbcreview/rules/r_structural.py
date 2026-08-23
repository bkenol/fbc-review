"""Wind design basis — which ASCE 7 the cited edition actually adopts.

A wind speed is meaningless without the standard it was read from. The 8th
Edition adopts ASCE 7-22, whose maps differ from the 7-16 maps the 7th Edition
adopted, so a speed carried across editions is a number without a source.
"""
from __future__ import annotations

import datetime as dt

from . import rule, Finding, RuleResult
from ..codes import editions as E
from ..confidence import Abstention
from ..facts import ProjectFacts
from ._context import basis, need, source_phrase, where


@rule("STRUCT.WIND_STANDARD")
def wind_standard(f: ProjectFacts, out: RuleResult):
    v = need(f, out, "STRUCT.WIND_STANDARD", "code_edition", "wind_speed_mph")
    if v is None:
        return
    key = str(v["code_edition"])
    speed = float(v["wind_speed_mph"])
    cited = E.edition(key)
    if cited is None or cited.asce7 is None:
        out.abstentions.append(Abstention(
            "STRUCT.WIND_STANDARD", "no ASCE 7 edition is recorded for this code edition",
            detail=key))
        return

    today = f.meta.get("as_of") or dt.date.today()
    current = E.in_force(today)
    page, sheet = where(f, "wind_speed_mph", "code_edition")
    b = basis(f, "code_edition", "wind_speed_mph")
    from ..reconcile import value_of
    exposure = value_of(f, "exposure_category")
    exposure_txt = (f" Exposure {exposure} is asserted; ASCE 7 26.7.3 requires upwind "
                    f"terrain to justify it in every direction, which is a site question, "
                    f"not a declaration one." if exposure else "")

    superseded = current is not None and current.effective > cited.effective
    if not superseded:
        out.findings.append(Finding(
            "V-WIND", "STRUCT.WIND_STANDARD", "PASS", "VERIFIED", "Structural",
            page, sheet, "WIND SPEED",
            f"Wind design basis is the current standard, {cited.asce7}",
            "The ASCE 7 edition the cited code edition adopts, against the edition in force.",
            f"{cited.label} adopts {cited.asce7}. Vult = {speed:g} mph "
            f"({source_phrase(f, 'wind_speed_mph')})." + exposure_txt,
            f"FBC-B 1609 · {cited.asce7}", "None.", basis=b))
        return

    out.findings.append(Finding(
        "H-WIND", "STRUCT.WIND_STANDARD", "OPEN", "HIGH", "Structural",
        page, sheet, "WIND SPEED",
        f"Wind design is to {cited.asce7}, which {current.ordinal} Edition replaced",
        "The ASCE 7 edition the cited code edition adopts, against the edition in force "
        "today, and what that does to the design wind speed.",
        f"The set is designed to {cited.label}, which adopts {cited.asce7} — Vult = "
        f"{speed:g} mph ({source_phrase(f, 'wind_speed_mph')}). "
        f"{current.label} took effect {current.effective.isoformat()} and adopts "
        f"{current.asce7}, whose maps are drawn from a different hazard analysis. The speed "
        f"has to be re-read for this parcel from the {current.asce7} maps, not carried "
        f"across." + exposure_txt,
        f"FBC-B 1609 · 1609.3 · {cited.asce7} → {current.asce7}",
        f"Re-read Vult for this parcel from {current.asce7} and re-run the wind analysis.",
        basis=b))
