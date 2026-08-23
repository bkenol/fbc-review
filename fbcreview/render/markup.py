"""General markup renderer: source PDF + findings -> reviewed PDF.

This is the v5 renderer generalised. Where `v5_markup_reference.py` was keyed to
one hand-authored register, this takes any list of `Finding` objects from the
rule engine, so the pipeline is PDF in / PDF out with nothing hand-placed.
"""
from __future__ import annotations
from typing import Dict, List, Optional, Sequence
import datetime
import pymupdf

from ..rules import Finding

GUTTER = 702
RED = (0.80, 0.06, 0.06); ORG = (0.93, 0.45, 0.00); AMB = (0.85, 0.62, 0.00)
GRN = (0.00, 0.55, 0.24); BLU = (0.10, 0.35, 0.80); GRY = (0.42, 0.45, 0.50)
MAG = (0.78, 0.05, 0.55); INK = (0.05, 0.07, 0.11); VIO = (0.42, 0.16, 0.72)
SEVC = {"CRITICAL": RED, "HIGH": ORG, "MEDIUM": AMB, "LOW": BLU,
        "VERIFIED": GRN, "MEASURED": MAG, "SCOPE": GRY}
HEX = {k: "#" + "".join(f"{int(v*255):02x}" for v in c) for k, c in SEVC.items()}
SEV_DESC = {
    "CRITICAL": "A quantified deficiency in the design as drawn. Cannot be resolved by a note.",
    "HIGH": "A stated code value is wrong, or a governing assumption is unresolved between sheets.",
    "MEDIUM": "Internally inconsistent or undocumented — a reviewer cannot confirm it from the sheet.",
    "LOW": "Editorial. Worth fixing on the next plot; will not hold up a permit.",
    "VERIFIED": "Checked against the code text and found sufficient.",
    "MEASURED": "Traced off this file's own vector geometry.",
    "SCOPE": "Not reviewed in this pass.",
}
ORDER = ["CRITICAL", "HIGH", "MEDIUM", "LOW", "MEASURED", "VERIFIED", "SCOPE"]

#: Statuses that need action. CONFLICT is a status, not a severity: the ink ramp
#: still says how much it matters, and the dashed marker says where it came from.
ACTIONABLE = ("OPEN", "CONFLICT")

VIO_HEX = "#" + "".join(f"{int(v * 255):02x}" for v in VIO)

#: The two marker entries the declaration adds to the legend.
MARKER_LEGEND = [
    ("DECLARED VS DRAWN",
     "A <b>dashed</b> box. Your project declaration and the drawings disagree here, and both "
     "readings were evaluated. Both outcomes are in the register.",
     "dashed"),
    ("FROM YOUR DECLARATION",
     "A <b>hollow</b> tag. This finding rests on an answer you gave, not on anything printed "
     "on the sheet — the drawings do not state it.",
     "hollow"),
]

#: How a divergent finding is worded on the card.
#
# The wording has to follow the *outcome*, not only the scenario: a check that
# passed under one reading and not the other is just as divergent as one that
# failed, and telling someone their passing item "fails as drawn" is a factual
# misstatement in a document a client may forward to a plans examiner.
def scenario_note(f) -> str:
    if f.scenario == "as_drawn":
        return ("Fails as drawn. Passes as you described it." if f.status in ACTIONABLE
                else "Holds as drawn. Comes out differently as you described it.")
    if f.scenario == "as_declared":
        return ("Passes as drawn. Fails as you described it." if f.status in ACTIONABLE
                else "Holds as you described it. Comes out differently as drawn.")
    return ""

DECLARED_BASIS_NOTE = ("Based on the project declaration; the drawings do not state this.")


def esc(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _fmt(value) -> str:
    """A declared or drawn value, as a person would write it."""
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, float) and value == int(value):
        return f"{int(value):,}"
    if isinstance(value, float):
        return f"{value:,.2f}"
    if isinstance(value, int):
        return f"{value:,}"
    return str(value)


def _field_count() -> int:
    from ..declaration_schema import FIELDS
    return len(FIELDS)


def _sprinkler_words(declaration) -> str:
    from ..declaration_schema import SPRINKLER_CHOICES
    key = getattr(declaration, "sprinkler_system", None)
    if not key:
        return "Not declared — read from the drawings where stated"
    return SPRINKLER_CHOICES.get(key, key)


def clip(s: str, n: int) -> str:
    """Truncate at a word boundary — a card that ends mid-word reads as a bug."""
    s = (s or "").strip()
    if len(s) <= n:
        return s
    cut = s[:n].rsplit(" ", 1)[0].rstrip(" .,;:")
    return cut + "\u2026"


