"""Per-view scale attribution.

The failing input, written down: a permit sheet is not one drawing.  A floor
plan at 1/4", an enlarged restroom at 1/2" and a wall section at 1-1/2" share
one 24x36 page, each with its scale printed under its title.  `page_scale` read
all three labels, could not tell which governed which geometry, and abstained
for the whole sheet — so every geometric rule stood down on the sheets that
carry the most geometry.  Scale resolved on 13 of 24 ITEC pages and 18 of 35
Sculpted pages (`docs/reference/`), and the remainder are overwhelmingly this.

What these tests hold to account is not that more sheets resolve.  It is that
the ones that do not still say precisely why, and that a view never borrows the
scale of the view beside it:

* a view no label claims abstains, naming that;
* a view two labels claim abstains, naming both;
* geometry spanning two views abstains rather than picking one;
* the value is always the number printed on the sheet — only the attribution of
  that number to a region is inferred, and every note says so.
"""
from __future__ import annotations

import pymupdf
import pytest

from fbcreview.confidence import Evidence, HIGH, MEDIUM
from fbcreview.extract import views as V
from fbcreview.extract.scale import (label_candidates, label_value, measure_viewports,
                                     page_scale, resolve, _LABEL)
from fbcreview.facts import PageGeometry, ViewScale
from fixtures import permit_sets as P

PLAN, ENLARGED, SECTION = 18.0, 36.0, 108.0


def opened(buf: bytes) -> pymupdf.Document:
    return pymupdf.open(stream=buf, filetype="pdf")


# ── the reproduction ──────────────────────────────────────────────────────
def test_a_sheet_printing_three_scales_has_no_page_wide_scale():
    """Unchanged, and correct: there is no one scale for this page."""
    with opened(P.multiview()) as doc:
        ev = page_scale(doc, 0, doc[0].get_text())
        assert ev.value is None
        assert not ev
        assert "3 different scales" in ev.note


def test_each_view_on_that_sheet_resolves_its_own_scale():
    """The fix. Three views, three labels, three scales, attributed by position."""
    with opened(P.multiview()) as doc:
        _page, views = resolve(doc, 0, doc[0].get_text())

    assert [v.scale.value for v in views] == [PLAN, ENLARGED, SECTION]
    assert all(v.scale.confidence == MEDIUM for v in views)
    # Ordered down the page, then across: the plan and the enlarged plan share a
    # band, the section is below them.
    assert views[0].rect[1] == views[1].rect[1]
    assert views[2].rect[1] > views[0].rect[3]


def test_the_page_abstention_now_says_how_many_views_resolved():
    """"Not checked" must not read the same as "checked and passed", and it must
    not read the same as "gave up" either when it did in fact resolve."""
    with opened(P.multiview()) as doc:
        ev, _views = resolve(doc, 0, doc[0].get_text())
    assert "segmented into 3 views and 3 of them resolved" in ev.note


def test_every_view_names_the_label_it_read_and_where_it_sits():
    """Provenance: somebody has to be able to check this against the sheet."""
    with opened(P.multiview()) as doc:
        _page, views = resolve(doc, 0, doc[0].get_text())

    plan = views[0]
    assert "page 1, view 1 of 3" in plan.scale.source
    assert "1/4\" = 1'-0\"" in plan.scale.note
    assert "attributed by position" in plan.scale.source
    assert plan.scale.page == 0


# ── the value is read, never invented ─────────────────────────────────────
def test_a_view_with_no_scale_label_abstains_and_says_so():
    layout = [
        ((60, 60, 460, 380), "LIFE SAFETY PLAN", '1/4" = 1\'-0"', PLAN),
        ((560, 60, 900, 300), "ENLARGED RESTROOM PLAN", '1/2" = 1\'-0"', ENLARGED),
        ((60, 470, 420, 690), "WALL SECTION", "", None),      # title, no scale
    ]
    with opened(P.multiview(views=layout)) as doc:
        _page, views = resolve(doc, 0, doc[0].get_text())

    orphan = views[-1]
    assert orphan.scale.value is None
    assert "no scale label is printed against this view" in orphan.scale.note
    # And it did not quietly inherit either neighbour.
    assert [v.scale.value for v in views[:2]] == [PLAN, ENLARGED]


