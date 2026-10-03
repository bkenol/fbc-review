"""The review drawn back into the drawing: the marked-up DXF says what the PDF says.

`fbcreview/cad/markup.py` writes each finding into a copy of the drawing, on the
layout it was found on. These tests build small drawings with ezdxf (never a
client drawing), run them through the whole path — ingest, the engine,
`findings.json`'s payload, the DXF writer — and re-open what was written:

* the mark is on the right layout, around the text it is about, on a plain
  sheet, on a sheet plotted rotated 90°, and in model space for a drawing with
  no layouts; and nothing is added to any other layout;
* the placement inverts the plot exactly: the outline, put back through the
  page's `to_page`, is the finding's `rect` padded by the PDF marker's 3 pt;
* the labelling rules of the PDF hold: an as-declared twin is never drawn, a
  divergent or conflicting finding is dashed and says DECLARED VS DRAWN, a
  declaration-based one says so in the PDF's words, an AI-raised or -revised one
  says so, and a pass is a green rectangle on its own layer, never a cloud;
* every outline carries XDATA that tells two findings with one id apart;
* nothing is dropped: the counts reconcile, a finding with no place is listed,
  and a drawing with no DXF to write into is reported, with its findings.

The rendered PDF's page contract is pinned here too, because the DXF writer
relies on it: every page is `/Rotate 0` with its media box at the origin.
"""
from __future__ import annotations

import copy
import os
import zipfile

import pytest

ezdxf = pytest.importorskip("ezdxf")

import pymupdf  # noqa: E402
from ezdxf import units  # noqa: E402

from fbcreview import cad  # noqa: E402
from fbcreview.cad import markup  # noqa: E402
from fbcreview.cad.render import apply, box_through, invert  # noqa: E402
from fbcreview.payload import findings_payload  # noqa: E402
from fbcreview.pipeline import build_facts  # noqa: E402
from fbcreview.render.markup import DECLARED_BASIS_NOTE, scenario_note  # noqa: E402
from fbcreview.rules import run_all  # noqa: E402

ROWS = ("OCCUPANCY: ASSEMBLY (A-3)", "RISK CATEGORY: III", "OCCUPANT LOAD: 70")
RISK = "RISK CATEGORY: III"


# ── drawings, built here ─────────────────────────────────────────────────────

def _paper_drawing(path: str, rotation: int = 0) -> dict:
    """Two layouts on an ARCH D page, a viewport onto model space, and the three
    rows a risk-category check reads. Returns each row's insert point.

    With `rotation=1` the layout plots rotated 90°, and the rows are set at 270°
    so they read left to right on the plotted page — the way a drafter sets
    text on a sheet plotted that way.
    """
    doc = ezdxf.new("R2018", setup=True)
    doc.units = units.IN
    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0), (1200, 0), (1200, 800), (0, 800)], close=True)
    msp.add_text("MODEL NOTE", height=12).set_placement((100, 100))
    doc.layouts.rename("Layout1", "A-1")
    sheet = doc.layouts.get("A-1")
    sheet.page_setup(size=(36, 24), margins=(0, 0, 0, 0), units="inch", rotation=rotation)
    sheet.add_viewport(center=(18, 14), size=(30, 18), view_center_point=(600, 400),
                       view_height=18 * 48)
    inserts = {}
    for i, row in enumerate(ROWS):
        if rotation == 1:
            at, angle = (4.0 + i * 0.25, 20.0), 270
        else:
            at, angle = (1.0, 4.0 - i * 0.25), 0
        sheet.add_text(row, height=0.125, rotation=angle).set_placement(at)
        inserts[row] = at
    sheet.add_text("G-0", height=0.25).set_placement((33, 1))
    other = doc.layouts.new("A-2")
    other.page_setup(size=(36, 24), margins=(0, 0, 0, 0), units="inch")
    other.add_text("GENERAL NOTES", height=0.25).set_placement((2, 20))
    doc.saveas(path)
    return inserts


