"""A drawing read into the set the engine reviews, end to end, on drawings built here.

`fbcreview.cad.ingest` plots each sheet of a DWG, DXF or zip to a PDF page,
writes the drafter's own strings back as an invisible text layer, and keeps
what a plot throws away in a sidecar (`cad.json`). These tests build drawings
with ezdxf (`tests/fixtures/cad_drawings.py` — never a client drawing), run them
through ingest and the engine, and check what the review is built on:

* the sheets: one page per layout something is drawn on, in tab order, with
  the layer table, the repaired viewports and each viewport's exact scale;
* the sheet numbers: read from a title-block field that says it is the sheet
  number, never from a layout's tab name;
* the facts: a stacked code block resolves; an attribute is a claim that says
  it came from the drawing; one entity read twice is one reading; a value the
  sheet does not print, and a measured area, are never a stated fact;
* the edges: xrefs embedded from a zip or reported missing by name, a member or
  a layout that cannot be read costing only itself, the deterministic floor,
  replay, and the rule layer never reaching the adapter.

Every number asserted was measured on the fixture through the code under test.
"""
from __future__ import annotations

import ast
import gc
import json
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

ezdxf = pytest.importorskip("ezdxf")

import pymupdf  # noqa: E402

from fbcreview import cad  # noqa: E402
from fbcreview.cad import __main__ as cad_cli  # noqa: E402
from fbcreview.cad import claims as cad_claims  # noqa: E402
from fbcreview.cad import convert, read, render  # noqa: E402
from fbcreview.cad.source import SourceError  # noqa: E402
from fbcreview.confidence import HIGH, MEDIUM  # noqa: E402
from fbcreview.factstore import CAD, MEASURED  # noqa: E402
from fbcreview.pipeline import build_facts  # noqa: E402
from fbcreview.read import cad as cad_read  # noqa: E402
from fbcreview.read.catalog import BY_KEY  # noqa: E402
from fbcreview.rules import run_all  # noqa: E402
from fixtures import cad_drawings as drawings  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def permit(tmp_path_factory):
    root = tmp_path_factory.mktemp("permit")
    built = drawings.permit_set(str(root / "permit.dxf"))
    cs = cad.ingest(built["path"], str(root / "work"), name="permit.dxf")
    facts = build_facts(cs.pdf_path, cad=cs.data)
    pdf = pymupdf.open(cs.pdf_path)
    yield SimpleNamespace(built=built, cs=cs, facts=facts, pdf=pdf, root=root,
                          h=built["handles"])
    pdf.close()


def page_text(pdf, pno: int) -> str:
    return pdf[pno].get_text()


def all_text(pdf) -> str:
    return "\n".join(p.get_text() for p in pdf)


# ── the sheets ───────────────────────────────────────────────────────────────
def test_one_page_per_drawn_layout_in_tab_order(permit):
    """`Layout1` is empty and skipped; the rest plot in tab order, not in the
    order they were created (A-201 was created first)."""
    assert [p["layout"] for p in permit.cs.pages] == ["PLAN", "A-201", "S-1"]
    assert [p["page"] for p in permit.cs.pages] == [0, 1, 2]
    assert permit.pdf.page_count == 3
    # ARCH D at 72 pt to the inch; A-201 plots rotated 90°, so it is portrait
    assert tuple(permit.pdf[0].rect) == (0, 0, 2592, 1728)
    assert tuple(permit.pdf[1].rect) == (0, 0, 1728, 2592)
    assert all(p.rotation == 0 for p in permit.pdf)
    assert [t[1].split(" — ")[0] for t in permit.pdf.get_toc()] == ["A-101", "A-501", "S-1"]


def test_the_layer_table_survives_with_the_drafters_names(permit):
    ocgs = sorted(v["name"] for v in permit.pdf.get_ocgs().values())
    assert "Doors & Windows" in ocgs and "A-WALL" in ocgs      # case kept, '&' kept
    assert "a-wall" not in ocgs
    assert "A-HIDDEN" not in ocgs                               # frozen: nothing drawn on it
    # the drawing's whole table, as the drafter named it, reaches the engine
    assert "A-HIDDEN" in permit.cs.data["layers"]
    assert set(permit.facts.meta["cad_layers"]) == set(permit.cs.data["layers"])
    assert permit.facts.meta["native_vector"] is True


