"""The invisible text layer written over a plotted sheet, read back by the engine.

ezdxf plots a drawing's text as glyph outlines; `fbcreview/cad/render.py` puts
the drafter's strings back as invisible text (render mode 3) so the layout
reader, the AI reader's grounding and the marked-up report all read one text
layer. Its contract is what `fbcreview.layout.page_layout` needs to see:

* a label and its value pair the way a plotted PDF pairs them — inline, in a
  row from two entities, stacked under single-line TEXT (1.25 × cap pitch) and
  under MTEXT (1.667 × cap);
* complex MTEXT, drawn one word per call, reads as the same text as a single
  run, and words never fuse — not when the CAD font is narrower than the
  Helvetica the layer is written in either;
* every word box lies over its ink, rotated text keeps its direction, and a
  stacked fraction is rejoined (`5'-0 3/4"`);
* the layer is invisible and adds no layer of its own.

Runs are built here directly (a `TextRun` is a string, a transform and an ink
extent) where the geometry has to be exact, and through small drawings where
what matters is what ezdxf itself draws.
"""
from __future__ import annotations

import math

import pytest

ezdxf = pytest.importorskip("ezdxf")

import pymupdf  # noqa: E402
from ezdxf.math import Matrix44  # noqa: E402

from fbcreview import cad  # noqa: E402
from fbcreview.cad.render import (FONT_PER_CAP, TextRun, cells, write_cells)  # noqa: E402
from fbcreview.layout import page_layout  # noqa: E402
from fbcreview.layout.model import INLINE, ROW, STACKED  # noqa: E402
from fixtures import cad_drawings as drawings  # noqa: E402

#: Layout inches -> page points on an ARCH D sheet, origin top-left.
TO_PAGE = (72.0, 0.0, 0.0, -72.0, 0.0, 24.0 * 72.0)
CAP = 0.125                                     # 1/8" lettering: 9 pt on the page
HELV = pymupdf.Font("helv")


def ink(text: str, cap: float = CAP, narrow: float = 1.0) -> float:
    """A CAD font's ink width for `text`, as a fraction of Helvetica's."""
    return HELV.text_length(text, fontsize=cap) * narrow


def run(text, x, y, *, cap=CAP, width=None, handle="10", angle=0.0, kind="TEXT",
        narrow=1.0, viewport=""):
    m = Matrix44.z_rotate(math.radians(angle)) @ Matrix44.translate(x, y, 0)
    return TextRun(text=text, layer="A-ANNO", handle=handle, kind=kind, parent=kind, tag="",
                   prompt="", viewport=viewport, m=m, x0=0.0,
                   x1=width if width is not None else ink(text, cap, narrow), cap=cap)


def words_of(runs, x=None):
    """Write the runs onto a fresh ARCH D page; return (doc, page)."""
    doc = pymupdf.open()
    page = doc.new_page(width=2592, height=1728)
    write_cells(page, cells(runs, TO_PAGE))
    return doc, page


def pairs(page):
    return [(p.kind, p.label, p.values) for p in page_layout(page).pairs]


# ── pairs ────────────────────────────────────────────────────────────────────
def test_one_entity_label_and_value_is_an_inline_pair():
    _doc, page = words_of([run("OCCUPANT LOAD: 70", 2.0, 20.0)])
    assert (INLINE, "OCCUPANT LOAD", ["70"]) in pairs(page)


def test_label_and_value_as_two_entities_on_a_line_is_a_row_pair():
    label = run("OCCUPANT LOAD", 2.0, 20.0, handle="10")
    value = run("70", 2.0 + ink("OCCUPANT LOAD") + 3 * CAP, 20.0, handle="11")
    _doc, page = words_of([label, value])
    assert (ROW, "OCCUPANT LOAD", ["70"]) in pairs(page)


CODE = (("TYPE OF CONSTRUCTION:", "III-B"), ("OCCUPANCY:", "B"), ("RISK CATEGORY:", "II"))


