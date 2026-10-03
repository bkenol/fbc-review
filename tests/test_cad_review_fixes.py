"""Defects an adversarial review of the drawing adapter found after it merged.

Each was claimed by one reviewer, reproduced by a second that tried to refute it,
and is written down here with the input that showed it before it was fixed.
"""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


# ── the DXF sniff ────────────────────────────────────────────────────────────

def test_the_dxf_sniff_answers_at_once_on_a_crafted_header():
    """`999` comment lines ending in CRLF could each be split two ways by the
    old pattern — `\\s*` or `\\r?` taking the `\\r` — so 40 of them and a
    non-match backtracked for minutes, on the event loop of the upload request.
    A subprocess with a deadline, so a regression fails here instead of hanging
    the suite."""
    probe = textwrap.dedent("""
        import time
        from fbcreview.cad.source import sniff_bytes
        t = time.perf_counter()
        assert sniff_bytes(b"999\\r\\nA\\r\\n" * 40 + b"X") is None
        assert sniff_bytes(b"999\\nA\\n" * 400 + b"X") is None
        print(time.perf_counter() - t)
    """)
    done = subprocess.run([sys.executable, "-c", probe], cwd=ROOT, capture_output=True,
                          text=True, timeout=20)
    assert done.returncode == 0, done.stderr[-2000:]
    assert float(done.stdout.strip()) < 0.5


@pytest.mark.parametrize("head", [
    b"0\r\nSECTION\r\n2\r\nHEADER\r\n",
    b"  0\nSECTION\n",
    b"999\r\ndxfrw 0.6.3\r\n  0\r\nSECTION\r\n",
    b"999\nmade by a CAD program\n999\nanother comment\n0\nSECTION\n",
    b"\r\n\r\n  0\r\nSECTION\r\n",
])
def test_every_ascii_dxf_header_still_sniffs_as_one(head):
    from fbcreview.cad.source import DXF, sniff_bytes
    assert sniff_bytes(head) == DXF


# ── a drawing never makes the plotter open a server file ───────────────────

ezdxf = pytest.importorskip("ezdxf")
import ezdxf.xref  # noqa: E402,F401 — a submodule `import ezdxf` does not load


def test_an_image_the_drawing_names_is_never_read_off_the_server(tmp_path):
    """An IMAGEDEF names a file by path, and ezdxf's plotter opened it — so a
    DXF naming any image on the server (`/app/...`, a key rendered as a PNG)
    had that file embedded in the PDF the uploader downloads. The plot draws
    the image's frame and never opens the file."""
    from PIL import Image

    from fbcreview.cad import ingest

    secret = tmp_path / "elsewhere" / "server-only.png"
    secret.parent.mkdir()
    Image.new("RGB", (64, 32), (200, 30, 30)).save(secret)
    doc = ezdxf.new("R2018", setup=True)
    doc.header["$INSUNITS"] = 1
    msp = doc.modelspace()
    msp.add_line((0, 0), (400, 0))
    msp.add_text("SITE PLAN", height=6).set_placement((10, 150))
    idef = doc.add_image_def(filename=str(secret), size_in_pixel=(64, 32))
    msp.add_image(idef, insert=(100, 50), size_in_units=(200, 100))
    path = tmp_path / "site.dxf"
    doc.saveas(path)

    cs = ingest(str(path), str(tmp_path / "w"), name="site.dxf")
    import pymupdf
    with pymupdf.open(cs.pdf_path) as pdf:
        assert pdf[0].get_images() == []
        assert "SITE PLAN" in pdf[0].get_text()


# ── external references ──────────────────────────────────────────────────────

def _drawing_with(path, *, note, xrefs=(), sheet=None):
    """A drawing whose model space holds `note` and INSERTs of `xrefs`
    (`(file, overlay)` pairs), with a paper-space sheet `sheet` showing its
    model space when one is named."""
    doc = ezdxf.new("R2018", setup=True)
    doc.header["$INSUNITS"] = 1
    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0), (480, 0), (480, 240), (0, 240)], close=True)
    msp.add_text(note, height=12).set_placement((60, 60 + 30 * len(xrefs)))
    for fname, overlay in xrefs:
        name = fname.rsplit(".", 1)[0]
        ezdxf.xref.define(doc, name, "C:\\Projects\\XREF\\" + fname, overlay=overlay)
        msp.add_blockref(name, (0, 0))
    if sheet:
        lay = doc.layouts.new(sheet)
        lay.page_setup(size=(36, 24), margins=(0, 0, 0, 0), units="inch")
        lay.dxf_layout.dxf.taborder = 1
        lay.add_viewport(center=(15, 12), size=(24, 14), view_center_point=(240, 120),
                         view_height=14 * 48)
    doc.saveas(path)
    return path


