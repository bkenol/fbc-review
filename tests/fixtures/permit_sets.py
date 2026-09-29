"""Synthetic permit sets, built to the documented shape of the real ones.

`samples/` is git-ignored — a client PDF is never committed — so the sets the
reference findings in `docs/reference/` were written against cannot live in the
repository. These builders stand in for them: every value below is taken from
those documents, so a test that asserts "107 occupants against the 152 stated"
is asserting the same arithmetic against the same numbers the real sheets carry.

What they reproduce faithfully:

* **ITEC Alico Park** — Group B shell, Type II-B, 15,376 SF in eight tenant
  units, one storey, 26 ft 4 in., NFPA 13, Vult 155 mph, Exposure B, Risk II,
  Lee County, cited throughout to the 7th Edition (2020). **No preserved CAD
  layers**, the code-analysis block pasted in as a picture, and `/Rotate 270`
  on some sheets — the three properties that made the real set return nothing.
* The `MIXED OCCUPANCY? NO` row sitting next to
  `SEPARATED PER TABLE 508.4` (finding H-01), and the `OCCUPANT FACTOR` column
  that counts unit numbers upward (finding H-02).

What they do not reproduce: 35 sheets of real drawing content. Twelve sheets is
enough to exercise every path and keeps the suite quick.
"""
from __future__ import annotations

import pymupdf

# ── ITEC Alico Park, from docs/reference ──────────────────────────────────
ITEC_UNIT_AREAS = [1855, 1441, 2142, 2262, 1545, 1418, 1441, 3272]      # = 15,376
ITEC_TOTAL_SF = sum(ITEC_UNIT_AREAS)
ITEC_STATED_LOAD = 152
#: The column on A-101 headed OCCUPANT FACTOR. It is the unit number.
ITEC_FACTOR_COLUMN = [100, 101, 102, 103, 104, 105, 106, 104]

ITEC_CODE_ROWS = [
    "USE AND OCCUPANCY CLASSIFICATION",
    "OCCUPANCY: BUSINESS / OFFICE, PROFESSIONAL SERVICES",
    "MIXED OCCUPANCY? NO",
    "OCCUPANCY SEPARATION RATING PROVIDED:",
    "MULTIPLE - SEPARATED PER TABLE 508.4",
    "CONSTRUCTION TYPE: II-B",
    "NUMBER OF STORIES: 1",
    "BUILDING HEIGHT: 26'-4\"",
    f"TOTAL BUILDING AREA: {ITEC_TOTAL_SF:,} SF",
    "SPRINKLER SYSTEM: NFPA 13",
    "FINISHED FLOOR ELEV. 24.85' NGVD",
]

#: The declaration a user would give if they read it off their own G-002.
ITEC_DECLARATION = {
    "occupancy_group": "B",
    "mixed_occupancy": False,
    "construction_type": "II-B",
    "building_area_sf": float(ITEC_TOTAL_SF),
    "total_area_sf": float(ITEC_TOTAL_SF),
    "height_ft": 26.33,
    "stories": 1,
    "sprinkler_system": "nfpa13",
    "wind_speed_mph": 155.0,
    "exposure_category": "B",
    "risk_category": "II",
    "zoning": "IPD",
    "jurisdiction": "Lee County, FL",
    "code_edition": "fbc2020",
}


def _titleblock(page: pymupdf.Page, code: str, title: str) -> None:
    """A title block in the bottom-right corner, where `sheet_index` looks."""
    r = page.rect
    x = r.x0 + 0.84 * r.width
    y = r.y0 + 0.82 * r.height
    page.draw_rect(pymupdf.Rect(x - 6, y - 10, r.x1 - 8, r.y1 - 8),
                   color=(0, 0, 0), width=0.8)
    page.insert_text((x, y + 8), "SHEET TITLE", fontsize=6)
    page.insert_text((x, y + 22), title, fontsize=8)
    page.insert_text((x, y + 46), code, fontsize=15, fontname="hebo")


