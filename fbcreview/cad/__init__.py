"""The CAD adapter: a DWG, a DXF or a zip of them, read into the PDF the engine reviews.

`docs/ARCHITECTURE-V2.md` §5 designed this and held it until there was a real
drawing to build against. There is now (EVERGREEN_BLDG_1.dwg, an AutoCAD 2018
precast set), and every number in this package's docstrings was measured on it.

    upload ──sniff──> DWG ──dwg2dxf──> DXF ──ezdxf──> sheets ──render──> source PDF
                       zip ──unpack──┘                    │               + invisible text
                                                          └──claims──> cad.json sidecar

The rendered PDF is the set the rest of the engine reads, unchanged: the layout
layer, both readers, the rules, the marked-up report and the viewer. What a PDF
plot throws away and the drawing still has — the drafter's exact strings, block
attributes, each viewport's true scale, the layer table, dimension values, room
outlines — is carried beside it in `cad.json` and enters the fact store as
claims (`fbcreview/cad/claims.py`), each saying it was read from the drawing.

This package never decides compliance and never reaches a model. It is ingest:
`fbcreview/rules` and `fbcreview/codes` do not import it.

Run it in its own process (`python -m fbcreview.cad`): reading a large drawing
holds about a gigabyte, and a converter crash should cost a subprocess, not the
service.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

from .source import (DWG, DXF, PDF, ZIP, SourceError, check_dwg_version, sniff,  # noqa: F401
                     sniff_bytes, unpack)

log = logging.getLogger("fbc.cad")

#: Bumped whenever rendering or the text layer changes what a page holds, so AI
#: readings cached against an older rendering of the same file are not replayed.
RENDER_VERSION = "cad-render-2"

#: File names inside a CAD job's working directory.
RENDERED_PDF = "rendered.pdf"
SIDECAR = "cad.json"

CAD_KINDS = (DWG, DXF, ZIP)

_SHEET_FIELD = re.compile(
    r"(SHEET|SHT|DWG|DRAWING|PLATE)[\s_.\-]*(NO\b|NO\.|NUM|NUMBER|#|ID\b)|^SHEET$|^SHT$",
    re.I)
#: A title-block field that holds the sheet's own title: `TITLE`, `TITLE2`,
#: `SHEET_TITLE`, `DWG TITLE`. Not `PROJECT_TITLE`, `JOB TITLE` or `CLIENT
#: TITLE` — a title block carries the project's name too, and that is not what
#: the sheet is called (measured: the first fixture's sheet came out as
#: "RIVERSIDE COMMUNITY HALL FIRST FLOOR PLAN" when any tag holding TITLE counted).
_TITLE_FIELD = re.compile(
    r"^(?:(?:SHEET|SHT|DWG|DRAWING)[\s_.\-]*)?TITLE(?:[\s_.\-]*\d{1,2})?$", re.I)
_SHEET_SHAPED = re.compile(r"^[A-Z]{0,4}[\-.\s]?\d{1,4}(?:[.\-]\d{1,3})?[A-Z]?$|^\d{1,3}[A-Z]{1,2}$",
                           re.I)


#: How much DXF one job may hold open, in MB (`FBC_CAD_MAX_DXF_MB`). Measured:
#: the reference drawing's 170 MB of DXF peaked at 1.13 GB in ezdxf, about 6.6×.
#: Every member of a zip is open at once while xrefs resolve, and on Cloud Run
#: the DXF itself sits on an in-memory disk beside the worker, so 300 MB is about
#: 2.3 GB of reading on a 4 GiB instance — room for the reference drawing
#: nearly twice over, and a refusal that says why instead of an out-of-memory
#: kill reported as "could not be read".
DEFAULT_MAX_DXF_MB = 300


def max_dxf_bytes() -> int:
    try:
        mb = float(os.environ.get("FBC_CAD_MAX_DXF_MB", "") or DEFAULT_MAX_DXF_MB)
    except ValueError:
        mb = DEFAULT_MAX_DXF_MB
    return int(mb * 1024 ** 2)


def _check_size(dxf_paths: Dict[str, str]) -> None:
    """Refuse a set whose DXF is more than one job can hold in memory."""
    total = sum(os.path.getsize(p) for p in dxf_paths.values())
    limit = max_dxf_bytes()
    if total <= limit:
        return
    several = len(dxf_paths) > 1
    raise SourceError(
        "drawing_too_large",
        f"{'Those drawings convert' if several else 'That drawing converts'} to "
        f"{total / 1024 ** 2:,.0f} MB of DXF, more than this service can hold in memory "
        f"for one review (the limit is {limit / 1024 ** 2:,.0f} MB). "
        + ("Upload fewer drawings at once — the sheets for this permit and the xrefs they "
           "use." if several else
           "Purge it, or save the layouts for this permit to a drawing of their own, and "
           "upload that."))


def is_cad(kind: Optional[str]) -> bool:
    return kind in CAD_KINDS


def identity(upload_sha256: str) -> str:
    """The AI readings cache key for a set rendered from a drawing.

    The rendered PDF is rebuilt on every run and its bytes never repeat, so its
    own hash cannot key a cache. What decides what the reader saw is the upload,
    the converter and this package's rendering — those three are the identity.

    The worker and `run.py` key readings with `ai.readings.plotted_identity`,
    which adds a digest of the plot's own pages and words, so a rendering
    change that forgot to bump `RENDER_VERSION` still misses the cache. This
    remains the short form, for a label or a log line.
    """
    from . import convert
    import ezdxf
    return (f"{upload_sha256}+cad:{convert.version() or 'no-dwg2dxf'}"
            f":ezdxf-{ezdxf.__version__}:{RENDER_VERSION}")


@dataclass
class CadSet:
    """Everything ingest produced: the rendered PDF and what it was made from."""
    kind: str
    pdf_path: str
    sidecar_path: str
    data: Dict = field(default_factory=dict)

    @property
    def pages(self) -> List[dict]:
        return self.data.get("pages", [])

    @property
    def warnings(self) -> List[str]:
        return self.data.get("warnings", [])


def load_sidecar(path: str) -> Dict:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _natural(name: str):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", name)]


def _title_block_identity(runs) -> tuple:
    """(sheet number, where it came from, title) from a sheet's title-block fields.

    A title block's fields are attributes. Their tag or prompt says what each one
    is — `SHEET_NO`, `DWG No.`, or on the reference drawing an ATTDEF prompted
    `SHEET No. (1)` showing `2A` — so the number is read off the field that says
    it is the number, not off whatever sits in a corner. Several matching fields
    (a revision block repeats the sheet number small) resolve to the largest.
    """
    number, source, best = "", "", 0.0
    titles = []
    for r in runs:
        if r.kind not in ("ATTRIB", "ATTDEF"):
            continue
        label = r.prompt if r.kind == "ATTDEF" and r.prompt else r.tag
        text = r.text.strip()
        if _SHEET_FIELD.search(label or "") and _SHEET_SHAPED.match(text):
            if r.cap > best:
                number, best = text.upper(), r.cap
                source = f"title-block {r.kind.lower()} “{label.strip()}”"
        elif r.kind == "ATTRIB" and _TITLE_FIELD.search((r.tag or "").strip()) and text:
            if text not in titles:          # a revision block repeats it
                titles.append(text)
    return number, source, " ".join(titles)[:120]


def _unread_code(exc: Exception) -> str:
    """The refusal code `python -m fbcreview.cad` would have exited with."""
    from . import convert, read
    if isinstance(exc, SourceError):
        return exc.code
    if isinstance(exc, convert.ConversionUnavailable):
        return "dwg_unavailable"
    if isinstance(exc, convert.ConversionFailed):
        return "dwg_conversion_failed"
    if isinstance(exc, read.ReadError):
        return "dxf_unreadable"
    return "internal"


def _unread_reason(exc: Exception) -> str:
    """Why a member was not read, in a few words — never the drawing's content."""
    return {"unsupported_dwg_version": "saved in a DWG format this service cannot read",
            "dwg_unavailable": "DWG conversion is not installed here",
            "dwg_conversion_failed": "the DWG could not be converted",
            "dxf_unreadable": "the DXF could not be read"}.get(
        _unread_code(exc), "it could not be read")