def _zip(tmp_path, members):
    import zipfile
    out = tmp_path / "set.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, path in members:
            zf.write(path, name)
    return str(out)


def _words(cs, page):
    import pymupdf
    with pymupdf.open(cs.pdf_path) as pdf:
        return " ".join(pdf[page].get_text().split())


@pytest.mark.parametrize("order", ["host first", "xref first"])
def test_an_overlay_inside_an_xref_is_not_drawn_on_the_sheet(tmp_path, order):
    """AutoCAD does not carry an overlaid xref into a drawing that references
    the one overlaying it. The adapter embedded it — as nested xref or as plain
    geometry, depending on the order the members were read — and its text then
    sat in the sheet's text layer as printed there."""
    from fbcreview.cad import ingest
    s = _drawing_with(tmp_path / "S.dxf", note="SHEET NOTE", xrefs=[("F.dwg", False)],
                      sheet="A-101")
    f = _drawing_with(tmp_path / "F.dxf", note="FLOOR NOTE", xrefs=[("X.dwg", True)])
    x = _drawing_with(tmp_path / "X.dxf", note="SITE NOTE")
    names = [("A/S.dxf", s), ("B/F.dxf", f), ("C/X.dxf", x)]
    z = _zip(tmp_path, names if order == "host first" else names[::-1])
    cs = ingest(z, str(tmp_path / "w"))
    assert len(cs.pages) == 1
    words = _words(cs, 0)
    assert "SHEET NOTE" in words and "FLOOR NOTE" in words
    assert "SITE NOTE" not in words
    assert not any("not included in the upload" in w for w in cs.warnings), cs.warnings


def test_an_attached_xref_inside_an_xref_is_drawn(tmp_path):
    """The control: attached, not overlaid, nested content does come along."""
    from fbcreview.cad import ingest
    s = _drawing_with(tmp_path / "S.dxf", note="SHEET NOTE", xrefs=[("F.dwg", False)],
                      sheet="A-101")
    f = _drawing_with(tmp_path / "F.dxf", note="FLOOR NOTE", xrefs=[("X.dwg", False)])
    x = _drawing_with(tmp_path / "X.dxf", note="SITE NOTE")
    cs = ingest(_zip(tmp_path, [("S.dxf", s), ("F.dxf", f), ("X.dxf", x)]), str(tmp_path / "w"))
    words = _words(cs, 0)
    assert all(n in words for n in ("SHEET NOTE", "FLOOR NOTE", "SITE NOTE"))
    assert words.count("SITE NOTE") == 1


@pytest.mark.parametrize("host, other", [("A", "B"), ("Z", "A")])
def test_a_circular_reference_is_drawn_once_and_never_called_missing(tmp_path, host, other):
    """A attaches B and B attaches A. The adapter warned that A "was not
    included in the upload" — it was — and, when the host sorted after its
    xref, drew the host's own note five times over itself."""
    from fbcreview.cad import ingest
    h = _drawing_with(tmp_path / f"{host}.dxf", note="HOST NOTE",
                      xrefs=[(f"{other}.dwg", False)], sheet="A-101")
    o = _drawing_with(tmp_path / f"{other}.dxf", note="OTHER NOTE",
                      xrefs=[(f"{host}.dwg", False)])
    cs = ingest(_zip(tmp_path, [(f"{host}.dxf", h), (f"{other}.dxf", o)]), str(tmp_path / "w"))
    assert len(cs.pages) == 1
    words = _words(cs, 0)
    assert words.count("HOST NOTE") == 1 and words.count("OTHER NOTE") == 1, words
    assert not any("not included in the upload" in w for w in cs.warnings), cs.warnings
    sheet = next(d for d in cs.data["drawings"] if d.get("role") == "sheets")
    assert any(v.startswith("circular") for v in sheet["xrefs"].values()), sheet["xrefs"]


def test_two_model_space_drawings_that_overlay_each_other_are_both_reviewed(tmp_path):
    """Each was "referenced", so each was taken for an xref only, and the set
    was refused: no sheets. Neither is reached from a sheet, so both are."""
    from fbcreview.cad import ingest
    a = _drawing_with(tmp_path / "A.dxf", note="PLAN A", xrefs=[("B.dwg", True)])
    b = _drawing_with(tmp_path / "B.dxf", note="PLAN B", xrefs=[("A.dwg", True)])
    cs = ingest(_zip(tmp_path, [("A.dxf", a), ("B.dxf", b)]), str(tmp_path / "w"))
    assert len(cs.pages) == 2
    assert [d["role"] for d in cs.data["drawings"]] == ["sheets", "sheets"]