def _linework(page: pymupdf.Page, rows: int = 160, top: float = 40,
              bottom: float = 270) -> None:
    """Enough vector content that the set reads as plotted, not scanned.

    Kept clear of the lower half of the sheet: `find_tables` reads ruled lines,
    and a field of vertical linework laid over a schedule is read as columns.
    Real linework is drawing content and sits where the drawing is.
    """
    for n in range(rows):
        page.draw_line((26 + n * 6, top), (26 + n * 6, bottom))


def _raster(lines, width=760, height=None, size=19, dpi=170) -> pymupdf.Pixmap:
    """Render text to a pixmap, so that on the page it is pixels and not text.

    This is the ITEC failure mode exactly: the sheet is genuinely vector with
    live text on it, and the rows the rules need are a picture.
    """
    height = height or (34 + len(lines) * (size + 9))
    scratch = pymupdf.open()
    page = scratch.new_page(width=width, height=height)
    y = size + 10
    for line in lines:
        page.insert_text((22, y), line, fontsize=size)
        y += size + 9
    pix = page.get_pixmap(dpi=dpi)
    scratch.close()
    return pix


def _occupant_table(page: pymupdf.Page, origin=(60, 310)) -> None:
    """A ruled occupant-load table, the way A-101 carries it.

    The OCCUPANT FACTOR column is the unit number, counting upward, which is the
    error the real set contains.
    """
    x0, y0 = origin
    cols = [0, 90, 190, 320, 430]
    rowh = 21
    header = ["UNIT", "AREA (SF)", "OCCUPANT FACTOR", "OCCUPANT LOAD"]
    body = [[f"{ITEC_FACTOR_COLUMN[i]}", f"{a:,}", f"{ITEC_FACTOR_COLUMN[i]}",
             f"{ITEC_FACTOR_COLUMN[i] // 8}"]
            for i, a in enumerate(ITEC_UNIT_AREAS)]
    body.append(["TOTAL", f"{ITEC_TOTAL_SF:,}", "", f"{ITEC_STATED_LOAD}"])
    rows = [header] + body

    for i in range(len(rows) + 1):                       # horizontal rules
        page.draw_line((x0, y0 + i * rowh), (x0 + cols[-1], y0 + i * rowh),
                       color=(0, 0, 0), width=0.7)
    for c in cols:                                       # vertical rules
        page.draw_line((x0 + c, y0), (x0 + c, y0 + len(rows) * rowh),
                       color=(0, 0, 0), width=0.7)
    for r, row in enumerate(rows):
        for c, cell in enumerate(row):
            page.insert_text((x0 + cols[c] + 5, y0 + r * rowh + 14), cell, fontsize=8)