def _model_drawing(path: str) -> dict:
    """No layouts: everything in model space, which is plotted fitted to ARCH D."""
    doc = ezdxf.new("R2018", setup=True)
    doc.units = units.IN
    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0), (432, 0), (432, 288), (0, 288)], close=True)
    inserts = {}
    for i, row in enumerate(ROWS):
        at = (12.0, 60.0 - i * 3.0)
        msp.add_text(row, height=1.5).set_placement(at)
        inserts[row] = at
    msp.add_text("G-0", height=3).set_placement((396, 12))
    doc.saveas(path)
    return inserts


def _counts(doc) -> dict:
    """Entities per layout, model space included."""
    out = {"*model": len(doc.modelspace())}
    for name in doc.layout_names():
        if name != "Model":
            out[name] = len(doc.paperspace(name))
    return out


class Reviewed:
    """One drawing through ingest, the engine and the payload."""

    def __init__(self, root, build, name: str, **kw):
        os.makedirs(root, exist_ok=True)
        self.root = str(root)
        self.src = os.path.join(self.root, name)
        self.inserts = build(self.src, **kw)
        self.work = os.path.join(self.root, "work")
        self.cs = cad.ingest(self.src, self.work, name=name)
        self.facts = build_facts(self.cs.pdf_path, cad=self.cs.data)
        self.result = run_all(self.facts)
        self.payload = findings_payload(self.cs.pdf_path, self.facts, self.result.findings)
        self.before = _counts(ezdxf.readfile(self.src))

    def finding(self, rule_id: str) -> dict:
        return next(f for f in self.payload if f["rule_id"] == rule_id)

    def write(self, findings, tag: str = "out", sidecar=None):
        out = os.path.join(self.root, f"{tag}.zip")
        summary = markup.write(self.work, sidecar or self.cs.data, findings, out, "Test set")
        with zipfile.ZipFile(out) as zf:
            names = zf.namelist()
            readme = zf.read("READ ME.txt").decode("utf-8")
            docs = []
            for n in names:
                if n.endswith(".dxf"):
                    target = os.path.join(self.root, f"{tag}-{len(docs)}.dxf")
                    with open(target, "wb") as fh:
                        fh.write(zf.read(n))
                    docs.append(ezdxf.readfile(target))
        return summary, docs, readme


@pytest.fixture(scope="module")
def plain(tmp_path_factory):
    return Reviewed(tmp_path_factory.mktemp("plain"), _paper_drawing, "set.dxf")


@pytest.fixture(scope="module")
def rotated(tmp_path_factory):
    return Reviewed(tmp_path_factory.mktemp("rotated"), _paper_drawing, "set.dxf", rotation=1)


@pytest.fixture(scope="module")
def model(tmp_path_factory):
    return Reviewed(tmp_path_factory.mktemp("model"), _model_drawing, "model.dxf")


def _tagged(entity) -> dict:
    if not entity.has_xdata(markup.APPID):
        return {}
    return dict(str(t.value).split("=", 1) for t in entity.get_xdata(markup.APPID))


def _ours(layout, key: str, dxftype: str = "LWPOLYLINE"):
    return [e for e in layout.query(dxftype) if _tagged(e).get("key") == key]


def _vertex_box(poly):
    pts = [(p[0], p[1]) for p in poly.get_points("xy")]
    xs, ys = zip(*pts)
    return min(xs), min(ys), max(xs), max(ys)


def _inside(pt, box) -> bool:
    return box[0] <= pt[0] <= box[2] and box[1] <= pt[1] <= box[3]


def _labels(doc) -> str:
    texts = []
    for name in ["Model", *doc.layout_names()]:
        layout = doc.modelspace() if name == "Model" else doc.paperspace(name)
        texts += [m.text for m in layout.query("MTEXT") if m.dxf.layer == markup.TEXT_LAYER]
    return "\n".join(texts).replace("\\P", "\n")


# ── the rendered PDF's page contract ─────────────────────────────────────────

@pytest.mark.parametrize("which", ["plain", "rotated", "model"])
def test_every_rendered_page_is_unrotated_with_its_media_box_at_the_origin(which, request):
    """`findings.json` rects are in the viewer's space on these pages; the DXF
    writer takes them as `to_page`'s output space. That is only the same space
    when the page has no /Rotate and its media box starts at the origin."""
    r = request.getfixturevalue(which)
    with pymupdf.open(r.cs.pdf_path) as doc:
        assert doc.page_count == len(r.cs.pages)
        for page in doc:
            w, h = page.rect.width, page.rect.height
            assert page.rotation == 0
            assert page.mediabox == pymupdf.Rect(0, 0, w, h)
            assert page.cropbox == pymupdf.Rect(0, 0, w, h)


