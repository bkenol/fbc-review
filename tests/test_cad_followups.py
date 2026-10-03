"""Follow-ups to the drawing adapter that the first build got wrong, each with the
input that showed it.

* A drawing job was offered "re-run with the pasted sheets rebuilt" when its
  plot carried an image (a logo, an underlay): `source` on the record is
  `pdfkind`'s profile of *our* plot, and a drawing's re-run never rebuilds, so
  the advice could do nothing.
* `MEASURE.EGRESS_EXTENT` described a scale read from a drawing — a viewport's
  exact ratio, or the factor a model-space drawing was fitted to the sheet at —
  as "the scale the sheet itself states", which a drawing never does.
"""
from __future__ import annotations

import pytest

from webapp import storage as storage_mod
from webapp.worker import CAD_STAGE, STAGES

#: Enough rules standing down for want of a value to make a pattern.
STARVED = [{"rule": f"RULE.{i}", "reason": "occupant load not extracted"} for i in range(8)]

SUMMARY = {
    "sheets": 1, "pages": 1, "cad_layers": 4, "annotations": 0, "marked": 0,
    "counts": {}, "open": 0, "verified": 0, "abstentions": STARVED, "rules_run": 8,
    "scale_pages": 1, "pdf_bytes": 9, "pdf_name": "Tower — CODE REVIEW.pdf",
    "findings_count": 0,
}

#: `pdfkind`'s profile of a vector sheet with a pasted picture on page 0.
SOURCE = {
    "kind": "mixed", "cad_layers": 4, "reviewable_pages": 1,
    "raster_pages": [], "region_pages": [0],
    "summary": "1 sheet carries a pasted image.",
    "sheets": [{"page": 0, "kind": "hybrid", "vector_items": 900, "live_chars": 400,
                "image_count": 1, "image_coverage": 0.2, "reason": "a pasted image",
                "raster_regions": [{"x0": 10.0, "y0": 10.0, "x1": 300.0, "y1": 200.0,
                                    "megapixels": 2.1, "coverage": 0.2}]}],
}


def _done(client, job_id, filename, source_format):
    store = client.fake_store
    store.create(job_id=job_id, uid="uid-alice", email="allowed@example.com",
                 filename=filename, size_bytes=10, pages=1, options={},
                 upload_blob=storage_mod.upload_path(job_id, filename),
                 stages=([CAD_STAGE] if source_format != "pdf" else []) + list(STAGES))
    fields = {"source": SOURCE}
    if source_format != "pdf":
        fields.update(source_format=source_format,
                      viewer_blob=storage_mod.output_path(job_id, storage_mod.SOURCE_PDF))
    store.update(job_id, **fields)
    summary = dict(SUMMARY)
    if source_format != "pdf":
        summary["source_format"] = source_format
    store.mark_done(job_id, summary)
    return client.get(f"/api/jobs/{job_id}").json()


def test_a_pdf_with_a_pasted_table_is_still_offered_the_rebuild(client):
    """The control: the diagnosis this file narrows must still reach a PDF."""
    body = _done(client, "job-pdf", "set.pdf", "pdf")
    assert "pasted_code_table" in [d["key"] for d in body["diagnosis"]]


def test_a_drawing_is_never_offered_a_rebuild_it_cannot_use(client):
    body = _done(client, "job-cad", "Tower.dwg", "dwg")
    keys = [d["key"] for d in body["diagnosis"]]
    assert "pasted_code_table" not in keys
    assert not any(d.get("rerun") for d in body["diagnosis"])
    assert all(a.get("suggested") != "data_in_image"
               for a in body["summary"]["abstentions"])


# ── the egress measurement's wording ─────────────────────────────────────────

ezdxf = pytest.importorskip("ezdxf")


def _egress_drawing(path):
    """A model-space-only drawing in inches: a 40 ft egress run on its own layer."""
    doc = ezdxf.new("R2018")
    doc.units = 1
    doc.header["$INSUNITS"] = 1
    doc.layers.add("A-EGRESS PATH")
    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0), (480, 0)], dxfattribs={"layer": "A-EGRESS PATH"})
    msp.add_lwpolyline([(0, -60), (600, -60), (600, 300), (0, 300)], close=True)
    msp.add_text("FIRST FLOOR", height=12, dxfattribs={"insert": (0, 320)})
    doc.saveas(path)
    return path