def test_viewports_left_at_status_zero_are_rebuilt_and_draw(permit, tmp_path):
    """LibreDWG writes status 0 on every viewport; ezdxf draws none of them
    until the status is rebuilt. Model-space text in the text layer is the
    proof they drew."""
    assert permit.cs.data["drawings"][0]["viewports_repaired"] == 5
    assert "ASSEMBLY HALL 101" in page_text(permit.pdf, 0)
    plan = permit.cs.pages[0]
    assert sorted(v["handle"] for v in plan["viewports"]) == sorted(
        [permit.h["vp48"], permit.h["vp16"]])                 # the 1:1 paper viewport is not one
    # A drawing whose statuses are already set (AutoCAD's own DXF) is left alone.
    built = drawings.permit_set(str(tmp_path / "ok.dxf"), status_zero=False)
    cs = cad.ingest(built["path"], str(tmp_path / "w"))
    assert cs.data["drawings"][0]["viewports_repaired"] == 0
    assert [p["layout"] for p in cs.pages] == ["PLAN", "A-201", "S-1"]
    assert len(cs.pages[0]["viewports"]) == 2


def test_each_sidecar_page_carries_its_placement_and_a_text_index(permit):
    plan = permit.cs.pages[0]
    assert len(plan["to_page"]) == 6 and plan["to_page"][0] == pytest.approx(72.0)
    for vp in plan["viewports"]:
        assert len(vp["to_page"]) == 6 and len(vp["rect"]) == 4
    cells = plan["text"]
    assert cells and all(len(c["box"]) == 4 and "handles" in c for c in cells)
    sheet_no = [c for c in cells if permit.h["attrib:SHEET_NO"] in c["handles"]]
    assert len(sheet_no) == 1 and sheet_no[0]["kind"] == "ATTRIB"
    # boxes and handles only: the strings live in the PDF
    assert not any("text" in c for c in cells)


def test_text_nobody_can_see_does_not_reach_the_text_layer(permit):
    """Text on a frozen layer, in paper space and in model space, and model
    text whose middle lies beyond the viewport's edge: none of it is on the
    sheet, so none of it may be read off it."""
    text = all_text(permit.pdf)
    for gone in ("FROZEN PAPER NOTE", "FROZEN MODEL NOTE", "BEYOND THE VIEWPORT EDGE"):
        assert gone not in text
    handles = {h for p in permit.cs.pages for c in p["text"] for h in c["handles"]}
    for key in ("frozen_paper", "frozen_model", "beyond"):
        assert permit.h[key] not in handles


# ── scale ────────────────────────────────────────────────────────────────────
def test_each_viewport_has_its_exact_scale_and_the_page_has_none(permit):
    page_wide, views = cad_read.page_scales(permit.cs.data, 0, "A-101")
    assert page_wide.value is None and not page_wide          # an abstention, saying why
    assert "18, 54" in page_wide.note
    by_value = {v.scale.value: v for v in views}
    assert set(by_value) == {18.0, 54.0}                       # 1:48 and 1:16, exactly
    assert all(v.scale.confidence == HIGH for v in views)
    assert permit.h["vp48"] in by_value[18.0].scale.source
    note = by_value[18.0].scale.note
    assert "1:48" in note and "not a printed label" in note
    # the 1:48 viewport: 22 x 12 in centred at (12.5, 13) on the sheet
    assert by_value[18.0].rect == pytest.approx((108.0, 360.0, 1692.0, 1224.0), abs=0.01)
    # the engine's geometry carries the same, and a box decides which governs
    geo = permit.facts.geometry[0]
    assert not geo.scale_pt_per_ft
    assert geo.scale_for((500, 700, 600, 800)).value == 18.0
    assert geo.scale_for((1900, 1200, 2000, 1300)).value == 54.0
    assert geo.scale_for((500, 700, 2000, 1300)).value is None  # spans both: no single scale


def test_a_sheet_with_no_viewport_says_nothing_on_it_is_to_scale(permit):
    page_wide, views = cad_read.page_scales(permit.cs.data, 2, "p3")
    assert views == [] and page_wide.value is None
    assert "no viewport" in page_wide.note


def test_a_drawing_with_no_layouts_is_one_sheet_at_an_exact_fitted_scale(tmp_path):
    built = drawings.model_only(str(tmp_path / "site.dxf"))
    cs = cad.ingest(built["path"], str(tmp_path / "w"))
    assert len(cs.pages) == 1
    page = cs.pages[0]
    assert page["layout"] == "Model" and page["model"] is True
    assert "ARCH D" in page["note"]
    pdf = pymupdf.open(cs.pdf_path)
    assert tuple(pdf[0].rect) == (0, 0, 2592, 1728)
    page_wide, views = cad_read.page_scales(cs.data, 0, "p1")
    assert views == []
    assert page_wide.value == pytest.approx(built["pt_per_ft"])
    assert page_wide.confidence == HIGH
    assert "model space fitted to the sheet" in page_wide.note
    facts = build_facts(cs.pdf_path, cad=cs.data)
    assert facts.geometry[0].scale_pt_per_ft.value == pytest.approx(72.0)
    assert facts.store.value("occupant_load") == 49


