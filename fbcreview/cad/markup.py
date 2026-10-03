"""The review drawn back into the drawing: a DXF copy with the findings on their own layers.

The marked-up PDF is the report. This is the same review for the person who
will fix the drawing: open it in AutoCAD and every finding is marked on the
layout it was found on, around what it is about, with its id, severity and
title beside it — on layers of their own, so they can be frozen, plotted or
deleted without touching a line of the drafter's work.

    FBC-REVIEW           revision clouds: findings that need action
    FBC-REVIEW-VERIFIED  plain rectangles: checked and found sufficient
    FBC-REVIEW-TEXT      ids, severities and titles, and the list of findings
                         that have no single place on a sheet

A revision cloud tells a drafter to change something. So only a finding that
needs action (`OPEN`, or `CONFLICT`) is a cloud; a check that passed is a green
rectangle on a layer of its own, and the two can be told apart with every
colour switched off.

## The same labelling rules as the PDF

The DXF is the review, so it says what `render/markup.py` says, in the same
words where words are shared:

* A finding that exists only under the project declaration (`scenario ==
  "as_declared"`) is never drawn and never listed: the drawings govern the
  sheet markup. It is counted in the summary and named in the READ ME.
* A finding resting on the declaration (`basis == "declaration"`) carries
  `DECLARED_BASIS_NOTE` on its label, so the drawing never appears to state
  what only an answer stated.
* A `CONFLICT`, or an as-drawn finding whose as-declared twin came out
  differently (the PDF's `Renderer.diverges`), is outlined dashed and tagged
  `DECLARED VS DRAWN`, with `scenario_note`'s wording, which follows the outcome.
* A finding the AI result review raised or revised says so on its label
  (CLAUDE.md rule 7: an AI-edited value never passes for a rule's).
* Each outline carries XDATA under `FBC_REVIEW` — the finding's `key`, `fid`,
  `rule_id`, severity and status — so two H-03s on one sheet stay two findings.

## Where a mark goes

The input is `findings.json`'s findings, and placement follows their `rect`:
the place the live viewer draws, in the rendered PDF's points (origin top-left,
y down). The rendered pages carry `/Rotate 0` and a media box at the origin
(`tests/test_cad_markup.py` pins it), so `rect` is exactly the space each page's
`to_page` maps the layout into, and its inverse puts the rect back on the
layout — all four corners, because a plot rotated 90° swaps the axes. `rect`
includes the payload's evidence fallback, which `render/markup.py` does not use;
following `rect` means the DXF marks everything the viewer marks.

A finding with no `rect` is listed beside its sheet. One with no usable page is
listed beside the first sheet. One whose drawing has no DXF to write into is
listed in the READ ME. Nothing is dropped, and the returned summary reconciles:
`placed + listed == len(findings) - as_declared_skipped`.
"""
from __future__ import annotations

import logging
import math
import os
import time
import zipfile
from types import SimpleNamespace
from typing import Dict, List, Optional, Sequence, Tuple

from ezdxf.enums import MTextEntityAlignment

from ..render.markup import ACTIONABLE, DECLARED_BASIS_NOTE, clip, scenario_note
from .read import ReadError, open_dxf
from .render import apply, box_through, invert, scale_of

log = logging.getLogger("fbc.cad")

CLOUD_LAYER = "FBC-REVIEW"
VERIFIED_LAYER = "FBC-REVIEW-VERIFIED"
TEXT_LAYER = "FBC-REVIEW-TEXT"
#: The registered application every outline's XDATA is filed under.
APPID = "FBC_REVIEW"
#: The linetype for a declared-versus-drawn outline. A name of our own: a
#: drawing's `DASHED` may be defined at any scale the office chose.
DASHED = "FBC-REVIEW-DASHED"

#: Layer colours (AutoCAD Colour Index): red for what needs action, green for
#: what was found sufficient, white/black for the words.
_LAYER_ACI = {CLOUD_LAYER: 1, VERIFIED_LAYER: 3, TEXT_LAYER: 7}

