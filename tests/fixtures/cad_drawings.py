"""Drawings built in code, shaped like the sheets of a real permit set.

A client drawing is never committed (`samples/` is git-ignored), so the CAD
tests build their own with ezdxf. Each builder below reproduces something the
adapter met on the reference drawing (EVERGREEN_BLDG_1.dwg, an AutoCAD 2018
set converted by LibreDWG) or something every permit drawing has:

* sheets are paper-space layouts on ARCH D (36 x 24 in), in tab order, with
  the default `Layout1` left empty — the way a drafter leaves it;
* every VIEWPORT carries `status 0`, which is what LibreDWG writes on every
  viewport it converts (the adapter must rebuild it, or each sheet plots as a
  title block over an empty frame);
* the sheet number is a title-block attribute — an INSERT whose ATTRIB is
  tagged `SHEET_NO`, or, as on the reference drawing, a free-standing ATTDEF
  prompted `SHEET No.` that displays its tag — and never the layout's tab name;
* a code block is stacked TEXT entities, label over value, one entity each,
  and MTEXT with a paragraph break; complex MTEXT with inline formatting and a
  stacked fraction; a schedule line laid out in columns with runs of spaces;
* viewports at exactly 1:48 and 1:16 beside the layout's own 1:1 viewport;
* text that must NOT reach the text layer: on a frozen layer, and in model
  space beyond a viewport's edge.

Units are inches (`$INSUNITS` 1) throughout, which is what the reference drawing
declares. Every builder returns a dict of what it put where, so a test asserts
against the drawing it built rather than against numbers copied by hand.
"""
from __future__ import annotations

import os
import shutil
import sys
import zipfile
from typing import Dict, Iterable, Optional, Tuple

import ezdxf
from ezdxf.document import Drawing

#: ARCH D, landscape, in inches.
SHEET_W, SHEET_H = 36.0, 24.0

#: Paper-space text height for notes, in inches: 1/8" lettering, 9 pt on the page.
CAP = 0.125

#: The stacked code block: (label, value) as separate TEXT entities.
CODE_BLOCK = (("TYPE OF CONSTRUCTION:", "III-B"),
              ("OCCUPANCY:", "ASSEMBLY (A-3)"),
              ("FIRE SPRINKLERS:", "SPRINKLERED"),
              ("RISK CATEGORY:", "III"))

#: MTEXT with a paragraph break between two inline pairs.
MTEXT_PAIRS = "OCCUPANT LOAD: 70\\PMAX. COMMON PATH: 75 LF"

#: Complex MTEXT: a font switch, a bold run, and a stacked fraction.
MTEXT_COMPLEX = ("{\\fArial|b1|i0|c0|p34;PRECAST:} 8\" HOLLOWCORE PLANK\\P"
                 "CLEAR HEIGHT: 5'-0\\S3/4;\"")

#: A schedule line whose columns are made with runs of spaces.
COLUMN_LINE = "40psf LL                 FLOOR"

#: Model space: an 80 ft x 40 ft floor plate, in inches.
PLATE = (0.0, 0.0, 960.0, 480.0)


def _drawing() -> Drawing:
    doc = ezdxf.new("R2018", setup=True)
    doc.header["$INSUNITS"] = 1
    return doc


def _zero_viewport_status(doc: Drawing) -> int:
    """What LibreDWG does to every viewport it converts: status 0, id 0."""
    n = 0
    for layout in doc.layouts:
        if layout.is_modelspace:
            continue
        for vp in layout.query("VIEWPORT"):
            vp.dxf.status = 0
            vp.dxf.id = 0
            n += 1
    return n


def _sheet(doc: Drawing, name: str, taborder: int, rotation: int = 0):
    layout = doc.layouts.new(name)
    layout.page_setup(size=(SHEET_W, SHEET_H), margins=(0, 0, 0, 0), units="inch",
                      rotation=rotation)
    layout.dxf_layout.dxf.taborder = taborder
    return layout


def _title_block(doc: Drawing) -> None:
    tb = doc.blocks.new("TB-ARCHD")
    tb.add_lwpolyline([(0, 0), (5.5, 0), (5.5, 23), (0, 23)], close=True)
    tb.add_text("SHEET", height=0.08).set_placement((0.25, 2.2))
    for tag, prompt, at, h in (("PROJECT_TITLE", "Project title", (0.25, 6.0), 0.15),
                               ("SHEET_TITLE", "Sheet title", (0.25, 3.0), 0.15),
                               ("SHEET_NO", "Sheet number", (0.25, 1.0), 0.5),
                               ("REV_SHEET_NO", "Sheet number (revision block)",
                                (3.5, 8.0), 0.08)):
        tb.add_attdef(tag, at, dxfattribs={"height": h, "prompt": prompt})