def itec(raster_tables: bool = True, pages: int = 12, code_rows=None) -> bytes:
    """The ITEC Alico Park set, as plotted (`raster_tables=True`) or as the
    region reader recovers it (`raster_tables=False`).

    The only difference between the two is whether G-2's code-analysis block is
    a picture or live text. That single difference is what stands between a
    review that can read the set's own code data and one that cannot, and it is
    the reason the declaration exists.
    """
    doc = pymupdf.open()
    sheets = [
        ("G-0", "COVER SHEET"), ("G-1", "GENERAL NOTES"),
        ("G-2", "CODE ANALYSIS"), ("G-3", "SITE PLAN"),
        ("A-1", "FLOOR PLAN"), ("A-2", "ELEVATIONS"),
        ("S-1", "STRUCTURAL NOTES"), ("S-2", "FOUNDATION PLAN"),
        ("M-1", "MECHANICAL PLAN"), ("P-1", "PLUMBING PLAN"),
        ("E-1", "ELECTRICAL PLAN"), ("E-2", "PANEL SCHEDULES"),
    ][:pages]

    rows = list(code_rows or ITEC_CODE_ROWS)
    code_pix = _raster(rows) if raster_tables else None

    for i, (code, title) in enumerate(sheets):
        page = doc.new_page(width=1224, height=792)
        _linework(page)
        page.insert_text((40, 560), f"ALICO ITEC PARK, LOTS 20-22   "
                                    f"12250 ITEC PARK DRIVE, FT. MYERS, LEE COUNTY, FLORIDA "
                                    f"33913   1/8\" = 1'-0\"", fontsize=8)

        if code == "G-0":
            page.insert_text((40, 585), "FLORIDA BUILDING CODE, 7TH EDITION (2020)   "
                                        "FFPC 7TH EDITION / NFPA 101 2018   NEC 2017",
                             fontsize=8)
            page.insert_text((40, 600), "100% PERMIT SET 11/12/21", fontsize=8)
        elif code == "G-2":
            if code_pix is not None:
                page.insert_image(pymupdf.Rect(430, 590, 1190, 780), pixmap=code_pix)
            else:
                y = 596
                for row in rows:
                    page.insert_text((430, y), row, fontsize=9)
                    y += 14
        elif code == "G-3":
            page.insert_text((40, 585), "ZONING: IPD   86 PARKING SPACES   2.21 AC   "
                                        "FINISHED FLOOR ELEV. 24.85 NAVD", fontsize=8)
        elif code == "A-1":
            _occupant_table(page)
            page.insert_text((40, 585), f"TOTAL OCCUPANT LOAD: {ITEC_STATED_LOAD}",
                             fontsize=8)
        elif code == "S-1":
            page.insert_text((40, 585), "DESIGN CRITERIA: ASCE 7-16   VULT = 155 MPH   "
                                        "RISK CATEGORY: II   EXPOSURE B   ENCLOSED   "
                                        "GCPI +/-0.18", fontsize=8)
        elif code == "A-2":
            page.insert_text((40, 585), "TYPICAL IMPACT RATED STOREFRONT SYSTEM",
                             fontsize=8)

        _titleblock(page, code, title)
        # Seven of the thirty-five real sheets carry /Rotate 270. Keeping some
        # here means the markup renderer is exercised against rotation, which is
        # what broke it the first time.
        if i in (5, 9):
            page.set_rotation(270)

    buf = doc.tobytes()
    doc.close()
    return buf


def sculpted_like(pages: int = 8) -> bytes:
    """A cleanly plotted tenant fit-out with readable code data.

    Stands in for the Sculpted Hot Pilates set: live vector text, a code data
    block whose rows carry their section citations, and a door schedule. This is
    the set the byte-identity baseline is taken against, because it is the one
    where rules actually fire.
    """
    doc = pymupdf.open()
    sheets = [("G-0", "LIFE SAFETY PLAN"), ("G-1", "CODE ANALYSIS"),
              ("A-1", "FLOOR PLAN"), ("A-2", "DOOR SCHEDULE"),
              ("A-3", "DETAILS"), ("M-1", "MECHANICAL"),
              ("E-1", "ELECTRICAL"), ("E-3", "PANEL SCHEDULE")][:pages]

    for code, title in sheets:
        page = doc.new_page(width=1224, height=792)
        _linework(page)
        page.insert_text((40, 545), "SCULPTED HOT PILATES   1/4\" = 1'-0\"", fontsize=8)

        if code == "G-0":
            rows = [
                "EGRESS",
                "COMMON PATH OF EGRESS TRAVEL (1006.2.1): 50 LF 38'-2\"",
                "MAX TRAVEL DISTANCE (1017.2): 250 LF 69'-4\"",
                "DEAD END CORRIDOR (1020.5): 20 LF 0'-0\"",
                "MIN. CORRIDOR WIDTH (1020.3): 44\" 60\"",
                "AREA: 1,436 SF",
                "RISK CATEGORY: III",
                # The real G-0 states this in its PROJECT DATA block. The
                # fixture left it out, and the egress rules used to pass here
                # only because they assumed Group A-3 when nothing said so.
                "OCCUPANCY: ASSEMBLY (A-3)",
            ]
            y = 580
            for row in rows:
                page.insert_text((40, y), row, fontsize=9)
                y += 15
        elif code == "G-1":
            rows = ["BUILDING CODE ANALYSIS",
                    "OCCUPANT LOAD 70",
                    "SPRINKLERED YES",
                    "TOTAL 1,375 SF"]
            y = 580
            for row in rows:
                page.insert_text((40, y), row, fontsize=9)
                y += 15

        _titleblock(page, code, title)

    buf = doc.tobytes()
    doc.close()
    return buf


