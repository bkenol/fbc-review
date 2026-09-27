"""What a sheet's text looks like once it is laid out — the shapes, not the meaning.

Every extractor before this package drew its own rectangle on the sheet and
bucketed words on its own y grid, and each one broke on a layout the others
handled (`docs/ENGINE-TEARDOWN.md` §3). These types are the one layout every
reader shares: built once per page, with every box kept, so whatever is read
off a sheet can be pointed at on the sheet.

Coordinates are PyMuPDF's unrotated page space throughout — the space
`get_text()`, `get_drawings()` and `search_for()` all report in, even on a
`/Rotate 270` sheet. Converting to what a viewer draws in is the output layer's
job, and is done in exactly one place (`fbcreview.layout.viewer_rect`).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

Box = Tuple[float, float, float, float]


def union(boxes) -> Box:
    boxes = [b for b in boxes if b]
    return (min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes))


@dataclass(frozen=True)
class Word:
    x0: float
    y0: float
    x1: float
    y1: float
    text: str
    #: PyMuPDF's (block, line) — which text object the word was drawn in. On a
    #: CAD plot that is the drafter's text entity, which is the strongest signal
    #: there is that two words belong together.
    group: Tuple[int, int] = (0, 0)

    @property
    def h(self) -> float:
        return max(self.y1 - self.y0, 0.1)

    @property
    def yc(self) -> float:
        return (self.y0 + self.y1) / 2.0

    @property
    def box(self) -> Box:
        return (self.x0, self.y0, self.x1, self.y1)


@dataclass
class Segment:
    """A run of words printed together: a label, a value, a table cell.

    Two tables printed side by side are separate segments however close their
    baselines are — which is exactly what the old y-bucketing could not say.
    """
    words: List[Word]
    index: int = -1

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)

    @property
    def x0(self) -> float:
        return min(w.x0 for w in self.words)

    @property
    def y0(self) -> float:
        return min(w.y0 for w in self.words)

    @property
    def x1(self) -> float:
        return max(w.x1 for w in self.words)

    @property
    def y1(self) -> float:
        return max(w.y1 for w in self.words)

    @property
    def h(self) -> float:
        hs = sorted(w.h for w in self.words)
        return hs[len(hs) // 2]

    @property
    def yc(self) -> float:
        return (self.y0 + self.y1) / 2.0

    @property
    def box(self) -> Box:
        return (self.x0, self.y0, self.x1, self.y1)


#: How a label and its value were laid out on the sheet.
INLINE = "inline"      # LABEL: value, in one text object
ROW = "row"            # label, then value(s) to its right on the same line
STACKED = "stacked"    # LABEL: on one line, the value directly beneath it
TABLE = "table"        # a ruled table's row, via find_tables


@dataclass
class Pair:
    """A label and what the sheet prints against it.

    `values` is plural because a code-analysis grid prints a required and a
    provided value against one label; `headers` says which column each came
    from (`REQUIRED`, `PROVIDED`), or "" where the sheet names none. `context`
    is the nearest heading above the label in its column — `EXITS/ EGRESS`,
    `EXIT DISCHARGE 1`, `BASIC WIND SPEED` — which is what tells a building's
    occupant load from one exit's.
    """
    label: str
    values: List[str]
    kind: str
    page: int
    label_box: Box
    value_boxes: List[Box] = field(default_factory=list)
    headers: List[str] = field(default_factory=list)
    context: List[str] = field(default_factory=list)
    heading: str = ""

    @property
    def value(self) -> str:
        return self.values[0] if self.values else ""

    @property
    def box(self) -> Box:
        return union([self.label_box, *self.value_boxes])

    @property
    def quote(self) -> str:
        """The pair as printed, label first — what a reviewer would search for."""
        sep = "" if self.kind == INLINE else " "
        mark = ": " if self.kind == INLINE else sep
        return f"{self.label}{mark}{' '.join(self.values)}".strip()

    def header_for(self, i: int) -> str:
        return self.headers[i] if i < len(self.headers) else ""


@dataclass
class PageLayout:
    page: int
    width: float
    height: float
    words: List[Word]
    segments: List[Segment]
    pairs: List[Pair]
    #: The page's text as lines in reading order, for callers that only need a
    #: phrase anywhere on the sheet (`FLORIDA BUILDING CODE 8TH EDITION`).
    lines: List[Segment] = field(default_factory=list)

    def segment_text(self) -> List[str]:
        return [s.text for s in self.segments]

    def find_pairs(self, needle: str) -> List[Pair]:
        n = needle.upper()
        return [p for p in self.pairs if n in p.label.upper()]

    def value_near(self, box: Box, reach: float) -> Optional[Segment]:
        """The segment nearest a box, within `reach` points. For tests and probes."""
        best, dist = None, reach
        for s in self.segments:
            dx = max(s.x0 - box[2], box[0] - s.x1, 0.0)
            dy = max(s.y0 - box[3], box[1] - s.y1, 0.0)
            d = (dx * dx + dy * dy) ** 0.5
            if d <= dist:
                best, dist = s, d
        return best