# ── sheet identity ───────────────────────────────────────────────────────────
def test_sheets_are_numbered_from_the_title_block_never_the_tab_name(permit):
    codes = [s.code for s in permit.facts.sheets]
    # PLAN: the INSERT's SHEET_NO attribute. A-201: a free-standing ATTDEF
    # prompted "SHEET No." showing A-501. S-1: nothing says what it is.
    assert codes == ["A-101", "A-501", "p3"]
    assert "A-201" not in codes and "S-1" not in codes
    assert permit.cs.pages[0]["number_source"] == "title-block attrib “SHEET_NO”"
    assert permit.cs.pages[1]["number_source"] == "title-block attdef “SHEET No.”"
    src = permit.facts.meta["sheet_sources"][1]
    assert src["source"] == "cad" and src["read"] == "A-501"


def test_the_sheet_title_is_the_sheets_not_the_projects(permit):
    assert permit.cs.pages[0]["title"] == "FIRST FLOOR PLAN"


def test_the_title_block_reader_takes_the_field_that_says_what_it_is():
    def run(kind, tag, text, cap=0.1, prompt=""):
        return SimpleNamespace(kind=kind, tag=tag, text=text, cap=cap, prompt=prompt)

    number, where, title = cad._title_block_identity([
        run("ATTRIB", "PROJECT_TITLE", "RIVERSIDE HALL"),
        run("ATTRIB", "JOB TITLE", "ALTERATION"),
        run("ATTRIB", "TITLE1", "FIRST FLOOR"),
        run("ATTRIB", "TITLE_2", "PLAN"),
        run("ATTRIB", "SHEET_TITLE", "PLAN"),             # a revision block repeats it
        run("ATTRIB", "SHEET_NOTES", "A-900"),            # NOTES, not a number field
        run("ATTRIB", "DWG_NO", "N.T.S.", cap=0.4),       # a number field, but not a number
        run("ATTRIB", "SHEET_NO", "a-101", cap=0.08),     # the revision block's small copy
        run("ATTRIB", "SHEET_NO", "A-101", cap=0.5),
        run("TEXT", "", "A-999", cap=1.0),                # not a field at all
    ])
    assert number == "A-101" and where == "title-block attrib “SHEET_NO”"
    assert title == "FIRST FLOOR PLAN"


# ── the facts ────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("key, value", [
    ("construction_type", "III-B"),
    ("occupancy_group", "A-3"),
    ("sprinkler_system", "YES"),
    ("risk_category", "III"),
    ("occupant_load", 70),
])
def test_the_stacked_code_block_resolves(permit, key, value):
    r = permit.facts.store.resolve(key)
    assert r is not None and r.value == value
    assert r.best.sheet == "A-101" and not r.rivals


def test_the_mtext_audit_row_reads_as_a_limit(permit):
    r = permit.facts.store.resolve("egress.common_path", "required")
    assert r is not None and r.value == 75.0


def test_an_attribute_is_a_claim_that_says_it_came_from_the_drawing(permit):
    r = permit.facts.store.resolve("stories")
    assert r is not None and r.value == 1
    c = r.best
    assert c.method == CAD and r.methods == [CAD]
    assert c.source == f"dxf:{permit.h['attrib:STORIES']}"
    assert c.layout == "PLAN" and c.raw == "1"
    meta = c.as_meta()
    assert meta["method"] == "cad" and meta["source"] == c.source and meta["layout"] == "PLAN"
    assert "drawing's STORIES field" in r.evidence().note
    assert r.confidence == MEDIUM                          # one reading of one field


def test_an_attribute_the_text_layer_also_shows_is_one_reading_not_two(permit):
    """EXPOSURE CATEGORY is a block: a constant label and an ATTRIB. The layout
    reader pairs them off the text layer; the CAD reader reads the ATTRIB. Both
    read one entity — agreeing with yourself is not corroboration."""
    r = permit.facts.store.resolve("exposure_category")
    assert r.value == "C"
    assert r.methods == ["cad", "pair"]
    handle = f"dxf:{permit.h['attrib:EXPOSURE_CATEGORY']}"
    assert all(handle in c.source.split("+") for c in r.claims)
    assert r.confidence == MEDIUM
    assert "independent" not in r.evidence().note