def test_a_drawings_scale_is_not_called_the_sheets_own(tmp_path):
    from fbcreview.cad import ingest
    from fbcreview.pipeline import build_facts
    from fbcreview.rules import run_all

    cs = ingest(str(_egress_drawing(tmp_path / "egress.dxf")), str(tmp_path / "w"),
                name="egress.dxf")
    res = run_all(build_facts(cs.pdf_path, cad=cs.data))
    meas = [f for f in res.findings if f.rule_id == "MEASURE.EGRESS_EXTENT"]
    assert meas, [a.reason for a in res.abstentions if a.rule_id == "MEASURE.EGRESS_EXTENT"]
    f = meas[0]
    assert "the scale the sheet itself states" not in f.checked
    assert "the drawing itself defines" in f.checked
    assert "/Measure" not in f.code and "drawing's own coordinates" in f.code
    # 480 in of model space is 40 ft, measured through the plot's exact fit.
    assert "40.0 ft" in f.title


# ── word fusion in complex MTEXT ─────────────────────────────────────────────

#: Gaps between the inks of consecutive words of one MTEXT, in × cap, measured on
#: the reference drawing's FLOORPLAN_VIEW1 notes (italic text in a substitute
#: font): every one of them is a space in the drawing, and every one fused.
MEASURED = [("CLIENT", "APPROVAL:", -0.014), ("PRODUCE", "ALL", 0.054),
            ("FROM", "ARCHITECTURAL", 0.083), ("ARCHITECTURAL", "DRAWINGS", 0.069),
            ("AMERICAN", "PRECAST", 0.138)]


@pytest.mark.parametrize("first, second, gap", MEASURED)
def test_complex_mtext_words_keep_their_space_however_tight(first, second, gap):
    from test_cad_overlay import CAP, run, words_of
    a = run(first, 2.0, 20.0, handle="40", kind="MTEXT")
    b = run(second, 2.0 + a.x1 + gap * CAP, 20.0, handle="40", kind="MTEXT")
    _doc, page = words_of([a, b])
    assert [w[4] for w in page.get_text("words")] == [first, second]


def test_an_inch_mark_after_a_number_still_closes_up():
    """The exception that must survive: closing punctuation drawn as its own
    word cell right after the value it belongs to."""
    from test_cad_overlay import CAP, run, words_of
    a = run("6", 2.0, 20.0, handle="41", kind="MTEXT")
    b = run('"', 2.0 + a.x1 + 0.05 * CAP, 20.0, handle="41", kind="MTEXT")
    _doc, page = words_of([a, b])
    assert [w[4] for w in page.get_text("words")] == ['6"']


# ── text ezdxf's layout engine refuses ───────────────────────────────────────

def test_a_zero_width_formatted_mtext_is_still_on_the_sheet():
    """The reference drawing's wall note `2-1/2"` has MTEXT width 9.7e-20 —
    AutoCAD does not wrap it; ezdxf's layout gave up and dropped it."""
    from ezdxf.addons.drawing import RenderContext
    from ezdxf.addons.drawing import pymupdf as pmb

    from fbcreview.cad.render import CapturePipeline, PlotFrontend

    doc = ezdxf.new("R2018")
    msp = doc.modelspace()
    msp.add_mtext('{\\C1;2-1/2"} ', dxfattribs={"char_height": 0.312, "width": 9.68e-20,
                                                 "attachment_point": 5})
    pipe = CapturePipeline(pmb.PyMuPdfBackend())
    PlotFrontend(RenderContext(doc), pipe).draw_layout(msp)
    assert [r.text.strip() for r in pipe.runs] == ['2-1/2"']


# ── one sheet number on several sheets ──────────────────────────────────────