@pytest.mark.parametrize("pitch, what", [(1.25, "TEXT"), (1.667, "MTEXT")])
def test_label_over_value_stacks_at_the_pitch_drafters_use(pitch, what):
    """Single-line TEXT is stacked at 1.25 × cap; MTEXT lines are 5/3 × cap
    apart. Both must give stacked pairs — the font-size rule (1.0 × cap) is
    what keeps a value's box from overlapping its label's too far."""
    runs, y = [], 20.0
    for i, (label, value) in enumerate(CODE):
        # MTEXT: one entity holds both lines; TEXT: one entity each
        h1, h2 = (f"{i}", f"{i}") if what == "MTEXT" else (f"{i}a", f"{i}b")
        runs.append(run(label, 2.0, y, handle=h1, kind=what))
        runs.append(run(value, 2.0, y - pitch * CAP, handle=h2, kind=what))
        y -= 4 * CAP
    _doc, page = words_of(runs)
    got = pairs(page)
    for label, value in CODE:
        assert (STACKED, label.rstrip(":"), [value]) in got


# ── words ────────────────────────────────────────────────────────────────────
PHRASE = ("PRECAST:", '8"', "HOLLOWCORE", "PLANK")


def per_word(narrow=1.0, gap=0.6):
    """Complex MTEXT as ezdxf draws it: one call per word, word spaces of
    `gap` × cap between the inks, all one entity."""
    out, x = [], 2.0
    for w in PHRASE:
        r = run(w, x, 20.0, narrow=narrow, handle="20", kind="MTEXT")
        out.append(r)
        x += r.x1 + gap * CAP
    return out


def test_per_word_runs_read_as_the_same_text_as_one_run():
    whole = run(" ".join(PHRASE), 2.0, 20.0, handle="20", kind="MTEXT")
    _d1, one = words_of([whole])
    _d2, many = words_of(per_word())
    assert [w[4] for w in one.get_text("words")] == list(PHRASE)
    assert [w[4] for w in many.get_text("words")] == list(PHRASE)
    seg_one = [s.text for s in page_layout(one).segments]
    seg_many = [s.text for s in page_layout(many).segments]
    assert seg_one == seg_many == ['PRECAST: 8" HOLLOWCORE PLANK']


@pytest.mark.parametrize("narrow, gap", [(0.7, 0.6), (0.6, 0.3)])
def test_a_cad_font_narrower_than_helvetica_does_not_fuse_words(narrow, gap):
    """Squeezed to the CAD font's ink, the text keeps its spaces however tight
    the font set the words (0.3 × cap between words of a condensed font)."""
    _doc, page = words_of(per_word(narrow, gap))
    assert [w[4] for w in page.get_text("words")] == list(PHRASE)
    assert "HOLLOWCOREPLANK" not in page.get_text()


def test_two_entities_a_word_space_apart_stay_two_words():
    a = run("MIXED", 2.0, 20.0, handle="30", narrow=0.7)
    b = run("OCCUPANCY:", 2.0 + a.x1 + 0.5 * CAP, 20.0, handle="31", narrow=0.7)
    _doc, page = words_of([a, b])
    assert [w[4] for w in page.get_text("words")] == ["MIXED", "OCCUPANCY:"]


def test_every_word_box_lies_over_its_ink():
    """Horizontally within a point of the glyphs; vertically from above the cap
    line to below the baseline, at the one height every threshold assumes."""
    runs = [run("OCCUPANT LOAD", 2.0, 20.0, handle="40", narrow=0.8),
            run("70", 5.0, 20.0, handle="41", narrow=0.8)]
    _doc, page = words_of(runs)
    words = {w[4]: w for w in page.get_text("words")}
    for r in runs:
        first, last = r.text.split()[0], r.text.split()[-1]
        x0 = 72 * 2.0 if r.handle == "40" else 72 * 5.0
        assert words[first][0] == pytest.approx(x0, abs=1.0)
        assert words[last][2] == pytest.approx(x0 + 72 * r.x1, abs=1.0)
    base, cap_pt = 1728 - 72 * 20.0, 72 * CAP
    for w in words.values():
        assert w[1] <= base - cap_pt and w[3] >= base                 # covers cap to baseline
        assert w[3] - w[1] == pytest.approx(1.374 * FONT_PER_CAP * cap_pt, rel=0.02)


@pytest.mark.parametrize("angle, direction", [(90, (0.0, -1.0)), (270, (0.0, 1.0)),
                                              (0, (1.0, 0.0))])