def test_xrefs_copied_past_the_memory_budget_are_refused(tmp_path, monkeypatch):
    """Every embed copies the referenced model space into the host. Measured:
    300 xref blocks naming one 1.2 MB base, in a 104 KB zip, peaked at 1.7 GB.
    Each copy is charged against the budget the DXF itself is."""
    from fbcreview import cad
    base = _drawing_with(tmp_path / "BASE.dxf", note="BASE PLAN")
    doc = ezdxf.new("R2018", setup=True)
    for k in range(30):
        ezdxf.xref.define(doc, f"X{k}", "C:\\P\\BASE.dwg")
        doc.modelspace().add_blockref(f"X{k}", (0, 0))
    lay = doc.layouts.new("A-1")
    lay.page_setup(size=(36, 24), margins=(0, 0, 0, 0), units="inch")
    lay.add_viewport(center=(15, 12), size=(24, 14), view_center_point=(240, 120),
                     view_height=14 * 48)
    host = tmp_path / "HOST.dxf"
    doc.saveas(host)
    sizes = base.stat().st_size + host.stat().st_size
    monkeypatch.setenv("FBC_CAD_MAX_DXF_MB",
                       f"{(sizes + 10 * base.stat().st_size) / 1024 ** 2:.6f}")
    with pytest.raises(cad.SourceError) as exc:
        cad.ingest(_zip(tmp_path, [("HOST.dxf", host), ("BASE.dxf", base)]),
                   str(tmp_path / "w"))
    assert exc.value.code == "drawing_too_large"
    assert "external reference" in exc.value.message


def test_many_sheets_sharing_one_base_fit_because_each_is_released(tmp_path, monkeypatch):
    """The ordinary shape that the budget must not refuse: twelve sheet files,
    each referencing one base plan. Each sheet is embedded, plotted and let go
    before the next, so the budget is charged for one copy at a time."""
    from fbcreview.cad import ingest
    base = _drawing_with(tmp_path / "BASE.dxf", note="BASE PLAN")
    members = [("XREF/BASE.dxf", base)]
    for i in range(12):
        members.append((f"A-{101 + i}.dxf",
                        _drawing_with(tmp_path / f"A-{101 + i}.dxf", note=f"SHEET {101 + i}",
                                      xrefs=[("BASE.dwg", False)], sheet=f"A-{101 + i}")))
    sizes = sum(p.stat().st_size for _n, p in members)
    monkeypatch.setenv("FBC_CAD_MAX_DXF_MB",
                       f"{(sizes + 3 * base.stat().st_size) / 1024 ** 2:.6f}")
    cs = ingest(_zip(tmp_path, members), str(tmp_path / "w"))
    assert len(cs.pages) == 12
    assert all("BASE PLAN" in _words(cs, p) for p in range(12))


# ── a converter that crashes, a file that ends early, output past the budget ─

def _converter(tmp_path, body):
    """A stand-in `dwg2dxf` running `body` (Python) with OUT and SRC defined."""
    exe = tmp_path / "dwg2dxf"
    exe.write_text("\n".join([
        f"#!{sys.executable}", "import os, shutil, signal, sys",
        "if '--version' in sys.argv:", "    print('dwg2dxf 0.0-test'); sys.exit(0)",
        "OUT = sys.argv[sys.argv.index('-o') + 1]", "SRC = sys.argv[-1]", body]) + "\n")
    exe.chmod(0o755)
    return str(exe)


def _dwg(tmp_path, name="A-101.dwg"):
    from fixtures import cad_drawings as drawings
    src = tmp_path / name
    src.write_bytes(drawings.dwg_bytes())
    return src


def test_a_converter_that_crashes_partway_is_a_failed_conversion(tmp_path, monkeypatch):
    """Measured: a converter that wrote 90% of a sheet set and died on SIGSEGV
    left a DXF ezdxf could recover — with no layouts, so it was reviewed as a
    model-space drawing fitted to ARCH D, and nobody was told."""
    from fixtures import cad_drawings as drawings

    from fbcreview import cad
    from fbcreview.cad import convert
    whole = drawings.permit_set(str(tmp_path / "whole.dxf"))["path"]
    monkeypatch.setenv("FBC_DWG2DXF", _converter(tmp_path, "\n".join([
        f"data = open({whole!r}, 'rb').read()",
        "open(OUT, 'wb').write(data[:int(len(data) * 0.9)])",
        "os.kill(os.getpid(), signal.SIGSEGV)"])))
    convert._version_of.cache_clear()
    with pytest.raises(convert.ConversionFailed):
        cad.ingest(str(_dwg(tmp_path)), str(tmp_path / "w"), name="A-101.dwg")
    convert._version_of.cache_clear()