def test_a_value_the_sheet_does_not_print_is_not_a_claim(permit):
    """OCCUPANCY_GROUP = B is an invisible attribute: in the drawing, not on
    the sheet. It must neither resolve nor rival the printed A-3."""
    claims = permit.facts.store.claims("occupancy_group")
    assert claims and all(c.value == "A-3" for c in claims)
    assert permit.facts.store.resolve("occupancy_group").rivals == []
    recs = [r for r in permit.cs.data["claims"] if r.get("tag") == "OCCUPANCY_GROUP"]
    assert recs == []


def test_a_measured_area_is_data_and_never_a_stated_fact(permit):
    areas = [r for r in permit.cs.data["claims"] if r["type"] == "area"]
    assert [round(a["area_sf"], 1) for a in areas] == [permit.built["area_sf"]]   # 3,200 SF
    assert areas[0]["basis"] == "measured" and areas[0]["handle"] == permit.h["area"]
    # an outline with an arc in it: its vertices' shoelace is not its area
    assert all(a["handle"] != permit.h["arced"] for a in areas)
    store = permit.facts.store
    for key in ("building_area_sf", "total_area_sf", "area.tabulated_total_sf"):
        assert store.resolve(key) is None
    for c in store.each():
        assert c.value != pytest.approx(permit.built["area_sf"])
        assert c.basis != MEASURED or c.field not in BY_KEY


def test_a_dimension_records_the_geometry_and_what_it_prints(permit):
    dims = [r for r in permit.cs.data["claims"] if r["type"] == "dimension"]
    assert len(dims) == 1
    d = dims[0]
    assert d["handle"] == permit.h["dimension"] and d["viewport"] == permit.h["vp48"]
    # DIMLFAC 0.5 prints half the geometry: what was measured is the geometry
    assert d["measured_in"] == pytest.approx(permit.built["dimension_in"])
    assert d["dimlfac"] == 0.5 and d["shown_units"] == pytest.approx(480.0)
    assert d["printed"] == permit.built["dimension_printed"] and d["overridden"] is False


def test_the_summary_on_the_facts_is_counts_and_provenance(permit):
    s = permit.facts.meta["cad"]
    assert s["records"] == {"attribute": 7, "dimension": 1, "area": 1}
    assert [x["number"] for x in s["sheets"]] == ["A-101", "A-501", ""]
    assert s["claims"] >= 1 and s["sources_stamped"] >= 7
    blob = json.dumps(s)
    assert "OCCUPANT LOAD" not in blob and "RIVERSIDE" not in blob     # no drawing text


# ── floors and replay ────────────────────────────────────────────────────────
def test_the_rendered_pdf_alone_still_reviews(permit):
    """With no sidecar the review runs on the text layer: smaller, never failed."""
    facts = build_facts(permit.cs.pdf_path)
    assert len(facts.sheets) == 3
    assert facts.store.value("occupant_load") == 70
    assert facts.store.resolve("stories") is None             # only the drawing knew
    assert "cad" not in facts.meta
    assert run_all(facts).findings is not None


def test_the_same_sidecar_gives_the_same_findings(permit):
    sidecar = cad.load_sidecar(permit.cs.sidecar_path)          # as the worker reloads it
    first = [f.to_dict() for f in run_all(build_facts(permit.cs.pdf_path, cad=sidecar)).findings]
    again = [f.to_dict() for f in run_all(build_facts(permit.cs.pdf_path, cad=sidecar)).findings]
    live = [f.to_dict() for f in run_all(permit.facts).findings]
    assert first == again == live and first


def _imports(path: Path, package: str):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                yield a.name
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package.split(".")
                base = base[: len(base) - node.level + 1]
                mod = ".".join(base + ([node.module] if node.module else []))
            else:
                mod = node.module or ""
            yield mod
            for a in node.names:
                yield f"{mod}.{a.name}"


def _module_path(module: str):
    p = ROOT / Path(*module.split("."))
    if p.with_suffix(".py").is_file():
        return p.with_suffix(".py")
    if (p / "__init__.py").is_file():
        return p / "__init__.py"
    return None