def test_two_labels_against_one_view_abstains_rather_than_choosing():
    """Both labels are printed under the same drawing. Position cannot rank them,
    so neither is used."""
    doc = pymupdf.open()
    page = doc.new_page(width=1224, height=792)
    P._view(page, (60, 60, 460, 380), "PLAN", '1/4" = 1\'-0"')
    page.insert_text((260, 410), '1/2" = 1\'-0"', fontsize=8)
    P._view(page, (60, 500, 420, 700), "SECTION", '1 1/2" = 1\'-0"')
    buf = doc.tobytes()
    doc.close()

    with opened(buf) as d:
        _page, views = resolve(d, 0, d[0].get_text())

    contested = views[0]
    assert contested.scale.value is None
    assert "2 scale labels sit against this view" in contested.scale.note
    assert "1/4" in contested.scale.note and "1/2" in contested.scale.note


def test_a_bare_equals_one_foot_is_not_a_scale():
    """Every group in the pattern is optional, so it also matches `" = 1'-0"`
    with no number in front of it. That names no length."""
    m = _LABEL.search('" = 1\'-0"')
    assert m is not None
    assert label_value(m) is None
    assert label_candidates('SEE DETAIL " = 1\'-0"') == []


# ── attributing geometry to a view ────────────────────────────────────────
def geometry_of(buf: bytes) -> PageGeometry:
    with opened(buf) as doc:
        ev, views = resolve(doc, 0, doc[0].get_text())
    return PageGeometry(0, ev, {}, views)


def test_geometry_inside_one_view_is_measured_at_that_view_s_scale():
    geo = geometry_of(P.multiview())
    assert geo.scale_for((100, 100, 400, 350)).value == PLAN
    assert geo.scale_for((600, 100, 880, 280)).value == ENLARGED


def test_geometry_spanning_two_views_abstains_rather_than_averaging():
    geo = geometry_of(P.multiview())
    ev = geo.scale_for((100, 100, 880, 350))
    assert ev.value is None
    assert "spans 2 views drawn at different scales" in ev.note


def test_geometry_outside_every_view_abstains():
    geo = geometry_of(P.multiview())
    ev = geo.scale_for((1000, 700, 1100, 750))
    assert ev.value is None
    assert "outside every view" in ev.note


def test_a_page_wide_scale_still_governs_everything_on_its_page():
    """The single-label sheet is the common case and must not have moved."""
    with opened(P.sculpted_like(pages=1)) as doc:
        ev, views = resolve(doc, 0, doc[0].get_text())
    assert ev.value == PLAN
    assert ev.confidence == MEDIUM
    assert views == []                       # not segmented; nothing to attribute

    geo = PageGeometry(0, ev, {}, views)
    assert geo.scale_for((10, 10, 1200, 780)).value == PLAN
    assert geo.scale_resolved


def test_a_page_resolves_nothing_when_neither_the_page_nor_a_view_does():
    """The coverage metric counts pages, and must not count this one."""
    nothing = Evidence.abstain("page 1", "no scale label and no /Measure viewports", 0)
    assert not PageGeometry(0, nothing, {}, []).scale_resolved
    assert not PageGeometry(0, nothing, {}, [
        ViewScale((0, 0, 10, 10), Evidence.abstain("page 1, view 1 of 1", "no label", 0)),
    ]).scale_resolved
    # One view resolving is enough for the page to count as resolved.
    assert PageGeometry(0, nothing, {}, [
        ViewScale((0, 0, 10, 10), Evidence.abstain("page 1, view 1 of 2", "no label", 0)),
        ViewScale((0, 20, 10, 30), Evidence(PLAN, "page 1, view 2 of 2", MEDIUM, "", 0)),
    ]).scale_resolved


# ── the /Measure cross-check, per view rather than per page ───────────────
def with_viewport(buf: bytes, bbox: str, c: float) -> bytes:
    """Attach one /VP viewport carrying a /Measure conversion factor."""
    doc = opened(buf)
    vp = ("[<</Type/Viewport/Measure<</Type/Measure/Subtype/RL"
          "/X[<</C {:.7f}/U(ft)>>]>>/BBox[{}]>>]".format(c, bbox))
    doc.xref_set_key(doc[0].xref, "VP", vp)
    out = doc.tobytes()
    doc.close()
    return out


def test_a_viewport_bbox_is_read_into_the_engine_s_own_coordinates():
    """PDF /BBox is bottom-left origin, y up; everything else here is top-left,
    y down. Getting the flip wrong would silently stop every cross-check."""
    # The plan view occupies (60,60)-(460,380) top-left on a 792 pt tall page,
    # which is y 412..732 measured from the bottom.
    buf = with_viewport(P.multiview(), "60 412 460 732", 1.0 / PLAN)
    with opened(buf) as doc:
        ports = measure_viewports(doc, 0)

    assert len(ports) == 1
    rect, value = ports[0]
    assert value == PLAN
    assert (round(rect.x0), round(rect.y0), round(rect.x1), round(rect.y1)) == (60, 60, 460, 380)