@pytest.mark.parametrize("which", ["plain", "rotated", "model"])
def test_to_page_puts_the_drawn_text_where_the_page_shows_it(which, request):
    """An independent check of the affine the writer inverts: the TEXT's
    insert point, through `to_page`, is where the plotted page has the words."""
    r = request.getfixturevalue(which)
    page = r.cs.pages[0]
    with pymupdf.open(r.cs.pdf_path) as doc:
        [hit] = doc[0].search_for("RISK CATEGORY")
    x, y = apply(tuple(page["to_page"]), *r.inserts[RISK])
    assert hit.x0 - 3 <= x <= hit.x1 + 3 and hit.y0 - 3 <= y <= hit.y1 + 3


# ── placement ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("which", ["plain", "rotated"])
def test_the_cloud_is_on_the_layout_around_the_text_and_nowhere_else(which, request):
    r = request.getfixturevalue(which)
    f = r.finding("XSHEET.RISK_CATEGORY")
    assert f["status"] == "OPEN" and f["rect"] and f["page"] == 0
    summary, [doc], _readme = r.write(r.payload, tag=f"place-{which}")
    [cloud] = _ours(doc.paperspace("A-1"), f["key"])
    assert cloud.dxf.layer == markup.CLOUD_LAYER
    assert _inside(r.inserts[RISK], _vertex_box(cloud))
    # Only the layout the findings are on gains anything; model space and the
    # second sheet are exactly as they were.
    after = _counts(doc)
    touched = {r.cs.pages[p["page"]]["layout"] for p in r.payload if p["page"] is not None}
    assert touched == {"A-1"}
    for name, n in r.before.items():
        if name != "A-1":
            assert after[name] == n, f"{name} was changed"
    assert after["A-1"] > r.before["A-1"]


@pytest.mark.parametrize("which", ["plain", "rotated", "model"])
def test_the_outline_inverts_the_plot_to_within_half_a_point(which, request):
    """Put back through `to_page`, the outline is the finding's rect padded by
    3 pt — the PDF marker's pad — whatever the plot rotation."""
    r = request.getfixturevalue(which)
    f = r.finding("XSHEET.RISK_CATEGORY")
    _summary, [doc], _readme = r.write([f], tag=f"invert-{which}")
    page = r.cs.pages[0]
    layout = doc.modelspace() if page["model"] else doc.paperspace(page["layout"])
    [outline] = _ours(layout, f["key"])
    back = box_through(tuple(page["to_page"]), _vertex_box(outline))
    want = (f["rect"][0] - 3, f["rect"][1] - 3, f["rect"][2] + 3, f["rect"][3] + 3)
    assert back == pytest.approx(want, abs=0.5)
    # and the inverse used is the exact inverse of the plot
    inv = invert(tuple(page["to_page"]))
    assert apply(tuple(page["to_page"]), *apply(inv, 100.0, 200.0)) == pytest.approx((100, 200))


def test_a_model_space_drawing_is_marked_in_model_space(model):
    f = model.finding("XSHEET.RISK_CATEGORY")
    assert model.cs.pages[0]["model"]
    _summary, [doc], _readme = model.write(model.payload, tag="model")
    [cloud] = _ours(doc.modelspace(), f["key"])
    assert _inside(model.inserts[RISK], _vertex_box(cloud))
    for name in doc.layout_names():
        if name != "Model":
            assert len(doc.paperspace(name)) == model.before.get(name, 0)


def test_a_cloud_swells_outward_and_a_label_reads_along_the_plotted_sheet(rotated):
    f = rotated.finding("XSHEET.RISK_CATEGORY")
    _summary, [doc], _readme = rotated.write([f], tag="shape")
    [cloud] = _ours(doc.paperspace("A-1"), f["key"])
    assert all(p[4] > 0 for p in cloud.get_points("xyseb"))
    [label] = _ours(doc.paperspace("A-1"), f["key"], "MTEXT")
    # The sheet plots rotated 90°, so text that reads left to right on the
    # page runs at 270° in the layout, like the drafter's own rows.
    assert label.dxf.rotation % 360 == pytest.approx(270, abs=0.01)