def _reach(root: str) -> set:
    """Every module `root` can import, third-party ones included, function
    bodies included — the way `test_ai_guardrails.py` walks it."""
    seen, queue, external = set(), [root], set()
    while queue:
        mod = queue.pop()
        if mod in seen:
            continue
        seen.add(mod)
        path = _module_path(mod)
        if path is None:
            continue
        package = mod if path.name == "__init__.py" else mod.rsplit(".", 1)[0]
        for imp in _imports(path, package):
            if imp.startswith("fbcreview"):
                queue.append(imp)
            else:
                external.add(imp.split(".")[0])
    return seen | external


def _rule_and_code_modules():
    mods = []
    for pkg in ("rules", "codes"):
        for p in sorted((ROOT / "fbcreview" / pkg).glob("*.py")):
            mods.append(f"fbcreview.{pkg}" if p.stem == "__init__" else f"fbcreview.{pkg}.{p.stem}")
    return mods


@pytest.mark.parametrize("module", _rule_and_code_modules())
def test_a_rule_or_the_code_corpus_cannot_reach_the_cad_adapter(module):
    reach = _reach(module)
    assert not any(m == "fbcreview.cad" or m.startswith("fbcreview.cad.") for m in reach), \
        f"{module} reaches {sorted(m for m in reach if m.startswith('fbcreview.cad'))}"
    assert "ezdxf" not in reach, f"{module} reaches ezdxf"


# ── xrefs and zips ───────────────────────────────────────────────────────────
def test_an_xref_in_the_zip_is_embedded_and_its_content_is_on_the_sheet(tmp_path):
    z = drawings.xref_zip(str(tmp_path / "set.zip"), str(tmp_path / "src"))
    cs = cad.ingest(z["path"], str(tmp_path / "w"), name="set.zip")
    assert [p["layout"] for p in cs.pages] == ["A-102"]       # the base plan is not a sheet
    roles = {d["name"]: (d.get("role"), d["xrefs"]) for d in cs.data["drawings"]}
    assert roles == {"SET/A-102.dxf": ("sheets", {"X-BASE": "embedded"}),
                     "SET/XREF/X-BASE.dxf": ("xref", {})}
    assert z["note"] in pymupdf.open(cs.pdf_path)[0].get_text()
    assert cs.pages[0]["number"] == "A-102"
    assert cs.warnings == []


def test_a_missing_xref_is_one_warning_that_names_it(tmp_path):
    z = drawings.xref_zip(str(tmp_path / "set.zip"), str(tmp_path / "src"), include_xref=False)
    cs = cad.ingest(z["path"], str(tmp_path / "w"))
    assert len(cs.warnings) == 1
    assert z["xref_file"] in cs.warnings[0] and "not included in the upload" in cs.warnings[0]
    assert cs.data["drawings"][0]["xrefs"] == {"X-BASE": "not uploaded"}
    assert len(cs.pages) == 1                                  # the sheet still plots
    assert "XREF CORRIDOR" not in pymupdf.open(cs.pdf_path)[0].get_text()


def test_an_embedded_overlay_no_longer_reports_itself_as_an_xref(tmp_path):
    """ezdxf's own embed clears XREF and EXTERNAL but not OVERLAY; the reference
    drawing's title block is an overlay (flags 12)."""
    host = str(tmp_path / "host.dxf")
    doc = drawings._drawing()
    ezdxf.xref.define(doc, "X-BASE", "C:\\XREF\\X-BASE.dwg", overlay=True)
    doc.modelspace().add_blockref("X-BASE", (0, 0))
    doc.saveas(host)
    base = drawings.xref_member(str(tmp_path / "X-BASE.dxf"))
    op = read.open_dxf(host, "host.dxf")
    loaded = read.open_dxf(base["path"], "X-BASE.dxf").doc
    assert op.doc.blocks.get("X-BASE").block_record.is_xref
    read.embed_xrefs(op, lambda fname: loaded if fname.lower() == "x-base.dwg" else None)
    assert op.xrefs == {"X-BASE": "embedded"}
    assert not op.doc.blocks.get("X-BASE").block_record.is_xref
    assert any(e.dxftype() == "TEXT" for e in op.doc.blocks.get("X-BASE"))