def test_a_viewport_over_a_view_confirms_that_view_s_label():
    """Two independent sources agreeing about this region — the whole point."""
    buf = with_viewport(P.multiview(), "60 412 460 732", 1.0 / PLAN)
    with opened(buf) as doc:
        _page, views = resolve(doc, 0, doc[0].get_text())

    assert views[0].scale.value == PLAN
    assert views[0].scale.confidence == HIGH
    assert "confirmed by a /Measure viewport covering it" in views[0].scale.note
    # The other two views are not covered by it and stay where they were.
    assert [v.scale.confidence for v in views[1:]] == [MEDIUM, MEDIUM]


def test_a_viewport_at_the_wrong_scale_over_a_view_does_not_confirm_it():
    """The ITEC failure: the same 125 viewports on every page at 23 different
    factors, the sheet viewport among them at 71.99 pt/ft. Agreement has to mean
    agreement, so this one corroborates nothing and the label still stands."""
    buf = with_viewport(P.multiview(), "60 412 460 732", 1.0 / 71.99)
    with opened(buf) as doc:
        _page, views = resolve(doc, 0, doc[0].get_text())

    assert views[0].scale.value == PLAN            # the printed label, not 71.99
    assert views[0].scale.confidence == MEDIUM


# ── the shapes real sheets come in ────────────────────────────────────────
@pytest.mark.parametrize("rotation", [0, 90, 270])
def test_attribution_survives_a_rotated_sheet(rotation):
    """Seven of the thirty-five real sheets carry /Rotate 270. get_drawings()
    and get_text() both report unrotated coordinates, so the two are directly
    comparable — this is the test that says so."""
    with opened(P.multiview(rotate=rotation)) as doc:
        assert doc[0].rotation == rotation
        _page, views = resolve(doc, 0, doc[0].get_text())
    assert [v.scale.value for v in views] == [PLAN, ENLARGED, SECTION]


def test_the_sheet_border_does_not_join_every_view_into_one():
    """A border rect abuts all three views. Clustered, it would merge them into
    one cluster holding three labels — which is the page-wide abstention again,
    wearing a different hat."""
    framed = P.multiview(border=True)
    bare = P.multiview(border=False)
    with opened(framed) as a, opened(bare) as b:
        assert len(V.drawn_views(a[0])) == len(V.drawn_views(b[0])) == 3


def test_a_stray_mark_is_not_a_view():
    """A north arrow or a callout bubble is geometry, not a drawing to measure."""
    doc = pymupdf.open()
    page = doc.new_page(width=1224, height=792)
    P._view(page, (60, 60, 460, 380), "PLAN", '1/4" = 1\'-0"')
    P._view(page, (60, 500, 420, 700), "SECTION", '1 1/2" = 1\'-0"')
    page.draw_circle((1100, 700), 4, width=0.5)
    buf = doc.tobytes()
    doc.close()

    with opened(buf) as d:
        assert len(V.drawn_views(d[0])) == 2


def test_clustering_is_not_re_fetching_the_page_s_drawings():
    """get_drawings() is the most expensive call in extraction. Segmentation
    takes the caller's copy; a regression to re-fetching doubles it per page."""
    with opened(P.multiview()) as doc:
        page = doc[0]
        paths = page.get_drawings()

        calls = []
        original = page.get_drawings
        page.get_drawings = lambda *a, **k: (calls.append(1), original(*a, **k))[1]
        V.drawn_views(page, paths)
        assert calls == []


# ── the payoff, end to end ────────────────────────────────────────────────
def test_egress_is_measured_on_a_sheet_that_used_to_abstain(tmp_path):
    """A 300 pt run inside the 1/4" plan is 300/18 = 16.7 ft. Measured at the
    page's old (absent) scale it was not measured at all; measured at the wall
    section's 108 pt/ft it would read 2.8 ft, which is the error this guards."""
    from fbcreview.pipeline import build_facts
    from fbcreview.rules import RuleResult
    from fbcreview.rules.r_geometry import egress_extent

    pdf = tmp_path / "multiview.pdf"
    pdf.write_bytes(P.multiview(egress=(80, 200, 380, 200)))

    facts = build_facts(str(pdf))
    out = RuleResult()
    egress_extent(facts, out)

    assert not out.abstentions, [a.reason for a in out.abstentions]
    assert len(out.findings) == 1
    run_ft = float(out.findings[0].title.split("—")[-1].strip().split()[0])
    assert run_ft == pytest.approx(300 / PLAN, abs=0.4)
    # The finding carries the view it was measured in, not just the number.
    assert "medium confidence" in out.findings[0].checked
    assert "view 1 of 3" in out.findings[0].checked
