"""Sheet identification and layer inventory. Deterministic."""
from __future__ import annotations
import re
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

import pymupdf

from ..confidence import Abstention
from ..facts import PageGeometry, ProjectFacts, Sheet
from ..rules import Finding, RuleResult, rule
from .scale import page_scale

#: A sheet number: a discipline prefix, an optional separator, a number.
#:
#: Three digits, because National CAD Standard numbering — `G-001`, `A-101`,
#: `S-501` — is the most common commercial convention. A dot as well as a dash,
#: because plenty of sets number `M.101`, `E.202` or `AA.101.1`. Up to three
#: letters, because `AA`, `AG`, `AD`, `AR`, `FP`, `FA` and `LS` are disciplines
#: too, and so is `T`.
_SHEET = re.compile(r"^([A-Z]{1,3})[-.]?(\d{1,3}(?:\.\d{1,2})?[A-Z]?)$")

#: Widening the pattern on its own is **not** safe. A drawing body is full of
#: tokens shaped exactly like a sheet number — `PT-1`, `EF-3`, `R-19`, `B13`,
#: `US26D` — so the prefix is gated against the disciplines a title block
#: actually uses. This gate and the position anchor below are what make the
#: wider pattern safe; neither is sufficient alone.
_DISCIPLINES = frozenset(
    "G GN T C L A AA AG AD AR S M E P FP FA LS ID K EL SP".split())

_DATEISH = re.compile(r"^\s*\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}\s*$")

#: A position on the sheet, as a fraction of the media box rounded to a tenth.
Cell = Tuple[float, float]

#: How far around the anchor to read for the sheet title. The title sits within
#: a couple of inches of the number — directly above it, or under a "SHEET
#: TITLE" label — so this window covers both without reaching into the drawing.
#: Fractions of the sheet, so it scales from a letter page to a 24x36 sheet.
_PAD_LEFT, _PAD_RIGHT, _PAD_Y = 0.08, 0.22, 0.12

#: Above this share of unread sheet numbers the review says so out loud rather
#: than quietly renumbering the set p1, p2, p3…
_COVERAGE_FLOOR = 0.20


def sheet_number(token: str) -> str:
    """The sheet number this token carries, or `""` if it is not one.

    The separator is reported as drawn — `M.101` stays `M.101` — because the
    number in the finding has to be the number on the sheet. A token with no
    separator at all is written with a dash (`G0` -> `G-0`), which is what this
    reader has always done.
    """
    t = token.strip().upper().replace(" ", "")
    m = _SHEET.match(t)
    if not m or m.group(1) not in _DISCIPLINES:
        return ""
    sep = "." if t[len(m.group(1)):].startswith(".") else "-"
    return f"{m.group(1)}{sep}{m.group(2)}"


def _discipline(code: str) -> str:
    """`AG.001` is an AG sheet, not an A sheet."""
    return re.split(r"[-.]", code)[0] if code else "?"


def _cells(page: pymupdf.Page) -> Dict[Cell, str]:
    """Every sheet-number-shaped token on the page, keyed by where it sits.

    Position is a fraction of `page.mediabox` rounded to one decimal, which is
    coarse enough that a title block drawn a few points off between sheets still
    buckets together, and fine enough that the drawing body does not collide
    with it.

    Positions are deliberately **not** normalised for `page.rotation`.
    `get_text("words")` already reports media-box coordinates, so a `/Rotate
    270` sheet buckets alongside its upright neighbours; normalising was tried
    against four real sets and changed no result on any of them.
    """
    mb = page.mediabox
    w, h = mb.width, mb.height
    found: Dict[Cell, str] = {}
    if w <= 0 or h <= 0:
        return found
    for x0, y0, _x1, _y1, word, *_ in page.get_text("words"):
        code = sheet_number(word)
        if code:
            # First in reading order wins its cell, so a page carrying two
            # candidates in one cell always reads the same way.
            found.setdefault((round(x0 / w, 1), round(y0 / h, 1)), code)
    return found


def _anchor(cells: List[Dict[Cell, str]]) -> Optional[Cell]:
    """Where this set's title block is: the cell that recurs on the most pages.

    The title block is wherever it consistently is. Bottom-right is the common
    case, bottom-left is not rare, and a set that puts general notes in the
    bottom-right corner will hand back text that is real but is not the sheet
    number — which is exactly what assuming a corner cost.
    """
    pages: Dict[Cell, int] = defaultdict(int)
    for page_cells in cells:
        for cell in page_cells:
            pages[cell] += 1
    if not pages:
        return None
    # Most pages wins. Ties break on position: arbitrary, but fixed, so the
    # same set always reads the same way.
    return max(pages, key=lambda c: (pages[c], -c[0], -c[1]))


def _region(page: pymupdf.Page, anchor: Cell) -> pymupdf.Rect:
    mb = page.mediabox
    ax, ay = anchor
    return pymupdf.Rect(max(0.0, ax - _PAD_LEFT) * mb.width,
                        max(0.0, ay - _PAD_Y) * mb.height,
                        min(1.0, ax + _PAD_RIGHT) * mb.width,
                        min(1.0, ay + _PAD_Y) * mb.height)