def test_rotated_text_extracts_in_the_direction_it_reads(angle, direction):
    """90° in the drawing reads bottom to top on the page (y grows down). The
    sign of `morph`'s rotation was measured: Matrix(θ) sends +x to
    (cos θ, −sin θ), so the page angle is negated."""
    r = run("OCCUPANCY:", 10.0, 10.0, angle=angle, handle="50")
    _doc, page = words_of([r])
    lines = [ln for b in page.get_text("dict")["blocks"] for ln in b.get("lines", [])]
    assert len(lines) == 1
    assert lines[0]["dir"] == pytest.approx(direction, abs=1e-3)
    (w,) = page.get_text("words")
    assert w[4] == "OCCUPANCY:"
    ox, oy = 72 * 10.0, 1728 - 72 * 10.0
    length = 72 * r.x1
    size = 72 * CAP * FONT_PER_CAP
    if angle == 90:            # runs up the page from its origin; its cap side is left
        assert w[1] == pytest.approx(oy - length, abs=1.0) and w[3] >= oy - 1
        assert w[0] == pytest.approx(ox - 1.075 * size, abs=1.0)
        assert w[2] == pytest.approx(ox + 0.299 * size, abs=1.0)
    elif angle == 270:         # runs down the page; its cap side is right
        assert w[3] == pytest.approx(oy + length, abs=1.0) and w[1] <= oy + 1
        assert w[2] == pytest.approx(ox + 1.075 * size, abs=1.0)


def test_the_layer_is_invisible_and_adds_no_layer():
    doc = pymupdf.open()
    page = doc.new_page(width=2592, height=1728)
    page.draw_rect(pymupdf.Rect(100, 100, 400, 300), color=(0, 0, 0))
    before = page.get_pixmap(dpi=36).samples
    ocgs = dict(doc.get_ocgs())
    n = write_cells(page, cells([run("OCCUPANT LOAD: 70", 1.5, 21.0),
                                 run("VISIBLE NOWHERE", 3.0, 20.5, angle=90)], TO_PAGE))
    assert n == 2
    assert page.get_pixmap(dpi=36).samples == before
    assert doc.get_ocgs() == ocgs
    assert "OCCUPANT LOAD: 70" in page.get_text()
    assert b"3 Tr" in page.read_contents()               # text render mode 3: no paint


# ── stacked fractions ────────────────────────────────────────────────────────
def fraction_runs(base="5'-0", top="3", bottom="4", after='"', scale=1.0, handle="60"):
    """`5'-0\\S3/4;"` laid out the way ezdxf lays it out: the halves centred
    over one another, the numerator's baseline 0.2·h_top + 1.2·h_bottom above
    the denominator's, which sits on the line's baseline."""
    y, h = 20.0, CAP * scale
    b = run(base, 2.0, y, handle=handle, kind="MTEXT")
    x = 2.0 + b.x1 + 0.03
    width = max(ink(top, h), ink(bottom, h))
    t = run(top, x + (width - ink(top, h)) / 2, y + 0.2 * h + 1.2 * h, cap=h, handle=handle,
            kind="MTEXT")
    d = run(bottom, x + (width - ink(bottom, h)) / 2, y, cap=h, handle=handle, kind="MTEXT")
    out = [b, t, d]
    if after:
        out.append(run(after, x + width + 0.2 * CAP, y, handle=handle, kind="MTEXT"))
    return out


def test_a_stacked_fraction_is_rejoined_with_its_inch_mark():
    (cell,) = cells(fraction_runs(), TO_PAGE)
    assert cell.text == "5'-0 3/4\""
    assert len(cell.runs) == 4


def test_a_fraction_with_halves_set_smaller_is_rejoined_too():
    (cell,) = cells(fraction_runs(scale=0.7, after=""), TO_PAGE)
    assert cell.text == "5'-0 3/4"


def test_a_fraction_that_opens_a_line_is_its_own_cell():
    runs = fraction_runs()[1:3]                       # just the halves
    (cell,) = cells(runs, TO_PAGE)
    assert cell.text == "3/4"
    assert cell.origin[1] == pytest.approx(1728 - 72 * 20.0, abs=0.01)   # on the baseline