def permit_set(path: str, *, status_zero: bool = True) -> dict:
    """Three sheets and an empty Layout1, built to look like a permit drawing.

    Tab order (creation order is deliberately different):

    1. `Layout1` — empty, as AutoCAD leaves it.
    2. `PLAN` — the title block INSERT (SHEET_NO `A-101`), the stacked code
       block, the MTEXT pairs, complex MTEXT, the column line, a 1:48 viewport
       onto the whole plate and a 1:16 detail viewport, block attributes naming
       catalog facts, and the text that must stay out of the text layer.
    3. `A-201` — plotted rotated 90°, its number on a free-standing ATTDEF
       prompted `SHEET No.` that displays `A-501`. The tab name is
       sheet-shaped on purpose: it must never be read as the number.
    4. `S-1` — paper-space notes only: no viewport, no title block, so the
       sheet stays unidentified however sheet-shaped its tab name is.
    """
    doc = _drawing()
    for name in ("A-WALL", "A-AREA", "A-ANNO-TEXT", "A-DIMS", "Doors & Windows",
                 "A-HIDDEN", "TITLE"):
        doc.layers.add(name)
    doc.layers.get("A-HIDDEN").freeze()
    msp = doc.modelspace()
    x0, y0, x1, y1 = PLATE
    msp.add_lwpolyline([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], close=True,
                       dxfattribs={"layer": "A-WALL"})
    area = msp.add_lwpolyline([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], close=True,
                              dxfattribs={"layer": "A-AREA"})
    # an outline with an arc in it: its vertices' shoelace is not its area
    arced = msp.add_lwpolyline([(100, 100, 0), (200, 100, 1.0), (200, 200, 0),
                                (100, 200, 0)], format="xyb", close=True,
                               dxfattribs={"layer": "A-AREA"})
    msp.add_line((300, 0), (300, 480), dxfattribs={"layer": "Doors & Windows"})
    room = msp.add_text("ASSEMBLY HALL 101", height=12,
                        dxfattribs={"layer": "A-ANNO-TEXT"}).set_placement((400, 300))
    frozen_model = msp.add_text("FROZEN MODEL NOTE", height=12,
                                dxfattribs={"layer": "A-HIDDEN"}).set_placement((400, 200))
    # Starts inside the 1:48 viewport's right edge; nearly all of it, and its
    # middle, lies beyond it.
    beyond = msp.add_text("BEYOND THE VIEWPORT EDGE", height=12,
                          dxfattribs={"layer": "A-ANNO-TEXT"}).set_placement((1000, 240))
    # An enlarged-detail convention: DIMLFAC 0.5 prints half the geometry.
    dim = msp.add_linear_dim(base=(0, -24), p1=(0, 0), p2=(960, 0),
                             dxfattribs={"layer": "A-DIMS"},
                             override={"dimtxt": 6.0, "dimlfac": 0.5, "dimasz": 3.0,
                                       "dimexe": 2.0, "dimexo": 1.0, "dimgap": 1.5})
    dim.render()
    dim_handle = dim.dimension.dxf.handle

    _title_block(doc)
    wind = doc.blocks.new("WIND-DATA")
    wind.add_text("EXPOSURE CATEGORY:", height=CAP).set_placement((0, 0))
    wind.add_attdef("EXPOSURE_CATEGORY", (2.0, 0), dxfattribs={"height": CAP})
    tag = doc.blocks.new("CODE-TAG")
    tag.add_circle((0, 0), 0.3)
    tag.add_attdef("STORIES", (-0.05, -0.06), dxfattribs={"height": CAP})
    tag.add_attdef("OCCUPANCY_GROUP", (-0.05, -0.5), dxfattribs={"height": CAP})

    # creation order != tab order
    details = _sheet(doc, "A-201", taborder=3, rotation=1)
    notes = _sheet(doc, "S-1", taborder=4)
    plan = _sheet(doc, "PLAN", taborder=2)

    # ── PLAN ──────────────────────────────────────────────────────────────
    # 1:48 — 22 in of paper shows 1056 in of model; centred on the plate.
    vp48 = plan.add_viewport(center=(12.5, 13.0), size=(22.0, 12.0),
                             view_center_point=(480, 240), view_height=12.0 * 48)
    # 1:16 — a detail of the plate's lower-left corner.
    vp16 = plan.add_viewport(center=(28.0, 6.0), size=(6.0, 6.0),
                             view_center_point=(120, 120), view_height=6.0 * 16)
    tb = plan.add_blockref("TB-ARCHD", (30.25, 0.5), dxfattribs={"layer": "TITLE"})
    tb.add_auto_attribs({"PROJECT_TITLE": "RIVERSIDE COMMUNITY HALL",
                         "SHEET_TITLE": "FIRST FLOOR PLAN",
                         "SHEET_NO": "A-101", "REV_SHEET_NO": "A-101"})
    handles: Dict[str, str] = {"title_block": tb.dxf.handle}
    for a in tb.attribs:
        handles[f"attrib:{a.dxf.tag}"] = a.dxf.handle

    y = 22.5
    for label, value in CODE_BLOCK:
        lab = plan.add_text(label, height=CAP, dxfattribs={"layer": "A-ANNO-TEXT"})
        lab.set_placement((24.5, y))
        val = plan.add_text(value, height=CAP, dxfattribs={"layer": "A-ANNO-TEXT"})
        val.set_placement((24.5, y - 1.25 * CAP))         # single-line TEXT: 1.25 x cap
        handles[f"label:{label}"] = lab.dxf.handle
        handles[f"value:{label}"] = val.dxf.handle
        y -= 4.0 * CAP
    mt = plan.add_mtext(MTEXT_PAIRS, dxfattribs={"char_height": CAP, "layer": "A-ANNO-TEXT"})
    mt.set_location((24.5, 20.0))
    handles["mtext_pairs"] = mt.dxf.handle
    cx = plan.add_mtext(MTEXT_COMPLEX, dxfattribs={"char_height": CAP, "layer": "A-ANNO-TEXT",
                                                   "width": 8.0})
    cx.set_location((1.0, 4.0))
    handles["mtext_complex"] = cx.dxf.handle
    col = plan.add_text(COLUMN_LINE, height=CAP, dxfattribs={"layer": "A-ANNO-TEXT"})
    col.set_placement((1.0, 2.0))
    handles["column_line"] = col.dxf.handle
    frozen_paper = plan.add_text("FROZEN PAPER NOTE", height=CAP,
                                 dxfattribs={"layer": "A-HIDDEN"}).set_placement((1.0, 1.0))
    w = plan.add_blockref("WIND-DATA", (24.5, 18.5))
    w.add_auto_attribs({"EXPOSURE_CATEGORY": "C"})
    handles["wind_insert"] = w.dxf.handle
    handles["attrib:EXPOSURE_CATEGORY"] = w.attribs[0].dxf.handle
    t = plan.add_blockref("CODE-TAG", (33.0, 18.5))
    t.add_auto_attribs({"STORIES": "1", "OCCUPANCY_GROUP": "B"})
    for a in t.attribs:
        handles[f"attrib:{a.dxf.tag}"] = a.dxf.handle
        if a.dxf.tag == "OCCUPANCY_GROUP":
            # Invisible: a value the sheet does not print is not a statement it makes.
            a.is_invisible = True

    # ── A-201, plotted rotated 90° ───────────────────────────────────────────
    # Text set at 270° reads left to right on the rotated plot.
    details.add_text("GENERAL NOTES", height=0.2, rotation=270).set_placement((4.0, 20.0))
    details.add_text("ALL PRECAST BY OTHERS", height=CAP, rotation=270).set_placement((4.5, 20.0))
    sn = details.add_attdef("A-501", (34.0, 3.0),
                            dxfattribs={"height": 0.5, "prompt": "SHEET No.", "rotation": 270})
    handles["attdef:A-501"] = sn.dxf.handle

    # ── S-1, paper space only ────────────────────────────────────────────────
    notes.add_text("STRUCTURAL NOTES", height=0.25).set_placement((2.0, 21.0))
    notes.add_text("SEE SPECIFICATIONS", height=CAP).set_placement((2.0, 20.5))

    if status_zero:
        _zero_viewport_status(doc)
    doc.saveas(path)
    return {
        "path": path,
        "pages": ["PLAN", "A-201", "S-1"],
        "numbers": {"PLAN": "A-101", "A-201": "A-501"},
        "handles": dict(handles, room=room.dxf.handle, frozen_model=frozen_model.dxf.handle,
                        frozen_paper=frozen_paper.dxf.handle, beyond=beyond.dxf.handle,
                        dimension=dim_handle, area=area.dxf.handle, arced=arced.dxf.handle,
                        vp48=vp48.dxf.handle, vp16=vp16.dxf.handle),
        "area_sf": (x1 - x0) * (y1 - y0) / 144.0,
        "dimension_in": 960.0,
        "dimension_printed": "480",
    }