def test_a_dxf_that_ends_early_is_refused_not_read_as_model_space(tmp_path):
    from fixtures import cad_drawings as drawings

    from fbcreview import cad
    from fbcreview.cad import read
    whole = drawings.permit_set(str(tmp_path / "whole.dxf"))["path"]
    data = open(whole, "rb").read()
    cut = tmp_path / "cut.dxf"
    cut.write_bytes(data[:int(len(data) * 0.9)])
    with pytest.raises(read.ReadError) as exc:
        cad.ingest(str(cut), str(tmp_path / "w"), name="cut.dxf")
    assert "incomplete" in str(exc.value)


def test_a_conversion_is_stopped_at_the_budget_not_after_it(tmp_path, monkeypatch):
    """The DXF budget was checked once every DWG was converted, on an in-memory
    disk; a DWG that converts to gigabytes filled it first. The converter now
    runs with the remaining budget as its file-size limit."""
    from fbcreview import cad
    from fbcreview.cad import convert
    monkeypatch.setenv("FBC_CAD_MAX_DXF_MB", "1")
    monkeypatch.setenv("FBC_DWG2DXF", _converter(tmp_path, "\n".join([
        "f = open(OUT, 'wb')",
        "for _ in range(40):", "    f.write(b'0\\nLINE\\n' * 20000); f.flush()"])))
    convert._version_of.cache_clear()
    with pytest.raises(cad.SourceError) as exc:
        cad.ingest(str(_dwg(tmp_path)), str(tmp_path / "w"), name="A-101.dwg")
    assert exc.value.code == "drawing_too_large"
    out = tmp_path / "w" / "000.dxf"
    assert not out.exists() or out.stat().st_size <= 1024 ** 2
    convert._version_of.cache_clear()


def test_the_second_dwg_is_not_converted_once_the_first_spent_the_budget(tmp_path,
                                                                          monkeypatch):
    from fixtures import cad_drawings as drawings

    from fbcreview import cad
    from fbcreview.cad import convert
    whole = drawings.permit_set(str(tmp_path / "whole.dxf"))["path"]
    log = tmp_path / "calls.log"
    monkeypatch.setenv("FBC_DWG2DXF", _converter(tmp_path, "\n".join([
        f"open({str(log)!r}, 'a').write(os.path.basename(SRC) + '\\n')",
        f"shutil.copyfile({whole!r}, OUT)"])))
    monkeypatch.setenv("FBC_CAD_MAX_DXF_MB", f"{os.path.getsize(whole) * 1.5 / 1024 ** 2:.6f}")
    convert._version_of.cache_clear()
    z = _zip(tmp_path, [("A.dwg", _dwg(tmp_path, "A.dwg")), ("B.dwg", _dwg(tmp_path, "B.dwg")),
                        ("C.dwg", _dwg(tmp_path, "C.dwg"))])
    with pytest.raises(cad.SourceError) as exc:
        cad.ingest(z, str(tmp_path / "w"))
    assert exc.value.code == "drawing_too_large"
    assert len(log.read_text().split()) == 2       # A fit; B spent it; C never ran
    convert._version_of.cache_clear()


# ── what a sheet's text layer and title block are read from ─────────────────

def _sheet_doc():
    doc = ezdxf.new("R2018", setup=True)
    doc.header["$INSUNITS"] = 1
    lay = doc.layouts.new("A-101")
    lay.page_setup(size=(36, 24), margins=(0, 0, 0, 0), units="inch")
    lay.dxf_layout.dxf.taborder = 1
    tb = doc.blocks.new("TB")
    tb.add_lwpolyline([(0, 0), (4, 0), (4, 22), (0, 22)], close=True)
    for tag, at in (("SHEET_NO", (0.25, 1.0)), ("OCCUPANCY_GROUP", (0.25, 4.0))):
        tb.add_attdef(tag, at, dxfattribs={"height": 0.3})
    return doc, lay


def _words_of_page(cs, page=0):
    import pymupdf
    with pymupdf.open(cs.pdf_path) as pdf:
        return pdf[page].get_text("words")


