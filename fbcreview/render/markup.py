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
MAG = (0.78, 0.05, 0.55); INK = (0.05, 0.07, 0.11)
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


def esc(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def clip(s: str, n: int) -> str:
    """Truncate at a word boundary — a card that ends mid-word reads as a bug."""
    s = (s or "").strip()
    if len(s) <= n:
        return s
    cut = s[:n].rsplit(" ", 1)[0].rstrip(" .,;:")
    return cut + "\u2026"


class Renderer:
    def __init__(self, src: str, findings: Sequence[Finding], sheets, options,
                 abstentions=None):
        self.doc = pymupdf.open(src)
        self.findings = list(findings)
        self.sheets = {s.index: s for s in sheets}
        self.opt = options
        self.abstentions = list(abstentions or [])
        self.author = f"Independent FBC Review ({options.edition_label()}) — advisory"
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
    def _box(self, f: Finding) -> Optional[pymupdf.Rect]:
        pg = self.doc[f.page]
        hits = pg.search_for(f.anchor) if f.anchor else []
        if len(hits) <= f.hit:
            return None
        r = hits[f.hit]
        b = pymupdf.Rect(r.x0 - 3, r.y0 - 3, r.x1 + 3, r.y1 + 3)
        c = SEVC.get(f.severity, GRY)
        a = pg.add_rect_annot(b); a.set_colors(stroke=c); a.set_border(width=2.2)
        a.set_info(title=self.author, subject=f"{f.fid} · {f.severity}",
                   content=f"{f.fid} [{f.severity}] {f.title}"); a.update()
        body = (f"{f.fid} — {f.severity}\n{f.title}\n\n"
                f"CHECKED: {f.checked}\n\nFOUND: {f.result}\n\n"
                f"CODE: {f.code}\n\nACTION: {f.action}")
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
            pg.draw_rect(chip, color=None, fill=c)
            pg.insert_textbox(chip + (4, 1.4, 0, 0), f.fid, fontsize=9.5,
                              fontname="hebo", color=(1, 1, 1))
            taken.append(chip); return

    # ── per-sheet rail ────────────────────────────────────────────────────
    CSS = ("body{font-family:Helvetica,Arial,sans-serif;color:#111;}"
           ".t{font-size:13.5pt;font-weight:bold;color:#0b1220;line-height:1.22;}"
           ".s{font-size:11.5pt;color:#333;line-height:1.35;}"
           ".id{font-size:10.5pt;color:#666;font-family:Courier;letter-spacing:.5pt;}")

    def _legend(self, pg, R, tally):
        pg.draw_rect(R, color=(0.84, 0.86, 0.90), fill=(0.985, 0.99, 1.0), width=1.3)
        t = pymupdf.Rect(R.x0, R.y0, R.x1, R.y0 + 34)
        pg.draw_rect(t, color=None, fill=(0.09, 0.13, 0.22))
        pg.insert_htmlbox(t + (14, 8, -8, -2),
            "<div style='font-family:Helvetica;font-size:11.5pt;font-weight:bold;color:#fff;"
            "letter-spacing:1.3pt;'>LEGEND &mdash; HOW TO READ THIS SHEET</div>")
        rows = [s for s in ORDER if s != "SCOPE"]
        space = R.height - 34 - 46 - 20
        inc_tally = space - len(rows) * 26 >= 84
        inc_about = space - len(rows) * 26 - (84 if inc_tally else 0) >= 190
        need = len(rows) * 26 + (84 if inc_tally else 0) + (190 if inc_about else 0)
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
            pg.insert_htmlbox(pymupdf.Rect(X0 + 2, y + 34, X1, y + 34 + 190),
                "<div style='font-family:Helvetica;font-size:10.5pt;color:#222;line-height:1.40;'>"
                "<b>The drawing has not been changed.</b> This margin was <i>added</i> to the left of "
                "the original sheet, so nothing on the drawing is moved, resized or covered. Print to "
                "fit, or crop the margin off to recover the original sheet exactly.<br><br>"
                "<b>Nothing to switch on.</b> Every original CAD layer survives in this file untouched, "
                "and no layer toggling is needed — everything here is visible as printed.<br><br>"
                "<b>Advisory only.</b> A licensed design professional remains responsible for code "
                "compliance. This is not a plan approval.</div>")
        fy = R.y1 - 46
        pg.draw_line(pymupdf.Point(X0, fy), pymupdf.Point(X1, fy), color=(0.84, 0.86, 0.90), width=1)
        pg.insert_htmlbox(pymupdf.Rect(X0 + 2, fy + 7, X1, R.y1 - 4),
            "<div style='font-family:Helvetica;font-size:10.5pt;color:#5b6572;line-height:1.32;'>"
            "The findings register and the method note are at the <b>back of this document</b>.</div>")

    def _rails(self, by_page, tally, nsheets):
        for pno in range(nsheets):
            pg = self.doc[pno]
            cards = by_page.get(pno, [])
            hs = [110 if len(c.title) > 74 else 92 for c in cards]
            head = 122 + sum(h + 9 for h in hs)
            R = pymupdf.Rect(self.rail[0], 40, self.rail[1], self.H - 40)
            pg.draw_rect(R, color=(0.80, 0.82, 0.86), fill=(1, 1, 1), fill_opacity=0.94, width=1.4)
            hdr = pymupdf.Rect(R.x0, R.y0, R.x1, R.y0 + 112)
            pg.draw_rect(hdr, color=None, fill=INK)
            present = [c.severity for c in cards]
            worst = next((s for s in ORDER if s in present), None)
            nopen = sum(1 for c in cards if c.status == "OPEN")
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
                f" &nbsp; {nopen} open &nbsp;·&nbsp; {len(cards)-nopen} verified on this sheet"
                f"</span></div></div>")
            y = R.y0 + 122
            for c, h in zip(cards, hs):
                r = pymupdf.Rect(R.x0 + 10, y, R.x1 - 10, y + h)
                pg.draw_rect(r, color=(0.88, 0.90, 0.93), fill=(0.985, 0.99, 1.0), width=1)
                pg.draw_rect(pymupdf.Rect(r.x0, r.y0, r.x0 + 7, r.y1), color=None,
                             fill=SEVC.get(c.severity, GRY))
                pg.insert_htmlbox(r + (19, 8, -10, -5),
                    f"<div class='id'>{esc(c.fid)} &nbsp; <b style='color:"
                    f"{HEX.get(c.severity,'#555')}'>{c.severity}</b></div>"
                    f"<div class='t'>{esc(c.title)}</div><div class='s'>{esc(clip(c.result, 175))}</div>",
                    css=self.CSS)
                y += h + 9
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

    def _register(self, opens, passes, tally):
        o = self.opt
        p = self._page("Findings register",
                       f"{o.project_name or 'Permit set'} · {o.edition_label()} · "
                       f"Group {o.occupancy_group}, "
                       f"{'sprinklered' if o.sprinklered else 'non-sprinklered'}")
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
<tr><td>Code edition</td><td>{esc(o.edition_label())}</td></tr>
<tr class='n'><td>Occupancy group</td><td>{esc(o.occupancy_group)}</td></tr>
<tr><td>Sprinkler system</td><td>{'Yes — 903.3.1.1 / 903.3.1.2' if o.sprinklered else 'No'}</td></tr>
<tr class='n'><td>Reporting floor</td><td>{esc(o.min_severity)} and above</td></tr>
<tr><td>Verified items shown</td><td>{'Yes' if o.include_verified else 'No'}</td></tr>
<tr class='n'><td>Measured geometry</td><td>{'Yes' if o.include_measured else 'No'}</td></tr>
</table>
<p class='sm'>Generated {datetime.date.today().isoformat()} · Advisory only. A licensed design
professional remains responsible for code compliance; this is not a plan approval and does not
replace review by the authority having jurisdiction.</p>""")

        def open_tbl(rows):
            h = ("<table><colgroup><col style='width:6%'><col style='width:7%'><col style='width:9%'>"
                 "<col style='width:46%'><col style='width:14%'><col style='width:18%'></colgroup>"
                 "<tr><th>ID</th><th>Sheet</th><th>Severity</th>"
                 "<th>What was checked, and what was found</th><th>Code</th><th>What to do</th></tr>")
            for i, f in enumerate(rows):
                nn = " class='n'" if i % 2 else ""
                h += (f"<tr{nn}><td><b>{esc(f.fid)}</b></td><td>{esc(f.sheet)}</td>"
                      f"<td class='{self.CLS.get(f.severity,'k')}'>{f.severity}</td>"
                      f"<td><b>{esc(f.title)}</b><br><span class='k'>Checked</span> {esc(f.checked)}"
                      f"<br><span class='k'>Found</span> {esc(f.result)}</td>"
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
                h += (f"<tr{nn}><td><b>{esc(f.fid)}</b></td><td>{esc(f.sheet)}</td>"
                      f"<td>{esc(f.discipline)}</td>"
                      f"<td><b>{esc(f.title)}</b><br>{esc(f.result)}</td><td>{esc(f.code)}</td></tr>")
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
        opens = [f for f in self.findings if f.status == "OPEN"]
        passes = [f for f in self.findings if f.status != "OPEN"]
        self._register(opens, passes, tally)
        self.doc.set_metadata({
            "title": f"{self.opt.project_name or 'Permit set'} — Independent FBC Code Review",
            "author": self.author, "subject": self.opt.edition_label()})
        self.doc.save(out_path, garbage=4, deflate=True, deflate_images=True,
                      deflate_fonts=True, clean=True, use_objstms=1)
        info = {"pages": self.doc.page_count, "sheets": nsheets,
                "annots": sum(len(list(p.annots())) for p in self.doc),
                "marked": sum(1 for _f, b in placed if b),
                "unplaced": [f.fid for f, b in placed if not b]}
        self.doc.close()
        return info


def render(src: str, out: str, findings, sheets, options, abstentions=None) -> dict:
    return Renderer(src, findings, sheets, options, abstentions).build(out)