def model_only(path: str) -> dict:
    """No layouts at all: everything in model space, plotted fitted to ARCH D.

    The plate is 36 ft x 24 ft — the sheet's own proportion — so the fit is
    exactly 72 pt (one inch of paper) per foot, a number a test can state.
    """
    doc = _drawing()
    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0), (432, 0), (432, 288), (0, 288)], close=True)
    msp.add_text("OCCUPANT LOAD: 49", height=3).set_placement((24, 240))
    msp.add_text("SITE PLAN", height=6).set_placement((24, 24))
    doc.saveas(path)
    return {"path": path, "pt_per_ft": 72.0}


def xref_member(path: str, note: str = "XREF CORRIDOR 105") -> dict:
    """A base plan: model space only, meant to be referenced by sheet files."""
    doc = _drawing()
    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0), (480, 0), (480, 240), (0, 240)], close=True,
                       dxfattribs={"layer": "X-WALL"})
    t = msp.add_text(note, height=12, dxfattribs={"layer": "X-TEXT"}).set_placement((120, 120))
    doc.saveas(path)
    return {"path": path, "note": note, "handle": t.dxf.handle}


def xref_host(path: str, xref_file: str = "X-BASE.dwg") -> dict:
    """A sheet file whose plan is an external reference to `xref_file`.

    The xref is recorded with the path it had on the drafter's machine — a
    Windows path that never exists here — exactly as AutoCAD stores it.
    """
    doc = _drawing()
    ezdxf.xref.define(doc, "X-BASE", "C:\\Projects\\1234\\XREF\\" + xref_file)
    doc.modelspace().add_blockref("X-BASE", (0, 0))
    plan = _sheet(doc, "A-102", taborder=1)
    plan.add_viewport(center=(15.0, 12.0), size=(24.0, 14.0), view_center_point=(240, 120),
                      view_height=14.0 * 48)
    tb_block = doc.blocks.new("TB")
    tb_block.add_attdef("SHEET_NO", (0, 0), dxfattribs={"height": 0.5})
    plan.add_blockref("TB", (31.0, 1.0)).add_auto_attribs({"SHEET_NO": "A-102"})
    _zero_viewport_status(doc)
    doc.saveas(path)
    return {"path": path, "xref_file": xref_file}