def test_a_callout_seen_through_a_viewport_never_names_the_sheet(tmp_path):
    """A section callout in model space carries SHEETNUMBER=A-501, the sheet it
    points to, drawn at 48x through a 1:48 viewport. Its raw cap height beat
    the title block's 0.3 in field and the sheet became A-501."""
    from fbcreview.cad import ingest
    doc, lay = _sheet_doc()
    lay.add_blockref("TB", (31.0, 1.0)).add_auto_attribs(
        {"SHEET_NO": "A-101", "OCCUPANCY_GROUP": "B"})
    lay.add_viewport(center=(15, 12), size=(24, 14), view_center_point=(240, 120),
                     view_height=14 * 48)
    callout = doc.blocks.new("CALLOUT")
    callout.add_circle((0, 0), 0.25)
    callout.add_attdef("SHEETNUMBER", (-0.1, -0.15), dxfattribs={"height": 0.1})
    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0), (480, 0), (480, 240), (0, 240)], close=True)
    msp.add_blockref("CALLOUT", (240, 120), dxfattribs={"xscale": 48, "yscale": 48}) \
        .add_auto_attribs({"SHEETNUMBER": "A-501"})
    doc.saveas(tmp_path / "s.dxf")
    cs = ingest(str(tmp_path / "s.dxf"), str(tmp_path / "w"), name="s.dxf")
    assert cs.data["pages"][0]["number"] == "A-101"


def test_text_parked_off_the_paper_is_not_read_as_on_the_sheet(tmp_path):
    """A second title block parked to the right of the 36 x 24 in paper, as
    drafters do with spare blocks. Nothing of it is printed, but its
    attributes became claims — `A-3` "printed on A-101" — and could name the
    sheet."""
    from fbcreview.cad import ingest
    doc, lay = _sheet_doc()
    lay.add_blockref("TB", (31.0, 1.0)).add_auto_attribs(
        {"SHEET_NO": "A-101", "OCCUPANCY_GROUP": "B"})
    lay.add_blockref("TB", (45.0, 1.0)).add_auto_attribs(
        {"SHEET_NO": "X-999", "OCCUPANCY_GROUP": "A-3"})
    doc.saveas(tmp_path / "s.dxf")
    cs = ingest(str(tmp_path / "s.dxf"), str(tmp_path / "w"), name="s.dxf")
    assert cs.data["pages"][0]["number"] == "A-101"
    texts = {r.get("text") for r in cs.data["claims"] if r.get("type") == "attribute"}
    assert "B" in texts and "A-3" not in texts and "X-999" not in texts
    page_words = [w[4] for w in _words_of_page(cs)]
    assert "A-3" not in page_words


def test_a_tab_is_placed_where_it_is_drawn(tmp_path):
    """ezdxf draws an MTEXT tab as eight spaces and a TEXT tab as `?`; the
    text layer measured the raw string, so the words after a tab sat about
    eight spaces left of their ink, and a TEXT `TYPE:<tab>III-B` fused."""
    from fbcreview.cad import ingest

    def drawn(text_mtext, text_text):
        doc, lay = _sheet_doc()
        lay.add_mtext(text_mtext, dxfattribs={"char_height": 0.125}).set_location((2, 20))
        lay.add_text(text_text, height=0.125).set_placement((2, 15))
        name = f"t{abs(hash(text_mtext))}.dxf"
        doc.saveas(tmp_path / name)
        return ingest(str(tmp_path / name), str(tmp_path / ("w" + name)), name=name)

    tabbed = _words_of_page(drawn("1.\tPROVIDE TWO LAYERS", "TYPE:\tIII-B"))
    spaced = _words_of_page(drawn("1.        PROVIDE TWO LAYERS", "TYPE: III-B"))
    by_word = {w[4]: w for w in spaced}
    for w in tabbed:
        if w[4] in ("PROVIDE", "LAYERS"):
            assert abs(w[0] - by_word[w[4]][0]) < 1.0, (w, by_word[w[4]])
    assert [w[4] for w in tabbed if w[4].startswith(("TYPE", "III"))] == ["TYPE:", "III-B"]


