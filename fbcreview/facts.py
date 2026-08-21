"""The project fact model.

This is the contract between extraction and rules.  Extraction is allowed to be
messy, per-vendor and occasionally model-assisted; rules see only this typed,
normalised structure and are therefore pure functions with no model in the loop.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
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

    def by_mark(self, mark: str) -> Optional[ScheduleRow]:
        for r in self.rows:
            if r.mark.strip().upper() == mark.strip().upper():
                return r
        return None


@dataclass
class PageGeometry:
    page: int
    scale_pt_per_ft: Evidence
    layers: Dict[str, int] = field(default_factory=dict)   # layer name -> path count


@dataclass
class ProjectFacts:
    source_path: str
    sheets: List[Sheet] = field(default_factory=list)
    code_data: List[CodeDatum] = field(default_factory=list)
    doors: List[Door] = field(default_factory=list)
    ventilation: List[VentilationRow] = field(default_factory=list)
    schedules: List[Schedule] = field(default_factory=list)
    geometry: Dict[int, PageGeometry] = field(default_factory=dict)
    text_by_page: Dict[int, str] = field(default_factory=dict)
    meta: Dict[str, Any] = field(default_factory=dict)

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