def write_zip(path: str, members: Iterable[Tuple[str, bytes]]) -> str:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in members:
            zf.writestr(name, data)
    return path


def xref_zip(path: str, workdir: str, *, include_xref: bool = True,
             extra: Iterable[Tuple[str, bytes]] = ()) -> dict:
    """A zip holding a sheet file and, unless told otherwise, the base plan it
    references — in a sub-folder, the way a project folder is zipped."""
    os.makedirs(workdir, exist_ok=True)
    host = xref_host(os.path.join(workdir, "A-102.dxf"))
    base = xref_member(os.path.join(workdir, "X-BASE.dxf"))
    members = [("SET/A-102.dxf", _read(host["path"]))]
    if include_xref:
        members.append(("SET/XREF/X-BASE.dxf", _read(base["path"])))
    members.extend(extra)
    write_zip(path, members)
    return {"path": path, "note": base["note"], "xref_file": host["xref_file"]}


def _read(path: str) -> bytes:
    with open(path, "rb") as fh:
        return fh.read()


def fake_converter(directory: str, prepared: Optional[str] = None, *,
                   fail: bool = False) -> str:
    """A stand-in for LibreDWG's `dwg2dxf`: answers `--version` and, for
    `-y -o OUT IN`, copies a prepared DXF to OUT — or, with `fail`, writes
    nothing and exits 1, the way a converter that cannot read a file does."""
    exe = os.path.join(directory, "dwg2dxf")
    body = [f"#!{sys.executable}", "import shutil, sys",
            "if '--version' in sys.argv:", "    print('dwg2dxf 0.0-test'); sys.exit(0)"]
    if fail:
        body += ["sys.stderr.write('ERROR: cannot read\\n')", "sys.exit(1)"]
    else:
        body += [f"shutil.copyfile({prepared!r}, sys.argv[sys.argv.index('-o') + 1])"]
    with open(exe, "w", encoding="utf-8") as fh:
        fh.write("\n".join(body) + "\n")
    os.chmod(exe, 0o755)
    return exe


def dwg_bytes(version: bytes = b"AC1032") -> bytes:
    """A file that sniffs as a DWG of `version`: the six-byte version string
    and the binary header that follows it."""
    return version + b"\x00\x00\x00\x00\x00\x01\x03" + b"\x00" * 4000


def copy(src: str, dst: str) -> str:
    shutil.copyfile(src, dst)
    return dst
