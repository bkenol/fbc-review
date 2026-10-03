"""The review drawn back into the drawing: a DXF copy with the findings on their own layers.

The marked-up PDF is the report. This is the same review for the person who
will fix the drawing: open it in AutoCAD and every finding is a revision cloud,
on the layout it was found on, around what it is about, with its id, severity
and title beside it — on layers of their own, so they can be frozen, plotted or
deleted without touching a line of the drafter's work.

    FBC-REVIEW        revision clouds
    FBC-REVIEW-TEXT   finding ids and titles, and the list of findings that
                      have no single place on a sheet

Placement is the inverse of how the sheet was plotted: each page kept the
affine map from its layout to the PDF (`render.SheetPage.to_page`), and a
finding's `rect` is in that PDF's points. Nothing about the drawing is changed
but the two layers and what is on them.
"""
from __future__ import annotations

import logging
import math
import os
import time
import zipfile
from typing import Dict, List, Optional, Sequence, Tuple

import ezdxf
from ezdxf.enums import MTextEntityAlignment

from .read import open_dxf
from .render import apply, invert, scale_of

log = logging.getLogger("fbc.cad")

CLOUD_LAYER = "FBC-REVIEW"
TEXT_LAYER = "FBC-REVIEW-TEXT"
_RED = 1

#: Sizes in page points (1/72 in on the plotted sheet), converted per layout.
_PAD_PT = 6.0
_ARC_PT = 14.0
_TEXT_PT = 9.0


def _clean(text: str) -> str:
    """Text safe to put in MTEXT: no control codes, no formatting braces."""
    return (str(text or "").replace("\\", "/").replace("{", "(").replace("}", ")")
            .replace("\r", " ").replace("\n", " ").strip())


def _layers(doc) -> None:
    for name in (CLOUD_LAYER, TEXT_LAYER):
        if name not in doc.layers:
            doc.layers.add(name, color=_RED)
        layer = doc.layers.get(name)
        layer.on()
        layer.thaw()
        layer.dxf.plot = 1


def _cloud_points(x0: float, y0: float, x1: float, y1: float, arc: float) -> List[Tuple[float, float, float]]:
    """A rectangle as a revision cloud: (x, y, bulge) vertices, counter-clockwise.

    A negative bulge on a counter-clockwise outline swells each arc outward,
    which is how a cloud reads.
    """
    pts: List[Tuple[float, float, float]] = []
    corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    for i in range(4):
        ax, ay = corners[i]
        bx, by = corners[(i + 1) % 4]
        length = math.hypot(bx - ax, by - ay)
        n = max(1, int(round(length / arc)))
        for k in range(n):
            t = k / n
            pts.append((ax + (bx - ax) * t, ay + (by - ay) * t, -0.6))
    return pts


def _label(f: dict) -> str:
    sev = str(f.get("severity", "")).upper()
    status = str(f.get("status", "")).upper()
    head = f"{f.get('fid', '')} {sev}".strip()
    if status and status not in ("OPEN",):
        head += f" ({status})"
    return _clean(f"{head}: {f.get('title', '')}")


def _target(doc, page: dict):
    return doc.modelspace() if page.get("model") else doc.paperspace(page["layout"])