def test_a_member_that_cannot_be_converted_costs_that_drawing_not_the_set(tmp_path, monkeypatch):
    """Decided for zips: one damaged file among forty sheet files loses one."""
    monkeypatch.setenv("FBC_DWG2DXF", drawings.fake_converter(str(tmp_path), fail=True))
    convert._version_of.cache_clear()
    z = drawings.xref_zip(str(tmp_path / "set.zip"), str(tmp_path / "src"),
                          extra=[("SET/A-103.dwg", drawings.dwg_bytes())])
    cs = cad.ingest(z["path"], str(tmp_path / "w"))
    assert [p["layout"] for p in cs.pages] == ["A-102"]
    assert cs.data["unread"] == [{"name": "SET/A-103.dwg", "error": "dwg_conversion_failed"}]
    assert cs.warnings == ["SET/A-103.dwg could not be read and is not in this review "
                           "(the DWG could not be converted)."]
    assert [d["name"] for d in cs.data["drawings"]] == ["SET/A-102.dxf", "SET/XREF/X-BASE.dxf"]


def test_an_xref_that_was_uploaded_but_unreadable_is_not_called_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("FBC_DWG2DXF", drawings.fake_converter(str(tmp_path), fail=True))
    convert._version_of.cache_clear()
    src = tmp_path / "src"
    src.mkdir()
    host = drawings.xref_host(str(src / "A-102.dxf"))
    drawings.write_zip(str(tmp_path / "set.zip"), [
        ("SET/A-102.dxf", Path(host["path"]).read_bytes()),
        ("SET/XREF/X-BASE.dwg", drawings.dwg_bytes())])
    cs = cad.ingest(str(tmp_path / "set.zip"), str(tmp_path / "w"))
    assert cs.data["drawings"][0]["xrefs"] == {"X-BASE": "unreadable"}
    said = [w for w in cs.warnings if "X-BASE.dwg" in w]
    assert len(said) == 2                     # the member itself, and the xref it would fill
    assert not any("not included in the upload" in w for w in cs.warnings)
    assert any("uploaded but could not be read" in w for w in said)


def test_when_nothing_in_a_zip_can_be_read_the_reason_is_the_sets(tmp_path, monkeypatch):
    monkeypatch.setenv("FBC_DWG2DXF", drawings.fake_converter(str(tmp_path), fail=True))
    convert._version_of.cache_clear()
    drawings.write_zip(str(tmp_path / "set.zip"), [("A-101.dwg", drawings.dwg_bytes())])
    with pytest.raises(convert.ConversionFailed):
        cad.ingest(str(tmp_path / "set.zip"), str(tmp_path / "w"))


def test_a_single_drawing_that_cannot_be_converted_fails_the_job(tmp_path, monkeypatch):
    monkeypatch.setenv("FBC_DWG2DXF", drawings.fake_converter(str(tmp_path), fail=True))
    convert._version_of.cache_clear()
    src = tmp_path / "A-101.dwg"
    src.write_bytes(drawings.dwg_bytes())
    with pytest.raises(convert.ConversionFailed):
        cad.ingest(str(src), str(tmp_path / "w"))


def test_a_dwg_goes_through_the_converter_and_its_version_is_the_sets_identity(
        tmp_path, monkeypatch):
    prepared = drawings.permit_set(str(tmp_path / "prepared.dxf"))
    monkeypatch.setenv("FBC_DWG2DXF", drawings.fake_converter(str(tmp_path), prepared["path"]))
    convert._version_of.cache_clear()
    src = tmp_path / "A-101.dwg"
    src.write_bytes(drawings.dwg_bytes())
    cs = cad.ingest(str(src), str(tmp_path / "w"), name="A-101.dwg")
    d = cs.data["drawings"][0]
    assert d["release"] == "AutoCAD 2018" and d["conversion"]["converter"] == "dwg2dxf 0.0-test"
    assert cs.data["converter"] == "dwg2dxf 0.0-test"
    assert cs.data["dxf_paths"] == {"A-101.dwg": "000.dxf"}     # relative to the workdir
    assert [p["layout"] for p in cs.pages] == ["PLAN", "A-201", "S-1"]
    ident = cad.identity("abc123")
    assert ident.startswith("abc123+cad:dwg2dxf 0.0-test:ezdxf-") and cad.RENDER_VERSION in ident


# ── a sheet that cannot be plotted ───────────────────────────────────────────
def _break_layout(monkeypatch, name: str):
    """Make one layout fail after its page was begun — the worst case for
    keeping page numbers aligned."""
    real = render.render_sheet

    def flaky(doc, spec, cache, target, names=None):
        sheet = real(doc, spec, cache, target, names)
        if spec.layout == name:
            raise ValueError("ezdxf could not draw this layout")
        return sheet
    monkeypatch.setattr(render, "render_sheet", flaky)


