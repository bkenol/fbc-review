"""The layout layer: one model of each page's text, shared by every reader.

See `docs/ARCHITECTURE-V2.md` §3.2. `page_layout()` is the entry point;
`viewer_rect()` is the one place a box leaves PyMuPDF's unrotated page space
for the rotated space a PDF viewer draws in.
"""
from __future__ import annotations

from typing import Optional, Sequence

import pymupdf

from .build import page_layout, page_words, segments_of, split_inline, value_like
from .model import (INLINE, ROW, STACKED, TABLE, Box, PageLayout, Pair, Segment,
                    Word, union)

__all__ = ["page_layout", "page_words", "segments_of", "split_inline", "value_like",
           "PageLayout", "Pair", "Segment", "Word", "Box", "union", "viewer_rect",
           "INLINE", "ROW", "STACKED", "TABLE"]


def viewer_rect(page: pymupdf.Page, box: Optional[Sequence[float]]) -> Optional[list]:
    """A box in the space pdf.js draws a page in at scale 1.

    PyMuPDF reports text and search hits in the page's *unrotated* space; pdf.js
    lays the page out with `/Rotate` applied, origin top-left. On an upright
    sheet they are the same numbers. On a `/Rotate 270` sheet they are not, and
    a box sent without this conversion lands on the wrong part of the sheet.
    """
    if not box:
        return None
    r = pymupdf.Rect(box) * page.rotation_matrix
    r.normalize()
    return [round(r.x0, 1), round(r.y0, 1), round(r.x1, 1), round(r.y1, 1)]
