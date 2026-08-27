"""Coverage is reported separately from severity.

A severity tally answers "what did this review find". It cannot answer "did
this review look at everything", and the two have the same-looking answer: a
set comes back with no findings either because it is clean or because its
sheets could not be read. MEASURED sitting at 0 is the case that prompted
this — it is a severity badge for a finding traced off CAD geometry, and 0
means "no egress-path layer to trace", not "no sheets examined".
"""
from __future__ import annotations

import pymupdf

from fbcreview.options import ReviewOptions
from fbcreview.pipeline import build_facts
from fbcreview.render.markup import Renderer
from fbcreview.rules import run_all


#: A 36" x 24" sheet, which is what a permit set is actually plotted at. The
#: margin legend sizes itself to the sheet, and the glance tiles only earn
#: their space on a sheet this tall — on a letter-size page they are dropped
#: and the counts are carried by the register instead.
SHEET_W, SHEET_H = 2592, 1728


def _set(tmp_path, readable=3, blank=2):
    """A set of readable sheets plus some that carry nothing to read."""
    doc = pymupdf.open()
    for i in range(readable):
        page = doc.new_page(width=SHEET_W, height=SHEET_H)
        for n in range(120):
            page.draw_line((40 + n * 16, 80), (40 + n * 16, 1100))
        page.insert_text((80, 1300),
                         "LIFE SAFETY PLAN  OCCUPANT LOAD 70  " * 6, fontsize=18)
        r = page.rect
        page.insert_text((r.x0 + 0.84 * r.width, r.y0 + 0.9 * r.height),
                         f"G-{i}", fontsize=30)
    for _ in range(blank):
        doc.new_page(width=SHEET_W, height=SHEET_H)   # nothing on it at all
    path = tmp_path / "set.pdf"
    doc.save(str(path))
    doc.close()
    return str(path)


def _render(path):
    facts = build_facts(path)
    options = ReviewOptions(project_name="Coverage")
    res = run_all(facts, options)
    return Renderer(path, res.findings, facts.sheets, options, res.abstentions)


def test_coverage_counts_every_sheet_not_just_the_ones_with_findings(tmp_path):
    cov = _render(_set(tmp_path, readable=3, blank=2))._coverage()
    assert cov["total"] == 5, cov
    # The three plotted sheets are readable and drawn; the two empties are not.
    assert cov["read"] == 3, cov
    assert cov["drawn"] == 3, cov


def test_unreadable_sheets_are_visible_as_a_shortfall(tmp_path):
    """The number a reader needs is the gap, and it must not be silent."""
    cov = _render(_set(tmp_path, readable=1, blank=4))._coverage()
    assert cov["total"] == 5
    assert cov["read"] == 1
    assert cov["read"] < cov["total"], "an unread sheet must show as a shortfall"


def test_a_fully_readable_set_reports_full_coverage(tmp_path):
    cov = _render(_set(tmp_path, readable=4, blank=0))._coverage()
    assert cov["read"] == cov["total"] == 4
    assert cov["numbered"] == 4, cov


def test_coverage_reaches_the_register_page(tmp_path):
    """It has to be printed, not merely computed."""
    path = _set(tmp_path, readable=3, blank=2)
    facts = build_facts(path)
    options = ReviewOptions(project_name="Coverage")
    res = run_all(facts, options)
    out = tmp_path / "review.pdf"
    Renderer(path, res.findings, facts.sheets, options, res.abstentions).build(str(out))

    doc = pymupdf.open(str(out))
    text = " ".join(doc[n].get_text() for n in range(doc.page_count)).upper()
    doc.close()
    assert "SHEET COVERAGE" in text
    assert "TEXT RECOVERED" in text
    assert "3 OF 5" in text, "the shortfall must be printed as a ratio"