# ── what each mark says ──────────────────────────────────────────────────────

def test_an_as_declared_twin_is_never_drawn_and_its_drawn_twin_is_dashed(plain):
    base = plain.finding("XSHEET.RISK_CATEGORY")
    drawn = dict(base, scenario="as_drawn", key=base["fid"])
    declared = dict(base, scenario="as_declared", key=f"{base['fid']}@as_declared",
                    rect=None, status="PASS", severity="VERIFIED")
    summary, [doc], readme = plain.write([drawn, declared], tag="twins")
    sheet = doc.paperspace("A-1")
    assert summary["as_declared_skipped"] == 1
    assert summary["placed"] + summary["listed"] == 1
    assert not [e for e in sheet if _tagged(e).get("key") == declared["key"]]
    assert declared["key"] not in _labels(doc)
    [outline] = _ours(sheet, drawn["key"])
    assert outline.dxf.linetype == markup.DASHED and markup.DASHED in doc.linetypes
    words = _labels(doc)
    assert "DECLARED VS DRAWN" in words
    assert scenario_note(type("F", (), {"scenario": "as_drawn", "status": "OPEN"})) in words
    assert "not drawn" in readme


def test_a_conflict_is_dashed_and_tagged(plain):
    f = dict(plain.finding("XSHEET.RISK_CATEGORY"), status="CONFLICT")
    _summary, [doc], _readme = plain.write([f], tag="conflict")
    [outline] = _ours(doc.paperspace("A-1"), f["key"])
    assert outline.dxf.layer == markup.CLOUD_LAYER and outline.dxf.linetype == markup.DASHED
    assert "DECLARED VS DRAWN" in _labels(doc)


def test_a_finding_on_the_declaration_says_so_in_the_pdfs_words(plain):
    f = dict(plain.finding("XSHEET.RISK_CATEGORY"), basis="declaration")
    _summary, [doc], _readme = plain.write([f], tag="basis")
    assert DECLARED_BASIS_NOTE in _labels(doc)


@pytest.mark.parametrize("op, words", [("add", "Raised by AI review"),
                                       ("revise", "Revised by AI review")])
def test_an_ai_edited_finding_never_reads_as_a_rules_own(plain, op, words):
    base = plain.finding("XSHEET.RISK_CATEGORY")
    f = dict(base, ai_revision={"op": op, "pass": 2, "reason": "r", "changed": []})
    if op == "add":
        f.update(fid="AI-01", rule_id="AI.REVIEW", key="AI-01")
    _summary, [doc], _readme = plain.write([f], tag=f"ai-{op}")
    [label] = _ours(doc.paperspace("A-1"), f["key"], "MTEXT")
    assert words in label.text
    [outline] = _ours(doc.paperspace("A-1"), f["key"])
    assert _tagged(outline)["ai_revision"] == op


def test_a_pass_is_a_green_rectangle_on_its_own_layer_not_a_cloud(plain):
    f = dict(plain.finding("XSHEET.RISK_CATEGORY"), status="PASS", severity="VERIFIED")
    summary, [doc], _readme = plain.write([f], tag="pass")
    [outline] = _ours(doc.paperspace("A-1"), f["key"])
    assert outline.dxf.layer == markup.VERIFIED_LAYER
    assert outline.dxf.color == markup.SEVERITY_ACI["VERIFIED"]
    points = list(outline.get_points("xyb"))
    assert len(points) == 4 and all(p[2] == 0 for p in points)
    assert not [e for e in doc.paperspace("A-1").query("LWPOLYLINE")
                if e.dxf.layer == markup.CLOUD_LAYER]
    assert summary["rectangles"] == 1 and summary["clouds"] == 0


