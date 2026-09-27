"""The project fact model.

This is the contract between extraction and rules.  Extraction is allowed to be
messy, per-vendor and occasionally model-assisted; rules see only this typed,
normalised structure and are therefore pure functions with no model in the loop.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
from .confidence import Evidence


@dataclass
class Sheet:
    index: int                       # 0-based page index
    code: str                        # "G-0", "M-1"
    title: str                       # "COVER SHEET"
    discipline: str                  # G / A / M / E / P


@dataclass
class CodeDatum:
    """One row of a code-data block: a cited section with a required and a
    provided value.  Keyed by the CITATION, not the label, because the section
    number is by far the most stable token on any code data block."""
    section: str                     # "1006.2.1"
    label: str                       # "COMMON PATH OF TRAVEL"
    required_raw: str
    provided_raw: str
    required: Optional[float] = None  # normalised to feet / inches / count
    provided: Optional[float] = None
    unit: str = ""
    sheet: str = ""
    page: int = 0
    anchor: str = ""                 # search string that locates it on the page
    #: Provenance a finding must repeat — "read by AI and verified on G-0" —
    #: when the row was located only by the AI reader. Empty for a block row.
    note: str = ""
    #: Where the row is printed, unrotated page points, when it is known.
    box: Optional[Tuple[float, float, float, float]] = None


@dataclass
class Door:
    number: str
    style: str
    width_in: Optional[float]
    height_in: Optional[float]
    thickness_in: Optional[float]
    material: str = ""
    hardware_set: str = ""
    remarks: str = ""
    is_existing: bool = False
    sheet: str = ""
    page: int = 0


@dataclass
class VentilationRow:
    room: str
    area_sf: Optional[float]
    density_per_1000: Optional[float]
    rp_cfm_person: Optional[float]
    ra_cfm_sf: Optional[float]
    persons: Optional[float]
    total_cfm: Optional[float]


@dataclass
class ExitDischarge:
    """One exit's own capacity statement, read from a heading and the rows under it:

        EXIT DISCHARGE 1:
          WIDTH REQUIRED:     32"
          WIDTH PROVIDED:     72"
          OCCUPANT CAPACITY:  360
          OCCUPANT LOAD:      35
    """
    name: str                                  # "EXIT DISCHARGE 1"
    width_required_in: Optional[float] = None
    width_provided_in: Optional[float] = None
    capacity: Optional[float] = None           # occupants the sheet says the width serves
    occupant_load: Optional[float] = None      # occupants assigned to this exit
    page: int = 0
    sheet: str = ""
    anchor: str = ""                           # the heading, as printed
    box: Optional[Tuple[float, float, float, float]] = None


@dataclass
class OccupancyRow:
    """One space in an occupant-load table: `MAT STUDIO | ASSEMBLY | 994 SF | 15 (NET) | 67`."""
    space: str
    use: str
    area_sf: Optional[float]
    factor: Optional[float]                    # square feet per occupant
    basis: str                                 # "net" | "gross" | ""
    load: Optional[float]
    #: Which code's table the row sits in, from its headings: "FBC" for a
    #: Table 1004.5 analysis, "FFPC" for the fire prevention code's (NFPA 101).
    code: str
    #: The table's heading and the lines over the row, as printed — where a set
    #: names the function ("ASSEMBLY - EXERCISE ROOMS WITHOUT EQUIPMENT").
    context: Tuple[str, ...] = ()
    page: int = 0
    sheet: str = ""
    box: Optional[Tuple[float, float, float, float]] = None


@dataclass
class PlumbingCount:
    """A plumbing fixture calculation as a set states it:

        ASSEMBLY OCCUPANCY: 70
        WC: 1/125 MALE, 1/65 FEMALE     LAV: 1/200     DF: 1/500     SERVICE SINK: 1
        TOTAL REQUIRED ...              TOTAL PROVIDED ...
    """
    occupant_load: Optional[float]
    #: Occupants per fixture, as stated: "wc" -> (male, female), "lav" likewise;
    #: "drinking_fountain" -> (n, n).
    ratios: Dict[str, Tuple[Optional[float], Optional[float]]] = field(default_factory=dict)
    service_sinks: Optional[float] = None
    #: Fixture counts the set states as required and as provided, by type
    #: ("wc", "lav", "drinking_fountain", "service_sink").
    required: Dict[str, float] = field(default_factory=dict)
    provided: Dict[str, float] = field(default_factory=dict)
    #: Provided fixtures the set describes as unisex (single-user rooms).
    unisex: bool = False
    page: int = 0
    sheet: str = ""
    anchor: str = ""
    box: Optional[Tuple[float, float, float, float]] = None


@dataclass
class CeilingTag:
    """A ceiling-height tag on a reflected ceiling plan — `A.F.F.` over `+10'-0"`,
    or `B/O BAR JOIST` over `+14'-8"` where the ceiling is open to structure."""
    height_ft: float
    raw: str                                   # the height as printed
    reference: str                             # what it is measured to, as printed
    page: int = 0
    sheet: str = ""
    box: Optional[Tuple[float, float, float, float]] = None


@dataclass
class ScheduleRow:
    mark: str
    fields: Dict[str, str] = field(default_factory=dict)

    def num(self, key: str) -> Optional[float]:
        import re
        v = self.fields.get(key, "")
        m = re.search(r"-?\d[\d,]*\.?\d*", str(v).replace(",", ""))
        return float(m.group()) if m else None


@dataclass
class Schedule:
    name: str                        # "ROOF TOP UNIT SCHEDULE"
    sheet: str
    page: int
    columns: List[str]
    rows: List[ScheduleRow]
    anchor: str = ""
    #: Where the table sits, unrotated page points. Lets a reader re-extract it
    #: on its own, which separates rows a whole-page extraction merges.
    bbox: Optional[Tuple[float, float, float, float]] = None

    def by_mark(self, mark: str) -> Optional[ScheduleRow]:
        for r in self.rows:
            if r.mark.strip().upper() == mark.strip().upper():
                return r
        return None


@dataclass
class ViewScale:
    """One drawing view on a sheet, and the scale that governs that view.

    A sheet is several drawings at several scales.  `PageGeometry.scale_pt_per_ft`
    can only speak for the whole page and therefore abstains whenever a sheet
    prints more than one scale, which is most architectural sheets.  This says
    which box on the page a scale applies to, so geometry inside that box can be
    measured while the rest of the sheet stays unmeasured.
    """
    rect: Tuple[float, float, float, float]   # unrotated PDF points, x0 y0 x1 y1
    scale: Evidence
    paths: int = 0

    def contains(self, other: Tuple[float, float, float, float],
                 slack: float = 2.0) -> bool:
        return (other[0] >= self.rect[0] - slack and other[1] >= self.rect[1] - slack
                and other[2] <= self.rect[2] + slack and other[3] <= self.rect[3] + slack)

    def overlaps(self, other: Tuple[float, float, float, float]) -> bool:
        return (self.rect[0] <= other[2] and other[0] <= self.rect[2]
                and self.rect[1] <= other[3] and other[1] <= self.rect[3])


@dataclass
class PageGeometry:
    page: int
    scale_pt_per_ft: Evidence
    layers: Dict[str, int] = field(default_factory=dict)   # layer name -> path count
    views: List[ViewScale] = field(default_factory=list)

    @property
    def scale_resolved(self) -> bool:
        """Did any scale resolve on this page — page-wide or for one view?"""
        return bool(self.scale_pt_per_ft) or any(bool(v.scale) for v in self.views)

    def scale_for(self, rect: Tuple[float, float, float, float]) -> Evidence:
        """The scale governing a box of geometry, or an abstention saying why not.

        A page-wide scale governs everything on the page.  Failing that the box
        has to sit wholly inside one view: geometry spanning two views is drawn
        at two scales and there is no single number to convert it at, which is a
        refusal rather than an average.
        """
        if self.scale_pt_per_ft:
            return self.scale_pt_per_ft

        src = f"page {self.page + 1}"
        if not self.views:
            return self.scale_pt_per_ft

        holding = [v for v in self.views if v.contains(rect)]
        if len(holding) == 1:
            return holding[0].scale
        if len(holding) > 1:
            resolved = {v.scale.value for v in holding if v.scale}
            if len(resolved) == 1:
                return next(v.scale for v in holding if v.scale)
            return Evidence.abstain(
                src, f"this geometry sits inside {len(holding)} nested views and they do "
                     f"not agree on a scale", self.page)

        touching = [v for v in self.views if v.overlaps(rect)]
        if len(touching) > 1:
            return Evidence.abstain(
                src, f"this geometry spans {len(touching)} views drawn at different scales; "
                     f"no single conversion applies to it", self.page)
        if touching:
            return Evidence.abstain(
                src, "this geometry runs outside the bounds of the view it starts in, so the "
                     "view's scale cannot be assumed to govern all of it", self.page)
        return Evidence.abstain(
            src, "this geometry falls outside every view the sheet was segmented into",
            self.page)


@dataclass
class ProjectFacts:
    source_path: str
    sheets: List[Sheet] = field(default_factory=list)
    code_data: List[CodeDatum] = field(default_factory=list)
    doors: List[Door] = field(default_factory=list)
    ventilation: List[VentilationRow] = field(default_factory=list)
    discharges: List[ExitDischarge] = field(default_factory=list)
    occupancy_rows: List[OccupancyRow] = field(default_factory=list)
    plumbing: Optional[PlumbingCount] = None
    ceilings: List[CeilingTag] = field(default_factory=list)
    schedules: List[Schedule] = field(default_factory=list)
    geometry: Dict[int, PageGeometry] = field(default_factory=dict)
    text_by_page: Dict[int, str] = field(default_factory=dict)
    meta: Dict[str, Any] = field(default_factory=dict)
    #: `fbcreview.factstore.FactStore` — every reading of every fact, with where
    #: it was read and by what. `None` only for facts built by hand in tests.
    store: Any = None

    # ---- convenience lookups used by rules -------------------------------
    def datum(self, section: str) -> Optional[CodeDatum]:
        for c in self.code_data:
            if c.section == section:
                return c
        return None

    def schedule(self, name_contains: str) -> Optional[Schedule]:
        n = name_contains.upper()
        for s in self.schedules:
            if n in s.name.upper():
                return s
        return None

    def sheet_code(self, page: int) -> str:
        for s in self.sheets:
            if s.index == page:
                return s.code
        return f"p{page+1}"

    def text_contains(self, needle: str) -> bool:
        n = needle.upper()
        return any(n in t.upper() for t in self.text_by_page.values())
