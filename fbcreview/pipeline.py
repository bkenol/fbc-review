"""PDF in, findings out. No model in this path."""
from __future__ import annotations
import re
from typing import Dict, List
import pymupdf

from .facts import ProjectFacts, Door, VentilationRow
from .extract.document import sheet_index, page_geometry, ocg_names
from .extract.blocks import code_data_block, labelled_values, normalise, to_feet, to_inches
from .extract.schedules import find_schedule, split_merged_row
from .rules import run_all, RuleResult, registered
from .rules import (r_egress, r_doors, r_mechanical, r_electrical,   # noqa: F401
                    r_crosssheet, r_geometry, r_declaration, r_heightarea,
                    r_occupancy, r_structural, r_code)
from .declaration import ProjectDeclaration

_FTIN = re.compile(r"(\d+)\s*'\s*-\s*(\d+)")


def _leaf_inches(raw: str):
    m = _FTIN.search(raw or "")
    if m:
        return float(m.group(1)) * 12 + float(m.group(2))
    return to_inches(raw)


def build_facts(path: str) -> ProjectFacts:
    doc = pymupdf.open(path)
    text = {p: doc[p].get_text() for p in range(doc.page_count)}
    facts = ProjectFacts(source_path=path, text_by_page=text)
    facts.sheets = sheet_index(doc, text)
    by_code = {s.code: s for s in facts.sheets}
    facts.meta["cad_layers"] = ocg_names(doc)
    facts.meta["native_vector"] = len(facts.meta["cad_layers"]) > 0

    for p in range(doc.page_count):
        facts.geometry[p] = page_geometry(doc, p, text[p])

    # ── code data blocks (unruled) ────────────────────────────────────────
    for code in ("G-0", "G-1"):
        s = by_code.get(code)
        if not s:
            continue
        for header in ("EGRESS", "BUILDING CODE ANALYSIS"):
            for d in code_data_block(doc, s.index, header, code):
                facts.code_data.append(normalise(d))

    # ── ruled schedules ───────────────────────────────────────────────────
    wanted = [("A-2", "DOOR AND FRAME SCHEDULE"), ("M-1", "ROOF TOP UNIT SCHEDULE"),
              ("M-1", "AIR BALANCE"), ("M-1", "OCCUPANT DENSITY"),
              ("E-3", "ELECTRICAL LOAD CALCULATIONS"), ("E-3", "PANEL SCHEDULE")]
    for code, title in wanted:
        s = by_code.get(code)
        if not s:
            continue
        sch = find_schedule(doc, s.index, title, code)
        if sch:
            facts.schedules.append(sch)

    # Occupant-load tables are not on a predictable sheet: a tenant fit-out puts
    # one on the life-safety sheet, a shell puts one on the first floor plan.
    # Sheets are filtered on their own text first, so this costs a find_tables()
    # only where the words actually appear.
    for s in facts.sheets:
        if "OCCUPANT LOAD" not in (text.get(s.index) or "").upper():
            continue
        if any(sch.page == s.index and "OCCUPANT" in sch.name.upper()
               for sch in facts.schedules):
            continue
        sch = find_schedule(doc, s.index, "OCCUPANT LOAD", s.code)
        if sch:
            facts.schedules.append(sch)

    # ── doors ─────────────────────────────────────────────────────────────
    ds = facts.schedule("DOOR AND FRAME SCHEDULE")
    if ds:
        for r in ds.rows:
            num = r.mark.split()[-1] if r.mark else ""
            if not re.match(r"^\d{2,3}[A-Z]?$", num):
                continue
            vals = list(r.fields.values())
            style = vals[1] if len(vals) > 1 else ""
            facts.doors.append(Door(
                number=num, style=style.split()[-1] if style else "",
                width_in=_leaf_inches(vals[2] if len(vals) > 2 else ""),
                height_in=_leaf_inches(vals[3] if len(vals) > 3 else ""),
                thickness_in=to_inches(vals[4] if len(vals) > 4 else "") or None,
                material=vals[5] if len(vals) > 5 else "",
                hardware_set=vals[9] if len(vals) > 9 else "",
                remarks=vals[10] if len(vals) > 10 else "",
                is_existing="EXISTING" in (style or "").upper(),
                sheet=ds.sheet, page=ds.page))

    # ── ventilation totals ────────────────────────────────────────────────
    oa = facts.schedule("OCCUPANT DENSITY")
    if oa:
        for r in oa.rows:
            if r.mark.upper().startswith("TOTAL"):
                nums = re.findall(r"\d[\d,]*\.?\d*", r.mark.replace(",", ""))
                if len(nums) >= 3:
                    facts.meta["area_m1_sf"] = float(nums[0])
                    facts.meta["oa_persons"] = float(nums[1])
                    facts.meta["oa_required_cfm"] = float(nums[2])
            parts = split_merged_row(r)
            if parts:
                for p in parts:
                    v = list(p.fields.values())
                    facts.ventilation.append(VentilationRow(
                        p.mark, _f(v, 1), _f(v, 2), _f(v, 3), _f(v, 4), _f(v, 5), _f(v, 6)))

    # ── scalar project data scraped from the general sheets ───────────────
    g0 = text.get(by_code["G-0"].index, "") if "G-0" in by_code else ""
    g1 = text.get(by_code["G-1"].index, "") if "G-1" in by_code else ""
    m = re.search(r"AREA:?\s*([\d,]+)\s*SF", g0, re.I)
    if m:
        facts.meta["area_g0_sf"] = float(m.group(1).replace(",", ""))
    m = re.search(r"TOTAL\s+([\d,]+)\s*SF", g1, re.I)
    if m:
        facts.meta["area_g1_sf"] = float(m.group(1).replace(",", ""))
    m = re.search(r"RISK\s*CATEGORY:?\s*(I{1,3}V?|\d)", g0, re.I)
    if m:
        facts.meta["risk_category"] = m.group(1)
    if "G-1" in by_code:
        bca = labelled_values(doc, by_code["G-1"].index, "BUILDING CODE ANALYSIS")
        facts.meta["building_code_analysis"] = bca
        for k, v in bca.items():
            ku = k.upper()
            if ku == "OCCUPANT LOAD":
                facts.meta["occupant_load"] = float(re.sub(r"[^\d.]", "", v) or 0) or None
            elif "SPRINKLER" in ku:
                facts.meta["sprinklered"] = v.strip().upper().startswith("Y")
            elif "EGRESS WIDTH FACTOR" in ku:
                facts.meta["stated_capacity_factor"] = float(re.sub(r"[^\d.]", "", v) or 0) or None
    e3 = text.get(by_code["E-3"].index, "") if "E-3" in by_code else ""
    m = re.search(r"ELECTRICAL\s+PANEL[^\n]*\n(?:[^\n]*\n){0,3}?\s*(\d{3,4})\s*A\b", e3, re.I)
    if m:
        facts.meta["panel_rating_a"] = float(m.group(1))
    m = re.search(r"(\d{3,4})\s*KAIC", e3, re.I)
    if m:
        facts.meta["service_kaic"] = float(m.group(1))
    doc.close()
    return facts


def _f(vals, i):
    try:
        return float(re.sub(r"[^\d.\-]", "", vals[i]) or 0) or None
    except Exception:
        return None


def review(path: str, options=None, declaration=None) -> RuleResult:
    """PDF in, findings out.

    `declaration` is a ProjectDeclaration — the answers the applicant gave before
    uploading. Omitting it reproduces the review exactly as it ran before the
    declaration existed.
    """
    return run_all(build_facts(path), options, declaration)