#: The PDF's severity palette (`render/markup.py` SEVC) in index colours, so a
#: plot style table keyed on ACI treats them as it treats the drafter's own.
SEVERITY_ACI = {"CRITICAL": 1, "HIGH": 30, "MEDIUM": 40, "LOW": 5,
                "VERIFIED": 3, "MEASURED": 6, "SCOPE": 8}

#: Sizes in page points (1/72 in on the plotted sheet), converted per layout.
#: The pad is the PDF marker's (`Renderer._box` pads 3 pt a side).
_PAD_PT = 3.0
_ARC_PT = 14.0
_TEXT_PT = 9.0
#: Dash and gap of the declared-versus-drawn outline: the PDF's `[5, 3]`.
_DASH_PT = (5.0, 3.0)
#: A bulge of 0.6 on each arc of a counter-clockwise outline swells it outward
#: (the arc's centre lies inside the outline), which is how a cloud reads.
_BULGE = 0.6


def _clean(text: str) -> str:
    """Text safe to put in MTEXT or XDATA: no control codes, no formatting braces."""
    return (str(text or "").replace("\\", "/").replace("{", "(").replace("}", ")")
            .replace("\r", " ").replace("\n", " ").strip())


# ── the drawing's tables ─────────────────────────────────────────────────────

def _prepare(doc) -> None:
    """The three layers, the application id and the linetype, created once."""
    for name, aci in _LAYER_ACI.items():
        if name not in doc.layers:
            doc.layers.add(name, color=aci)
        layer = doc.layers.get(name)
        layer.on()
        layer.thaw()
        layer.dxf.plot = 1
    if APPID not in doc.appids:
        doc.appids.new(APPID)
    if DASHED not in doc.linetypes:
        dash, gap = _DASH_PT
        # Defined in points; each outline's own linetype scale converts it to
        # the units of the layout it is drawn on (`_dash_scale`).
        doc.linetypes.add(DASHED, pattern=[dash + gap, dash, -gap],
                          description="FBC review: declared vs drawn __ __ __")


def _dash_scale(doc, ppu: float) -> float:
    """Entity linetype scale that makes `DASHED`'s points come out as points.

    AutoCAD multiplies an entity's scale by the drawing's `$LTSCALE`, so that
    is divided back out.
    """
    try:
        global_scale = float(doc.header.get("$LTSCALE", 1.0) or 1.0)
    except (TypeError, ValueError):
        global_scale = 1.0
    return (1.0 / ppu) / (global_scale if global_scale > 0 else 1.0)


# ── geometry ─────────────────────────────────────────────────────────────────

def _cloud_points(x0: float, y0: float, x1: float, y1: float, arc: float,
                  bulge: float) -> List[Tuple[float, float, float]]:
    """A rectangle as (x, y, bulge) vertices, counter-clockwise in layout space.

    With a bulge of 0 it is a plain rectangle; with `_BULGE`, a revision cloud
    whose arcs are each about `arc` long.
    """
    corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    if not bulge:
        return [(x, y, 0.0) for x, y in corners]
    pts: List[Tuple[float, float, float]] = []
    for i in range(4):
        ax, ay = corners[i]
        bx, by = corners[(i + 1) % 4]
        length = math.hypot(bx - ax, by - ay)
        n = max(1, int(round(length / arc)))
        for k in range(n):
            t = k / n
            pts.append((ax + (bx - ax) * t, ay + (by - ay) * t, bulge))
    return pts


def _to_layout(page: dict):
    """(page points -> layout units, points per layout unit) for one sheet."""
    a = tuple(page["to_page"])
    return invert(a), scale_of(a)


def _page_size(page: dict) -> Tuple[float, float]:
    """The plotted page's width and height in points: the window, through `to_page`."""
    x0, y0, x1, y1 = box_through(tuple(page["to_page"]), tuple(page["window"]))
    return x1, y1