def test_two_lines_of_a_paragraph_are_not_a_fraction():
    """MTEXT at single spacing puts lines 1.667 × cap apart; a fraction's rise
    is 1.4 × cap. A column of numbers stays a column."""
    runs = [run("3", 2.0, 20.0, handle="70", kind="MTEXT"),
            run("4", 2.0, 20.0 - 1.667 * CAP, handle="70", kind="MTEXT")]
    assert [c.text for c in cells(runs, TO_PAGE)] == ["3", "4"]


def test_a_tolerance_stack_is_not_written_as_a_fraction():
    (cell,) = cells(fraction_runs(top="+0.5", bottom="-0.2", after=""), TO_PAGE)
    assert cell.text == "5'-0 +0.5 -0.2"


def test_halves_from_two_entities_are_not_one_fraction():
    runs = fraction_runs(after="")
    runs[2] = run("4", runs[2].m.transform((0, 0, 0))[0], 20.0, handle="61", kind="MTEXT")
    assert "3/4" not in " ".join(c.text for c in cells(runs, TO_PAGE))


# ── through ezdxf itself ─────────────────────────────────────────────────────
@pytest.fixture(scope="module")
def plotted(tmp_path_factory):
    root = tmp_path_factory.mktemp("overlay")
    built = drawings.permit_set(str(root / "permit.dxf"))
    cs = cad.ingest(built["path"], str(root / "work"))
    doc = pymupdf.open(cs.pdf_path)
    yield doc
    doc.close()


def test_ezdxfs_own_fraction_is_rejoined_on_the_sheet(plotted):
    assert "CLEAR HEIGHT: 5'-0 3/4\"" in plotted[0].get_text()
    assert ("inline", "CLEAR HEIGHT", ["5'-0 3/4\""]) in pairs(plotted[0])


def test_complex_mtext_formatting_never_reaches_the_text(plotted):
    text = plotted[0].get_text()
    assert 'PRECAST: 8" HOLLOWCORE PLANK' in text
    assert "\\f" not in text and "Arial" not in text and "\\S" not in text


def test_column_pieces_sit_where_the_cad_font_put_them(plotted):
    """`40psf LL` and `FLOOR` are one TEXT laid out in columns with spaces.
    Each piece is written where ezdxf drew its glyphs — compared with the
    glyph outlines on the plotted page, not with a font metric."""
    page = plotted[0]
    first = next(w for w in page.get_text("words") if w[4] == "40psf")
    words = [w for w in page.get_text("words") if abs(w[3] - first[3]) < 1.0]
    assert [w[4] for w in words] == ["40psf", "LL", "FLOOR"]
    band = pymupdf.Rect(0, words[0][1], 600, words[0][3])
    xs = []                                      # every point of the glyph outlines
    for d in page.get_drawings():
        if d["rect"].intersects(band) and d["rect"].width < 600:
            for item in d["items"]:
                xs.extend(p.x for p in item[1:] if isinstance(p, pymupdf.Point))
    groups = []                                  # clustered into the pieces of the line
    for x in sorted(xs):
        if groups and x - groups[-1][1] < 2 * 9.0:
            groups[-1][1] = x
        else:
            groups.append([x, x])
    assert len(groups) == 2
    assert words[0][0] == pytest.approx(groups[0][0], abs=1.0)
    assert words[2][0] == pytest.approx(groups[1][0], abs=1.0)
    assert words[2][2] == pytest.approx(groups[1][1], abs=1.5)


def test_control_codes_are_decoded_before_they_reach_the_text(tmp_path):
    """`%%U` (underline) is dropped and `%%D` is a degree sign: a leftover
    `%%UOCCUPANCY` would read as UOCCUPANCY and match nothing."""
    doc = drawings._drawing()
    sheet = drawings._sheet(doc, "P", 1)
    sheet.add_text("%%UOCCUPANCY%%U: B", height=CAP).set_placement((2, 20))
    sheet.add_text("SLOPE 45%%D", height=CAP).set_placement((2, 19))
    doc.saveas(str(tmp_path / "codes.dxf"))
    cs = cad.ingest(str(tmp_path / "codes.dxf"), str(tmp_path / "w"))
    page = pymupdf.open(cs.pdf_path)[0]
    text = page.get_text()
    assert "OCCUPANCY: B" in text and "SLOPE 45°" in text
    assert "%%" not in text
    assert (INLINE, "OCCUPANCY", ["B"]) in pairs(page)
