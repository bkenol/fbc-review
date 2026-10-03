"""Where a value came from, said on the finding — for a set read from a drawing.

CLAUDE.md: every value carries its provenance, and an inferred value never
masquerades as a stated one. A set uploaded as a DWG or DXF adds a reader the
PDF path never had — the drawing's own block attributes (method `cad`) — and a
document the PDF path never had to describe: a sheet this review plotted itself.

* `findings.json` evidence carries the claim's `basis`, and a note that names
  the drawing's field, the entity and the layout it was read from. A co-reader
  from the drawing is never called "the layout reader", two readers of one
  drawing entity are not passed off as agreement, and a measured value says
  measured and is never shown as a printed quote.
* A rule's own wording (`rules/_stated.py`) says when a row was read only from a
  drawing's attribute, as it does for a row only the AI reader found.
* The marked-up PDF of a drawing upload never says the drawing was not changed,
  that cropping the margin recovers the original sheet, that every CAD layer
  survives untouched, or that the sheets are the pages of a submitted PDF. It
  names the drawing, says the sheet was plotted by this review, and that the
  drawing file is the authority. A PDF set's wording is unchanged.
"""
from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

ezdxf = pytest.importorskip("ezdxf")

import pymupdf  # noqa: E402
from ezdxf import units  # noqa: E402

from fbcreview import cad  # noqa: E402
from fbcreview.factstore import Claim, FactStore  # noqa: E402
from fbcreview.facts import ProjectFacts  # noqa: E402
from fbcreview.options import ReviewOptions  # noqa: E402
from fbcreview.payload import _evidence, findings_payload  # noqa: E402
from fbcreview.pipeline import build_facts  # noqa: E402
from fbcreview.render.markup import render  # noqa: E402
from fbcreview.rules import Finding, run_all  # noqa: E402
from fbcreview.rules._stated import noted, stated  # noqa: E402

#: PyMuPDF returns the ligatures the font carries; prose is compared without them.
LIGATURES = {"ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi", "ﬄ": "ffl"}

#: What the PDF path says about the original sheet — false of a sheet we plotted.
PDF_ONLY = ("The drawing has not been changed",
            "crop the margin off to recover the original sheet exactly",
            "Every original CAD layer survives in this file untouched",
            "Every page of the submitted PDF")


def plain(text: str) -> str:
    for ligature, letters in LIGATURES.items():
        text = text.replace(ligature, letters)
    return " ".join(text.split())


def _attribute_drawing(path: str) -> None:
    """A title block whose fields say what they are, and two printed rows.

    The risk category is only in an attribute (`RISK_CATEGORY` = `III`): what
    prints on the sheet is `III`, and the field's name is the drawing's.
    """
    doc = ezdxf.new("R2018", setup=True)
    doc.units = units.IN
    doc.layouts.rename("Layout1", "A-1")
    sheet = doc.layouts.get("A-1")
    sheet.page_setup(size=(36, 24), margins=(0, 0, 0, 0), units="inch")
    block = doc.blocks.new("TITLE")
    block.add_attdef("SHEET_NO", (0, 0), dxfattribs={"height": 0.25, "prompt": "SHEET No."})
    block.add_attdef("RISK_CATEGORY", (0, -0.5), dxfattribs={"height": 0.125})
    sheet.add_blockref("TITLE", (30, 3)).add_auto_attribs(
        {"SHEET_NO": "G-0", "RISK_CATEGORY": "III"})
    for i, row in enumerate(("OCCUPANCY: ASSEMBLY (A-3)", "OCCUPANT LOAD: 70")):
        sheet.add_text(row, height=0.125).set_placement((1.0, 4.0 - i * 0.25))
    # A second layout on a smaller page: the report must size its margin to it.
    small = doc.layouts.new("A-2")
    small.page_setup(size=(17, 11), margins=(0, 0, 0, 0), units="inch")
    small.add_text("GENERAL NOTES", height=0.2).set_placement((1, 9))
    doc.saveas(path)