def write(workdir: str, sidecar: dict, findings: Sequence[dict], out_zip: str,
          set_name: str = "") -> dict:
    """Write one marked-up DXF per drawing with sheets, zipped, to `out_zip`.

    Returns counts for the job record. Raises nothing for a finding it cannot
    place — that finding is listed in text beside its sheet instead.
    """
    t0 = time.monotonic()
    pages = {int(p["page"]): p for p in sidecar.get("pages", [])}
    by_drawing: Dict[str, List[dict]] = {}
    for p in pages.values():
        by_drawing.setdefault(p["drawing"], []).append(p)
    dxf_paths = sidecar.get("dxf_paths", {})

    placed = unplaced = 0
    written: List[Tuple[str, str]] = []
    first_page = min(pages) if pages else None
    for drawing, dpages in sorted(by_drawing.items()):
        rel = dxf_paths.get(drawing)
        if not rel:
            continue
        opened = open_dxf(os.path.join(workdir, rel), drawing)
        doc = opened.doc
        _layers(doc)
        loose: Dict[int, List[dict]] = {}
        for f in findings:
            pno = f.get("page")
            if not isinstance(pno, int) or pno not in pages or pages[pno]["drawing"] != drawing:
                if (pno is None or pno not in pages) and first_page is not None \
                        and pages[first_page]["drawing"] == drawing:
                    loose.setdefault(first_page, []).append(f)
                continue
            page = pages[pno]
            rect = f.get("rect")
            if not rect or len(rect) != 4:
                loose.setdefault(pno, []).append(f)
                continue
            _cloud(doc, page, rect, _label(f))
            placed += 1
        for pno, items in loose.items():
            _listing(doc, pages[pno], items)
            unplaced += len(items)
        stem = os.path.splitext(os.path.basename(drawing))[0] or "drawing"
        out_name = f"{stem} — FBC REVIEW.dxf"
        out_path = os.path.join(workdir, f"markup_{len(written):03d}.dxf")
        doc.saveas(out_path)
        written.append((out_path, out_name))
        del doc, opened

    with zipfile.ZipFile(out_zip, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for path, arc in written:
            zf.write(path, arcname=arc)
        zf.writestr("READ ME.txt", _readme(set_name, placed, unplaced))
    for path, _ in written:
        try:
            os.remove(path)
        except OSError:
            pass
    summary = {"drawings": len(written), "placed": placed, "listed": unplaced,
               "seconds": round(time.monotonic() - t0, 2),
               "bytes": os.path.getsize(out_zip)}
    log.info("cad markup", extra=summary)
    return summary


def _to_layout(page: dict):
    a = tuple(page["to_page"])
    inv = invert(a)
    pts_per_unit = scale_of(a)
    return inv, pts_per_unit


def _cloud(doc, page: dict, rect: Sequence[float], label: str) -> None:
    inv, ppu = _to_layout(page)
    x0, y0, x1, y1 = rect
    x0, y0, x1, y1 = x0 - _PAD_PT, y0 - _PAD_PT, x1 + _PAD_PT, y1 + _PAD_PT
    corners = [apply(inv, x, y) for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1))]
    xs, ys = zip(*corners)
    lx0, ly0, lx1, ly1 = min(xs), min(ys), max(xs), max(ys)
    target = _target(doc, page)
    target.add_lwpolyline(_cloud_points(lx0, ly0, lx1, ly1, _ARC_PT / ppu), format="xyb",
                          close=True, dxfattribs={"layer": CLOUD_LAYER, "color": 256})
    height = _TEXT_PT / ppu
    m = target.add_mtext(label, dxfattribs={"layer": TEXT_LAYER, "char_height": height,
                                            "color": 256, "width": max(lx1 - lx0, 30 * height)})
    m.set_location((lx0, ly1 + height * 0.6), attachment_point=MTextEntityAlignment.BOTTOM_LEFT)


def _listing(doc, page: dict, items: List[dict]) -> None:
    """Findings with no single place on the sheet, listed just right of it."""
    inv, ppu = _to_layout(page)
    w = page["window"]
    height = _TEXT_PT / ppu
    lines = ["FBC REVIEW — findings without a single place on this sheet:"]
    lines += [f"- {_label(f)}" for f in items]
    target = _target(doc, page)
    m = target.add_mtext("\\P".join(lines),
                         dxfattribs={"layer": TEXT_LAYER, "char_height": height, "color": 256,
                                     "width": 60 * height})
    m.set_location((w[2] + 4 * height, w[3]), attachment_point=MTextEntityAlignment.TOP_LEFT)


def _readme(set_name: str, placed: int, listed: int) -> str:
    return (
        f"FBC Reviewer — marked-up drawing{(' for ' + set_name) if set_name else ''}\r\n\r\n"
        f"{placed} finding(s) are clouded where they were found, and {listed} without a single "
        "place on a sheet are listed beside it.\r\n\r\n"
        f"Clouds are on layer {CLOUD_LAYER}; ids and titles on {TEXT_LAYER}. Freeze or delete "
        "those two layers to return to the drawing as submitted. Nothing else was changed.\r\n\r\n"
        "This is a DXF: AutoCAD opens it directly (OPEN, file type DXF). The marked-up PDF "
        "report is the review of record.\r\n")