def _frame(inv, px: float, py: float) -> Tuple[Tuple[float, float], float]:
    """A page point in layout units, and the angle (degrees) at which text placed
    there reads left to right on the plotted page."""
    o = apply(inv, px, py)
    e = apply(inv, px + 1.0, py)
    return o, math.degrees(math.atan2(e[1] - o[1], e[0] - o[0]))


def _target(doc, page: dict):
    return doc.modelspace() if page.get("model") else doc.paperspace(page["layout"])


def _rect_of(f: dict) -> Optional[Tuple[float, float, float, float]]:
    rect = f.get("rect")
    if not isinstance(rect, (list, tuple)) or len(rect) != 4:
        return None
    try:
        r = tuple(float(v) for v in rect)
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(v) for v in r) or r[2] < r[0] or r[3] < r[1]:
        return None
    return r


def _page_of(f: dict, pages: Dict[int, dict]) -> Optional[int]:
    pno = f.get("page")
    if isinstance(pno, bool) or not isinstance(pno, int) or pno not in pages:
        return None
    return pno


# ── what a mark says ─────────────────────────────────────────────────────────

def _twin(f: dict) -> tuple:
    """`Renderer.diverges`'s key: the same check under the other reading."""
    return (f.get("rule_id"), f.get("sheet"), f.get("anchor"), f.get("fid"))


def _ai_words(f: dict) -> str:
    label = f.get("ai_revision")
    if not isinstance(label, dict):
        return ""
    verb = "Raised" if label.get("op") == "add" else "Revised"
    n = label.get("pass")
    return f"{verb} by AI review" + (f", pass {n}" if n else "")


def _notes(f: dict, divergent: bool) -> List[str]:
    """The provenance a label has to carry, in the PDF's words."""
    out = []
    if f.get("status") == "CONFLICT":
        out.append("DECLARED VS DRAWN: your project declaration and this sheet disagree.")
    elif divergent:
        note = scenario_note(SimpleNamespace(scenario=f.get("scenario"), status=f.get("status")))
        out.append(f"DECLARED VS DRAWN: {note}" if note else "DECLARED VS DRAWN")
    if f.get("basis") == "declaration":
        out.append(DECLARED_BASIS_NOTE)
    ai = _ai_words(f)
    if ai:
        out.append(ai)
    return out


def _head(f: dict) -> str:
    sev = str(f.get("severity", "")).upper()
    status = str(f.get("status", "")).upper()
    head = f"{f.get('fid', '')} · {sev}".strip(" ·")
    if status and status != "OPEN":
        head += f" · {status}"
    return head


def _label_lines(f: dict, divergent: bool) -> List[str]:
    return [_clean(x) for x in (_head(f), clip(str(f.get("title", "")), 90),
                                *_notes(f, divergent)) if _clean(x)]


def _one_line(f: dict, divergent: bool) -> str:
    """A finding as one line of a listing."""
    parts = [f"{_head(f)}: {clip(str(f.get('title', '')), 90)}"] + _notes(f, divergent)
    return _clean(" — ".join(parts))


def _xdata(entity, f: dict) -> None:
    pairs = [("key", f.get("key", "")), ("fid", f.get("fid", "")),
             ("rule_id", f.get("rule_id", "")), ("severity", f.get("severity", "")),
             ("status", f.get("status", "")), ("scenario", f.get("scenario", "")),
             ("basis", f.get("basis", ""))]
    ai = f.get("ai_revision")
    if isinstance(ai, dict) and ai.get("op"):
        pairs.append(("ai_revision", ai.get("op")))
    entity.set_xdata(APPID, [(1000, _clean(f"{k}={v}")[:255]) for k, v in pairs])


# ── drawing ──────────────────────────────────────────────────────────────────