class Renderer:
    def __init__(self, src: str, findings: Sequence[Finding], sheets, options,
                 abstentions=None, reconciled=None):
        self.doc = pymupdf.open(src)
        self.findings = list(findings)
        self.sheets = {s.index: s for s in sheets}
        self.opt = options
        self.abstentions = list(abstentions or [])
        self.rec = reconciled
        self.declaration = getattr(reconciled, "declaration", None)
        self.declared = bool(self.declaration and not self.declaration.is_empty())
        # A finding is *divergent* when the same check came out differently
        # under the two readings. The sheet marker follows the drawings — the
        # permit is issued against what was submitted — and says that the two
        # disagree; the detail is in the register.
        self._divergent = {
            (f.rule_id, f.sheet, f.anchor, f.fid)
            for f in self.findings if f.scenario == "as_declared"
        }
        self.author = (f"Independent FBC Review "
                       f"({options.edition_label(self.declaration)}) — advisory")
        # Widen every sheet to the LEFT. After this the page origin sits at the
        # left edge of the new margin, so page-space x=30 is inside the margin and
        # the original drawing starts at x=GUTTER. All later coordinates are page
        # space — do not subtract GUTTER again.
        for p in self.doc:
            w, h = p.rect.width, p.rect.height
            p.set_mediabox(pymupdf.Rect(-GUTTER, 0, w, h))
        self.W = self.doc[0].rect.width
        self.H = self.doc[0].rect.height
        self.rail = (30, 672)
        self._chips: Dict[int, List[pymupdf.Rect]] = {}
        self._boxes: Dict[int, List[pymupdf.Rect]] = {}

    # ── on-drawing markers ────────────────────────────────────────────────
    def diverges(self, f: Finding) -> bool:
        return (f.rule_id, f.sheet, f.anchor, f.fid) in self._divergent

    def _box(self, f: Finding) -> Optional[pymupdf.Rect]:
        # The drawings govern the sheet markup: the permit is issued against
        # what was submitted, and the AHJ reviews the sheet. A finding that only
        # exists under the declared reading therefore gets no marker — it is
        # reported in the register, where its basis is stated.
        if f.scenario == "as_declared":
            return None
        pg = self.doc[f.page]
        hits = pg.search_for(f.anchor) if f.anchor else []
        if len(hits) <= f.hit:
            return None
        r = hits[f.hit]
        b = pymupdf.Rect(r.x0 - 3, r.y0 - 3, r.x1 + 3, r.y1 + 3)
        c = SEVC.get(f.severity, GRY)
        divergent = self.diverges(f) or f.status == "CONFLICT"
        a = pg.add_rect_annot(b); a.set_colors(stroke=c); a.set_border(width=2.2)
        if divergent:
            # Dashed, so a disagreement between the two sources is legible as a
            # different kind of mark and not only as a different colour.
            a.set_border(width=2.2, dashes=[5, 3])
        a.set_info(title=self.author, subject=f"{f.fid} · {f.severity}",
                   content=f"{f.fid} [{f.severity}] {f.title}"); a.update()
        extra = ""
        if f.status == "CONFLICT":
            extra = "\n\nDECLARED VS DRAWN: your project declaration and this sheet disagree."
        elif self.diverges(f):
            extra = f"\n\nDECLARED VS DRAWN: {scenario_note(f)}"
        elif f.basis == "declaration":
            extra = f"\n\nBASIS: {DECLARED_BASIS_NOTE}"
        body = (f"{f.fid} — {f.severity}\n{f.title}\n\n"
                f"CHECKED: {f.checked}\n\nFOUND: {f.result}\n\n"
                f"CODE: {f.code}\n\nACTION: {f.action}{extra}")
        t = pg.add_text_annot(pymupdf.Point(b.x0 - 34, b.y0 - 4), body, icon="Comment")
        t.set_colors(stroke=c)
        t.set_info(title=self.author, subject=f"{f.fid} · {f.severity} · {f.title}"); t.update()
        return b

    def _chip(self, f: Finding, rect: pymupdf.Rect):
        pg = self.doc[f.page]; c = SEVC.get(f.severity, GRY)
        w = 11 + 6.2 * len(f.fid); h = 18
        cy = rect.y0 + (rect.height - h) / 2.0
        cands = [pymupdf.Rect(rect.x0 - 1, rect.y0 - h - 2, rect.x0 - 1 + w, rect.y0 - 2),
                 pymupdf.Rect(rect.x0 - 44 - w, cy, rect.x0 - 44, cy + h),
                 pymupdf.Rect(rect.x1 + 4, cy, rect.x1 + 4 + w, cy + h),
                 pymupdf.Rect(rect.x1 - w + 1, rect.y1 + 2, rect.x1 + 1, rect.y1 + h + 2)]
        taken = self._chips.setdefault(f.page, [])
        others = [b for b in self._boxes.get(f.page, []) if b != rect]
        for chip in cands:
            if chip.x0 < 8 or chip.x1 > self.W - 4 or chip.y0 < 2 or chip.y1 > self.H - 2:
                continue
            if any(chip.intersects(t) for t in taken) or any(chip.intersects(o) for o in others):
                continue
            hollow = f.basis == "declaration"
            if hollow:
                # Hollow, not filled: the tag is for something the sheet does
                # not say, and it must not read as an annotation of the sheet.
                pg.draw_rect(chip, color=c, fill=(1, 1, 1), width=1.4)
            else:
                pg.draw_rect(chip, color=None, fill=c)
            ink = c if hollow else (1, 1, 1)
            # insert_textbox returns a negative number when the text does not
            # fit, and draws NOTHING at all — silently. A chip with no id in it
            # is worse than a slightly smaller id, so shrink and retry.
            for size in (9.5, 8.2, 7.0):
                if pg.insert_textbox(chip + (4, 1.4, 0, 0), f.fid, fontsize=size,
                                     fontname="hebo", color=ink) >= 0:
                    break
            taken.append(chip); return

    # ── per-sheet rail ────────────────────────────────────────────────────
    CSS = ("body{font-family:Helvetica,Arial,sans-serif;color:#111;}"
           ".t{font-size:13.5pt;font-weight:bold;color:#0b1220;line-height:1.22;}"
           ".s{font-size:11.5pt;color:#333;line-height:1.35;}"
           ".id{font-size:10.5pt;color:#666;font-family:Courier;letter-spacing:.5pt;}"
           ".b{font-size:10pt;color:#5b6572;font-style:italic;margin-top:3pt;}")

    def _legend(self, pg, R, tally):
        pg.draw_rect(R, color=(0.84, 0.86, 0.90), fill=(0.985, 0.99, 1.0), width=1.3)
        t = pymupdf.Rect(R.x0, R.y0, R.x1, R.y0 + 34)
        pg.draw_rect(t, color=None, fill=(0.09, 0.13, 0.22))
        pg.insert_htmlbox(t + (14, 8, -8, -2),
            "<div style='font-family:Helvetica;font-size:11.5pt;font-weight:bold;color:#fff;"
            "letter-spacing:1.3pt;'>LEGEND &mdash; HOW TO READ THIS SHEET</div>")
        rows = [s for s in ORDER if s != "SCOPE"]
        # The two marker entries only earn their space on a review that actually
        # has a declaration behind it.
        markers = MARKER_LEGEND if self.declared else []
        space = R.height - 34 - 46 - 20
        fixed = len(rows) * 26 + len(markers) * 28
        # The strip is 233 pt of usable height at its tightest, so the marker
        # rows have to be able to lose the argument: if they do not fit, they
        # are dropped rather than overrunning the block below them.
        if markers and space - fixed < 0:
            markers = []
            fixed = len(rows) * 26
        # The "about this margin" block gains a paragraph when a declaration is
        # in play, and it is the block that says findings may rest on user
        # input — so it outranks the tally for space. The tally is not lost when
        # it is dropped: the same counts are printed on the register's first
        # page. Nothing else in the legend is duplicated anywhere.
        about_h = 250 if self.declared else 190
        inc_about = space - fixed >= about_h
        inc_tally = space - fixed - (about_h if inc_about else 0) >= 84
        need = fixed + (84 if inc_tally else 0) + (about_h if inc_about else 0)
        slack = max(0.0, space - need)
        grow = min(slack, len(rows) * 16); rh = 26 + grow / len(rows); slack -= grow
        gap = slack / (1 + int(inc_tally) + int(inc_about))
        X0, X1 = R.x0 + 14, R.x1 - 12
        y = R.y0 + 42
        for name in rows:
            col = SEVC[name]; cy = y + 9.5
            if name == "MEASURED":
                pg.draw_line(pymupdf.Point(X0 + 2, cy), pymupdf.Point(X0 + 24, cy),
                             color=col, width=3.6, dashes="[6 4] 0")
            else:
                pg.draw_rect(pymupdf.Rect(X0 + 2, cy - 8, X0 + 24, cy + 8), color=None, fill=col)
            pg.insert_htmlbox(pymupdf.Rect(X0 + 36, y - 2, X1, y + rh + 8),
                f"<div style='font-family:Helvetica;font-size:11pt;color:#333;line-height:1.28;'>"
                f"<b style='color:{HEX[name]}'>{name}</b> &nbsp;&mdash;&nbsp; {SEV_DESC[name]}</div>")
            y += rh
        for name, desc, style in markers:
            cy = y + 9.0
            swatch = pymupdf.Rect(X0 + 2, cy - 8, X0 + 24, cy + 8)
            if style == "dashed":
                pg.draw_rect(swatch, color=VIO, width=1.8, dashes="[4 3] 0")
            else:
                pg.draw_rect(swatch, color=INK, fill=(1, 1, 1), width=1.4)
            # insert_htmlbox force-fits by scaling the text down and returns
            # the scale it used. Anything under about four fifths is too small
            # to read on a plotted sheet, so stop adding rows rather than leave
            # a key nobody can make out.
            _spare, scale = pg.insert_htmlbox(
                pymupdf.Rect(X0 + 36, y - 2, X1, y + 28),
                f"<div style='font-family:Helvetica;font-size:10.5pt;color:#333;"
                f"line-height:1.24;'><b style='color:{VIO_HEX if style == 'dashed' else '#0b1220'}'>"
                f"{name}</b> &nbsp;&mdash;&nbsp; {desc}</div>")
            if scale < 0.8:
                break
            y += 28
        y += gap
        if inc_tally:
            pg.draw_line(pymupdf.Point(X0, y), pymupdf.Point(X1, y), color=(0.84, 0.86, 0.90), width=1)
            pg.insert_htmlbox(pymupdf.Rect(X0, y + 6, X1, y + 30),
                "<div style='font-family:Helvetica;font-size:10pt;font-weight:bold;color:#5b6572;"
                "letter-spacing:1.3pt;'>THE WHOLE SET AT A GLANCE</div>")
            y += 30
            cw = (X1 - X0) / max(1, len(tally))
            for k, (name, n) in enumerate(tally):
                bx = pymupdf.Rect(X0 + k * cw + 1, y + 2, X0 + (k + 1) * cw - 3, y + 50)
                pg.draw_rect(bx, color=(0.88, 0.90, 0.93), fill=(1, 1, 1), width=1)
                pg.draw_rect(pymupdf.Rect(bx.x0, bx.y0, bx.x1, bx.y0 + 4), color=None, fill=SEVC[name])
                pg.insert_htmlbox(bx + (0, 7, 0, -2),
                    f"<div style='font-family:Helvetica;text-align:center;'>"
                    f"<div style='font-size:17pt;font-weight:bold;color:{HEX[name]};'>{n}</div>"
                    f"<div style='font-size:8.5pt;color:#5b6572;letter-spacing:.6pt;'>{name}</div></div>")
            y += 56 + gap
        if inc_about:
            pg.draw_line(pymupdf.Point(X0, y), pymupdf.Point(X1, y), color=(0.84, 0.86, 0.90), width=1)
            pg.insert_htmlbox(pymupdf.Rect(X0, y + 6, X1, y + 30),
                "<div style='font-family:Helvetica;font-size:10pt;font-weight:bold;color:#5b6572;"
                "letter-spacing:1.3pt;'>ABOUT THIS REVIEW MARGIN</div>")
            pg.insert_htmlbox(pymupdf.Rect(X0 + 2, y + 34, X1, y + 34 + about_h),
                "<div style='font-family:Helvetica;font-size:10.5pt;color:#222;line-height:1.40;'>"
                "<b>The drawing has not been changed.</b> This margin was <i>added</i> to the left of "
                "the original sheet, so nothing on the drawing is moved, resized or covered. Print to "
                "fit, or crop the margin off to recover the original sheet exactly.<br><br>"
                "<b>Nothing to switch on.</b> Every original CAD layer survives in this file untouched, "
                "and no layer toggling is needed — everything here is visible as printed.<br><br>"
                + ("<b>Some findings rest on what you told us.</b> Answers from the project "
                   "declaration were used as a second source alongside the drawings. Anything "
                   "resting on an answer rather than on the sheet is tagged as such, here and "
                   "in the register, and is never presented as something the drawing "
                   "states.<br><br>" if self.declared else "") +
                "<b>Advisory only.</b> A licensed design professional remains responsible for code "
                "compliance. This is not a plan approval.</div>")
        fy = R.y1 - 46
        pg.draw_line(pymupdf.Point(X0, fy), pymupdf.Point(X1, fy), color=(0.84, 0.86, 0.90), width=1)
        pg.insert_htmlbox(pymupdf.Rect(X0 + 2, fy + 7, X1, R.y1 - 4),
            "<div style='font-family:Helvetica;font-size:10.5pt;color:#5b6572;line-height:1.32;'>"
            "The findings register and the method note are at the <b>back of this document</b>.</div>")

    #: The legend needs this much of the rail to be worth drawing at all: the
    #: severity rows and the footer. Below it there is no legend, and a sheet
    #: with no legend cannot be read on its own.
    MIN_LEGEND = 300
    #: What the two marker rows add. Reserved up front rather than competing
    #: with the cards for leftovers: the dashed box and the hollow tag are ON
    #: the sheet, and a mark with no key is worse than one card fewer.
    MARKER_LEGEND_H = 60

    def _card_height(self, card) -> float:
        """How tall a card has to be to hold its own text.

        `insert_htmlbox` clips silently when the box is too small, so the height
        is estimated from what will actually go in it rather than from the title
        alone — a card whose finding text is cut off mid-sentence reads as a bug
        in the review, not as a layout compromise.
        """
        h = 110 if len(card.title) > 74 else 92
        h += max(0, (len(clip(card.result, 175)) - 120) // 42) * 13
        if card.basis == "declaration":
            h += 16
        if card.status == "CONFLICT" or card.scenario != "both":
            h += 14
        return h

    def _rails(self, by_page, tally, nsheets):
        for pno in range(nsheets):
            pg = self.doc[pno]
            all_cards = by_page.get(pno, [])
            R = pymupdf.Rect(self.rail[0], 40, self.rail[1], self.H - 40)

            # Fit as many cards as the rail can hold with the legend still on
            # it, most severe first — they are already in that order. What does
            # not fit is COUNTED and said, never silently dropped: a rail that
            # quietly stops at seven findings reads as a sheet with seven
            # findings.
            reserved = self.MIN_LEGEND + (self.MARKER_LEGEND_H if self.declared else 0)
            room = R.height - 122 - reserved - 16
            cards, hs, used = [], [], 0.0
            for card in all_cards:
                h = self._card_height(card)
                if used + h + 9 > room and cards:
                    break
                cards.append(card)
                hs.append(h)
                used += h + 9
            hidden = len(all_cards) - len(cards)
            head = 122 + used + (22 if hidden else 0)
            pg.draw_rect(R, color=(0.80, 0.82, 0.86), fill=(1, 1, 1), fill_opacity=0.94, width=1.4)
            hdr = pymupdf.Rect(R.x0, R.y0, R.x1, R.y0 + 112)
            pg.draw_rect(hdr, color=None, fill=INK)
            present = [c.severity for c in all_cards]
            worst = next((s for s in ORDER if s in present), None)
            nopen = sum(1 for c in all_cards if c.status in ACTIONABLE)
            badge = (f"<span style='color:#fff;background:{HEX[worst]};padding:2pt 8pt;"
                     f"font-size:12pt;font-weight:bold;'>{worst}</span>") if worst else \
                    "<span style='color:#9fb0c8;font-size:11pt;'>NOT REVIEWED IN THIS PASS</span>"
            sh = self.sheets.get(pno)
            label = f"{sh.code} {sh.title.title()}" if sh else f"Sheet {pno+1}"
            pg.insert_htmlbox(hdr + (14, 9, -12, -6),
                f"<div style='font-family:Helvetica;color:#fff;'>"
                f"<div style='font-size:10pt;color:#8fa3bd;letter-spacing:1pt;'>INDEPENDENT FBC CODE "
                f"REVIEW &nbsp;·&nbsp; SHEET {pno+1} OF {nsheets}</div>"
                f"<div style='font-size:19pt;font-weight:bold;margin-top:3pt;'>{esc(label)}</div>"
                f"<div style='margin-top:5pt;'>{badge}<span style='color:#8fa3bd;font-size:11pt;'>"
                f" &nbsp; {nopen} open &nbsp;·&nbsp; {len(all_cards)-nopen} verified on this sheet"
                f"</span></div></div>")
            y = R.y0 + 122
            for c, h in zip(cards, hs):
                r = pymupdf.Rect(R.x0 + 10, y, R.x1 - 10, y + h)
                pg.draw_rect(r, color=(0.88, 0.90, 0.93), fill=(0.985, 0.99, 1.0), width=1)
                pg.draw_rect(pymupdf.Rect(r.x0, r.y0, r.x0 + 7, r.y1), color=None,
                             fill=SEVC.get(c.severity, GRY))
                tag = ""
                if c.status == "CONFLICT":
                    tag = (f" &nbsp; <b style='color:{VIO_HEX}'>DECLARED VS DRAWN</b>")
                elif self.diverges(c) or c.scenario != "both":
                    tag = (f" &nbsp; <b style='color:{VIO_HEX}'>"
                           f"{esc(scenario_note(c))}</b>")
                # Never attribute to the drawings something the drawings do not
                # say: a card resting on the declaration states that on its face.
                note = (f"<div class='b'>{DECLARED_BASIS_NOTE}</div>"
                        if c.basis == "declaration" else "")
                pg.insert_htmlbox(r + (19, 8, -10, -5),
                    f"<div class='id'>{esc(c.fid)} &nbsp; <b style='color:"
                    f"{HEX.get(c.severity,'#555')}'>{c.severity}</b>{tag}</div>"
                    f"<div class='t'>{esc(c.title)}</div>"
                    f"<div class='s'>{esc(clip(c.result, 175))}</div>{note}",
                    css=self.CSS)
                y += h + 9
            if hidden:
                pg.insert_htmlbox(
                    pymupdf.Rect(R.x0 + 10, y, R.x1 - 10, y + 20),
                    f"<div style='font-family:Helvetica;font-size:10.5pt;color:#5b6572;'>"
                    f"&hellip; and <b>{hidden}</b> more on this sheet &mdash; all of them are "
                    f"in the register at the back.</div>")
                y += 22
            self._legend(pg, pymupdf.Rect(R.x0 + 10, R.y0 + head + 6, R.x1 - 10, R.y1 - 10), tally)

    # ── back matter ───────────────────────────────────────────────────────
    PCSS = """
body{font-family:Helvetica,Arial,sans-serif;font-size:24pt;color:#111;line-height:1.36;}
h2{font-size:31pt;margin:26pt 0 10pt 0;color:#0b1220;border-bottom:3px solid #0b1220;padding-bottom:5pt;}
p{margin:0 0 13pt 0;} ul{margin:0 0 13pt 36pt;} li{margin:0 0 7pt 0;}
table{border-collapse:collapse;width:100%;font-size:19pt;}
th{background:#0b1220;color:#fff;text-align:left;padding:8pt 11pt;font-size:19pt;}
td{border-bottom:1.5px solid #c8ccd4;padding:7pt 11pt;vertical-align:top;line-height:1.26;}
.c{color:#cc0f0f;font-weight:bold;}.h{color:#d97400;font-weight:bold;}.m{color:#a58000;font-weight:bold;}
.l{color:#1a56b0;font-weight:bold;}.p{color:#0a6b32;font-weight:bold;}.x{color:#c7008c;font-weight:bold;}
.k{color:#5b6572;font-size:17pt;text-transform:uppercase;letter-spacing:.8pt;}
.sm{font-size:18pt;color:#555;} .n{background:#fbfbfc;}
.box{background:#fdf2f2;border-left:9px solid #cc0f0f;padding:15pt 21pt;margin:0 0 16pt 0;}
.box2{background:#f4f6f9;border-left:9px solid #0b1220;padding:15pt 21pt;margin:0 0 16pt 0;}
"""
    CLS = {"CRITICAL": "c", "HIGH": "h", "MEDIUM": "m", "LOW": "l",
           "VERIFIED": "p", "MEASURED": "x", "SCOPE": "k"}

    @staticmethod
    def _fixtables(html: str) -> str:
        import re
        def repl(m):
            widths = re.findall(r"width:([\d.]+)%", m.group(1)); row = m.group(2); i = [0]
            def th(mm):
                w = widths[i[0]] if i[0] < len(widths) else None; i[0] += 1
                return f"<th width='{w}%'{mm.group(1)}>" if w else mm.group(0)
            return re.sub(r"<th([^>]*)>", th, row)
        return re.sub(r"<colgroup>(.*?)</colgroup>\s*(<tr>.*?</tr>)", repl, html, flags=re.S)

    def _page(self, big, small):
        p = self.doc.new_page(-1, width=self.W, height=self.H)
        p.draw_rect(pymupdf.Rect(0, 0, self.W, 190), color=None, fill=INK)
        p.insert_htmlbox(pymupdf.Rect(80, 26, self.W - 80, 182),
            f"<div style='color:#fff;font-family:Helvetica,Arial;'>"
            f"<div style='font-size:20pt;color:#8fa3bd;letter-spacing:2.4pt;'>"
            f"INDEPENDENT FLORIDA BUILDING CODE REVIEW</div>"
            f"<div style='font-size:46pt;font-weight:bold;margin-top:6pt;'>{esc(big)}</div>"
            f"<div style='font-size:23pt;color:#9fb0c8;margin-top:8pt;'>{esc(small)}</div></div>")
        return p

    def _put(self, p, html):
        p.insert_htmlbox(pymupdf.Rect(80, 216, self.W - 80, self.H - 46),
                         self._fixtables(html), css=self.PCSS)

    # ── the declaration, as submitted ─────────────────────────────────────
    STATE_WORDS = {
        "CORROBORATED": ("Corroborated", "p",
                         "You answered, the drawings say the same thing."),
        "CONFLICT": ("Conflicting", "c",
                     "You answered, the drawings say something else. Both readings "
                     "were evaluated."),
        "DECLARED_ONLY": ("From your answer only", "m",
                          "The drawings do not state this in text that could be read."),
        "DRAWN_ONLY": ("From the drawings only", "l",
                       "You left this blank; the drawings state it."),
        "UNKNOWN": ("Unanswered", "k",
                    "Neither source states it. Every check needing it stood down."),
    }

    def _declaration_page(self):
        """Every answer, with its state. This is the audit record of what was
        asserted, and it protects the reviewer as much as it informs the user."""
        from ..declaration_schema import BY_KEY, FIELDS, GROUPS

        d = self.declaration
        answered = d.answered_count()
        p = self._page("Project declaration as submitted",
                       f"{answered} of {len(FIELDS)} answered · "
                       f"reviewed against the drawings field by field")
        rows = ""
        i = 0
        for gkey, gname in GROUPS:
            fields = [f for f in FIELDS if f.group == gkey]
            if not fields:
                continue
            rows += (f"<tr><td colspan='4' class='k' style='background:#f4f6f9;'>"
                     f"{esc(gname)}</td></tr>")
            for fld in fields:
                rec = self.rec.fields.get(fld.key) if self.rec else None
                state = rec.state if rec else "UNKNOWN"
                word, cls, _why = self.STATE_WORDS[state]
                declared = rec.declared_value if rec and rec.declared else None
                drawn = rec.drawn_value if rec and rec.drawn else None
                nn = " class='n'" if i % 2 else ""
                i += 1
                rows += (f"<tr{nn}><td>{esc(fld.pro_label)}</td>"
                         f"<td>{esc(_fmt(declared))}</td>"
                         f"<td>{esc(_fmt(drawn))}"
                         + (f"<br><span class='sm'>{esc(rec.drawn.source)}</span>"
                            if rec and rec.drawn else "")
                         + f"</td><td class='{cls}'>{word}</td></tr>")
        legend = "".join(
            f"<li><b>{w}</b> &mdash; {why}</li>"
            for w, _c, why in self.STATE_WORDS.values())
        self._put(p, f"""
<div class='box2'><b>What this page is.</b> These are the answers given before the set was
uploaded. They were treated as a <i>second source</i> alongside the drawings, never as an
override: where the two agree the check ran at higher confidence, where they disagree the
review was run twice and both outcomes are reported, and where a field was left blank
nothing was assumed.</div>
<table><colgroup><col style='width:27%'><col style='width:22%'><col style='width:29%'>
<col style='width:22%'></colgroup>
<tr><th>Field</th><th>Your answer</th><th>The drawings</th><th>State</th></tr>{rows}</table>
<h2>What the states mean</h2><ul>{legend}</ul>""")

    def _conflicts_page(self, conflicts, divergent):
        """Declared versus drawn: both values, both sources, both outcomes."""
        p = self._page("Declared versus drawn",
                       "Where your declaration and the set disagree")
        rows = ""
        for i, f in enumerate(conflicts):
            nn = " class='n'" if i % 2 else ""
            rows += (f"<tr{nn}><td><b>{esc(f.fid)}</b></td>"
                     f"<td class='{self.CLS.get(f.severity,'k')}'>{f.severity}</td>"
                     f"<td><b>{esc(f.title)}</b><br>{esc(f.result)}</td>"
                     f"<td>{esc(f.code)}</td><td>{esc(f.action)}</td></tr>")
        table = (("<table><colgroup><col style='width:8%'><col style='width:10%'>"
                  "<col style='width:52%'><col style='width:14%'><col style='width:16%'></colgroup>"
                  "<tr><th>ID</th><th>Severity</th><th>The disagreement</th><th>Code</th>"
                  f"<th>What to do</th></tr>{rows}</table>") if conflicts else
                 "<p>No field in the declaration contradicts the drawings.</p>")

        div_rows = ""
        for i, f in enumerate(divergent):
            nn = " class='n'" if i % 2 else ""
            div_rows += (f"<tr{nn}><td><b>{esc(f.fid)}</b></td><td>{esc(f.sheet)}</td>"
                         f"<td><b>{esc(f.title)}</b><br>{esc(clip(f.result, 260))}</td>"
                         f"<td class='x'>{esc(scenario_note(f))}</td>"
                         f"</tr>")
        divergence = ("<h2>Checks that came out differently under each reading</h2>"
                      "<table><colgroup><col style='width:8%'><col style='width:9%'>"
                      "<col style='width:57%'><col style='width:26%'></colgroup>"
                      "<tr><th>ID</th><th>Sheet</th><th>Finding</th><th>Which reading</th></tr>"
                      f"{div_rows}</table>") if divergent else (
                      "<h2>Checks that came out differently under each reading</h2>"
                      "<p>None. Every other check landed the same way whichever reading was "
                      "used, which is the common case &mdash; most rules never touch a field "
                      "the two sources disagree about.</p>")

        self._put(p, f"""
<div class='box2'><b>How this was evaluated.</b> Where the declaration and the drawings
disagree, the whole rule set was run <b>twice</b>: once against the set exactly as drawn, once
against the set as you described it. Exactly two readings, never a combination of them &mdash;
a permit set is submitted as a whole, so the two things worth comparing are the whole set as
drawn and the whole set as described.<br><br>
<b>The drawings govern the sheet markup.</b> On-sheet markers show the as-drawn outcome,
because the permit is issued against what was submitted and the authority having jurisdiction
reviews the sheet. A dashed marker means the two readings disagree; the detail is here.</div>
{table}
{divergence}""")

    def _register(self, opens, passes, tally):
        o = self.opt
        d = self.declaration
        group = getattr(d, "occupancy_group", None)
        sprinkler = getattr(d, "sprinkler_system", None)
        subtitle = f"{o.project_name or 'Permit set'} · {o.edition_label(d)}"
        if group:
            subtitle += f" · Group {group}"
        if sprinkler:
            subtitle += f", {'sprinklered' if d.sprinklered else 'non-sprinklered'}"
        p = self._page("Findings register", subtitle)
        crit = [f for f in opens if f.severity == "CRITICAL"]
        lead = (f"<div class='box'><b>{crit[0].title}.</b> {esc(crit[0].result)}</div>"
                if crit else
                "<div class='box2'><b>No critical findings.</b> Everything below is resolvable "
                "on paper.</div>")
        counts = "".join(
            f"<tr><td class='{self.CLS[n]}'>{n}</td><td><b>{c}</b></td>"
            f"<td>{SEV_DESC[n]}</td></tr>" for n, c in tally)
        self._put(p, lead + f"""
<table><colgroup><col style='width:14%'><col style='width:9%'><col style='width:77%'></colgroup>
<tr><th>Severity</th><th>Count</th><th>What it means</th></tr>{counts}</table>
<h2>Review parameters</h2>
<table><colgroup><col style='width:28%'><col style='width:72%'></colgroup>
<tr><th>Setting</th><th>Value</th></tr>
<tr><td>Code edition</td><td>{esc(o.edition_label(d))}</td></tr>
<tr class='n'><td>Occupancy group</td><td>{esc(group or 'not declared — read from the drawings where stated')}</td></tr>
<tr><td>Sprinkler system</td><td>{esc(_sprinkler_words(d))}</td></tr>
<tr class='n'><td>Project declaration</td><td>{
    f"{d.answered_count()} of {_field_count()} fields answered" if self.declared
    else 'Not submitted — every fact was read off the drawings'}</td></tr>
<tr><td>Reporting floor</td><td>{esc(o.min_severity)} and above</td></tr>
<tr class='n'><td>Verified items shown</td><td>{'Yes' if o.include_verified else 'No'}</td></tr>
<tr><td>Measured geometry</td><td>{'Yes' if o.include_measured else 'No'}</td></tr>
</table>
<p class='sm'>Generated {datetime.date.today().isoformat()} · Advisory only. A licensed design
professional remains responsible for code compliance; this is not a plan approval and does not
replace review by the authority having jurisdiction.</p>""")

        if self.declared:
            self._declaration_page()
            conflicts = [f for f in self.findings if f.status == "CONFLICT"]
            divergent = [f for f in self.findings if f.scenario != "both"]
            if conflicts or divergent:
                self._conflicts_page(conflicts, divergent)

        def open_tbl(rows):
            h = ("<table><colgroup><col style='width:6%'><col style='width:7%'><col style='width:9%'>"
                 "<col style='width:46%'><col style='width:14%'><col style='width:18%'></colgroup>"
                 "<tr><th>ID</th><th>Sheet</th><th>Severity</th>"
                 "<th>What was checked, and what was found</th><th>Code</th><th>What to do</th></tr>")
            for i, f in enumerate(rows):
                nn = " class='n'" if i % 2 else ""
                extra = ""
                if f.status == "CONFLICT":
                    extra = ("<br><span class='k'>Basis</span> Declared versus drawn — "
                             "your declaration and the set disagree.")
                elif f.scenario != "both":
                    extra = (f"<br><span class='k'>Basis</span> {esc(scenario_note(f))}")
                elif f.basis == "declaration":
                    extra = f"<br><span class='k'>Basis</span> {DECLARED_BASIS_NOTE}"
                h += (f"<tr{nn}><td><b>{esc(f.fid)}</b></td><td>{esc(f.sheet)}</td>"
                      f"<td class='{self.CLS.get(f.severity,'k')}'>{f.severity}</td>"
                      f"<td><b>{esc(f.title)}</b><br><span class='k'>Checked</span> {esc(f.checked)}"
                      f"<br><span class='k'>Found</span> {esc(f.result)}{extra}</td>"
                      f"<td>{esc(f.code)}</td><td>{esc(f.action)}</td></tr>")
            return h + "</table>"

        for i, chunk in enumerate([opens[j:j+7] for j in range(0, len(opens), 7)] or [[]]):
            if not chunk:
                break
            p = self._page(f"Open findings — {i+1}", "Sorted by severity")
            self._put(p, open_tbl(chunk))

        def pass_tbl(rows):
            h = ("<table><colgroup><col style='width:6%'><col style='width:6%'><col style='width:14%'>"
                 "<col style='width:56%'><col style='width:18%'></colgroup>"
                 "<tr><th>ID</th><th>Sheet</th><th>Discipline</th>"
                 "<th>What was checked, and why it is sufficient</th><th>Code</th></tr>")
            for i, f in enumerate(rows):
                nn = " class='n'" if i % 2 else ""
                note = (f"<br><span class='k'>Basis</span> {DECLARED_BASIS_NOTE}"
                        if f.basis == "declaration" else "")
                h += (f"<tr{nn}><td><b>{esc(f.fid)}</b></td><td>{esc(f.sheet)}</td>"
                      f"<td>{esc(f.discipline)}</td>"
                      f"<td><b>{esc(f.title)}</b><br>{esc(f.result)}{note}</td>"
                      f"<td>{esc(f.code)}</td></tr>")
            return h + "</table>"

        for i, chunk in enumerate([passes[j:j+13] for j in range(0, len(passes), 13)]):
            p = self._page(f"Verified register — {i+1}",
                           "Checked against the code text and found sufficient")
            intro = ("<div class='box2'><b>Why this register exists.</b> The value of a review is not "
                     "only what it catches — it is knowing what was looked at. Every item below was "
                     "checked against the governing code section and carries a green marker on its "
                     "sheet.</div>") if i == 0 else ""
            self._put(p, intro + pass_tbl(chunk))

        if self.abstentions:
            p = self._page("Not checked", "Rules that declined to run, and why")
            rows = ""
            for i, a in enumerate(self.abstentions):
                nn = " class='n'" if i % 2 else ""
                rows += (f"<tr{nn}><td><b>{esc(a.rule_id)}</b></td>"
                         f"<td>{esc(a.reason)}</td><td>{esc(a.detail)}</td></tr>")
            self._put(p, f"""
<div class='box2'><b>&ldquo;Not checked&rdquo; must never look like &ldquo;checked and
passed.&rdquo;</b> Each rule below could not obtain an input it needed and declined to run rather
than guess. They are listed so coverage can be judged honestly.</div>
<table><colgroup><col style='width:24%'><col style='width:40%'><col style='width:36%'></colgroup>
<tr><th>Rule</th><th>Why it abstained</th><th>Detail</th></tr>{rows}</table>""")

    # ── entry point ───────────────────────────────────────────────────────
    def build(self, out_path: str) -> dict:
        nsheets = self.doc.page_count
        placed = []
        for f in self.findings:
            b = self._box(f)
            if b:
                self._boxes.setdefault(f.page, []).append(b)
            placed.append((f, b))
        for f, b in placed:
            if b:
                self._chip(f, b)
        by_page: Dict[int, List[Finding]] = {}
        for f in self.findings:
            by_page.setdefault(f.page, []).append(f)
        for v in by_page.values():
            v.sort(key=lambda f: ORDER.index(f.severity) if f.severity in ORDER else 9)
        tally = [(s, sum(1 for f in self.findings if f.severity == s))
                 for s in ORDER if s != "SCOPE"]
        self._rails(by_page, tally, nsheets)
        opens = [f for f in self.findings if f.status in ACTIONABLE]
        passes = [f for f in self.findings if f.status not in ACTIONABLE]
        self._register(opens, passes, tally)
        self.doc.set_metadata({
            "title": f"{self.opt.project_name or 'Permit set'} — Independent FBC Code Review",
            "author": self.author, "subject": self.opt.edition_label(self.declaration)})
        self.doc.save(out_path, garbage=4, deflate=True, deflate_images=True,
                      deflate_fonts=True, clean=True, use_objstms=1)
        info = {"pages": self.doc.page_count, "sheets": nsheets,
                "annots": sum(len(list(p.annots())) for p in self.doc),
                "marked": sum(1 for _f, b in placed if b),
                "unplaced": [f.fid for f, b in placed if not b]}
        self.doc.close()
        return info


def render(src: str, out: str, findings, sheets, options, abstentions=None,
           reconciled=None) -> dict:
    """`reconciled` is the ReconciledFacts the rules ran against. Without it the
    output is exactly what it was before the declaration existed."""
    return Renderer(src, findings, sheets, options, abstentions, reconciled).build(out)