#: A code block whose construction type and area put the building near enough to
#: the Table 506.2 limit that a *different* declared construction type changes
#: the answer. Used to exercise dual evaluation, where the ITEC set cannot: on
#: ITEC the one conflicting field is not consumed by any arithmetic rule, so
#: both readings agree, which is the common case and not the interesting one.
DIVERGENCE_CODE_ROWS = [
    "USE AND OCCUPANCY CLASSIFICATION",
    "OCCUPANCY: BUSINESS",
    "MIXED OCCUPANCY? NO",
    "CONSTRUCTION TYPE: II-B",
    "NUMBER OF STORIES: 1",
    "BUILDING HEIGHT: 62'-0\"",
    "TOTAL BUILDING AREA: 40,000 SF",
    "SPRINKLER SYSTEM: NFPA 13",
]

#: The same building, declared as Type V-B. Table 506.2 allows 92,000 SF for
#: Group B / II-B / sprinklered single storey and 36,000 SF for V-B, and
#: Table 504.3 allows 75 ft against 60 ft — so 40,000 SF at 62 ft passes as
#: drawn and fails as declared, on two separate rules.
DIVERGENCE_DECLARATION = {
    "occupancy_group": "B",
    "construction_type": "V-B",
    "building_area_sf": 40000.0,
    "height_ft": 62.0,
    "stories": 1,
    "sprinkler_system": "nfpa13",
}


def divergent(raster_tables: bool = False) -> bytes:
    """A set whose declared construction type changes two Chapter 5 outcomes."""
    return itec(raster_tables=raster_tables, code_rows=DIVERGENCE_CODE_ROWS)


# ── a sheet carrying several views, each at its own scale ─────────────────
#: The layout of a real architectural sheet, and the one `extract/scale.py`
#: could not read until views were segmented: a floor plan, an enlarged plan
#: and a wall section on one page, each with its scale printed under its title.
#: Three true scales, no page-wide one.
MULTIVIEW_VIEWS = [
    ((60, 60, 460, 380), "LIFE SAFETY PLAN", '1/4" = 1\'-0"', 18.0),
    ((560, 60, 900, 300), "ENLARGED RESTROOM PLAN", '1/2" = 1\'-0"', 36.0),
    ((60, 470, 420, 690), "WALL SECTION", '1 1/2" = 1\'-0"', 108.0),
]


def _view(page: pymupdf.Page, box, title: str, scale: str, rows: int = 14,
          oc: int = 0) -> None:
    """One view: a grid of linework with its title and scale printed beneath."""
    x0, y0, x1, y1 = box
    for n in range(rows):
        page.draw_line((x0, y0 + n * (y1 - y0) / rows),
                       (x1, y0 + n * (y1 - y0) / rows), width=0.6)
        page.draw_line((x0 + n * (x1 - x0) / rows, y0),
                       (x0 + n * (x1 - x0) / rows, y1), width=0.6)
    if title:
        page.insert_text((x0, y1 + 16), title, fontsize=9)
    if scale:
        page.insert_text((x0, y1 + 30), scale, fontsize=8)