def _padded(rect: Sequence[float]) -> Tuple[float, float, float, float]:
    x0, y0, x1, y1 = rect
    return (x0 - _PAD_PT, y0 - _PAD_PT, x1 + _PAD_PT, y1 + _PAD_PT)


def _outline(doc, page: dict, f: dict, rect: Sequence[float], divergent: bool) -> str:
    """One finding outlined where it was found. Returns "cloud" or "rectangle"."""
    inv, ppu = _to_layout(page)
    x0, y0, x1, y1 = _padded(rect)
    corners = [apply(inv, x, y) for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1))]
    xs, ys = zip(*corners)
    lx0, ly0, lx1, ly1 = min(xs), min(ys), max(xs), max(ys)
    actionable = f.get("status") in ACTIONABLE
    attribs = {"layer": CLOUD_LAYER if actionable else VERIFIED_LAYER,
               "color": SEVERITY_ACI.get(str(f.get("severity", "")).upper(), 8),
               "lineweight": 50 if actionable else 35}
    if f.get("status") == "CONFLICT" or divergent:
        attribs["linetype"] = DASHED
        attribs["ltscale"] = _dash_scale(doc, ppu)
    outline = _target(doc, page).add_lwpolyline(
        _cloud_points(lx0, ly0, lx1, ly1, _ARC_PT / ppu, _BULGE if actionable else 0.0),
        format="xyb", close=True, dxfattribs=attribs)
    _xdata(outline, f)
    return "cloud" if actionable else "rectangle"


def _overlaps(a, b) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _label(doc, page: dict, f: dict, rect: Sequence[float], divergent: bool,
           taken: List[tuple], marks: List[tuple]) -> None:
    """A mark's id, severity, title and provenance, beside it.

    The label reads left to right on the plotted sheet. Like the PDF's chips it
    goes above the mark, else below, right or left — the first place that stays
    on the page and clears every other label and mark on the sheet — so two
    findings on adjacent rows do not print over each other. Sizes are estimated
    in page points (Helvetica-like widths); a CAD font may run a little wider.
    """
    inv, ppu = _to_layout(page)
    pw, ph = _page_size(page)
    x0, y0, x1, y1 = _padded(rect)
    lines = _label_lines(f, divergent)
    wrap = max(x1 - x0, 40 * _TEXT_PT)
    per_line = [max(1, math.ceil(len(t) * 0.55 * _TEXT_PT / wrap)) for t in lines]
    w = min(wrap, max(len(t) for t in lines) * 0.55 * _TEXT_PT)
    h = sum(per_line) * _TEXT_PT * 1.7
    gap = 0.6 * _TEXT_PT
    TL, TR, BL = (MTextEntityAlignment.TOP_LEFT, MTextEntityAlignment.TOP_RIGHT,
                  MTextEntityAlignment.BOTTOM_LEFT)
    candidates = [((x0, y0 - gap - h, x0 + w, y0 - gap), (x0, y0 - gap), BL),
                  ((x0, y1 + gap, x0 + w, y1 + gap + h), (x0, y1 + gap), TL),
                  ((x1 + gap, y0, x1 + gap + w, y0 + h), (x1 + gap, y0), TL),
                  ((x0 - gap - w, y0, x0 - gap, y0 + h), (x0 - gap, y0), TR)]
    others = [m for m in marks if m != (x0, y0, x1, y1)]

    def on_page(box) -> bool:
        return box[0] >= 0 and box[1] >= 0 and box[2] <= pw and box[3] <= ph

    clear = [c for c in candidates if on_page(c[0])
             and not any(_overlaps(c[0], t) for t in taken)
             and not any(_overlaps(c[0], m) for m in others)]
    box, at, attach = (clear or [c for c in candidates if on_page(c[0])] or candidates)[0]
    taken.append(box)
    where, angle = _frame(inv, *at)
    colour = SEVERITY_ACI.get(str(f.get("severity", "")).upper(), 8)
    m = _target(doc, page).add_mtext("\\P".join(lines), dxfattribs={
        "layer": TEXT_LAYER, "char_height": _TEXT_PT / ppu, "color": colour,
        "width": wrap / ppu})
    m.set_location(where, rotation=angle, attachment_point=attach)
    _xdata(m, f)