@pytest.fixture(scope="module")
def drawn(tmp_path_factory):
    root = tmp_path_factory.mktemp("provenance")
    src = os.path.join(str(root), "EVERGREEN TEST.dxf")
    _attribute_drawing(src)
    cs = cad.ingest(src, os.path.join(str(root), "work"), name="EVERGREEN TEST.dxf")
    facts = build_facts(cs.pdf_path, cad=cs.data)
    result = run_all(facts)
    payload = findings_payload(cs.pdf_path, facts, result.findings)
    out = os.path.join(str(root), "markup.pdf")
    info = render(cs.pdf_path, out, result.findings, facts.sheets, ReviewOptions(),
                  result.abstentions, result.reconciled, cad=facts.meta["cad"])
    with pymupdf.open(out) as doc:
        text = plain("\n".join(p.get_text() for p in doc))
    return SimpleNamespace(cs=cs, facts=facts, result=result, payload=payload, out=out,
                           info=info, text=text)


# ── findings.json evidence ───────────────────────────────────────────────────

def test_evidence_read_from_a_drawing_attribute_names_the_field_entity_and_layout(drawn):
    f = next(f for f in drawn.payload if f["rule_id"] == "XSHEET.RISK_CATEGORY")
    ev = next(e for e in f["evidence"] if e["field"] == "risk_category")
    claim = drawn.facts.store.resolve("risk_category").best
    assert ev["method"] == "cad" and ev["basis"] == "stated"
    assert ev["quote"] == "III"                     # what prints, not the tag
    handle = claim.source.split(":", 1)[1]
    assert ev["note"] == (f"read from the drawing's RISK CATEGORY field "
                          f"(entity {handle}, layout A-1)")
    # A value the layout reader read from the plotted text needs no note, as before.
    load = next(e for e in f["evidence"] if e["field"] == "occupant_load")
    assert load["method"] == "pair" and load["note"] == "" and load["basis"] == "stated"


def _claim(method, **kw):
    base = dict(field="risk_category", value="III", raw="III", page=0, sheet="G-0",
                box=(10.0, 10.0, 30.0, 20.0), method=method, label="RISK CATEGORY")
    base.update(kw)
    return Claim(**base)


def _facts(*claims):
    store = FactStore()
    store.extend(claims)
    return SimpleNamespace(store=store)


def test_a_drawing_co_reader_is_never_called_the_layout_reader():
    facts = _facts(_claim("ai", raw="RISK CATEGORY III"),
                   _claim("cad", source="dxf:93", layout="A-1"))
    [ev] = _evidence(None, facts, "XSHEET.RISK_CATEGORY")
    assert "layout reader" not in ev["note"]
    assert ev["note"] == ("read by AI and from the drawing's RISK CATEGORY field "
                          "(entity 93, layout A-1), in agreement")


def test_two_readers_of_one_drawing_entity_are_not_called_agreement():
    """The plotted text and the attribute behind it are one reading."""
    facts = _facts(_claim("pair", raw="III", source="dxf:93", layout="A-1"),
                   _claim("cad", source="dxf:93", layout="A-1"))
    [ev] = _evidence(None, facts, "XSHEET.RISK_CATEGORY")
    assert "in agreement" not in ev["note"]
    assert ev["note"].endswith("one reading of the same drawing text")


def test_the_ai_and_layout_reader_wording_is_unchanged():
    facts = _facts(_claim("ai", raw="RISK CATEGORY III"),
                   _claim("pair", raw="RISK CATEGORY: III"))
    [ev] = _evidence(None, facts, "XSHEET.RISK_CATEGORY")
    assert ev["note"] == "read by AI and by the layout reader, in agreement"


def test_a_measured_value_says_measured_and_is_never_a_printed_quote():
    measured = Claim(field="building_area_sf", value=1234.5,
                     raw="1,234.5 sf, closed outline", page=0, sheet="A-1",
                     box=(0.0, 0.0, 50.0, 50.0), method="cad", basis="measured",
                     source="dxf:2F", layout="A-1", layer="A-AREA-GROS")
    [ev] = _evidence(None, _facts(measured), "XSHEET.BUILDING_AREA")
    assert ev["basis"] == "measured"
    assert ev["quote"] == ""
    assert ev["note"].startswith("measured from the drawing (entity 2F, layout A-1, "
                                 "layer A-AREA-GROS), not printed on the sheet")


# ── a rule's own words ───────────────────────────────────────────────────────

