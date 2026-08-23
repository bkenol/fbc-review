"""Chapter 5 — general building heights and areas, and Table 601.

These are the checks the Project Declaration unlocks. Every one is a lookup and
a comparison: construction type, occupancy, sprinkler status, and one number.
None of them needs a single line of drawing geometry, which is exactly why they
still run on a set whose code-analysis block was pasted in as a picture.
"""
from __future__ import annotations

from . import rule, Finding, RuleResult
from ..codes import fbc2023 as C
from ..confidence import Abstention
from ..facts import ProjectFacts
from ._context import basis, feet_inches, need, show, source_phrase, where


@rule("HEIGHT_AREA.TABLE_504_HEIGHT")
def table_504_height(f: ProjectFacts, out: RuleResult):
    v = need(f, out, "HEIGHT_AREA.TABLE_504_HEIGHT",
             "occupancy_group", "construction_type", "sprinkler_system", "height_ft")
    if v is None:
        return
    group, ctype = v["occupancy_group"], v["construction_type"]
    sprinklered = v["sprinkler_system"] in ("NFPA13", "NFPA13R", "YES")
    height = float(v["height_ft"])

    limit = C.height_limit_ft(group, ctype, sprinklered)
    if limit is None:
        out.abstentions.append(Abstention(
            "HEIGHT_AREA.TABLE_504_HEIGHT",
            "Table 504.3 row not carried in this build's corpus",
            detail=f"Group {group}, Type {ctype}. Heights are populated for Groups "
                   f"{', '.join(sorted(C.height_groups_covered()))} only."))
        return

    page, sheet = where(f, "height_ft", "construction_type", "occupancy_group")
    b = basis(f, "occupancy_group", "construction_type", "sprinkler_system", "height_ft")
    limit_txt = "unlimited" if limit == C.UNLIMITED else f"{limit:g} ft"
    over = height > limit
    out.findings.append(Finding(
        "H-504H" if over else "V-504H", "HEIGHT_AREA.TABLE_504_HEIGHT",
        "OPEN" if over else "PASS", "CRITICAL" if over else "VERIFIED",
        "Height and area", page, sheet, "BUILDING HEIGHT",
        f"Building height {feet_inches(height)} exceeds the {limit_txt} Table 504.3 allows"
        if over else f"Building height is within the {limit_txt} Table 504.3 allows",
        "Building height against Table 504.3 for the construction type, occupancy group and "
        "sprinkler status this review was given.",
        f"Type {ctype} ({source_phrase(f, 'construction_type')}), Group {group}, "
        f"{'with' if sprinklered else 'without'} a sprinkler system the code credits: "
        f"Table 504.3 allows {limit_txt}. The building is {feet_inches(height)} "
        f"({source_phrase(f, 'height_ft')})."
        + (f" That is {height - limit:.1f} ft over." if over else " Inside the limit."),
        "FBC-B 504.3 · Table 504.3",
        "Reduce the height, change construction type, or justify a 504 exception."
        if over else "None.", basis=b))


@rule("HEIGHT_AREA.TABLE_504_STORIES")
def table_504_stories(f: ProjectFacts, out: RuleResult):
    v = need(f, out, "HEIGHT_AREA.TABLE_504_STORIES",
             "occupancy_group", "construction_type", "sprinkler_system", "stories")
    if v is None:
        return
    group, ctype = v["occupancy_group"], v["construction_type"]
    sprinklered = v["sprinkler_system"] in ("NFPA13", "NFPA13R", "YES")
    stories = int(float(v["stories"]))

    limit = C.stories_limit(group, ctype, sprinklered)
    if limit is None:
        out.abstentions.append(Abstention(
            "HEIGHT_AREA.TABLE_504_STORIES",
            "Table 504.4 row not carried in this build's corpus",
            detail=f"Group {group}, Type {ctype}."))
        return

    page, sheet = where(f, "stories", "construction_type", "occupancy_group")
    b = basis(f, "occupancy_group", "construction_type", "sprinkler_system", "stories")
    limit_txt = "unlimited" if limit == C.UNLIMITED else f"{limit:g}"
    over = stories > limit
    out.findings.append(Finding(
        "H-504S" if over else "V-504S", "HEIGHT_AREA.TABLE_504_STORIES",
        "OPEN" if over else "PASS", "CRITICAL" if over else "VERIFIED",
        "Height and area", page, sheet, "NUMBER OF STORIES",
        f"{stories} storeys exceeds the {limit_txt} Table 504.4 allows" if over else
        f"{stories} storey{'s' if stories != 1 else ''} is within the {limit_txt} "
        f"Table 504.4 allows",
        "Storey count against Table 504.4 for the construction type, occupancy group and "
        "sprinkler status this review was given.",
        f"Table 504.4, Group {group}, Type {ctype}, "
        f"{'sprinklered' if sprinklered else 'non-sprinklered'}: {limit_txt} storeys. "
        f"The set has {stories} ({source_phrase(f, 'stories')}).",
        "FBC-B 504.4 · Table 504.4",
        "Reduce the storey count or change construction type." if over else "None.",
        basis=b))