def _plot_sheet(op, member: str, spec, cache, target, names):
    """Plot one sheet onto the next page of `target` and write its text layer.

    Returns (SheetPage, the runs captured, text runs that could not be placed).
    """
    from . import render
    sheet = render.render_sheet(op.doc, spec, cache, target, names)
    page = target.doc[sheet.page]
    cell_list = render.cells(sheet.runs, sheet.to_page)
    written = render.write_cells(page, cell_list)
    number, number_source, title = _title_block_identity(sheet.runs)
    sp = render.SheetPage(
        page=sheet.page, drawing=member, layout=spec.layout, model=spec.model,
        paper_mm=spec.paper_mm, window=spec.window, to_page=sheet.to_page,
        viewports=sheet.viewports, cells=written, runs=len(sheet.runs),
        seconds=sheet.seconds, note=spec.note, number=number,
        number_source=number_source, title=title,
        text=[{"box": [round(v, 1) for v in c.box()],
               "handles": sorted({r.handle for r in c.runs if r.handle}),
               "kind": c.runs[0].kind, "layer": c.runs[0].layer} for c in cell_list])
    return sp, sheet.runs, sheet.skipped_text


def ingest(src: str, workdir: str, name: str = "",
           progress: Optional[Callable[[str], None]] = None) -> CadSet:
    """Read a drawing upload into a rendered PDF and a sidecar, in `workdir`.

    Raises `SourceError` (a refusal to show the user), `convert.ConversionUnavailable`
    / `convert.ConversionFailed`, or `read.ReadError`.
    """
    import ezdxf
    import pymupdf
    from . import convert, read, render, claims as cad_claims

    say = progress or (lambda _m: None)
    t_start = time.monotonic()
    work = Path(workdir)
    work.mkdir(parents=True, exist_ok=True)
    kind = sniff(src)
    if kind not in CAD_KINDS:
        raise SourceError("unsupported_media", "That file is not a DWG, DXF or zip of drawings.")

    if kind == ZIP:
        say("unpacking")
        unpacked = unpack(src, str(work / "drawings"))
        members: Dict[str, str] = {k: str(v) for k, v in unpacked.drawings.items()}
        skipped = dict(unpacked.skipped)
    else:
        members = {name or os.path.basename(src): src}
        skipped = {}

    warnings: List[str] = [f"{m} was not read: {why}." for m, why in sorted(skipped.items())
                           if why != "not a drawing"]
    drawings: Dict[str, dict] = {}
    dxf_paths: Dict[str, str] = {}
    # A zip member that cannot be converted or read costs that drawing, not
    # the set: the rest is reviewed and the warning names what is missing. A
    # single drawing upload has nothing else to review, so its failure is the
    # job's. Decided 2026-10-03 (core-tests pass): a person who zips forty sheet
    # files should not lose thirty-nine to one damaged file, and an xref that
    # fails is reported as unreadable, never as "not uploaded".
    unread: Dict[str, Exception] = {}
    unread_errors = (SourceError, convert.ConversionUnavailable, convert.ConversionFailed,
                     read.ReadError)
    for i, (member, path) in enumerate(sorted(members.items(), key=lambda kv: _natural(kv[0]))):
        info: Dict = {"name": member}
        try:
            if sniff(path) == DWG:
                info["release"] = check_dwg_version(path)
                say(f"converting {os.path.basename(member)}")
                conv = convert.dwg_to_dxf(path, str(work / f"{i:03d}.dxf"))
                info["conversion"] = conv.to_dict()
                dxf_paths[member] = conv.dxf_path
            else:
                dxf_paths[member] = path
        except unread_errors as exc:
            if kind != ZIP:
                raise
            unread[member] = exc
            continue
        drawings[member] = info

    # ── read every drawing; sort hosts from xref-only members ──────────────
    _check_size(dxf_paths)
    opened: Dict[str, "read.Opened"] = {}
    for member, path in dxf_paths.items():
        say(f"reading {os.path.basename(member)}")
        try:
            opened[member] = read.open_dxf(path, member)
        except read.ReadError as exc:
            if kind != ZIP:
                raise
            unread[member] = exc
    if unread and not opened:
        # nothing in the zip could be read: the first reason is the set's
        raise unread[sorted(unread, key=_natural)[0]]
    for member, exc in sorted(unread.items(), key=lambda kv: _natural(kv[0])):
        drawings.pop(member, None)
        dxf_paths.pop(member, None)
        warnings.append(f"{member} could not be read and is not in this review "
                        f"({_unread_reason(exc)}).")

    by_file = {os.path.basename(m).lower(): m for m in opened}
    by_stem = {os.path.splitext(os.path.basename(m))[0].lower(): m for m in opened}
    referenced = set()
    for member, op in opened.items():
        for block in op.doc.blocks:
            if getattr(block.block_record, "is_xref", False):
                p = str(block.block.dxf.get("xref_path", "") or "").replace("\\", "/")
                base = os.path.basename(p).lower()
                target = by_file.get(base) or by_stem.get(os.path.splitext(base)[0])
                if target and target != member:
                    referenced.add(target)

    def resolver(host: str):
        def resolve(fname: str):
            base = fname.lower()
            target = by_file.get(base) or by_stem.get(os.path.splitext(base)[0])
            if target is None or target == host:
                return None
            return opened[target].doc
        return resolve

    # An xref that was uploaded but could not be read is that, not missing.
    unreadable: Dict[str, str] = {}
    for member, exc in unread.items():
        base = os.path.basename(member).lower()
        unreadable[base] = unreadable[os.path.splitext(base)[0]] = _unread_reason(exc)

    # nested xrefs arrive with the first embed; a few rounds settle them
    for member, op in opened.items():
        for _round in range(4):
            before = dict(op.xrefs)
            read.embed_xrefs(op, resolver(member), unreadable)
            if op.xrefs == before:
                break

    # ── plot ───────────────────────────────────────────────────────────────
    target = render.PlotTarget()
    out = target.doc
    pages: List[render.SheetPage] = []
    toc = []
    cad_found: List[dict] = []
    layers = set()
    failed_sheets = 0
    for member in sorted(opened, key=_natural):
        op = opened[member]
        specs = read.sheets(op.doc)
        has_layouts = any(not s.model for s in specs)
        if member in referenced and not has_layouts:
            drawings[member]["role"] = "xref"
            continue
        drawings[member]["role"] = "sheets"
        drawings[member]["blocks"] = cad_claims.blocks(op.doc)
        if not specs:
            warnings.append(f"{member} has nothing drawn in it.")
            warnings.extend(op.warnings)
            continue
        say(f"indexing {os.path.basename(member)}")
        cache = render.model_bbox_cache(op.doc) if has_layouts else None
        names = render._layer_names(op.doc)
        layers.update(read.layer_names(op.doc))
        where = f" in {member}" if len(opened) > 1 else ""
        for spec in specs:
            say(f"plotting {spec.layout}")
            before = out.page_count
            try:
                sp, runs, skipped_text = _plot_sheet(op, member, spec, cache, target, names)
                found = cad_claims.from_sheet(op, sp, runs)
            except MemoryError:
                raise
            except Exception as exc:
                # One layout ezdxf cannot draw costs that sheet, not the set.
                # Any page it began is removed, so page N of the PDF stays the
                # sidecar's page N.
                while out.page_count > before:
                    out.delete_page(out.page_count - 1)
                failed_sheets += 1
                log.warning("cad sheet not plotted", extra={"error": exc.__class__.__name__})
                warnings.append(f"Layout {spec.layout}{where} could not be plotted "
                                f"({exc.__class__.__name__}); it is not in this review.")
                continue
            pages.append(sp)
            toc.append([1, f"{sp.number or spec.layout} — {spec.layout}"
                           + (f" ({os.path.basename(member)})" if len(opened) > 1 else ""),
                        sp.page + 1])
            cad_found.extend(found)
            if skipped_text:
                warnings.append(f"{skipped_text} text item(s) on {spec.layout} could "
                                "not be placed in the text layer.")
        warnings.extend(op.warnings)

    if out.page_count == 0:
        if failed_sheets:
            raise SourceError("no_sheets", f"None of the drawing's {failed_sheets} sheet(s) "
                                           "could be plotted. Run AUDIT and save it in "
                                           "AutoCAD, or export it as DXF, and upload it again.")
        raise SourceError("no_sheets", "No sheets could be plotted from that drawing: it has "
                                       "no layouts with anything drawn on them, and nothing "
                                       "in model space.")
    # A title block copied to another layout and not renumbered: the number is
    # what is printed, so each sheet keeps it, but every finding on those
    # sheets would name the same one, and the person should know why.
    by_number: Dict[str, List[render.SheetPage]] = {}
    for sp in pages:
        if sp.number:
            by_number.setdefault(sp.number, []).append(sp)
    for number, same in sorted(by_number.items(), key=lambda kv: _natural(kv[0])):
        if len(same) > 1:
            tabs = ", ".join(sp.layout for sp in same)
            warnings.append(f"The title blocks of {len(same)} sheets give sheet number "
                            f"{number} (layouts {tabs}); findings on them all name {number}, "
                            "and the page tells them apart.")
    warnings = list(dict.fromkeys(warnings))     # one missing xref, said once
    if toc:
        out.set_toc(toc)
    out.set_metadata({"producer": f"fbcreview {RENDER_VERSION} / ezdxf {ezdxf.__version__}",
                      "title": name or "drawing set", "creator": "FBC Reviewer CAD adapter"})
    pdf_path = str(work / RENDERED_PDF)
    out.save(pdf_path, garbage=3, deflate=True)
    out.close()

    data = {
        "version": 1,
        "kind": kind,
        "render_version": RENDER_VERSION,
        "converter": convert.version(),
        "ezdxf": ezdxf.__version__,
        # what the upload's own header said (a DWG's release, its conversion)
        # wins over what the converted DXF says about itself
        "drawings": [dict(opened[m].to_dict(), **drawings[m])
                     for m in sorted(opened, key=_natural)],
        # relative to the working directory: the markup step re-opens these
        "dxf_paths": {m: os.path.relpath(p, work) for m, p in dxf_paths.items()},
        "pages": [p.to_dict() for p in pages],
        "layers": sorted(layers),
        "claims": cad_found,
        # zip members that could not be converted or read: name and why, by kind
        "unread": [{"name": m, "error": _unread_code(e)}
                   for m, e in sorted(unread.items(), key=lambda kv: _natural(kv[0]))],
        "warnings": warnings,
        "seconds": round(time.monotonic() - t_start, 2),
    }
    sidecar_path = str(work / SIDECAR)
    with open(sidecar_path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1)
    log.info("cad ingest", extra={"kind": kind, "drawings": len(opened), "pages": len(pages),
                                  "unread": len(unread), "sheets_failed": failed_sheets,
                                  "claims": len(cad_found), "warnings": len(warnings),
                                  "seconds": data["seconds"]})
    return CadSet(kind=kind, pdf_path=pdf_path, sidecar_path=sidecar_path, data=data)
