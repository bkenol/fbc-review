"""Sheet identification and layer inventory. Deterministic."""
from __future__ import annotations
import re
from typing import Dict, List
import pymupdf
from ..facts import Sheet, PageGeometry
from .scale import page_scale

_SHEET = re.compile(r"^([GACSMEPFL])-?(\d{1,2}[A-Z]?)$")
_DATEISH = re.compile(r"^\s*\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}\s*$")


def sheet_index(doc: pymupdf.Document, text_by_page: Dict[int, str]) -> List[Sheet]:
    """Read each page's own title block. The sheet number is the last short
    token matching a discipline-number pattern; the title precedes it."""
    sheets: List[Sheet] = []
    for pno in range(doc.page_count):
        # the sheet number lives in the title block: bottom-right corner of the sheet
        r = doc[pno].rect
        tb = pymupdf.Rect(r.x0 + 0.82 * r.width, r.y0 + 0.78 * r.height, r.x1, r.y1)
        lines = [l.strip() for l in doc[pno].get_textbox(tb).split("\n") if l.strip()]
        if not lines:
            lines = [l.strip() for l in text_by_page[pno].split("\n") if l.strip()]
        code, title = "", ""
        for i in range(len(lines) - 1, -1, -1):
            m = _SHEET.match(lines[i].replace(" ", ""))
            if m:
                code = f"{m.group(1)}-{m.group(2)}"
                # The sheet title is the run of lines following a "SHEET TITLE"
                # marker. Title blocks also carry DATE:, SCALE:, DRAWN BY: and
                # friends — anything with a colon is a field label, not the title.
                if "SHEET TITLE" in [l.upper() for l in lines]:
                    k = [l.upper() for l in lines].index("SHEET TITLE")
                    parts = []
                    for l in lines[k + 1:]:
                        u = l.upper()
                        if (u in ("SHEET NUMBER", "SHEET TITLE") or ":" in l
                                or _DATEISH.match(l) or _SHEET.match(l.replace(" ", ""))):
                            if parts:
                                break
                            continue
                        parts.append(l)
                        if len(parts) >= 3:
                            break
                    title = " ".join(parts)
                if not title:
                    for j in range(i - 1, max(-1, i - 8), -1):
                        l = lines[j]
                        if l.upper() in ("SHEET NUMBER", "SHEET TITLE") or ":" in l or len(l) <= 2:
                            continue
                        title = l
                        break
                break
        sheets.append(Sheet(pno, code or f"p{pno+1}", title, (code or "?")[0]))
    return sheets


def page_geometry(doc: pymupdf.Document, pno: int, text: str) -> PageGeometry:
    layers: Dict[str, int] = {}
    for p in doc[pno].get_drawings():
        lay = p.get("layer")
        if lay:
            layers[lay] = layers.get(lay, 0) + 1
    return PageGeometry(pno, page_scale(doc, pno, text), layers)


def ocg_names(doc: pymupdf.Document) -> List[str]:
    """Original CAD layer names preserved as PDF optional content groups."""
    return sorted({v["name"] for v in doc.get_ocgs().values()})