@rule("HEIGHT_AREA.TABLE_506_AREA")
def table_506_area(f: ProjectFacts, out: RuleResult):
    v = need(f, out, "HEIGHT_AREA.TABLE_506_AREA",
             "occupancy_group", "construction_type", "sprinkler_system", "building_area_sf")
    if v is None:
        return
    group, ctype = v["occupancy_group"], v["construction_type"]
    sprinklered = v["sprinkler_system"] in ("NFPA13", "NFPA13R", "YES")
    area = float(v["building_area_sf"])
    from ..reconcile import value_of
    stories = value_of(f, "stories")

    limit = C.area_limit_sf(group, ctype, sprinklered, int(stories) if stories else None)
    if limit is None:
        out.abstentions.append(Abstention(
            "HEIGHT_AREA.TABLE_506_AREA",
            "Table 506.2 row not carried in this build's corpus",
            detail=f"Group {group}, Type {ctype}."))
        return

    page, sheet = where(f, "building_area_sf", "construction_type", "occupancy_group")
    b = basis(f, "occupancy_group", "construction_type", "sprinkler_system", "building_area_sf")
    limit_txt = "unlimited" if limit == C.UNLIMITED else f"{limit:,.0f} SF"
    column = ("NS" if not sprinklered
              else "S1 (single storey)" if stories and int(stories) <= 1
              else "SM (multi-storey)")
    over = limit != C.UNLIMITED and area > limit
    out.findings.append(Finding(
        "H-506" if over else "V-506", "HEIGHT_AREA.TABLE_506_AREA",
        "OPEN" if over else "PASS", "CRITICAL" if over else "VERIFIED",
        "Height and area", page, sheet, "BUILDING AREA",
        f"Floor area {area:,.0f} SF exceeds the {limit_txt} Table 506.2 allows" if over else
        f"Floor area is within the {limit_txt} Table 506.2 allows",
        "Area per storey against Table 506.2 for the construction type, occupancy group and "
        "sprinkler status, before any frontage increase under 506.3.",
        f"Table 506.2, Group {group}, Type {ctype}, column {column}: {limit_txt}. "
        f"The largest floor is {area:,.0f} SF ({source_phrase(f, 'building_area_sf')})."
        + (f" That is {area - limit:,.0f} SF over before frontage." if over else
           " Inside the limit; no frontage increase is needed to make it work."),
        "FBC-B 506.2 · Table 506.2",
        "Take the 506.3 frontage increase, add a fire wall, or change construction type."
        if over else "None.", basis=b))


@rule("FIRE.TABLE_601")
def table_601(f: ProjectFacts, out: RuleResult):
    v = need(f, out, "FIRE.TABLE_601", "construction_type")
    if v is None:
        return
    ctype = v["construction_type"]
    row = C.table_601(ctype)
    if row is None:
        out.abstentions.append(Abstention(
            "FIRE.TABLE_601", "Table 601 row not carried in this build's corpus",
            detail=f"Type {ctype}."))
        return

    labels = C.table_601_labels()
    parts = []
    for key, label in labels.items():
        val = row.get(key)
        if val == "HT":
            parts.append(f"{label} heavy timber per 2304.11")
        else:
            parts.append(f"{label} {val:g} hr" if val else f"{label} 0 hr")
    unrated = all(row.get(k) == 0 for k in labels)

    page, sheet = where(f, "construction_type")
    b = basis(f, "construction_type")
    # A set that says "non-rated" and a Table 601 row of all zeros agree; a set
    # that says non-rated while the table demands hours does not.
    claims_nonrated = f.text_contains("NON-RATED") or f.text_contains("NON RATED")
    mismatch = claims_nonrated and not unrated
    out.findings.append(Finding(
        "H-601" if mismatch else "V-601", "FIRE.TABLE_601",
        "OPEN" if mismatch else "PASS", "HIGH" if mismatch else "VERIFIED",
        "Fire protection", page, sheet, "CONSTRUCTION TYPE",
        f"Type {ctype} is described as non-rated, but Table 601 requires rated assemblies"
        if mismatch else
        f"Table 601 ratings for Type {ctype} are consistent with the set",
        "The fire-resistance ratings Table 601 requires for the declared construction type, "
        "against what the set says about rating.",
        f"Type {ctype} ({source_phrase(f, 'construction_type')}) requires: "
        + "; ".join(parts) + ". "
        + ("The set describes the structure as non-rated, which Table 601 does not permit "
           "for this type." if mismatch else
           ("All elements are 0 hr, so a non-rated description is correct."
            if unrated else "Exterior non-bearing walls are a Table 602 question — fire "
                            "separation distance is not a declaration field, so that column "
                            "is not checked here.")),
        "FBC-B 601 · Table 601",
        "Reconcile the stated rating with Table 601, or change construction type."
        if mismatch else "None.", basis=b))