def test_two_texts_in_one_block_are_two_statements(tmp_path):
    """Two `OCCUPANCY: B` notes inside one model-space INSERT, one seen on
    each of two sheets, carried the INSERT's handle alike, so the fact store
    took them for one entity shown twice (MEDIUM). Without the sidecar the
    same PDF says HIGH, "also stated on". Each text is now its own source."""
    from fbcreview.cad import ingest
    from fbcreview.pipeline import build_facts
    doc = ezdxf.new("R2018", setup=True)
    doc.header["$INSUNITS"] = 1
    notes = doc.blocks.new("NOTES")
    notes.add_text("RISK CATEGORY: III", height=12).set_placement((0, 0))
    notes.add_text("RISK CATEGORY: III", height=12).set_placement((2000, 0))
    msp = doc.modelspace()
    msp.add_blockref("NOTES", (0, 0))
    for i, x in enumerate((100, 2100)):
        lay = doc.layouts.new(f"A-10{i + 1}")
        lay.page_setup(size=(36, 24), margins=(0, 0, 0, 0), units="inch")
        lay.dxf_layout.dxf.taborder = i + 1
        lay.add_viewport(center=(15, 12), size=(24, 14), view_center_point=(x, 0),
                         view_height=14 * 48)
    doc.saveas(tmp_path / "n.dxf")
    cs = ingest(str(tmp_path / "n.dxf"), str(tmp_path / "w"), name="n.dxf")
    from fbcreview.confidence import HIGH
    handles = {h for p in cs.data["pages"] for c in p["text"] for h in c["handles"]}
    assert len(handles) >= 2, handles
    r = build_facts(cs.pdf_path, cad=cs.data).store.resolve("risk_category")
    assert r.value == "III" and r.confidence == HIGH, (r.confidence, r.evidence().note)
    assert "also stated on p2" in r.evidence().note        # no title block: named by page


# ── units ────────────────────────────────────────────────────────────────────

#: A US survey foot is 1200/3937 m exactly (NIST); the others are 1/12, 3 and
#: 5280 of it. ezdxf converts none of them, so these are from the definition.
SURVEY_FOOT_IN = 1200 / 3937 / 0.0254


@pytest.mark.parametrize("code, inches", [
    (21, SURVEY_FOOT_IN), (22, SURVEY_FOOT_IN / 12), (23, SURVEY_FOOT_IN * 3),
    (24, SURVEY_FOOT_IN * 5280),
])
def test_us_survey_units_are_what_the_law_says(code, inches):
    """$INSUNITS 21 was stored as 3.6576 in and 22 as 0.3048 in — metres per
    survey foot taken for inches, about 3.28x short — so every HIGH viewport
    scale and measurement on a civil drawing in survey feet was off by that."""
    from fbcreview.cad.read import drawing_units
    doc = ezdxf.new("R2018")
    doc.header["$INSUNITS"] = code
    assert drawing_units(doc).inches == pytest.approx(inches, rel=1e-9)
    assert drawing_units(doc).basis == "stated"


@pytest.mark.parametrize("code", [1, 2, 3, 4, 5, 6, 7, 10, 14, 15, 16])
def test_every_other_unit_agrees_with_ezdxf(code):
    from ezdxf import units

    from fbcreview.cad.read import drawing_units
    doc = ezdxf.new("R2018")
    doc.header["$INSUNITS"] = code
    assert drawing_units(doc).inches == pytest.approx(units.conversion_factor(code, 1),
                                                      rel=1e-6)


# ── viewport repair ──────────────────────────────────────────────────────────

def test_a_layout_with_no_paper_viewport_keeps_its_only_view(tmp_path):
    """LibreDWG zeroes viewport statuses; the repair gave status 1 — the
    layout's own, undrawn viewport — to the lowest handle when none looked
    like the paper viewport. On a layout whose only viewport shows the plan,
    the plan vanished, and the sidecar still reported its exact scale."""
    from fbcreview.cad import ingest
    doc = ezdxf.new("R2018", setup=True)
    doc.header["$INSUNITS"] = 1
    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0), (480, 0), (480, 240), (0, 240)], close=True)
    msp.add_text("FLOOR PLAN NOTE", height=12).set_placement((100, 100))
    lay = doc.layouts.new("A-101")
    lay.page_setup(size=(36, 24), margins=(0, 0, 0, 0), units="inch")
    for vp in list(lay.query("VIEWPORT")):
        lay.delete_entity(vp)                       # no paper viewport at all
    lay.add_viewport(center=(15, 12), size=(24, 14), view_center_point=(240, 120),
                     view_height=14 * 48)
    lay.add_text("GENERAL NOTES", height=0.2).set_placement((30, 2))
    for vp in lay.query("VIEWPORT"):
        vp.dxf.status = 0
        vp.dxf.id = 0
    doc.saveas(tmp_path / "one.dxf")
    cs = ingest(str(tmp_path / "one.dxf"), str(tmp_path / "w"), name="one.dxf")
    assert "FLOOR PLAN NOTE" in " ".join(w[4] for w in _words_of_page(cs))
    vps = cs.data["pages"][0]["viewports"]
    assert len(vps) == 1 and vps[0]["drawn"] > 0


# ── a PDF set whose sheets differ in size ────────────────────────────────────