def _title_block(page: pymupdf.Page, anchor: Optional[Cell], text: str) -> List[str]:
    """The title block's own lines, read around the anchor."""
    lines: List[str] = []
    if anchor is not None:
        lines = [l.strip() for l in page.get_textbox(_region(page, anchor)).split("\n")
                 if l.strip()]
    if not lines:
        lines = [l.strip() for l in text.split("\n") if l.strip()]
    return lines


def _code_line(lines: List[str], code: str) -> Optional[int]:
    """Where the sheet number sits among the title block's lines.

    The line carrying the number the anchor found, or — when the anchor found
    nothing on this page — the last sheet-number-shaped line, which is what this
    reader looked for before it knew where the title block was.
    """
    last = None
    for i in range(len(lines) - 1, -1, -1):
        c = sheet_number(lines[i])
        if not c:
            continue
        if c == code:
            return i
        if last is None:
            last = i
    return last


def _title(lines: List[str], code: str) -> str:
    """The sheet title, read from the lines around the sheet number.

    The title is the run of lines following a "SHEET TITLE" marker. Title blocks
    also carry DATE:, SCALE:, DRAWN BY: and friends — anything with a colon is a
    field label, not the title.
    """
    i = _code_line(lines, code)
    if i is None:
        return ""
    title = ""
    upper = [l.upper() for l in lines]
    if "SHEET TITLE" in upper:
        k = upper.index("SHEET TITLE")
        parts: List[str] = []
        for l in lines[k + 1:]:
            u = l.upper()
            if (u in ("SHEET NUMBER", "SHEET TITLE") or ":" in l
                    or _DATEISH.match(l) or sheet_number(l)):
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
    return title


def sheet_index(doc: pymupdf.Document, text_by_page: Dict[int, str]) -> List[Sheet]:
    """Read each page's own title block.

    The sheet number is found by anchor scan rather than by assuming a corner:
    every sheet-number-shaped token in the set is bucketed by its position on
    the page, the bucket that recurs on the most pages is taken to be the title
    block, and each page's number is then the token sitting in that bucket. A
    page with no token there keeps its page number, and `sheet_numbers_read`
    below reports that rather than letting it pass unnoticed.
    """
    cells = [_cells(doc[pno]) for pno in range(doc.page_count)]
    anchor = _anchor(cells)
    sheets: List[Sheet] = []
    for pno in range(doc.page_count):
        code = cells[pno].get(anchor, "") if anchor is not None else ""
        lines = _title_block(doc[pno], anchor, text_by_page.get(pno, ""))
        sheets.append(Sheet(pno, code or f"p{pno+1}", _title(lines, code),
                            _discipline(code)))
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


@rule("DOC.SHEET_NUMBERS")
def sheet_numbers_read(f: ProjectFacts, out: RuleResult) -> None:
    """Report the identification gate instead of failing silently.

    A set whose sheet numbers cannot be read is numbered p1, p2, p3…, every rule
    that references a specific sheet stands down for want of a sheet reference,
    and the user is handed a review with no findings — indistinguishable from a
    clean set. That is the worst failure mode a compliance tool has, so it is
    reported as a finding the user reads rather than as a log line nobody sees.

    The rule lives here, beside the identification it reports on, because
    `fbcreview/extract` owns sheet numbering. It is still what every other rule
    is: a pure function of `ProjectFacts`, registered the same way, with no
    model and no I/O in it.
    """
    total = len(f.sheets)
    if not total:
        out.abstentions.append(Abstention(
            "DOC.SHEET_NUMBERS", "the set has no sheets to identify"))
        return

    missed = [s for s in f.sheets if s.code == f"p{s.index + 1}"]
    read = total - len(missed)
    if len(missed) / total <= _COVERAGE_FLOOR:
        out.abstentions.append(Abstention(
            "DOC.SHEET_NUMBERS",
            "every sheet's number was read from its title block" if not missed else
            f"sheet numbers were read on {read} of {total} sheets, which is inside "
            f"the {_COVERAGE_FLOOR:.0%} reporting floor",
            detail="" if not missed else
            "numbered by page instead: " + ", ".join(s.code for s in missed)))
        return

    out.findings.append(Finding(
        "H-SN", "DOC.SHEET_NUMBERS", "OPEN", "HIGH", "Administration",
        0, f.sheet_code(0), "",
        f"Sheet numbers were not recognised on {len(missed)} of {total} sheets",
        "Every sheet's title block, for a discipline-and-number token naming the sheet.",
        f"Sheet numbers were not recognised on {len(missed)} of {total} sheets. Checks "
        f"that reference a specific sheet will stand down. Expected a "
        f"discipline-and-number token (A-101, M.101, G-0) in the title block.",
        "Review coverage · not a code citation",
        action=("Confirm the sheet number is live text in the title block and is written as "
                "a discipline and a number. A number plotted as part of an image, exploded "
                "into linework, or split across two text objects cannot be read: re-plot the "
                "set from CAD rather than scanning it, then submit again."),
        body=("Reviewed as: " + ", ".join(s.code for s in f.sheets) + ".\n\n"
              "Every rule that names a sheet — the code data block on G-0, the door schedule "
              "on A-2, the panel schedule on E-3 — looks the sheet up by its number. Where "
              "the number is a page number those rules abstain, and the abstentions are "
              "listed under 'Not checked'. A review that reports nothing because it could "
              "not read the set is not a review that found nothing wrong.")))