def test_one_layout_that_cannot_be_plotted_costs_that_sheet_only(tmp_path, monkeypatch):
    built = drawings.permit_set(str(tmp_path / "permit.dxf"))
    _break_layout(monkeypatch, "A-201")
    cs = cad.ingest(built["path"], str(tmp_path / "w"))
    assert [(p["page"], p["layout"]) for p in cs.pages] == [(0, "PLAN"), (1, "S-1")]
    pdf = pymupdf.open(cs.pdf_path)
    assert pdf.page_count == 2
    assert "STRUCTURAL NOTES" in pdf[1].get_text()               # page 1 is S-1's, not A-201's
    assert cs.warnings == ["Layout A-201 could not be plotted (ValueError); it is not in this "
                           "review."]
    assert all(c["page"] in (0, 1) for c in cs.data["claims"])
    assert not any(c.get("tag") == "A-501" for c in cs.data["claims"])
    facts = build_facts(cs.pdf_path, cad=cs.data)
    assert [s.code for s in facts.sheets] == ["A-101", "p2"]


def test_the_command_line_reports_a_partial_set_as_a_set(tmp_path, monkeypatch, capsys):
    built = drawings.permit_set(str(tmp_path / "permit.dxf"))
    _break_layout(monkeypatch, "A-201")
    code = cad_cli.main(["ingest", built["path"], str(tmp_path / "w")])
    out = json.loads(capsys.readouterr().out)
    assert code == 0
    assert out["pages"] == 2 and out["warnings"] == 1
    side = cad.load_sidecar(str(tmp_path / "w" / cad.SIDECAR))
    assert pymupdf.open(str(tmp_path / "w" / out["pdf"])).page_count == len(side["pages"]) == 2


def test_when_no_layout_can_be_plotted_the_refusal_says_so(tmp_path, monkeypatch, capsys):
    built = drawings.permit_set(str(tmp_path / "permit.dxf"))

    def broken(*a, **k):
        raise ValueError("no")
    monkeypatch.setattr(render, "render_sheet", broken)
    with pytest.raises(SourceError) as exc:
        cad.ingest(built["path"], str(tmp_path / "w"))
    assert exc.value.code == "no_sheets" and "3 sheet(s)" in exc.value.message
    code = cad_cli.main(["ingest", built["path"], str(tmp_path / "w2")])
    assert code == 2
    assert json.loads(capsys.readouterr().out)["error"] == "no_sheets"


# ── the window a layout plots ────────────────────────────────────────────────
def _layout(plot_type=None, setup=True):
    doc = ezdxf.new("R2018")
    layout = doc.layouts.new("X")
    if setup:
        layout.page_setup(size=(36, 24), margins=(0, 0, 0, 0), units="inch")
    layout.add_line((2, 3), (10, 7))
    if plot_type is not None:
        layout.dxf_layout.dxf.plot_type = plot_type
    return doc, layout


def test_a_layout_that_plots_its_extents_never_plots_a_sentinel():
    """Stored extents of a layout never regenerated are +1e20 / -1e20."""
    _doc, layout = _layout(plot_type=1)
    assert tuple(layout.dxf_layout.dxf.extmin)[0] == 1e20
    assert read._window(layout) == pytest.approx((2, 3, 10, 7))


def test_a_layout_that_plots_a_window_plots_that_window():
    _doc, layout = _layout(plot_type=4)
    d = layout.dxf_layout.dxf
    d.plot_window_x1, d.plot_window_y1, d.plot_window_x2, d.plot_window_y2 = 1, 1, 13, 9
    assert read._window(layout) == (1, 1, 13, 9)
    d.plot_window_x2 = 1                                          # degenerate: limits instead
    assert read._window(layout) == pytest.approx((0, 0, 36, 24))


@pytest.mark.parametrize("plot_type", [None, 0, 2, 3, 5])
def test_a_layout_that_plots_its_paper_plots_its_limits(plot_type):
    _doc, layout = _layout(plot_type=plot_type)
    assert read._window(layout) == pytest.approx((0, 0, 36, 24))


def test_a_layout_with_no_page_set_up_is_fitted_to_arch_d():
    """Its limits are a default (ezdxf's are A3 in millimetres): plotted one unit
    to the inch they made a page ten metres wide."""
    doc, _layout_ = _layout(setup=False)
    spec = next(s for s in read.sheets(doc) if s.layout == "X")
    assert spec.window == pytest.approx((2, 3, 10, 7))
    assert spec.paper_mm == read.ARCH_D_MM
    assert "no page size" in spec.note