def test_a_sheet_number_printed_on_several_sheets_is_reported(tmp_path):
    """The reference drawing's title blocks give `SZ-1` on three layouts — a
    title block copied and not renumbered. The number is what is printed, so
    each sheet keeps it; the person is told it repeats, since every finding on
    those sheets names the same sheet."""
    from fbcreview.cad import ingest

    doc = ezdxf.new("R2018")
    doc.header["$INSUNITS"] = 1
    tb = doc.blocks.new("TB")
    tb.add_attdef("SHEET_NO", (0, 0), dxfattribs={"height": 0.5, "prompt": "Sheet number"})
    for i, (tab, number) in enumerate((("ZONE A", "SZ-1"), ("ZONE B", "SZ-1"),
                                       ("ZONE C", "SZ-2"), ("ZONE D", "SZ-1"))):
        lay = doc.layouts.new(tab)
        lay.page_setup(size=(36, 24), margins=(0, 0, 0, 0), units="inch")
        lay.dxf_layout.dxf.taborder = i + 1
        lay.add_lwpolyline([(1, 1), (35, 1), (35, 23), (1, 23)], close=True)
        lay.add_blockref("TB", (31.0, 1.0)).add_auto_attribs({"SHEET_NO": number})
    path = tmp_path / "zones.dxf"
    doc.saveas(path)

    cs = ingest(str(path), str(tmp_path / "w"), name="zones.dxf")
    assert [p["number"] for p in cs.data["pages"]] == ["SZ-1", "SZ-1", "SZ-2", "SZ-1"]
    repeats = [w for w in cs.data["warnings"] if "SZ-1" in w]
    assert len(repeats) == 1, cs.data["warnings"]
    assert "3 sheets" in repeats[0]
    assert all(t in repeats[0] for t in ("ZONE A", "ZONE B", "ZONE D"))
    assert not any("SZ-2" in w for w in cs.data["warnings"])


# ── a set too large to hold in memory ───────────────────────────────────────

def test_a_set_too_large_to_read_is_refused_before_it_is_read(tmp_path, monkeypatch):
    """Reading the reference drawing's 170 MB of DXF peaked at 1.1 GB. A zip at
    the 120 MB upload limit can hold several such drawings, and every member is
    open at once while xrefs resolve: on a 4 GiB instance that is an
    out-of-memory kill, reported as "could not be read". The size is known as
    soon as the DXF exists, so the refusal comes then, and says what it is."""
    from fbcreview import cad
    from fbcreview.cad import read

    path = _egress_drawing(tmp_path / "big.dxf")
    size = path.stat().st_size
    monkeypatch.setenv("FBC_CAD_MAX_DXF_MB", f"{(size - 1) / 1024 ** 2:.6f}")
    opened = []
    monkeypatch.setattr(read, "open_dxf", lambda *a, **k: opened.append(a) or 1 / 0)
    with pytest.raises(cad.SourceError) as exc:
        cad.ingest(str(path), str(tmp_path / "w"), name="big.dxf")
    assert exc.value.code == "drawing_too_large"
    assert "MB" in exc.value.message and "memory" in exc.value.message
    assert opened == []                     # refused before ezdxf held any of it


def test_every_refusal_the_adapter_can_raise_is_typed_by_the_service():
    """The same closed-set check `test_cad_source` makes for `source.py`, over
    the package's entry point, against what `webapp/cadjob.py` maps."""
    import ast
    import inspect

    from fbcreview import cad
    from webapp.cadjob import _CODES
    tree = ast.parse(inspect.getsource(cad))
    codes = {node.args[0].value for node in ast.walk(tree)
             if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "SourceError"
             and node.args and isinstance(node.args[0], ast.Constant)}
    assert "drawing_too_large" in codes
    assert codes <= set(_CODES), codes - set(_CODES)


# ── a converter is named only when it ran ───────────────────────────────────

def test_a_dxf_upload_names_no_converter_even_where_one_is_installed(tmp_path, monkeypatch):
    """`CadReport.converter` is "empty when none ran (a DXF)". It was filled
    from whatever `dwg2dxf` was installed, so on the production image every DXF
    review said it had been converted by LibreDWG."""
    from fixtures import cad_drawings as drawings

    from fbcreview import cad
    from fbcreview.cad import convert

    prepared = drawings.permit_set(str(tmp_path / "prepared.dxf"))
    monkeypatch.setenv("FBC_DWG2DXF", drawings.fake_converter(str(tmp_path), prepared["path"]))
    convert._version_of.cache_clear()
    assert convert.version() == "dwg2dxf 0.0-test"           # installed

    dxf = cad.ingest(prepared["path"], str(tmp_path / "dxf"), name="A-101.dxf")
    assert dxf.data["converter"] == ""

    src = tmp_path / "A-101.dwg"
    src.write_bytes(drawings.dwg_bytes())
    dwg = cad.ingest(str(src), str(tmp_path / "dwg"), name="A-101.dwg")
    assert dwg.data["converter"] == "dwg2dxf 0.0-test"
    convert._version_of.cache_clear()