def test_a_smaller_pdf_sheet_keeps_its_margin_on_the_page(tmp_path):
    """Sizing each sheet's margin from page 0 drew the rail and legend of a
    smaller sheet up to 900 pt below its foot. Each sheet is now sized from
    itself — for a PDF too, which is a change from before, and the right one."""
    import pymupdf

    from fbcreview.options import ReviewOptions
    from fbcreview.pipeline import build_facts
    from fbcreview.render.markup import render
    from fbcreview.rules import run_all
    src = tmp_path / "mixed.pdf"
    doc = pymupdf.open()
    for w, h, sheet in ((2592, 1728, "A-101"), (1224, 792, "A-102")):
        page = doc.new_page(width=w, height=h)
        page.draw_rect(pymupdf.Rect(36, 36, w - 36, h - 36))
        page.insert_text((w - 200, h - 60), f"SHEET {sheet}", fontsize=14)
    doc.save(src)
    facts = build_facts(str(src))
    res = run_all(facts)
    out = tmp_path / "markup.pdf"
    render(str(src), str(out), res.findings, facts.sheets, ReviewOptions(), res.abstentions,
           res.reconciled)
    with pymupdf.open(out) as pdf:
        small = pdf[1]
        lowest = max(d["rect"].y1 for d in small.get_drawings())
        assert lowest <= small.rect.height + 0.5
        assert "LEGEND" in small.get_text().upper()


# ── a tag describes what it tags ─────────────────────────────────────────────

def _life_safety(tmp_path, *, layouts=True):
    """The reviewer's life-safety plan: room tags in model space carry each
    room's AREA and OCCUPANT_LOAD; the sheet's code block prints the building's
    totals — occupant load 600, which needs three exits, and two provided."""
    doc = ezdxf.new("R2018", setup=True)
    doc.header["$INSUNITS"] = 1
    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0), (960, 0), (960, 480), (0, 480)], close=True)
    room = doc.blocks.new("ROOM-TAG")
    room.add_lwpolyline([(-24, -14), (24, -14), (24, 14), (-24, 14)], close=True)
    for tag, y in (("ROOM_NAME", 7), ("AREA", 0), ("OCCUPANT_LOAD", -7)):
        room.add_attdef(tag, (-22, y), dxfattribs={"height": 3})
    for i, (name, area, load) in enumerate((("HALL 101", "675 SF", "45"),
                                            ("MEETING 102", "300 SF", "20"),
                                            ("OFFICE 103", "150 SF", "1"))):
        msp.add_blockref("ROOM-TAG", (200 + 250 * i, 300)).add_auto_attribs(
            {"ROOM_NAME": name, "AREA": area, "OCCUPANT_LOAD": load})
    space = msp
    if layouts:
        space = doc.layouts.new("LS")
        space.page_setup(size=(36, 24), margins=(0, 0, 0, 0), units="inch")
        space.add_viewport(center=(12.5, 13.0), size=(22.0, 12.0),
                           view_center_point=(480, 240), view_height=12.0 * 48)
        x, y, h = 24.5, 22.5, 0.125
    else:
        x, y, h = 1000, 460, 6.0
    for label, value in (("OCCUPANCY GROUP:", "A-3"), ("TOTAL OCCUPANT LOAD:", "600"),
                         ("NUMBER OF EXITS PROVIDED:", "2")):
        space.add_text(label, height=h).set_placement((x, y))
        space.add_text(value, height=h).set_placement((x, y - 1.25 * h))
        y -= 4 * h
    path = tmp_path / "ls.dxf"
    doc.saveas(path)
    return path


@pytest.mark.parametrize("layouts", [True, False], ids=["sheet", "model-space"])
def test_a_room_tags_load_is_not_the_buildings(tmp_path, layouts):
    """With the sidecar, room 101's tag OCCUPANT_LOAD = 45 scored 1.0 against
    the printed "TOTAL OCCUPANT LOAD" (0.99), won, and EGRESS.EXIT_COUNT went
    from OPEN CRITICAL ("600 requires 3 exits; 2 provided") to a VERIFIED pass.
    Uploading the drawing behind a set must never turn a failure into a pass."""
    from fbcreview.cad import ingest
    from fbcreview.pipeline import build_facts
    from fbcreview.rules import run_all

    cs = ingest(str(_life_safety(tmp_path, layouts=layouts)), str(tmp_path / "w"),
                name="ls.dxf")
    for cad in (None, cs.data):
        facts = build_facts(cs.pdf_path, cad=cad)
        load = facts.store.resolve("occupant_load")
        assert load is not None and load.value == 600, (cad is not None, load)
        exits = [f for f in run_all(facts).findings if f.rule_id == "EGRESS.EXIT_COUNT"]
        assert exits and exits[0].severity == "CRITICAL", (cad is not None, exits)