def _listing(doc, page: dict, sections: Sequence[Tuple[str, List[dict]]],
             divergent: set) -> None:
    """Findings with no single place on the sheet, listed just past its right edge."""
    inv, ppu = _to_layout(page)
    w, _h = _page_size(page)
    height = _TEXT_PT / ppu
    lines: List[str] = []
    keys: List[str] = []
    for heading, items in sections:
        if not items:
            continue
        if lines:
            lines.append("")
        lines.append(heading)
        for f in items:
            lines.append(f"- {_one_line(f, _twin(f) in divergent)}")
            keys.append(str(f.get("key", f.get("fid", ""))))
    where, angle = _frame(inv, w + 4 * _TEXT_PT, 0.0)
    m = _target(doc, page).add_mtext("\\P".join(lines), dxfattribs={
        "layer": TEXT_LAYER, "char_height": height, "color": 256, "width": 70 * height})
    m.set_location(where, rotation=angle, attachment_point=MTextEntityAlignment.TOP_LEFT)
    m.set_xdata(APPID, [(1000, "kind=listing")]
                + [(1000, _clean(f"key={k}")[:255]) for k in keys[:200]])


# ── the whole set ────────────────────────────────────────────────────────────

_NO_PLACE = "FBC REVIEW — findings without a single place on this sheet:"
_NO_PAGE = "FBC REVIEW — findings not tied to one sheet of the set:"