# ── a sheet whose views frame nothing ───────────────────────────────────────

def _two_plans(path):
    """`PLAN` frames the floor plate at 1:96. `ROOF` is the same sheet with its
    view panned to empty model space — the reference drawing's 6C, whose 1:128
    "ROOF LEVEL PLANKOUT" viewport frames 5 entities, none on a plotting layer,
    while 2A's 1:96 plan frames 704. Both carry a small detail viewport that
    does show something, and a title-block note in paper space."""
    doc = ezdxf.new("R2018")
    doc.header["$INSUNITS"] = 1
    msp = doc.modelspace()
    for i in range(40):                                  # an 80 x 40 ft plate of walls
        msp.add_line((i * 24, 0), (i * 24, 480))
    msp.add_lwpolyline([(0, 0), (960, 0), (960, 480), (0, 480)], close=True)
    doc.layers.add("HIDDEN-OFF").off()
    msp.add_line((5000, 5000), (5100, 5100), dxfattribs={"layer": "HIDDEN-OFF"})
    for i, (tab, centre) in enumerate((("PLAN", (480, 240)), ("ROOF", (5050, 5050)))):
        lay = doc.layouts.new(tab)
        lay.page_setup(size=(36, 24), margins=(0, 0, 0, 0), units="inch")
        lay.dxf_layout.dxf.taborder = i + 1
        lay.add_viewport(center=(15, 13), size=(26, 18), view_center_point=centre,
                         view_height=18 * 96)
        lay.add_viewport(center=(32, 20), size=(3, 3), view_center_point=(24, 24),
                         view_height=3 * 16)
        lay.add_text("GENERAL NOTES", height=0.2).set_placement((30, 2))
    doc.saveas(path)
    return path


def test_a_sheet_whose_views_frame_nothing_says_so(tmp_path):
    from fbcreview.cad import ingest

    cs = ingest(str(_two_plans(tmp_path / "plans.dxf")), str(tmp_path / "w"), name="plans.dxf")
    plan, roof = cs.data["pages"]
    drawn = {p["layout"]: [v["drawn"] for v in p["viewports"]] for p in (plan, roof)}
    big_plan, big_roof = (max(p["viewports"], key=lambda v: (v["rect"][2] - v["rect"][0])
                              * (v["rect"][3] - v["rect"][1])) for p in (plan, roof))
    assert big_plan["drawn"] >= 40, drawn
    assert big_roof["drawn"] == 0, drawn                 # the line on an OFF layer does not plot
    assert all(n > 0 for n in drawn["PLAN"]), drawn
    assert plan.get("empty_view_share", 0) == 0
    assert roof["empty_view_share"] > 0.9

    said = [w for w in cs.data["warnings"] if "frame nothing" in w]
    assert len(said) == 1 and "ROOF" in said[0] and "PLAN" not in said[0], cs.data["warnings"]


def test_the_margin_of_a_sheet_that_shows_nothing_says_what_was_not_seen(tmp_path):
    import pymupdf

    from fbcreview.cad import ingest
    from fbcreview.options import ReviewOptions
    from fbcreview.pipeline import build_facts
    from fbcreview.render.markup import render
    from fbcreview.rules import run_all

    cs = ingest(str(_two_plans(tmp_path / "plans.dxf")), str(tmp_path / "w"), name="plans.dxf")
    facts = build_facts(cs.pdf_path, cad=cs.data)
    assert [s["empty_view_share"] > 0.5 for s in facts.meta["cad"]["sheets"]] == [False, True]
    res = run_all(facts)
    out = str(tmp_path / "markup.pdf")
    render(cs.pdf_path, out, res.findings, facts.sheets, ReviewOptions(), res.abstentions,
           res.reconciled, cad=facts.meta["cad"])
    with pymupdf.open(out) as doc:
        plan, roof = (" ".join(doc[i].get_text().split()) for i in (0, 1))
    assert "frame nothing drawn" not in plan
    assert "frame nothing drawn" in roof
