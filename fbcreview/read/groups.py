"""Readings that span several label/value rows under one heading.

The catalog reader (`deterministic.py`) reads one fact from one label. Some
statements are a small block instead — a heading, then the rows that belong to
it — and only mean something together:

    EXIT DISCHARGE 1:
      WIDTH PROVIDED:     72"
      OCCUPANT CAPACITY:  360

`72"` alone is a width and `360` alone is a count; under one heading they say
the designer rated a 72-inch exit for 360 people, which is a capacity factor of
0.20 in/occupant. The layout layer already records the heading over each pair
(`Pair.context`), so grouping is a matter of reading it.
"""
from __future__ import annotations

import re
from typing import Dict, List

from ..facts import ExitDischarge, OccupancyRow
from ..layout import PageLayout
from .deterministic import normalise_label
from .parse import parse

#: `EXIT DISCHARGE 1`, `EXIT 2`, `EXIT #3`, `EXIT NO. 4`.
_EXIT = re.compile(r"^(EXIT(?:\s+DISCHARGE)?\s*(?:#|NO\.?)?\s*\d+[A-Z]?)\b")


def exit_discharges(layouts: Dict[int, PageLayout], codes: Dict[int, str]) -> List[ExitDischarge]:
    """Every exit block a sheet states, with whichever of its rows are readable."""
    out: List[ExitDischarge] = []
    for page, layout in sorted(layouts.items()):
        groups: Dict[str, ExitDischarge] = {}
        for pair in layout.pairs:
            heading = next((c.strip().rstrip(":").strip() for c in pair.context
                            if _EXIT.match(c.strip().upper())), None)
            if heading is None or not pair.values:
                continue
            name = _EXIT.match(heading.upper()).group(1)
            g = groups.setdefault(name, ExitDischarge(
                name, page=page, sheet=codes.get(page, f"p{page + 1}"), anchor=heading))
            label, raw = normalise_label(pair.label), pair.values[0]
            if "WIDTH" in label and "PROVIDED" in label:
                g.width_provided_in = parse("inches", raw)
            elif "WIDTH" in label and "REQUIRED" in label:
                g.width_required_in = parse("inches", raw)
            elif "CAPACITY" in label:
                g.capacity = parse("count", raw)
            elif label in ("OCCUPANT LOAD", "OCCUPANTS", "OCCUPANTS SERVED"):
                g.occupant_load = parse("count", raw)
        out.extend(groups[k] for k in sorted(groups))
    return out


_FACTOR = re.compile(r"(\d[\d,]*\.?\d*)\s*(?:SF)?\s*\(?\s*(NET|GROSS)?\s*\)?", re.I)


def _factor(raw: str):
    m = _FACTOR.match((raw or "").strip())
    if not m:
        return None, ""
    return float(m.group(1).replace(",", "")), (m.group(2) or "").lower()


def occupancy_rows(layouts: Dict[int, PageLayout], codes: Dict[int, str]) -> List[OccupancyRow]:
    """Every row of an occupant-load table: a space, its use, area, factor and load.

    A table is recognised by its column headers — an area per occupant (or an
    occupant factor) and an occupant load — not by its title, so an analysis
    titled anything at all is read the same way.
    """
    out: List[OccupancyRow] = []
    for page, layout in sorted(layouts.items()):
        for pair in layout.pairs:
            heads = [normalise_label(pair.header_for(i)) for i in range(len(pair.values))]
            factor_i = next((i for i, h in enumerate(heads)
                             if "PER OCCUPANT" in h or "FACTOR" in h), None)
            load_i = next((i for i, h in enumerate(heads) if h in ("OCCUPANT LOAD", "OCCUPANTS")),
                          None)
            if factor_i is None or load_i is None:
                continue
            if normalise_label(pair.label) in ("TOTAL", "TOTALS"):
                continue
            use_i = next((i for i, h in enumerate(heads) if h in ("USE", "FUNCTION", "OCCUPANCY")), None)
            area_i = next((i for i, h in enumerate(heads) if h in ("AREA", "AREA SF", "NET AREA",
                                                                    "GROSS AREA", "SF")), None)
            factor, basis = _factor(pair.values[factor_i])
            context = tuple(c for c in (*pair.context, pair.heading) if c)
            words = " ".join(context).upper()
            code = ("FFPC" if ("FFPC" in words or "NFPA" in words) else
                    "FBC" if ("FBC" in words or "1004.5" in words) else "")
            out.append(OccupancyRow(
                pair.label.strip(),
                pair.values[use_i].strip() if use_i is not None else "",
                parse("area", pair.values[area_i]) if area_i is not None else None,
                factor, basis, parse("count", pair.values[load_i]), code, context,
                page=page, sheet=codes.get(page, f"p{page + 1}")))
    return out