def test_two_findings_with_one_id_are_two_keys_in_the_xdata(plain):
    base = plain.finding("XSHEET.RISK_CATEGORY")
    a = dict(base, fid="H-03", key="H-03")
    b = dict(base, fid="H-03", key="H-03~2", rule_id="OTHER.RULE")
    _summary, [doc], _readme = plain.write([a, b], tag="xdata")
    assert markup.APPID in doc.appids
    tags = [_tagged(e) for e in doc.paperspace("A-1").query("LWPOLYLINE") if _tagged(e)]
    assert {t["key"] for t in tags} == {"H-03", "H-03~2"}
    for t in tags:
        assert {"key", "fid", "rule_id", "severity", "status"} <= set(t)


def test_severity_sets_the_colour(plain):
    f = dict(plain.finding("XSHEET.RISK_CATEGORY"), severity="CRITICAL")
    _summary, [doc], _readme = plain.write([f], tag="colour")
    [outline] = _ours(doc.paperspace("A-1"), f["key"])
    assert outline.dxf.color == markup.SEVERITY_ACI["CRITICAL"]


# ── nothing dropped ──────────────────────────────────────────────────────────

def test_every_finding_is_marked_or_listed_and_the_counts_reconcile(plain):
    base = plain.finding("XSHEET.RISK_CATEGORY")
    findings = [
        base,
        dict(base, key="H-SN", fid="H-SN", rect=None, title="Sheet numbers missing"),
        dict(base, key="X-1", fid="X-1", page=None, rect=None, title="Not on one sheet"),
        dict(base, key="X-2", fid="X-2", page=99, title="Page the set does not have"),
        dict(base, key="X-3", fid="X-3", page=-1, title="Negative page"),
        dict(base, key="D-1@as_declared", fid="D-1", scenario="as_declared", rect=None),
    ]
    summary, [doc], _readme = plain.write(findings, tag="reconcile")
    assert summary["placed"] + summary["listed"] == len(findings) - summary["as_declared_skipped"]
    assert summary["placed"] == 1 and summary["listed"] == 4
    [listing] = [m for m in doc.paperspace("A-1").query("MTEXT")
                 if _tagged(m).get("kind") == "listing"]
    for fid in ("H-SN", "X-1", "X-2", "X-3"):
        assert fid in listing.text
    assert "D-1" not in listing.text


def test_the_engines_own_findings_reconcile_and_abstentions_draw_nothing(plain):
    """Abstentions never reach the writer; only findings are marked, and every
    one is either marked or listed."""
    assert plain.result.abstentions
    summary, [doc], _readme = plain.write(plain.payload, tag="engine")
    skipped = sum(1 for f in plain.payload if f["scenario"] == "as_declared")
    assert summary["placed"] + summary["listed"] == len(plain.payload) - skipped
    outlines = [e for name in doc.layout_names() if name != "Model"
                for e in doc.paperspace(name).query("LWPOLYLINE") if _tagged(e)]
    assert len(outlines) == summary["placed"]
    assert len(outlines) == sum(1 for f in plain.payload
                                if f["rect"] and f["scenario"] != "as_declared")
    abstained = {a.rule_id for a in plain.result.abstentions}
    assert not abstained & {_tagged(e)["rule_id"] for e in outlines}


def test_a_drawing_with_no_dxf_to_write_into_is_reported_with_its_findings(plain):
    sidecar = copy.deepcopy(plain.cs.data)
    sidecar["dxf_paths"] = {}
    summary, docs, readme = plain.write(plain.payload, tag="nodxf", sidecar=sidecar)
    assert docs == [] and summary["drawings"] == 0
    assert summary["pages_without_dxf"] == [p["page"] for p in plain.cs.pages]
    assert summary["listed_in_readme"] == len(plain.payload)
    assert summary["placed"] + summary["listed"] == len(plain.payload)
    for f in plain.payload:
        assert f["fid"] in readme


def test_the_read_me_explains_the_layers(plain):
    _summary, _docs, readme = plain.write(plain.payload, tag="readme")
    for name in (markup.CLOUD_LAYER, markup.VERIFIED_LAYER, markup.TEXT_LAYER, markup.APPID,
                 markup.DASHED, DECLARED_BASIS_NOTE, "Raised by AI review", "Not checked"):
        assert name in readme