def multiview(views=None, rotate: int = 0, border: bool = True,
              egress: tuple = None) -> bytes:
    """A single sheet laid out as several separately-scaled views.

    `egress` is a box in the same coordinates; when given, a dashed run is drawn
    inside it on an optional-content group named "EGRESS PATH", which is the
    layer `MEASURE.EGRESS_EXTENT` traces. Its length is chosen by the caller so
    a test can assert the converted feet.
    """
    doc = pymupdf.open()
    page = doc.new_page(width=1224, height=792)
    if border:
        # The sheet border abuts every view; left in the clustering it would
        # join all of them into one.
        page.draw_rect(pymupdf.Rect(18, 18, 1206, 774), color=(0, 0, 0), width=1.2)

    for box, title, scale, _pt in (views or MULTIVIEW_VIEWS):
        _view(page, box, title, scale)

    if egress:
        ocg = doc.add_ocg("EGRESS PATH", on=True)
        ex0, ey, ex1, _ = egress
        # Dashes, the way CAD plots an egress path — the rule merges them back.
        # The gap stays inside r_geometry.JOIN_TOL, or they are not one run.
        x = ex0
        while x < ex1:
            page.draw_line((x, ey), (min(x + 6, ex1), ey), width=1.4, oc=ocg)
            x += 7

    if rotate:
        page.set_rotation(rotate)

    buf = doc.tobytes()
    doc.close()
    return buf


# ── an MEP-only submittal, the shape of SUB1-JSP_Naples ───────────────────
#: The OCCUPANCY CALCULATION table on M.001. Reported through Refine analysis
#: (feedback feac59646e6c) against the real set: no G-series sheet anywhere, no
#: sheet phrasing an area as "BUILDING AREA: n SF", and these rows carrying the
#: only areas the set states. DECL.BUILDING_AREA stood down saying "neither the
#: drawings nor the declaration state this", which is false — the components are
#: printed right there.
MEP_AREA_ROWS = [("LOBBY/RECEPTION", 400), ("STUDIO AREA", 1300)]
MEP_AREA_TOTAL = sum(sf for _label, sf in MEP_AREA_ROWS)


def mep_only(pages: int = 6, area_rows=None, stated_total: bool = False,
             heading: str = "OCCUPANCY CALCULATION",
             risk_category: str = "") -> bytes:
    """A mechanical/plumbing/electrical submittal with no architectural sheets.

    The case the declaration exists to cover, and the case where every read
    scoped to the general sheets finds nothing to read: there is no G series in
    this set at all.
    """
    doc = pymupdf.open()
    sheets = [("M.001", "MECHANICAL COVER"), ("M.101", "MECHANICAL PLAN"),
              ("M.501", "MECHANICAL DETAILS"), ("P.101", "PLUMBING PLAN"),
              ("E.101", "ELECTRICAL PLAN"), ("E.501", "PANEL SCHEDULE")][:pages]

    rows = MEP_AREA_ROWS if area_rows is None else area_rows
    for code, title in sheets:
        page = doc.new_page(width=1224, height=792)
        _linework(page)
        page.insert_text((40, 545), "JSP NAPLES, FL   1/4\" = 1'-0\"", fontsize=8)

        if code == "M.001":
            y = 580
            page.insert_text((40, y), heading, fontsize=9)
            y += 15
            for label, sf in rows:
                page.insert_text((40, y), f"{label}   {sf:,} SQ. FT.", fontsize=9)
                y += 15
            if stated_total:
                page.insert_text((40, y), f"TOTAL   {sum(s for _l, s in rows):,} SQ. FT.",
                                 fontsize=9)
                y += 15
            if risk_category:
                # An MEP cover sheet that carries the project's code summary,
                # which is where this value lives on a single-discipline
                # submittal. Default off: the sets already built on this
                # fixture state no such thing, and they must not start to.
                page.insert_text((40, y), f"RISK CATEGORY: {risk_category}", fontsize=9)

        _titleblock(page, code, title)

    buf = doc.tobytes()
    doc.close()
    return buf