# ── blocks that expand past what one review can plot ────────────────────────

def _nested(path, depth=6, fan=10):
    """Block L0 is one line; each L_k holds `fan` inserts of L_(k-1). About 20
    KB on disk; `fan ** depth` lines drawn."""
    doc = ezdxf.new("R2018")
    doc.blocks.new("L0").add_line((0, 0), (1, 0))
    for k in range(1, depth + 1):
        b = doc.blocks.new(f"L{k}")
        for i in range(fan):
            b.add_blockref(f"L{k - 1}", (i * 2, 0))
    doc.modelspace().add_blockref(f"L{depth}", (0, 0))
    doc.saveas(path)
    return path


def test_nested_blocks_that_expand_past_the_cap_are_refused_at_once(tmp_path):
    """Measured: depth 5 took 131 s, depth 6 passed the 600 s timeout at 634 MB
    and climbing, holding the instance's only CAD slot. Counted, not drawn: the
    reference drawing expands to 304 147 entities in 0.09 s."""
    import time

    from fbcreview import cad
    t0 = time.monotonic()
    with pytest.raises(cad.SourceError) as exc:
        cad.ingest(str(_nested(tmp_path / "nest.dxf")), str(tmp_path / "w"), name="nest.dxf")
    assert exc.value.code == "drawing_too_large"
    assert "expands" in exc.value.message
    assert time.monotonic() - t0 < 10


def test_a_block_array_is_counted_by_its_rows_and_columns(tmp_path):
    from fbcreview.cad import read
    doc = ezdxf.new("R2018")
    doc.blocks.new("ONE").add_line((0, 0), (1, 0))
    doc.modelspace().add_blockref("ONE", (0, 0), dxfattribs={
        "row_count": 1000, "column_count": 1000, "row_spacing": 2, "column_spacing": 2})
    assert read.expanded_count(doc) >= 1_000_000


def test_in_a_zip_the_drawing_too_large_to_plot_costs_only_itself(tmp_path):
    from fbcreview.cad import ingest
    sheet = _drawing_with(tmp_path / "A-101.dxf", note="SHEET NOTE", sheet="A-101")
    cs = ingest(_zip(tmp_path, [("A-101.dxf", sheet), ("NEST.dxf", _nested(tmp_path / "n.dxf"))]),
                str(tmp_path / "w"))
    assert len(cs.pages) == 1 and "SHEET NOTE" in _words(cs, 0)
    assert any("NEST.dxf" in w and "expands" in w for w in cs.warnings), cs.warnings


# ── one note seen twice is not two sheets agreeing ──────────────────────────

def test_one_note_shown_on_two_sheets_is_not_a_cross_sheet_pass(tmp_path):
    """One model-space TEXT "RISK CATEGORY: II", seen through a viewport on
    each of two sheets, is one statement shown twice — the fact store says so —
    but XSHEET.STATED_CONFLICT counted two sheets and issued a VERIFIED pass for
    a comparison that could never fail. With nothing compared, it abstains."""
    from fbcreview.cad import ingest
    from fbcreview.pipeline import build_facts
    from fbcreview.rules import run_all
    doc = ezdxf.new("R2018", setup=True)
    doc.header["$INSUNITS"] = 1
    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0), (480, 0), (480, 240), (0, 240)], close=True)
    msp.add_text("RISK CATEGORY: II", height=12).set_placement((60, 120))
    tb = doc.blocks.new("TB")
    tb.add_attdef("SHEET_NO", (0, 0), dxfattribs={"height": 0.5})
    for i, number in enumerate(("A-101", "A-102")):
        lay = doc.layouts.new(number)
        lay.page_setup(size=(36, 24), margins=(0, 0, 0, 0), units="inch")
        lay.dxf_layout.dxf.taborder = i + 1
        lay.add_viewport(center=(15, 12), size=(24, 14), view_center_point=(240, 120),
                         view_height=14 * 48)
        lay.add_blockref("TB", (31.0, 1.0)).add_auto_attribs({"SHEET_NO": number})
    doc.saveas(tmp_path / "x.dxf")
    cs = ingest(str(tmp_path / "x.dxf"), str(tmp_path / "w"), name="x.dxf")
    res = run_all(build_facts(cs.pdf_path, cad=cs.data))
    passed = [f for f in res.findings if f.rule_id == "XSHEET.STATED_CONFLICT"]
    assert passed == [], [(f.status, f.result) for f in passed]
    assert any(a.rule_id == "XSHEET.STATED_CONFLICT" for a in res.abstentions)