def test_coverage_tiles_sit_in_the_glance_row_on_the_sheet(tmp_path):
    """The margin legend carries the counts too, as tiles beside the severities.

    A reader holding a plotted sheet has no register in front of them, so the
    "was it all read" answer has to be on the sheet itself.
    """
    path = _set(tmp_path, readable=3, blank=2)
    facts = build_facts(path)
    options = ReviewOptions(project_name="Coverage")
    res = run_all(facts, options)
    out = tmp_path / "review.pdf"
    Renderer(path, res.findings, facts.sheets, options, res.abstentions).build(str(out))

    doc = pymupdf.open(str(out))
    margin = doc[0].get_text().upper()
    doc.close()
    assert "THE WHOLE SET AT A GLANCE" in margin
    assert "OF 5 SHEETS IN THE SET" in margin
    for label in ("TEXT READ", "NUMBERED", "DRAWINGS"):
        assert label in margin, f"{label} tile missing from the glance row"
    # A short count is shown against its total; a full one stands alone.
    assert "3/5" in margin, "a shortfall must show as a ratio, not a bare count"


def test_each_coverage_tile_is_explained_in_the_legend(tmp_path):
    """A metric named but never explained is what sent a reader looking for a
    bug in the measurement code. Every tile label must carry a description."""
    path = _set(tmp_path, readable=3, blank=2)
    facts = build_facts(path)
    options = ReviewOptions(project_name="Coverage")
    res = run_all(facts, options)
    out = tmp_path / "review.pdf"
    Renderer(path, res.findings, facts.sheets, options, res.abstentions).build(str(out))

    doc = pymupdf.open(str(out))
    margin = " ".join(doc[0].get_text().split()).upper()
    doc.close()

    from fbcreview.render.markup import COVERAGE_LEGEND
    assert "HOW MUCH OF THE SET WAS READ" in margin
    for name, desc in COVERAGE_LEGEND:
        assert name in margin, f"{name} missing from the legend"
        # The first few words of each description, so a reworded blurb still
        # passes but a missing one does not.
        opener = " ".join(desc.split()[:4]).upper()
        assert opener in margin, f"{name} has no description in the legend"


def test_the_legend_is_sized_to_its_contents_not_stretched_to_the_rail(tmp_path):
    """A rail with one card on it has hundreds of points to spare.

    Those used to be split into gaps between the legend's sections, which put
    a ~200 pt hole above the glance row and made the block read as broken
    rather than as spaced. The legend now takes what it can use and sits at
    the foot of the rail, so the run from the last legend row to the glance
    heading stays a gap and does not become a void.
    """
    path = _set(tmp_path, readable=3, blank=2)
    facts = build_facts(path)
    options = ReviewOptions(project_name="Coverage")
    res = run_all(facts, options)
    out = tmp_path / "review.pdf"
    Renderer(path, res.findings, facts.sheets, options, res.abstentions).build(str(out))

    doc = pymupdf.open(str(out))
    words = doc[0].get_text("words")
    doc.close()

    def where(phrase):
        """The foot of a phrase in the margin.

        Phrases, not single words — "THE" and "DRAWINGS" each occur several
        times over. Matched against the word list rather than with
        `search_for`, which will not match across a line wrap.
        """
        bare = lambda t: t.upper().strip(".,;:—-()")
        want = [bare(t) for t in phrase.split()]
        for i in range(len(words) - len(want) + 1):
            run = words[i:i + len(want)]
            if [bare(w[4]) for w in run] == want:
                return max(w[3] for w in run)
        raise AssertionError(f"{phrase!r} not on the sheet")

    last_row = where("flat picture of a drawing")   # end of the DRAWINGS blurb
    glance = where("THE WHOLE SET AT A GLANCE")

    assert glance > last_row, "the glance row must follow the legend rows"
    assert glance - last_row < 140, (
        f"{glance - last_row:.0f} pt between the last legend row and the "
        "glance heading — that is a hole, not a gap")


def test_the_legend_block_reports_its_own_height_before_it_is_drawn(tmp_path):
    """The height has to be known up front — that is what lets the block be
    sized to its contents. Each section it carries has to add to it."""
    rows = ["CRITICAL", "HIGH"]
    h = Renderer._legend_h
    bare = h(rows, [], 0, 42, 55, 44, 34, False, False, False, 190)
    tally = h(rows, [], 0, 42, 55, 44, 34, True, False, False, 190)
    cover = h(rows, [], 3, 42, 55, 44, 34, True, True, False, 190)
    about = h(rows, [], 3, 42, 55, 44, 34, True, True, True, 190)
    assert bare < tally < cover < about, (bare, tally, cover, about)
    # Coverage costs its three explained rows plus the second tile row.
    assert cover - tally >= 3 * 55
