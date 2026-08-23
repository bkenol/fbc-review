"""What the marked-up PDF says about the declaration.

The output is the product. A finding that rests on something the user typed must
say so on the page, because the alternative is a document that attributes to the
drawings something the drawings do not state — and a client may forward that
document to a plans examiner.
"""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

import pymupdf
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fbcreview.declaration import ProjectDeclaration
from fbcreview.options import ReviewOptions
from fbcreview.pipeline import build_facts
from fbcreview.render.markup import Renderer, render
from fbcreview.rules import run_all
from fixtures.permit_sets import (DIVERGENCE_DECLARATION, ITEC_DECLARATION,
                                  divergent, itec)


#: PyMuPDF returns the ligatures the font actually carries, so `findings` comes
#: back as `ﬁndings`. Asserting on prose means normalising them first.
LIGATURES = {"\ufb00": "ff", "\ufb01": "fi", "\ufb02": "fl",
             "\ufb03": "ffi", "\ufb04": "ffl"}


def plain(text: str) -> str:
    for ligature, letters in LIGATURES.items():
        text = text.replace(ligature, letters)
    return text


def _review(tmp_path, data, declaration, name="set"):
    src = tmp_path / f"{name}.pdf"
    src.write_bytes(data)
    facts = build_facts(str(src))
    facts.meta["as_of"] = dt.date(2026, 8, 23)
    result = run_all(facts, ReviewOptions(project_name="ITEC Alico Park"), declaration)
    out = tmp_path / f"{name}-markup.pdf"
    info = render(str(src), str(out), result.findings, facts.sheets,
                  ReviewOptions(project_name="ITEC Alico Park"),
                  result.abstentions, result.reconciled)
    return result, out, info


@pytest.fixture(scope="module")
def rendered(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("render")
    result, out, info = _review(
        tmp, itec(False), ProjectDeclaration.from_dict(ITEC_DECLARATION), "itec")
    doc = pymupdf.open(str(out))
    text = plain("\n".join(page.get_text() for page in doc))
    doc.close()
    return result, out, info, text


def test_the_declaration_is_printed_as_submitted(rendered):
    _result, _out, _info, text = rendered
    assert "Project declaration as submitted" in text
    # Every answer with its state, not only the interesting ones.
    for word in ("Corroborated", "Conflicting", "Unanswered"):
        assert word in text
    assert "Construction type" in text and "II-B" in text
    assert "of 15 answered" in text


def test_the_declared_versus_drawn_section_names_both_sources(rendered):
    _result, _out, _info, text = rendered
    assert "Declared versus drawn" in text
    assert "508.4" in text
    assert "MIXED OCCUPANCY? NO" in text


def test_the_legend_gains_the_two_marker_entries(rendered):
    _result, _out, _info, text = rendered
    assert "DECLARED VS DRAWN" in text
    assert "FROM YOUR DECLARATION" in text


def test_the_about_block_says_findings_may_rest_on_user_data(rendered):
    _result, _out, _info, text = rendered
    assert "Some findings rest on what you told us" in text


def test_no_declaration_prints_none_of_it(tmp_path):
    _result, out, _info = _review(tmp_path, itec(False), None, "plain")
    doc = pymupdf.open(str(out))
    text = plain("\n".join(page.get_text() for page in doc))
    doc.close()
    assert "Project declaration as submitted" not in text
    assert "Declared versus drawn" not in text
    assert "FROM YOUR DECLARATION" not in text


def test_a_declared_basis_finding_says_so_on_its_card(tmp_path):
    """On a set whose code block is a picture, every Chapter 5 finding rests on
    the declaration alone."""
    result, out, _info = _review(
        tmp_path, itec(True), ProjectDeclaration.from_dict(ITEC_DECLARATION), "raster")
    assert any(f.basis == "declaration" for f in result.findings)
    doc = pymupdf.open(str(out))
    text = plain("\n".join(page.get_text() for page in doc))
    doc.close()
    assert "Based on the project declaration; the drawings do not state this." in text


def test_both_scenario_outcomes_reach_the_register(tmp_path):
    result, out, _info = _review(
        tmp_path, divergent(), ProjectDeclaration.from_dict(DIVERGENCE_DECLARATION),
        "divergent")
    doc = pymupdf.open(str(out))
    text = plain("\n".join(page.get_text() for page in doc))
    doc.close()
    assert "Passes as drawn. Fails as you described it." in text
    assert "Checks that came out differently under each reading" in text


def test_a_pass_is_never_described_as_a_failure(tmp_path):
    """A check that held as drawn and came out differently as declared is still
    a pass, and must not be captioned "fails as drawn"."""
    from fbcreview.render.markup import scenario_note

    result, _out, _info = _review(
        tmp_path, divergent(), ProjectDeclaration.from_dict(DIVERGENCE_DECLARATION),
        "wording")
    for f in result.findings:
        if f.scenario == "both":
            continue
        note = scenario_note(f)
        if f.status == "PASS":
            assert "Fails" not in note, f"{f.fid}: a passing check captioned {note!r}"


def test_the_legend_still_fits_after_adding_two_rows(tmp_path):
    """`insert_htmlbox` returns a negative height when content does not fit and
    draws nothing at all. The legend is a fixed strip, so the marker rows have
    to be measured rather than assumed."""
    src = tmp_path / "fit.pdf"
    src.write_bytes(itec(False))
    facts = build_facts(str(src))
    facts.meta["as_of"] = dt.date(2026, 8, 23)
    result = run_all(facts, ReviewOptions(), ProjectDeclaration.from_dict(ITEC_DECLARATION))

    r = Renderer(str(src), result.findings, facts.sheets, ReviewOptions(),
                 result.abstentions, result.reconciled)
    try:
        page = r.doc[0]
        before = page.get_text()
        # The tightest realistic strip: the height the rail leaves on a sheet
        # already carrying several cards.
        box = pymupdf.Rect(40, 300, 660, 533)
        r._legend(page, box, [("CRITICAL", 1), ("HIGH", 2), ("MEDIUM", 1),
                              ("LOW", 0), ("MEASURED", 0), ("VERIFIED", 9)])
        added = page.get_text()[len(before):]
        assert "CRITICAL" in added and "VERIFIED" in added
        # Either both marker rows are drawn or neither is; a half-drawn legend
        # is the failure mode this guards.
        assert ("DECLARED VS DRAWN" in added) == ("FROM YOUR DECLARATION" in added)
    finally:
        r.doc.close()


def test_the_markup_still_renders_rotated_sheets(rendered):
    """Seven of ITEC's thirty-five sheets carry /Rotate 270. Adding finding
    types is fine; the geometry handling is not to be touched."""
    _result, out, info, _text = rendered
    assert info["sheets"] == 12
    assert info["pages"] > info["sheets"]        # the register was appended
    doc = pymupdf.open(str(out))
    try:
        assert {doc[i].rotation for i in range(12)} == {0, 270}
    finally:
        doc.close()