# ── measured outlines are cached per drawing, not per id ─────────────────────
def test_outlines_are_remembered_per_drawing_and_released_with_it():
    def plate(w):
        doc = drawings._drawing()
        doc.layers.add("A-AREA")
        doc.modelspace().add_lwpolyline([(0, 0), (w, 0), (w, 120), (0, 120)], close=True,
                                         dxfattribs={"layer": "A-AREA"})
        return doc
    a, b = plate(120), plate(240)
    assert [round(o[2]) for o in cad_claims._outlines(a, 1.0)] == [100]
    assert [round(o[2]) for o in cad_claims._outlines(b, 1.0)] == [200]
    assert [round(o[2]) for o in cad_claims._outlines(a, 12.0)] == [14400]   # feet, not inches
    held = len(cad_claims._OUTLINES)
    del a, b
    gc.collect()
    assert len(cad_claims._OUTLINES) <= held - 2


def test_the_zip_member_names_are_what_the_warning_and_sidecar_carry(tmp_path):
    """Member names are the person's own file names inside their zip; never a
    path on this machine."""
    z = drawings.xref_zip(str(tmp_path / "set.zip"), str(tmp_path / "src"), include_xref=False)
    cs = cad.ingest(z["path"], str(tmp_path / "w"))
    blob = json.dumps(cs.data)
    assert str(tmp_path) not in blob
    with zipfile.ZipFile(z["path"]) as zf:
        assert [d["name"] for d in cs.data["drawings"]] == zf.namelist()


def test_one_model_space_note_on_two_sheets_is_one_reading(tmp_path):
    """A note drawn once in model space and seen through a viewport on each of
    two sheets is one statement shown twice — not two sheets agreeing."""
    doc = drawings._drawing()
    note = doc.modelspace().add_text("RISK CATEGORY: II", height=6.0)
    note.set_placement((100, 100))
    tb = doc.blocks.new("TB")
    tb.add_attdef("SHEET_NO", (0, 0), dxfattribs={"height": 0.5})
    for i, number in enumerate(("A-101", "A-102")):
        sheet = drawings._sheet(doc, f"S{i}", taborder=i + 1)
        sheet.add_viewport(center=(12, 12), size=(20, 12), view_center_point=(200, 100),
                           view_height=12 * 48)
        sheet.add_blockref("TB", (31, 1)).add_auto_attribs({"SHEET_NO": number})
    drawings._zero_viewport_status(doc)
    doc.saveas(str(tmp_path / "two.dxf"))
    cs = cad.ingest(str(tmp_path / "two.dxf"), str(tmp_path / "w"))
    facts = build_facts(cs.pdf_path, cad=cs.data)
    r = facts.store.resolve("risk_category")
    assert r.value == "II" and r.sheets == ["A-101", "A-102"]
    assert {c.source for c in r.claims} == {f"dxf:{note.dxf.handle}"}
    assert r.confidence == MEDIUM
    assert "also stated on" not in r.evidence().note
    assert "the same drawing entity is also shown on A-102" in r.evidence().note
    # The PDF alone cannot tell, and says what it always said.
    plain = build_facts(cs.pdf_path).store.resolve("risk_category")
    assert plain.confidence == HIGH and "also stated on" in plain.evidence().note


def test_a_claim_in_a_tight_stack_names_only_its_own_entities(tmp_path):
    """Label over value at 1.25 × cap, rows touching. A PDF word box reaches
    below its baseline, so a claim's box grazes the next row's label; that
    graze is not where the claim was read from (measured: before the cell had
    to lie mostly inside the claim, the occupancy claim named the risk-category
    label as one of its sources)."""
    doc = drawings._drawing()
    sheet = drawings._sheet(doc, "P", 1)
    handles, y = [], 20.0
    for text in ("OCCUPANCY:", "B", "RISK CATEGORY:", "II", "CONSTRUCTION TYPE:", "V-B"):
        t = sheet.add_text(text, height=drawings.CAP)
        t.set_placement((2, y))
        handles.append(t.dxf.handle)
        y -= 1.25 * drawings.CAP
    doc.saveas(str(tmp_path / "tight.dxf"))
    cs = cad.ingest(str(tmp_path / "tight.dxf"), str(tmp_path / "w"))
    facts = build_facts(cs.pdf_path, cad=cs.data)
    for key, (label, value) in (("occupancy_group", handles[0:2]),
                                ("risk_category", handles[2:4]),
                                ("construction_type", handles[4:6])):
        (c,) = facts.store.claims(key)
        assert set(c.source.split("+")) == {f"dxf:{label}", f"dxf:{value}"}, key