def write(workdir: str, sidecar: dict, findings: Sequence[dict], out_zip: str,
          set_name: str = "") -> dict:
    """Write one marked-up DXF per drawing with sheets, zipped, to `out_zip`.

    `findings` are `findings.json`'s findings (`fbcreview.payload.findings_payload`),
    after calibration — the list the PDF report and the viewer were given.
    Returns counts for the job record. Raises nothing for a finding it cannot
    place, nor for a drawing it cannot re-open: those findings are listed, in
    the drawing beside their sheet or in the READ ME, and counted.
    """
    t0 = time.monotonic()
    findings = [f for f in findings if isinstance(f, dict)]
    pages = {int(p["page"]): p for p in sidecar.get("pages", [])}
    dxf_paths = sidecar.get("dxf_paths", {}) or {}
    first = min(pages) if pages else None
    divergent = {_twin(f) for f in findings if f.get("scenario") == "as_declared"}

    # Route every finding to (drawing, page) as a mark or a listing line.
    skipped = 0
    marks: Dict[int, List[Tuple[dict, Tuple[float, ...]]]] = {}
    unplaced: Dict[int, List[dict]] = {}
    pageless: List[dict] = []
    readme: List[dict] = []
    for f in findings:
        if f.get("scenario") == "as_declared":
            skipped += 1                       # the drawings govern the sheet markup
            continue
        pno = _page_of(f, pages)
        rect = _rect_of(f) if pno is not None else None
        if pno is None:
            (pageless if first is not None else readme).append(f)
        elif rect is None:
            unplaced.setdefault(pno, []).append(f)
        else:
            marks.setdefault(pno, []).append((f, rect))

    by_drawing: Dict[str, List[int]] = {}
    for pno in sorted(pages):
        by_drawing.setdefault(pages[pno].get("drawing", ""), []).append(pno)

    placed = clouds = rects = listed = 0
    unreadable = 0
    pages_without_dxf: List[int] = []
    written: List[Tuple[str, str]] = []
    changed: List[Dict[str, int]] = []
    names_used: set = set()
    for drawing, dpages in sorted(by_drawing.items(), key=lambda kv: kv[1][0]):
        mine = [f for pno in dpages for f, _ in marks.get(pno, [])]
        mine += [f for pno in dpages for f in unplaced.get(pno, [])]
        if first in dpages:
            mine += pageless
        rel = dxf_paths.get(drawing)
        path = os.path.join(workdir, rel) if rel else ""
        if not path or not os.path.isfile(path):
            pages_without_dxf.extend(dpages)
            readme.extend(mine)
            continue
        try:
            opened = open_dxf(path, drawing)
        except ReadError:
            unreadable += 1
            pages_without_dxf.extend(dpages)
            readme.extend(mine)
            continue
        doc = opened.doc
        _prepare(doc)
        for pno in dpages:
            page = pages[pno]
            loose = list(unplaced.get(pno, []))
            drawn: List[Tuple[dict, Tuple[float, ...]]] = []
            for f, rect in marks.get(pno, []):
                try:
                    kind = _outline(doc, page, f, rect, _twin(f) in divergent)
                except (ValueError, ZeroDivisionError):
                    loose.append(f)            # a singular plot transform: list it
                    continue
                drawn.append((f, rect))
                placed += 1
                clouds += kind == "cloud"
                rects += kind == "rectangle"
            # Labels after every outline, so each can keep clear of all of them.
            taken: List[tuple] = []
            outlines = [_padded(rect) for _f, rect in drawn]
            for f, rect in drawn:
                _label(doc, page, f, rect, _twin(f) in divergent, taken, outlines)
            sections = [(_NO_PLACE, loose)]
            if pno == first:
                sections.append((_NO_PAGE, pageless))
            n = sum(len(items) for _h, items in sections)
            if n:
                try:
                    _listing(doc, page, sections, divergent)
                    listed += n
                except (ValueError, ZeroDivisionError):
                    for _h, items in sections:
                        readme.extend(items)
        changed.append({"viewports_repaired": opened.viewports_repaired,
                        "audit_fixes": opened.audit_fixes})
        out_name = _archive_name(drawing, names_used)
        out_path = os.path.join(workdir, f"markup_{len(written):03d}.dxf")
        doc.saveas(out_path)
        written.append((out_path, out_name))
        del doc, opened

    listed += len(readme)
    summary = {"drawings": len(written), "placed": placed, "clouds": clouds,
               "rectangles": rects, "listed": listed, "listed_in_readme": len(readme),
               "as_declared_skipped": skipped, "pages_without_dxf": sorted(pages_without_dxf),
               "drawings_unreadable": unreadable}
    with zipfile.ZipFile(out_zip, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for path, arc in written:
            zf.write(path, arcname=arc)
        zf.writestr("READ ME.txt", _readme(set_name, sidecar, summary, [a for _p, a in written],
                                           changed, readme, divergent))
    for path, _ in written:
        try:
            os.remove(path)
        except OSError:
            pass
    summary["seconds"] = round(time.monotonic() - t0, 2)
    summary["bytes"] = os.path.getsize(out_zip)
    # Counts only: never a drawing's name, path or text.
    log.info("cad markup", extra={k: v for k, v in summary.items() if k != "pages_without_dxf"})
    return summary


def _archive_name(drawing: str, used: set) -> str:
    stem = _clean(os.path.splitext(os.path.basename(drawing.replace("\\", "/")))[0]) or "drawing"
    name = f"{stem} — FBC REVIEW.dxf"
    k = 2
    while name.lower() in used:
        name = f"{stem} ({k}) — FBC REVIEW.dxf"
        k += 1
    used.add(name.lower())
    return name


def _readme(set_name: str, sidecar: dict, s: dict, files: Sequence[str],
            changed: Sequence[Dict[str, int]], readme: Sequence[dict], divergent: set) -> str:
    kind = str(sidecar.get("kind") or "").lower()
    converter = sidecar.get("converter") or ""
    repaired = sum(c["viewports_repaired"] for c in changed)
    fixes = sum(c["audit_fixes"] for c in changed)
    beside = s["listed"] - s["listed_in_readme"]
    lines = [
        f"FBC Reviewer — marked-up drawing{(' for ' + set_name) if set_name else ''}",
        "",
        "The marked-up PDF report is the review of record. These are the same findings, "
        "drawn into the drawing for the person who will change it.",
        "",
        "WHAT IS HERE",
        *(f"  {name}" for name in files),
        *([] if files else ["  No drawing could be written; every finding is listed below."]),
        "",
        f"  {s['placed']} finding(s) are marked where they were found: {s['clouds']} revision "
        f"cloud(s) and {s['rectangles']} rectangle(s).",
        f"  {beside} finding(s) with no single place on a sheet are listed in text beside it.",
    ]
    if s["listed_in_readme"]:
        lines.append(f"  {s['listed_in_readme']} finding(s) could not be written into a drawing "
                     "and are listed at the end of this file.")
    if s["as_declared_skipped"]:
        lines.append(f"  {s['as_declared_skipped']} finding(s) exist only under your project "
                     "declaration and are not drawn: the drawings govern the sheet markup. They "
                     "are in the PDF report's register, with both readings.")
    lines += [
        "  Rules that could not run are not marked anywhere in the drawing. They are on the "
        "\"Not checked\" page of the PDF report: not checked is not the same as passed.",
        "",
        "LAYERS THIS REVIEW ADDED",
        f"  {CLOUD_LAYER}  Revision clouds, one per finding that needs action (OPEN, or "
        "CONFLICT). Colour is severity: red CRITICAL, orange HIGH, yellow MEDIUM, blue LOW.",
        f"  {VERIFIED_LAYER}  Plain green rectangles: items checked against the code and found "
        "sufficient. A rectangle is a record, not a request to change anything.",
        f"  {TEXT_LAYER}  The id, severity and title beside each mark, and the list beside "
        "each sheet of findings that have no single place on it.",
        "",
        "  A dashed outline (linetype " + DASHED + ") marks DECLARED VS DRAWN: your project "
        "declaration and the drawings disagree there, and both readings were evaluated.",
        f"  A label that says \"{DECLARED_BASIS_NOTE}\" rests on an answer you gave, not on "
        "anything the drawing states.",
        "  A label that says \"Raised by AI review\" or \"Revised by AI review\" was raised or "
        "changed by the AI result review, not by a rule; the PDF report gives its reason.",
        f"  Every cloud, rectangle and label carries XDATA under the application {APPID}: the "
        "finding's key (as in findings.json), id, rule, severity and status. Two findings with "
        "one id are two keys.",
        "",
        "WHAT ELSE CHANGED",
        f"  Freeze or delete the three {CLOUD_LAYER} layers to return to the drawing as this "
        "review read it. The review also added the application id " + APPID + " and the "
        "linetype " + DASHED + ".",
    ]
    if kind in ("dwg", "zip"):
        lines.append("  A DWG was converted to DXF" + (f" with LibreDWG ({converter})" if converter
                                                        else "") + " before it was read; "
                     "this file is that conversion, re-saved. Your DWG is the authority.")
    resaved = "  The file was re-saved by ezdxf" + (f" {sidecar.get('ezdxf')}" if
                                                   sidecar.get("ezdxf") else "") + "."
    if fixes:
        resaved += f" Reading it repaired {fixes} structural fault(s)."
    if repaired:
        resaved += (f" The status number of {repaired} viewport(s), which the converter wrote "
                    "as 0, was rebuilt so that they display.")
    lines.append(resaved)
    lines += ["", "This is a DXF: AutoCAD opens it directly (OPEN, file type DXF)."]
    if readme:
        lines += ["", "FINDINGS NOT WRITTEN INTO A DRAWING"]
        lines += [f"  - p{f['page'] + 1 if isinstance(f.get('page'), int) else '?'} "
                  f"{_one_line(f, _twin(f) in divergent)}" for f in readme]
    lines.append("")
    return "\r\n".join(lines)