def test_a_row_read_only_from_a_drawing_attribute_says_so_in_the_finding():
    store = FactStore()
    for role, value in (("required", 2.0), ("provided", 3.0)):
        store.add(Claim(field="egress.exits", value=value, raw=f"NUMBER OF EXITS {value:g}",
                        page=0, sheet="G-0", box=(0.0, 0.0, 9.0, 9.0), method="cad",
                        label="NUMBER OF EXITS", role=role, source="dxf:A1", layout="A-1"))
    facts = ProjectFacts(source_path="set.pdf", store=store)
    d = stated(facts, "1006.3.2")
    assert d is not None and d.required == 2.0 and d.provided == 3.0
    assert d.note == "read from the drawing's own attribute on G-0"
    assert noted(d, "Two exits required") == (
        "Two exits required (read from the drawing's own attribute on G-0.)")


# ── the marked-up PDF ────────────────────────────────────────────────────────

def test_the_report_never_calls_our_plot_the_applicants_sheet(drawn):
    for sentence in PDF_ONLY:
        assert sentence not in drawn.text, sentence


def test_the_report_names_the_drawing_and_says_who_plotted_it(drawn):
    t = drawn.text
    assert "EVERGREEN TEST.dxf" in t
    assert "This sheet was plotted by this review" in t
    assert "The drawing file is the authority" in t
    assert "Every sheet this review plotted from the submitted DXF" in t
    assert "Sheets plotted by this review" in t and "A-1, A-2" in t
    assert "switchable layer" in t


def test_each_sheet_names_its_own_layout(drawn):
    with pymupdf.open(drawn.out) as doc:
        first = plain(doc[0].get_text())
        second = plain(doc[1].get_text())
    assert "layout A-1 of EVERGREEN TEST.dxf" in first
    assert "layout A-2 of EVERGREEN TEST.dxf" in second


def test_a_smaller_sheet_gets_a_margin_that_fits_it(drawn):
    """The second layout is a tabloid page under an ARCH D first page. Sized
    from page 0, its rail ran 900 pt past its foot."""
    with pymupdf.open(drawn.out) as doc:
        page = doc[1]
        assert page.rect.height < doc[0].rect.height
        lowest = max(d["rect"].y1 for d in page.get_drawings())
        assert lowest <= page.rect.height + 0.5


def test_a_pdf_sets_wording_is_unchanged(tmp_path):
    # ARCH D, like the drawing above: a rail tall enough to carry the 'about' block.
    doc = pymupdf.open()
    page = doc.new_page(width=2592, height=1728)
    for i, row in enumerate(("OCCUPANCY: ASSEMBLY (A-3)", "RISK CATEGORY: III",
                             "OCCUPANT LOAD: 70")):
        page.insert_text((40, 100 + i * 15), row, fontsize=9)
    page.insert_text((1100, 760), "G-0", fontsize=15)
    src = tmp_path / "set.pdf"
    doc.save(str(src))
    facts = build_facts(str(src))
    result = run_all(facts)
    out = tmp_path / "markup.pdf"
    render(str(src), str(out), result.findings, facts.sheets, ReviewOptions(),
           result.abstentions, result.reconciled)
    with pymupdf.open(str(out)) as done:
        text = plain("\n".join(p.get_text() for p in done))
    for sentence in PDF_ONLY:
        assert sentence in text, sentence
    assert "plotted by this review" not in text
    assert "Submitted drawing" not in text


@pytest.mark.parametrize("page", [5, -1])
def test_a_finding_on_a_page_the_set_does_not_have_is_unplaced_not_a_crash(drawn, tmp_path,
                                                                            page):
    bad = Finding(fid="X-9", rule_id="X.Y", status="OPEN", severity="HIGH", discipline="d",
                  page=page, sheet="G-0", anchor="OCCUPANT LOAD", title="t", checked="c",
                  result="r", code="k")
    out = tmp_path / "bad.pdf"
    info = render(drawn.cs.pdf_path, str(out), [bad], drawn.facts.sheets, ReviewOptions(),
                  cad=drawn.facts.meta["cad"])
    assert info["marked"] == 0 and info["unplaced"] == ["X-9"]
    with pymupdf.open(str(out)) as doc:
        # A negative page must not silently mark the last sheet.
        assert not any(a.info.get("subject", "").startswith("X-9")
                       for p in doc for a in p.annots())
