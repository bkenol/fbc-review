import pymupdf, datetime, sys, re
sys.path.insert(0,'/tmp')
from v5_data import R, LINE

SRC="/root/.claude/uploads/3bdb61da-1669-59a7-8b71-a59af3520261/cf5878ce-SCULPTED_HOT_PILATESPERMIT_SET_8.18.2026.pdf"
OUT="/home/claude/out/SCULPTED HOT PILATES - CODE REVIEW v5 - MARKUP.pdf"
doc=pymupdf.open(SRC)
PT=18.005
G=702
for _p in doc:
    _p.set_mediabox(pymupdf.Rect(-G, 0, 2592, 1728))
SW, SH_ = 2592+G, 1728

RED=(0.80,0.06,0.06); ORG=(0.93,0.45,0.00); AMB=(0.85,0.62,0.00)
GRN=(0.00,0.55,0.24); BLU=(0.10,0.35,0.80); GRY=(0.42,0.45,0.50); MAG=(0.78,0.05,0.55)
INK=(0.05,0.07,0.11)
AUTH="Independent FBC Review v5 (advisory)"
BY0={e["fid"]:e for e in R}
OPEN_ROWS=[("C-01",["C-01","C-01b","C-01c"]), ("H-01",["H-01","H-01r"]), ("H-02",["H-02"]),
 ("H-03",["H-03"]), ("M-01",["M-01"]), ("M-02",["M-02"]), ("M-03",["M-03"]),
 ("M-04",["M-04"]), ("M-05",["M-05"]), ("M-06",["M-06"]), ("M-07",["M-07"]),
 ("M-08",["M-08"]), ("L-01",["L-01"]), ("L-02",["L-02"])]
SEVC={"CRITICAL":RED,"HIGH":ORG,"MEDIUM":AMB,"LOW":BLU,"VERIFIED":GRN,"SCOPE":GRY,"MEASURED":MAG}
HEX={k:'#'+''.join(f'{int(v*255):02x}' for v in c) for k,c in SEVC.items()}
RAIL_X0, RAIL_X1, RAIL_Y0 = 30, 672, 40

SHEETS=["G-0 Cover","G-1 Life Safety","G-2 ADA Details","G-3 ADA Details","A-1 Existing Floor Plan",
"A-2 Proposed Floor Plan","A-3 Reflected Ceiling Plan","A-4 Finish Plan","A-5 Equipment Plan",
"A-6 Enlarged Restroom Plan","A-7 Interior Elevations","A-8 Interior Elevations","A-9 Millwork Details",
"A-10 Partition Types & Details","A-11 UL Details","A-12 UL Details","M-1 Mechanical Plan",
"M-2 Mechanical Details","E-1 Electrical Power Plan","E-2 Electrical Lighting Plan",
"E-3 Electrical Panel Schedule","P-1 Sanitary Plan","P-2 Water Plan","P-3 Riser Diagrams & Details"]
CODE=[s.split()[0] for s in SHEETS]

# what was NOT reviewed on each sheet (additive — every sheet now also carries verified markup)
SCOPE_NOTE={
 0:"Structural, energy and fire-suppression data on this sheet were not reviewed.",
 2:"Detail geometry was checked against the code text; field construction tolerance was not.",
 4:"Existing conditions were taken as drawn. No field verification was performed.",
 6:"Structural capacity of the bar joists for the suspended infrared panels and light fixtures.",
 7:"Flame-spread classification is a finding (M-06), not a scope gap. Slip resistance of FF-1 and T-1 was not verified.",
 8:"Anchorage and blocking design for wall-mounted equipment.",
 10:"Millwork structural design and the reception counter dimension (M-07).",
 11:"Structural support of the suspended infrared panels, speakers and signage.",
 12:"Millwork fabrication engineering. The accessible counter dimension is a finding — M-07.",
 13:"Acoustic performance, and fire-resistant joint system parameters at the head of wall.",
 15:"<b>Raster insert</b> — 42 images, ~206 MPx, 68 words of live text. Firestop system parameters are not machine-readable.",
 16:"Duct sizing, refrigerant piping and energy-code compliance.",
 17:"Duct insulation R-values and duct leakage testing.",
 18:"Conductor sizing, voltage drop and energy-code compliance path.",
 19:"Lighting power density and emergency illumination levels at the floor.",
 20:"Short-circuit and coordination study; demand-factor calculations.",
 21:"Developed length, slope, and capacity of the existing building sewer.",
 22:"Water pressure, developed length, and fixture-unit hydraulic sizing.",
 23:"Developed length and slope of vent piping.",
}

def esc(s): return s.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")

def box_text(pno, needle, hit, fid, sev, title, body, pad=3, dx=-34):
    pg=doc[pno]; hits=pg.search_for(needle)
    if len(hits)<=hit: print(f"   !! MISS {fid} {needle!r}#{hit}"); return None
    r=hits[hit]; b=pymupdf.Rect(r.x0-pad,r.y0-pad,r.x1+pad,r.y1+pad)
    c=SEVC[sev]
    a=pg.add_rect_annot(b); a.set_colors(stroke=c); a.set_border(width=2.2); a.set_opacity(0.95)
    a.set_info(title=AUTH, subject=f"{fid} · {sev}", content=f"{fid} [{sev}] {title}"); a.update()
    t=pg.add_text_annot(pymupdf.Point(b.x0+dx, b.y0-4), f"{fid} — {sev}\n{title}\n\n{body}", icon="Comment")
    t.set_colors(stroke=c); t.set_info(title=AUTH, subject=f"{fid} · {sev} · {title}"); t.update()
    return b

def tag(pno, rect, label, sev):
    pg=doc[pno]; c=SEVC[sev]
    w=13+7.2*len(label); h=21
    chip=pymupdf.Rect(rect.x0-1, rect.y0-h-1, rect.x0-1+w, rect.y0-1)
    pg.draw_rect(chip, color=None, fill=c, fill_opacity=1)
    pg.insert_textbox(chip+(4,4.5,0,0), label, fontsize=12, fontname="hebo", color=(1,1,1), align=0)

def leader(pno, x0,y0, x1,y1, sev, dash="[5 4] 0"):
    doc[pno].draw_line(pymupdf.Point(x0,y0), pymupdf.Point(x1,y1), color=SEVC[sev], width=1.5, dashes=dash)

def measured(pno, pts, label, sub, lx, ly, lw=250, off=(11,0)):
    pg=doc[pno]
    P2=[pymupdf.Point(p[0]+off[0], p[1]+off[1]) for p in pts]
    pg.draw_polyline(P2, color=(1,1,1), width=8.0, stroke_opacity=0.85)
    pg.draw_polyline(P2, color=MAG, width=3.4, dashes="[10 6] 0", stroke_opacity=1)
    q=[(p.x,p.y) for p in P2]
    for p in (q[0], q[-1]):
        pg.draw_circle(pymupdf.Point(*p), 7, color=MAG, fill=(1,1,1), width=2.6)
    fl=pymupdf.Rect(lx, ly, lx+lw, ly+54)
    pg.draw_rect(fl, color=MAG, fill=(1,1,1), fill_opacity=0.94, width=1.8)
    pg.insert_htmlbox(fl+(7,4,-6,-3),
        f"<div style='font-family:Helvetica;font-size:14pt;color:#c7008c;font-weight:bold;'>{esc(label)}</div>"
        f"<div style='font-family:Helvetica;font-size:11.5pt;color:#333;'>{esc(sub)}</div>")

# ══════════════════════ 1. on-drawing markup from the register ══════════════════════
print("── on-drawing markup ──")
FIND={}
for e in R:
    sev=e["sev"]; fid=e["fid"]
    b=box_text(e["pg"], e["anchor"], e.get("hit",0), fid, sev, e["title"], e["body"])
    if b: tag(e["pg"], b, fid, sev)
    FIND.setdefault(e["pg"],[]).append((fid,sev,e["title"],LINE[fid],b))

# ── G-1 traced geometry (the measured overlay) ──
pg=doc[1]
measured(1,[(1880.5,188.0),(1880.5,1428.1)],
 "TRAVEL PATH 1 — MEASURED 68.9 ft",
 "stated 69'-4\" (69.33 ft) · Δ ≈ 5 in (0.6%) · limit 250 ft", 1392, 900, 268)
leader(1,1660,922,1874,922,"MEASURED","[4 4] 0")
measured(1,[(1708.8,476.9),(1880.5,476.9),(1880.5,188.0)],
 "TRAVEL PATH 2 — MEASURED 25.6 ft",
 "stated 24'-7\" (24.58 ft) · Δ ≈ 12 in · limit 250 ft", 1392, 250, 268)
leader(1,1660,272,1802,430,"MEASURED","[4 4] 0")
measured(1,[(1708.8,500.0),(1880.5,500.0)],
 "COMMON PATH — MEASURED 9.5 ft",
 "stated 8'-1\" (8.08 ft) · Δ ≈ 17 in · limit 75 ft (Table 1006.2.1)", 1392, 560, 288)
leader(1,1680,582,1762,510,"MEASURED","[4 4] 0")
pg.draw_line(pymupdf.Point(1847.5,1180),pymupdf.Point(1913.5,1180),color=MAG,width=2.8)
for x in (1847.5,1913.5):
    pg.draw_line(pymupdf.Point(x,1170),pymupdf.Point(x,1190),color=MAG,width=2.4)
fl=pymupdf.Rect(1392,1150,1680,1196)
pg.draw_rect(fl,color=MAG,fill=(1,1,1),fill_opacity=0.94,width=1.8)
pg.insert_htmlbox(fl+(7,4,-6,-3),
 "<div style='font-family:Helvetica;font-size:12.5pt;color:#c7008c;font-weight:bold;'>EGRESS BAND — MEASURED 3'-8&Prime;</div>"
 "<div style='font-family:Helvetica;font-size:10.5pt;color:#333;'>stated 3'-8&Prime; · exact match · scale from PDF /Measure, 18.005 pt/ft</div>")
leader(1,1680,1173,1843,1180,"MEASURED","[4 4] 0")

# ── M-1 headline callout ──
bs={f:b for f,s,t,l,b in FIND.get(16,[])}
b1,b2=bs.get("C-01"),bs.get("C-01b")
if b1 and b2:
    leader(16,2368,1424,b1.x0+30,b1.y1+4,"CRITICAL","[7 5] 0")
    leader(16,2368,1460,b2.x0+30,b2.y0-24,"CRITICAL","[7 5] 0")
    mid=pymupdf.Rect(1938, 1408, 1938+430, 1408+70)
    doc[16].draw_rect(mid,color=RED,fill=(1,1,1),fill_opacity=0.96,width=2.2)
    doc[16].insert_htmlbox(mid+(9,5,-7,-4),
      "<div style='font-family:Helvetica;font-size:19pt;color:#cc0f0f;font-weight:bold;'>881 CFM required &nbsp;vs&nbsp; 350 CFM scheduled</div>"
      "<div style='font-family:Helvetica;font-size:12.5pt;color:#333;'>531 CFM short — the unit delivers 40% of the outdoor air this sheet calculates</div>"
      "<div style='font-family:Helvetica;font-size:11pt;color:#666;'>FBC-M 403.3.1.1 · 403.3.1.1.1.1 · 405.1</div>")

# ══════════════════════ 2. per-sheet review rail ══════════════════════
TALLY=[("CRITICAL",sum(1 for f,_ in OPEN_ROWS if BY0[f]["sev"]=="CRITICAL")),
       ("HIGH",    sum(1 for f,_ in OPEN_ROWS if BY0[f]["sev"]=="HIGH")),
       ("MEDIUM",  sum(1 for f,_ in OPEN_ROWS if BY0[f]["sev"]=="MEDIUM")),
       ("LOW",     sum(1 for f,_ in OPEN_ROWS if BY0[f]["sev"]=="LOW")),
       ("VERIFIED",sum(1 for e in R if e.get("status")=="PASS")),
       ("MEASURED",4)]
print("── review rail ──")
CSS=("body{font-family:Helvetica,Arial,sans-serif;color:#111;}"
     ".t{font-size:13.5pt;font-weight:bold;color:#0b1220;line-height:1.22;}"
     ".s{font-size:11.5pt;color:#333;line-height:1.35;}"
     ".id{font-size:10.5pt;color:#666;font-family:Courier;letter-spacing:0.5pt;}")

SEV_ROWS=[("CRITICAL",RED,"Quantified deficiency in the design as drawn."),
          ("HIGH",ORG,"Stated code value wrong, or a governing assumption unresolved."),
          ("MEDIUM",AMB,"Inconsistent or unsupported — confirm before submittal."),
          ("LOW",BLU,"Editorial. Will not hold up a permit."),
          ("VERIFIED",GRN,"Checked against the code text and found sufficient."),
          ("MEASURED",MAG,"Traced off this file's own vector geometry."),
          ("SCOPE",GRY,"Not reviewed in this pass. Named per sheet.")]
SEVLONG={
 "CRITICAL":"A quantified deficiency in the design as drawn. Cannot be resolved by a note.",
 "HIGH":"A stated code value is wrong, or a governing assumption is unresolved between sheets.",
 "MEDIUM":"Internally inconsistent or undocumented — a reviewer cannot confirm it from the sheet.",
 "LOW":"Editorial. Worth fixing on the next plot; will not hold up a permit.",
 "VERIFIED":"Checked against the code text and found sufficient. Shown so you can see what was examined, not only what failed.",
 "MEASURED":"Traced off this file's own vector geometry and compared against the printed dimension.",
 "SCOPE":"Not reviewed in this pass. What was skipped is named on each sheet."}

LEG_MIN = 34 + 7*26 + 46 + 20          # title + tightest severity key + footer + padding

def _sect(pg, x0, x1, y, label):
    pg.draw_line(pymupdf.Point(x0, y), pymupdf.Point(x1, y), color=(0.84,0.86,0.90), width=1.0)
    pg.insert_htmlbox(pymupdf.Rect(x0, y+6, x1, y+30),
      f"<div style='font-family:Helvetica;font-size:10pt;font-weight:bold;color:#5b6572;"
      f"letter-spacing:1.3pt;'>{label}</div>")
    return y+30

def draw_legend(pg, Rr, tally):
    """Fills whatever vertical space is left in the rail. Sections are added by priority
    while they fit, then leftover slack is absorbed by the severity key and the notes block."""
    pg.draw_rect(Rr, color=(0.84,0.86,0.90), fill=(0.985,0.99,1.0), width=1.3)
    X0, X1 = Rr.x0+14, Rr.x1-12
    # ── title bar
    t=pymupdf.Rect(Rr.x0,Rr.y0,Rr.x1,Rr.y0+34)
    pg.draw_rect(t, color=None, fill=(0.09,0.13,0.22))
    pg.insert_htmlbox(t+(14,8,-8,-2),
      "<div style='font-family:Helvetica;font-size:11.5pt;font-weight:bold;color:#ffffff;"
      "letter-spacing:1.3pt;'>LEGEND &nbsp;&mdash;&nbsp; HOW TO READ THIS SHEET</div>")

    space = Rr.height - 34 - 46 - 20        # minus title, footer, padding
    ANAT_H, GLANCE_H, ABOUT_H = 250, 84, 208
    need = 7*26
    inc_about  = space - need >= ABOUT_H;   need += ABOUT_H  if inc_about  else 0
    inc_anat   = space - need >= ANAT_H;    need += ANAT_H   if inc_anat   else 0
    inc_glance = space - need >= GLANCE_H;  need += GLANCE_H if inc_glance else 0
    slack = max(0.0, space - need)
    grow  = min(slack, 7*(42-26)); rh = 26 + grow/7.0; slack -= grow
    # whatever is still left is shared out as breathing room between sections,
    # so the panel reads as designed rather than as content stranded at the top
    gaps = 1 + int(inc_anat) + int(inc_glance) + int(inc_about)
    gap  = slack/gaps
    about_h = ABOUT_H

    # ── severity key
    y=Rr.y0+42
    for name,col,_ in SEV_ROWS:
        cy=y+rh/2.0
        if name=="MEASURED":
            pg.draw_line(pymupdf.Point(X0+2,cy), pymupdf.Point(X0+24,cy), color=col, width=3.6, dashes="[6 4] 0")
        else:
            pg.draw_rect(pymupdf.Rect(X0+2,cy-8,X0+24,cy+8), color=None, fill=col)
        pg.insert_htmlbox(pymupdf.Rect(X0+36, y-2, X1, y+rh+8),
          f"<div style='font-family:Helvetica;font-size:11pt;color:#333;line-height:1.28;'>"
          f"<b style='color:{HEX[name]}'>{name}</b> &nbsp;&mdash;&nbsp; {SEVLONG[name]}</div>")
        y+=rh
    y+=gap

    # ── markup anatomy
    if inc_anat:
        y=_sect(pg, X0, X1, y+4, "WHAT THE MARKUP LOOKS LIKE ON THE DRAWING")
        # 1 numbered chip over a box
        pg.draw_rect(pymupdf.Rect(X0+4, y+2, X0+56, y+21), color=None, fill=ORG)
        pg.insert_textbox(pymupdf.Rect(X0+8, y+6, X0+56, y+21), "H-02", fontsize=11, fontname="hebo", color=(1,1,1))
        pg.draw_rect(pymupdf.Rect(X0+4, y+23, X0+108, y+45), color=ORG, width=2.0)
        pg.insert_htmlbox(pymupdf.Rect(X0+124, y-2, X1, y+68),
          "<div style='font-family:Helvetica;font-size:10.5pt;color:#222;line-height:1.30;'>"
          "<b>Coloured box + ID tag</b> on the drawing marks exactly what the finding refers to. The tag "
          "repeats the ID used in the cards above and in the registers at the back.</div>")
        y+=74
        # 2 sticky note
        pg.draw_rect(pymupdf.Rect(X0+34, y+6, X0+70, y+34), color=(0.55,0.12,0.12), fill=(1,0.93,0.6), width=1.5)
        for k in (0,1,2):
            pg.draw_line(pymupdf.Point(X0+40, y+13+6*k), pymupdf.Point(X0+64, y+13+6*k), color=(0.35,0.25,0.05), width=1.1)
        pg.insert_htmlbox(pymupdf.Rect(X0+124, y-2, X1, y+68),
          "<div style='font-family:Helvetica;font-size:10.5pt;color:#222;line-height:1.30;'>"
          "<b>Click any marker</b> for the full finding &mdash; what the drawing says, what the code says, the "
          "exact section, and what to do. Live PDF annotations; they export to a comment list.</div>")
        y+=74
        # 3 measured path
        pg.draw_line(pymupdf.Point(X0+6, y+16), pymupdf.Point(X0+98, y+16), color=MAG, width=3.4, dashes="[8 5] 0")
        for xx in (X0+6, X0+98):
            pg.draw_circle(pymupdf.Point(xx, y+16), 5.5, color=MAG, fill=(1,1,1), width=2.2)
        pg.insert_htmlbox(pymupdf.Rect(X0+124, y-2, X1, y+68),
          "<div style='font-family:Helvetica;font-size:10.5pt;color:#222;line-height:1.30;'>"
          "<b>Magenta dashed paths</b> were measured off the drawing&#39;s own vector geometry at the scale "
          "recorded in this file &mdash; not read off a label. On sheet G-1.</div>")
        y+=76
        y+=gap

    # ── whole-set tally
    if inc_glance:
        y=_sect(pg, X0, X1, y+2, "THE WHOLE SET AT A GLANCE")
        cw=(X1-X0)/6.0
        for k,(name,n) in enumerate(tally):
            bx=pymupdf.Rect(X0+k*cw+1, y+2, X0+(k+1)*cw-3, y+50)
            pg.draw_rect(bx, color=(0.88,0.90,0.93), fill=(1,1,1), width=1.0)
            pg.draw_rect(pymupdf.Rect(bx.x0,bx.y0,bx.x1,bx.y0+4), color=None, fill=SEVC[name])
            pg.insert_htmlbox(bx+(0,7,0,-2),
              f"<div style='font-family:Helvetica;text-align:center;'>"
              f"<div style='font-size:17pt;font-weight:bold;color:{HEX[name]};'>{n}</div>"
              f"<div style='font-size:8.5pt;color:#5b6572;letter-spacing:0.6pt;'>{name}</div></div>")
        y+=56
        y+=gap

    # ── about this margin
    if inc_about:
        y=_sect(pg, X0, X1, y+2, "ABOUT THIS REVIEW MARGIN")
        pg.insert_htmlbox(pymupdf.Rect(X0+2, y+2, X1, y+about_h),
          "<div style='font-family:Helvetica;font-size:10.5pt;color:#222;line-height:1.40;'>"
          "<b>The drawing has not been changed.</b> This margin was <i>added</i> to the left of the original "
          "36&Prime;&times;24&Prime; sheet, so nothing on the drawing is moved, resized or covered. The sheet is "
          "now 45.7&Prime;&times;24&Prime; &mdash; print to fit, or crop the margin off to recover the original "
          "sheet exactly.<br><br>"
          "<b>Nothing to switch on.</b> All 200 original AutoCAD layers survive in this file untouched, but no "
          "layer toggling is needed: everything in this review is visible as printed.<br><br>"
          "<b>Advisory only.</b> A licensed design professional remains responsible for code compliance. This is "
          "not a plan approval and does not replace review by the authority having jurisdiction.</div>")
        y+=about_h+2+gap

    # ── footer pointer
    fy=Rr.y1-46
    pg.draw_line(pymupdf.Point(X0, fy), pymupdf.Point(X1, fy), color=(0.84,0.86,0.90), width=1.0)
    pg.insert_htmlbox(pymupdf.Rect(X0+2, fy+7, X1, Rr.y1-4),
      "<div style='font-family:Helvetica;font-size:10.5pt;color:#5b6572;line-height:1.32;'>"
      "Full findings registers, the cross-discipline reconciliation and the method note are at the "
      "<b>back of this document</b> &mdash; pages 25 to 34.</div>")

for pno in range(doc.page_count):
    pg=doc[pno]; cards=FIND.get(pno,[])
    hs=[110 if len(t)>74 else 92 for _f,_s,t,_l,_b in cards]
    cards_h = 122 + sum(h+9 for h in hs) + (86 if pno in SCOPE_NOTE else 0)
    RAIL = pymupdf.Rect(RAIL_X0, RAIL_Y0, RAIL_X1, SH_ - RAIL_Y0)   # full sheet height, always
    pg.draw_rect(RAIL, color=(0.80,0.82,0.86), fill=(1,1,1), fill_opacity=0.94, width=1.4)
    hdr=pymupdf.Rect(RAIL.x0,RAIL.y0,RAIL.x1,RAIL.y0+112)
    pg.draw_rect(hdr, color=None, fill=INK)
    order=["CRITICAL","HIGH","MEDIUM","LOW","MEASURED","VERIFIED","SCOPE"]
    present=[s for _f,s,_t,_l,_b in cards]
    worst=next((s for s in order if s in present), None)
    nopen=sum(1 for _f,s,_t,_l,_b in cards if s in ("CRITICAL","HIGH","MEDIUM","LOW"))
    npass=len(cards)-nopen
    badge=(f"<span style='color:#fff;background:{HEX[worst]};padding:2pt 8pt;font-size:12pt;font-weight:bold;'>{worst}</span>"
           if worst else "")
    counts=f"<span style='color:#8fa3bd;font-size:11pt;'> &nbsp; {nopen} open &nbsp;·&nbsp; {npass} verified on this sheet</span>"
    pg.insert_htmlbox(hdr+(14,9,-12,-6),
      f"<div style='font-family:Helvetica;color:#fff;'>"
      f"<div style='font-size:10pt;color:#8fa3bd;letter-spacing:1pt;'>INDEPENDENT FBC CODE REVIEW &nbsp;·&nbsp; v5 &nbsp;·&nbsp; SHEET {pno+1} OF 24</div>"
      f"<div style='font-size:19pt;font-weight:bold;margin-top:3pt;'>{esc(SHEETS[pno])}</div>"
      f"<div style='margin-top:5pt;'>{badge}{counts}</div></div>")
    y=RAIL.y0+122
    for (fid,sev,title,line,_b),h in zip(cards,hs):
        r=pymupdf.Rect(RAIL.x0+10,y,RAIL.x1-10,y+h)
        pg.draw_rect(r, color=(0.88,0.90,0.93), fill=(0.985,0.99,1.0), width=1.0)
        pg.draw_rect(pymupdf.Rect(r.x0,r.y0,r.x0+7,r.y1), color=None, fill=SEVC[sev])
        pg.insert_htmlbox(r+(19,8,-10,-5),
          f"<div class='id'>{esc(fid)} &nbsp; <b style='color:{HEX[sev]}'>{sev}</b></div>"
          f"<div class='t'>{esc(title)}</div><div class='s'>{line}</div>", css=CSS)
        y+=h+9
    if pno in SCOPE_NOTE:
        r=pymupdf.Rect(RAIL.x0+10,y,RAIL.x1-10,y+78)
        pg.draw_rect(r,color=(0.88,0.90,0.93),fill=(0.97,0.975,0.98),width=1.0)
        pg.draw_rect(pymupdf.Rect(r.x0,r.y0,r.x0+7,r.y1),color=None,fill=GRY)
        pg.insert_htmlbox(r+(19,8,-10,-5),
          f"<div class='id'>SCOPE &nbsp; <b style='color:#6b7280'>ALSO ON THIS SHEET, NOT REVIEWED</b></div>"
          f"<div class='s'>{SCOPE_NOTE[pno]}</div>", css=CSS)
        y+=86
    legrect = pymupdf.Rect(RAIL.x0+10, RAIL.y0+cards_h+6, RAIL.x1-10, RAIL.y1-10)
    if legrect.height < LEG_MIN:
        print(f"   !! LEGEND SQUEEZED p{pno+1}: {legrect.height:.0f} < {LEG_MIN}")
    draw_legend(pg, legrect, TALLY)
print("rail done")

# ══════════════════════ 3. summary / register section ══════════════════════
print("── summary pages ──")
W,H=SW,SH_
def newpage(i,big,small,kicker="INDEPENDENT FLORIDA BUILDING CODE REVIEW · v5"):
    p=doc.new_page(-1,width=W,height=H)   # i ignored — back matter appends in creation order
    p.draw_rect(pymupdf.Rect(0,0,W,190),color=None,fill=INK)
    p.insert_htmlbox(pymupdf.Rect(80,26,W-80,182),
      f"<div style='color:#fff;font-family:Helvetica,Arial;'>"
      f"<div style='font-size:20pt;color:#8fa3bd;letter-spacing:2.4pt;'>{kicker}</div>"
      f"<div style='font-size:46pt;font-weight:bold;margin-top:6pt;'>{big}</div>"
      f"<div style='font-size:23pt;color:#9fb0c8;margin-top:8pt;'>{small}</div></div>", css="")
    return p

PCSS="""
body{font-family:Helvetica,Arial,sans-serif;font-size:24pt;color:#111;line-height:1.36;}
h2{font-size:31pt;margin:26pt 0 10pt 0;color:#0b1220;border-bottom:3px solid #0b1220;padding-bottom:5pt;}
h3{font-size:25pt;margin:20pt 0 8pt 0;color:#0b1220;}
p{margin:0 0 13pt 0;} ul{margin:0 0 13pt 36pt;padding:0;} li{margin:0 0 7pt 0;}
table{border-collapse:collapse;width:100%;font-size:19pt;table-layout:fixed;}
th{background:#0b1220;color:#fff;text-align:left;padding:8pt 11pt;font-size:19pt;}
td{border-bottom:1.5px solid #c8ccd4;padding:7pt 11pt;vertical-align:top;line-height:1.26;}
.c{color:#cc0f0f;font-weight:bold;} .h{color:#d97400;font-weight:bold;} .m{color:#a58000;font-weight:bold;}
.p{color:#0a6b32;font-weight:bold;} .l{color:#1a56b0;font-weight:bold;} .g{color:#5b6572;font-weight:bold;}
.x{color:#c7008c;font-weight:bold;}
.k{color:#5b6572;font-size:17pt;text-transform:uppercase;letter-spacing:0.8pt;}
.sm{font-size:18pt;color:#555;} code{font-family:Courier,monospace;font-size:18pt;background:#eef0f4;}
.box{background:#fdf2f2;border-left:9px solid #cc0f0f;padding:15pt 21pt;margin:0 0 16pt 0;}
.box2{background:#f4f6f9;border-left:9px solid #0b1220;padding:15pt 21pt;margin:0 0 16pt 0;}
.box3{background:#fdf4fb;border-left:9px solid #c7008c;padding:15pt 21pt;margin:0 0 16pt 0;}
.box4{background:#f1f8f3;border-left:9px solid #0a6b32;padding:15pt 21pt;margin:0 0 16pt 0;}
.n{background:#fbfbfc;}
"""
CLS={"CRITICAL":"c","HIGH":"h","MEDIUM":"m","LOW":"l","VERIFIED":"p","MEASURED":"x","SCOPE":"g"}
BY={e["fid"]:e for e in R}
def sheets_for(fids): return ", ".join(dict.fromkeys(CODE[BY[f]["pg"]] for f in fids))

# ── finding groups for the register (OPEN_ROWS defined near the top) ──
def open_table(rows):
    h=("<table><colgroup><col style='width:4%'><col style='width:6%'><col style='width:7%'>"
       "<col style='width:47%'><col style='width:15%'><col style='width:21%'></colgroup>"
       "<tr><th>ID</th><th>Sheet</th><th>Severity</th><th>What was checked, and what was found</th>"
       "<th>Code reference</th><th>What to do</th></tr>")
    for i,(fid,grp) in enumerate(rows):
        e=BY[fid]; sev=e["sev"]
        nn = " class='n'" if i%2 else ""
        h+=(f"<tr{nn}><td><b>{fid}</b></td><td>{sheets_for(grp)}</td>"
            f"<td class='{CLS[sev]}'>{sev}</td>"
            f"<td><b>{esc(e['title'])}</b><br>"
            f"<span class='k'>Checked</span> {esc(e['checked'])}<br>"
            f"<span class='k'>Found</span> {esc(e['result'])}</td>"
            f"<td>{esc(e['code'])}</td><td>{esc(e['action'])}</td></tr>")
    return h+"</table>"

VER=[e for e in R if e.get("status")=="PASS"]
def ver_table(rows):
    h=("<table><colgroup><col style='width:4%'><col style='width:5%'><col style='width:14%'>"
       "<col style='width:62%'><col style='width:18%'></colgroup>"
       "<tr><th>ID</th><th>Sheet</th><th>Discipline</th><th>What was checked, and why it is sufficient</th>"
       "<th>Code reference</th></tr>")
    for i,e in enumerate(rows):
        nn = " class='n'" if i%2 else ""
        h+=(f"<tr{nn}><td><b>{e['fid']}</b></td><td>{CODE[e['pg']]}</td>"
            f"<td>{esc(e['disc'])}</td>"
            f"<td><b>{esc(e['title'])}</b><br>{esc(e['result'])}</td>"
            f"<td>{esc(e['code'])}</td></tr>")
    return h+"</table>"

PUTN=[0]
def fixtables(html):
    """PyMuPDF's Story engine ignores <colgroup>; it honours width= on <th>."""
    def repl(m):
        widths=re.findall(r"width:([\d.]+)%", m.group(1)); row=m.group(2); i=[0]
        def th(mm):
            w=widths[i[0]] if i[0]<len(widths) else None; i[0]+=1
            return f"<th width='{w}%'{mm.group(1)}>" if w else mm.group(0)
        return re.sub(r"<th([^>]*)>", th, row)
    return re.sub(r"<colgroup>(.*?)</colgroup>\s*(<tr>.*?</tr>)", repl, html, flags=re.S)

def put(p, html, tag=""):
    PUTN[0]+=1
    html=fixtables(html)
    r=pymupdf.Rect(80,216,W-80,H-46)
    res=p.insert_htmlbox(r, html, css=PCSS)
    if isinstance(res,tuple) and res[1]<1.0: print(f"   !! page {PUTN[0]} {tag} shrunk to {res[1]:.2f}")
    return res

TODAY=datetime.date.today().isoformat()
nC=sum(1 for f,_ in OPEN_ROWS if BY[f]['sev']=='CRITICAL')
nH=sum(1 for f,_ in OPEN_ROWS if BY[f]['sev']=='HIGH')
nM=sum(1 for f,_ in OPEN_ROWS if BY[f]['sev']=='MEDIUM')
nL=sum(1 for f,_ in OPEN_ROWS if BY[f]['sev']=='LOW')
nV=len(VER)


# ── per-sheet index ──
ORD=["CRITICAL","HIGH","MEDIUM","LOW","MEASURED","VERIFIED"]
def sheet_stats(pno):
    es=[e for e in R if e["pg"]==pno]
    op=sum(1 for e in es if e.get("status")=="OPEN")
    vr=len(es)-op
    pres=[e["sev"] for e in es]
    worst=next((x for x in ORD if x in pres), "VERIFIED")
    return op,vr,worst
def sheet_index():
    h=("<table><colgroup>"
       "<col style='width:6%'><col style='width:23%'><col style='width:6%'><col style='width:7%'><col style='width:8%'>"
       "<col style='width:2%'>"
       "<col style='width:6%'><col style='width:23%'><col style='width:6%'><col style='width:7%'><col style='width:6%'>"
       "</colgroup>"
       "<tr><th>Sheet</th><th>Title</th><th>Open</th><th>Verified</th><th>Status</th><th></th>"
       "<th>Sheet</th><th>Title</th><th>Open</th><th>Verified</th><th>Status</th></tr>")
    for i in range(12):
        cells=""
        for k,pno in enumerate((i, i+12)):
            op,vr,w=sheet_stats(pno)
            t=" ".join(SHEETS[pno].split()[1:])
            if k: cells+="<td></td>"
            cells+=(f"<td><b>{CODE[pno]}</b></td><td>{esc(t)}</td><td>{op if op else '&mdash;'}</td>"
                    f"<td>{vr}</td><td class='{CLS[w]}'>{w if op else 'CLEAR'}</td>")
        nn=" class='n'" if i%2 else ""
        h+=f"<tr{nn}>{cells}</tr>"
    return h+"</table>"

# ─── page 1 — cover / executive summary ───
p=newpage(0,"Code Review — Sculpted Hot Pilates",
 "9985 Estero Oaks Drive, Fort Myers, FL 33967 · Permit Set 8/18/2026 · Project 214-2026 · 2023 FBC, 8th Edition")
put(p, f"""
<div class='box'><b>ONE CRITICAL FINDING. RTU-2 is scheduled for 350 CFM of outdoor air against the 881 CFM this
project's own mechanical sheet calculates — a 531 CFM shortfall.</b> The arithmetic on M-1 is correct; the equipment
selected cannot deliver it, and no note on any sheet reconciles the two figures. Everything else in this review is
resolvable on paper. This one is equipment.</div>

<table>
<colgroup><col style='width:14%'><col style='width:9%'><col style='width:77%'></colgroup>
<tr><th>Severity</th><th>Count</th><th>What it means, and what it costs you to leave it</th></tr>
<tr><td class='c'>CRITICAL</td><td><b>{nC}</b></td><td>A quantified deficiency in the design as drawn. Will not pass review, and cannot be resolved by a note.</td></tr>
<tr><td class='h'>HIGH</td><td><b>{nH}</b></td><td>A stated code value is wrong, or a governing assumption is unresolved across sheets. Each is a likely written correction.</td></tr>
<tr><td class='m'>MEDIUM</td><td><b>{nM}</b></td><td>Internally inconsistent or undocumented. A reviewer cannot confirm compliance from the sheet as drawn.</td></tr>
<tr><td class='l'>LOW</td><td><b>{nL}</b></td><td>Editorial. Worth fixing on the next plot; will not hold up a permit.</td></tr>
<tr><td class='p'>VERIFIED</td><td><b>{nV}</b></td><td>Checked against the code text and found sufficient. Listed by sheet so you can see what was actually examined, not just what failed.</td></tr>
<tr><td class='x'>MEASURED</td><td><b>4</b></td><td>Distances traced off this file&#39;s own vector geometry and compared against the dimension printed beside them.</td></tr>
</table>

<h2>What this review is</h2>
<p>An independent, pre-submittal read of all 24 sheets against the 2023 Florida Building Code. Every sheet carries
markup: a review margin at the left with the findings for that sheet, and coloured markers on the drawing itself
showing exactly what each finding refers to — <b>including the items that were checked and found sufficient</b>.
No sheet in this set is silent.</p>

<div class='box4'><b>Why the green markers matter.</b> A review that only shows failures tells you nothing about
coverage. The {nV} verified items are what let you answer the question a plans examiner will ask you first:
<i>what did you actually check?</i> Each one names the section it was checked against and states, in a sentence, why
it passes.</div>

<h2>The four things to fix before you submit</h2>
<table>
<colgroup><col style='width:9%'><col style='width:11%'><col style='width:47%'><col style='width:33%'></colgroup>
<tr><th>ID</th><th>Sheet</th><th>Issue</th><th>Fix</th></tr>
<tr><td class='c'><b>C-01</b></td><td>M-1</td><td>Outdoor air 881 CFM required vs 350 CFM scheduled on RTU-2</td><td>Increase RTU-2 OA capacity, add a dedicated OA unit or ERV, or re-establish the ventilation density with AHJ approval</td></tr>
<tr><td class='h'><b>H-01</b></td><td>G-1, A-2, M-1</td><td>Mat Studio 102 carries three different occupant densities</td><td>State one governing occupant load with its Table 1004.5 basis and carry it across all sheets</td></tr>
<tr><td class='h'><b>H-02</b></td><td>G-0</td><td>Common path stated as 50 LF; Table 1006.2.1 gives 75 ft, and G-1 already says 75</td><td>Change 50 LF to 75 LF on G-0</td></tr>
<tr><td class='h'><b>H-03</b></td><td>A-2</td><td>Door 104 is a 2&prime;-8&Prime; leaf — roughly 30&Prime; clear against a 32&Prime; minimum</td><td>Change Door 104 to a 3&prime;-0&Prime; leaf, matching Door 103</td></tr>
</table>

<h2>Revision status</h2>
<p>Compared page by page against the <b>DRAFT SET dated 8/11/2026</b>. After removing markup stamps and the
title-block date, <b>all 24 pages are textually identical</b> — the permit set is the draft set with the date changed.
No prior review comment was addressed, including the outdoor-air shortfall.</p>

<p class='sm'>Prepared {TODAY} · Advisory only. A licensed design professional remains responsible for code compliance.
This review does not constitute a plan approval and is not a substitute for review by the authority having jurisdiction.</p>
""")

# ─── page 2 — how to read ───
p=newpage(1,"How to read this review","Every sheet carries a review margin. Nothing on the original drawing has been covered or changed.")
put(p, """
<h2>The sheet layout</h2>
<p>Each of the 24 drawing sheets in this document is <b>wider than the original</b>. A 9.75-inch review margin was
<i>added to the left</i> of the original 36&Prime;&times;24&Prime; sheet — the drawing itself was not moved, resized,
covered or altered in any way. The sheets are now 45.7&Prime;&times;24&Prime;. Print to fit, or crop the margin off to
recover the original sheet exactly.</p>

<div class='box2'><b>No layers to switch on.</b> Everything in this review is visible as printed. All 200 original
AutoCAD layers survive in this file untouched, but you do not need to touch any of them — there is nothing hidden and
nothing to toggle.</div>

<h2>What the markers mean</h2>
<table>
<colgroup><col style='width:14%'><col style='width:20%'><col style='width:66%'></colgroup>
<tr><th>Colour</th><th>Label</th><th>Meaning</th></tr>
<tr><td class='c'>Red</td><td class='c'>CRITICAL</td><td>A quantified deficiency in the design as drawn. One in this set.</td></tr>
<tr><td class='h'>Orange</td><td class='h'>HIGH</td><td>A stated code value is wrong, or a governing assumption is unresolved between sheets.</td></tr>
<tr><td class='m'>Amber</td><td class='m'>MEDIUM</td><td>Internally inconsistent or undocumented — a reviewer cannot confirm it from the sheet.</td></tr>
<tr><td class='l'>Blue</td><td class='l'>LOW</td><td>Editorial. Fix on the next plot.</td></tr>
<tr><td class='p'>Green</td><td class='p'>VERIFIED</td><td><b>Checked against the code text and found sufficient.</b> These are on the drawings deliberately — they show what was examined and passed, not just what failed.</td></tr>
<tr><td class='x'>Magenta dashed</td><td class='x'>MEASURED</td><td>A path traced off this file&#39;s own vector geometry and compared against the dimension printed beside it. On sheet G-1.</td></tr>
<tr><td class='g'>Grey</td><td class='g'>SCOPE</td><td>Named on each sheet: what was <i>not</i> reviewed there.</td></tr>
</table>

<h2>Three things on every sheet</h2>
<ul>
<li><b>A coloured box with an ID tag</b> on the drawing marks exactly what a finding refers to. The tag repeats the
finding ID from the cards in the margin and from the registers on the following pages.</li>
<li><b>A clickable note</b> at each marker. Open it for the full finding — what the drawing says, what the code says,
the exact section number, and what to do about it. These are live PDF annotations: they open in Acrobat, Bluebeam,
Preview and any standard viewer, and they export to a comment list.</li>
<li><b>A review margin</b> at the left of the sheet listing every finding on that sheet, with its severity, plus a
scope note stating what was not reviewed there.</li>
</ul>

""")


# ─── page 3 — document map + sheet index ───
p=newpage(2,"Sheet index and document map","What is on every sheet, and how the registers that follow are organised")
put(p, """
<h2>How the registers on the following pages are organised</h2>
<ul>
<li><b>Open findings register</b> &mdash; the {n} items that need action, each with what was checked, what was found,
the governing code section, and the specific fix. Sorted by severity.</li>
<li><b>Verified register</b> &mdash; the {v} items checked against the code text and found sufficient, in sheet order.
Kept short on purpose: what was checked, and why it passes.</li>
<li><b>Cross-discipline reconciliation</b> &mdash; the one issue in this set that no sheet-by-sheet review can find,
because every sheet is internally consistent.</li>
<li><b>Method and scope</b> &mdash; how the measurements were taken, what was reviewed, what was not, and what would
make any of these findings wrong.</li>
</ul>

<h2>Sheet index</h2>
""".replace("{n}",str(len(OPEN_ROWS))).replace("{v}",str(nV)) + sheet_index() + """<h2>Four distances measured, not read</h2>
<p>This set is a native vector plot from AutoCAD Architecture, and the exact drawing scale is recorded inside the
file. That made it possible to <b>trace the egress paths off the drawing&#39;s own geometry</b> and compare them
against the dimensions printed beside them, rather than taking the annotations on trust.</p>
<table>
<colgroup><col style='width:20%'><col style='width:16%'><col style='width:16%'><col style='width:13%'><col style='width:35%'></colgroup>
<tr><th>Item</th><th>Drawing states</th><th>Measured here</th><th>Difference</th><th>Limit</th></tr>
<tr><td>Travel Path 1</td><td>69&prime;-4&Prime;</td><td class='x'><b>68.9 ft</b></td><td>~5 in &middot; 0.6%</td><td>250 ft — Table 1017.2</td></tr>
<tr class='n'><td>Travel Path 2</td><td>24&prime;-7&Prime;</td><td class='x'><b>25.6 ft</b></td><td>~12 in</td><td>250 ft — Table 1017.2</td></tr>
<tr><td>Common path</td><td>8&prime;-1&Prime;</td><td class='x'><b>9.5 ft</b></td><td>~17 in</td><td>75 ft — Table 1006.2.1</td></tr>
<tr class='n'><td>Egress band width</td><td>3&prime;-8&Prime;</td><td class='x'><b>3&prime;-8&Prime;</b></td><td>exact</td><td>44&Prime; — Table 1020.3</td></tr>
</table>
<p>All four sit far inside their limits, so none is a deficiency. They are reported because a review that only
re-reads the designer&#39;s own labels has verified nothing. Method on the last page.</p>

""", "index")

# ─── open findings register (paginated) ───
IDX=[3]
def add_page(big, small):
    q=newpage(IDX[0], big, small); IDX[0]+=1; return q

OCH=[OPEN_ROWS[i:i+7] for i in range(0,len(OPEN_ROWS),7)]
for i,ch in enumerate(OCH):
    ttl=f"Open findings register — {i+1} of {len(OCH)}"
    sub=(f"{nC} critical &middot; {nH} high &middot; {nM} medium &middot; {nL} low &nbsp;|&nbsp; sorted by severity"
         if i==0 else "Continued")
    q=add_page(ttl,sub)
    put(q, open_table(ch), f"open{i+1}")

# ─── verified register (paginated) ───
VCH=[VER[0:13], VER[13:25], VER[25:]]
for i,ch in enumerate(VCH):
    ttl=f"Verified register — {i+1} of {len(VCH)}"
    rng=f"sheets {CODE[ch[0]['pg']]} through {CODE[ch[-1]['pg']]}"
    sub=(f"{nV} items checked against the code text and found sufficient &nbsp;|&nbsp; {rng}"
         if i==0 else rng)
    q=add_page(ttl,sub)
    intro=("<div class='box4'><b>Why this register exists.</b> The value of a code review is not only what it "
      "catches — it is knowing what was looked at. Every item below was checked against the governing code section "
      "and found sufficient, and every one carries a green marker on the sheet it was checked on. Deliberately "
      "brief: what was checked, and why it passes.</div>") if i==0 else ""
    put(q, intro + ver_table(ch), f"ver{i+1}")

# ─── page 7 — the cross-discipline reconciliation ───
p=newpage(IDX[0],"The reconciliation nobody runs","One room, three occupant densities, three disciplines — and what each one changes")
put(p, """
<p>Sheet-by-sheet review cannot find this class of error, because each sheet is internally consistent. It only
appears when the same physical space is tracked across every discipline that assigns it a number.</p>
<h2>Mat Studio 102 — 994 SF</h2>
<table>
<colgroup><col style='width:8%'><col style='width:15%'><col style='width:19%'><col style='width:9%'><col style='width:49%'></colgroup>
<tr><th>Sheet</th><th>Discipline</th><th>Density applied</th><th>Occ.</th><th>Basis stated on the drawing</th></tr>
<tr><td><b>G-1</b></td><td>Egress</td><td>15 SF <b>net</b> (1 per 15)</td><td><b>67</b></td><td>&ldquo;ASSEMBLY&rdquo; — FBC-B Table 1004.5, <i>Assembly without fixed seats, Unconcentrated (tables and chairs)</i></td></tr>
<tr class='n'><td><b>G-1</b></td><td>Life safety (FFPC)</td><td>15 SF (1 per 15)</td><td><b>67</b></td><td>Block headed &ldquo;ASSEMBLY - <b>EXERCISE ROOMS WITHOUT EQUIPMENT</b>&rdquo; — a different classification, same number</td></tr>
<tr><td><b>M-1</b></td><td>Ventilation</td><td>40 per 1000 SF (1 per 25)</td><td><b>40</b></td><td>Health-club / aerobics default — FBC-M Table 403.3.1.1</td></tr>
<tr class='n'><td>&mdash;</td><td>Not applied</td><td>50 SF <b>gross</b> (1 per 50)</td><td><b>20</b></td><td>FBC-B Table 1004.5, <i>Exercise rooms</i> — an expressly listed function that describes this room</td></tr>
</table>
<div class='box2'><b>A partial reconciliation exists, in one place only.</b> M-1 carries the note &ldquo;DAILY BUSINESS
OCCUPANCY LIMITED TO 42 OCCUPANTS. OCCUPANT LOAD CALCULATED ON SHEET G-1 REFERS TO MAXIMUM OCCUPANT LOAD PER FBC
TABLE.&rdquo; That reconciles egress against ventilation. It does not appear on G-1, and it does not address the
Table 1004.5 exercise-room factor at all.</div>
<h2>What each choice changes</h2>
<table>
<colgroup><col style='width:23%'><col style='width:23%'><col style='width:24%'><col style='width:30%'></colgroup>
<tr><th>Consequence</th><th>At OL 70 (as drawn)</th><th>At OL 23 (exercise rooms)</th><th>Governing section</th></tr>
<tr><td>Exits required</td><td>2</td><td><b>1 permitted</b></td><td>Table 1006.3.2 / 1006.3.3(2)</td></tr>
<tr class='n'><td>Egress width required</td><td>10.50&Prime; at 0.15 &middot; 14.0&Prime; at 0.20</td><td>3.45&Prime; / 4.60&Prime;</td><td>1005.3.2</td></tr>
<tr><td>Plumbing fixtures</td><td>1 WC + 1 LAV each sex, 1 DF, 1 SS</td><td><b>unchanged</b> — all ratios still round to 1</td><td>Table 2902.1, 2902.1.1</td></tr>
<tr class='n'><td>Risk category</td><td>II (III as drawn — see M-03)</td><td>II</td><td>Table 1604.5</td></tr>
<tr><td>Occupant-load posting</td><td>Required</td><td>Required</td><td>1004.9</td></tr>
<tr class='n'><td>Outdoor air required</td><td>881 CFM at the M-1 density</td><td>881 CFM — <b>set by FBC-M, not by the egress load</b></td><td>FBC-M 403.3.1.1</td></tr>
</table>
<div class='box'><b>Note the last row.</b> Resolving the occupant-load question does <i>not</i> resolve C-01. The
ventilation requirement is driven by the mechanical code&#39;s own occupant density, which the designer applied
correctly. The 531 CFM shortfall is an equipment-selection problem and must be fixed on its own terms.</div>
""")

# ─── page 8 — method, measurement, scope ───
p=newpage(IDX[0]+1,"Method, measurement and scope","How the numbers were obtained, what was reviewed, and what would make these findings wrong")
put(p, """
<h2>Four measurements taken off this file's own geometry</h2>
<table>
<colgroup><col style='width:24%'><col style='width:19%'><col style='width:19%'><col style='width:14%'><col style='width:24%'></colgroup>
<tr><th>Item</th><th>Drawing states</th><th>Measured here</th><th>Difference</th><th>Limit</th></tr>
<tr><td>Travel Path 1</td><td>69&prime;-4&Prime; (69.33 ft)</td><td><b>68.9 ft</b></td><td>~5 in &middot; 0.6%</td><td>250 ft — Table 1017.2</td></tr>
<tr class='n'><td>Travel Path 2</td><td>24&prime;-7&Prime; (24.58 ft)</td><td><b>25.6 ft</b></td><td>~12 in</td><td>250 ft — Table 1017.2</td></tr>
<tr><td>Common path</td><td>8&prime;-1&Prime; (8.08 ft)</td><td><b>9.5 ft</b></td><td>~17 in</td><td>75 ft — Table 1006.2.1</td></tr>
<tr class='n'><td>Egress band width</td><td>3&prime;-8&Prime;</td><td><b>3&prime;-8&Prime;</b></td><td>exact</td><td>44&Prime; — Table 1020.3</td></tr>
</table>
<p>All four are far inside their limits, so none is a deficiency. They are reported because <b>a review that only
re-reads the designer&#39;s own labels has verified nothing.</b> The traced paths are drawn on G-1 in magenta so the
measurement can be checked by eye against the drawn path.</p>

<div class='box3'><b>How the scale was obtained.</b> Not estimated from a scale bar. Each page carries an ISO 32000
<code>/VP</code> viewport array whose <code>/Measure</code> dictionaries hold exact conversion factors; G-1 carries 125
of them, and the one governing the life-safety plan gives <code>C = 0.05554</code> ft per point =
<b>18.005 points per foot</b> (1/4&Prime; = 1&prime;-0&Prime;). Geometry was then selected by AutoCAD layer name —
198 vector paths on <code>Life Safety|Egress Path</code> — because all 200 layers survive in this file as PDF
optional content groups. This is a native vector plot from AutoCAD Architecture, not a scan, and that is what makes
this class of check possible.</div>

<h2>Coverage, stated plainly</h2>
<table>
<colgroup><col style='width:18%'><col style='width:82%'></colgroup>
<tr><th>Status</th><th>Scope</th></tr>
<tr><td><b>Reviewed</b></td><td>FBC-B Chapter 10 means of egress in full; Chapter 29 plumbing fixture counts; Chapter 8 interior finish documentation; Table 1604.5 risk category; FBC-EBC Level II scoping; FBC-M 403.3.1.1 ventilation and duct construction details; <b>FBC-Accessibility — space allowances, reach ranges, water closets, lavatories, drinking fountains, door maneuvering clearances, signage and changes in level</b>; UL U465 against the listing text reproduced on A-11; NEC GFCI, service receptacle, emergency unit-equipment circuiting and panel loading; FBC-P drainage and vent sizing spot checks; and cross-sheet consistency across all 24 sheets.</td></tr>
<tr class='n'><td><b>Not reviewed</b></td><td>Structural capacity — bar joists, the W24x76 beam and unistrut for suspended infrared panels. Energy code (FBC-EC) compliance path, lighting power density, duct insulation and leakage. Fire-suppression sprinkler layout. Short-circuit and coordination study. Hydraulic sizing by developed length. Field verification of any existing condition. Each sheet&#39;s review margin names what was skipped on that sheet.</td></tr>
<tr><td><b>Could not read</b></td><td>Sheet <b>A-12</b> is a raster insert — 42 embedded images, roughly 206 megapixels, 68 words of live text. The six UL firestop design numbers were read; the parameters inside each system were not machine-checked.</td></tr>
</table>

<h2>What would make these findings wrong</h2>
<ul>
<li><b>C-01</b> — only if RTU-2 has outdoor-air capacity beyond its scheduled 350 CFM, or a separate outdoor-air unit exists outside this set. The requirement itself is the designer&#39;s own number.</li>
<li><b>H-01</b> — if the AHJ has already accepted &lsquo;assembly, unconcentrated&rsquo; for this space, or the studio will contain tables and chairs. The 15-net figure errs safe for egress.</li>
<li><b>H-03</b> — if Door 104 is in fact an existing door to remain rather than the new Type A leaf the schedule shows, or if the specified frame and hardware demonstrably yield a true 32&Prime; clear.</li>
<li><b>M-01</b> — if an EVACS meeting 907.5.2.2 exists in the base-building fire alarm design.</li>
<li><b>M-03</b> — if the risk category was inherited from the shell design rather than set by this alteration.</li>
<li><b>M-08</b> — if the floor drains are on a regularly-used waste line, or the AHJ does not treat them as subject to evaporation.</li>
<li><b>The measured paths</b> — the deltas are measurement, not error. Annotated distances begin at POINT A1, whose exact coordinate is a drafting decision, and the traced centreline is derived from the band edges.</li>
</ul>

<p class='sm'>Generated """ + TODAY + """ &middot; All markers are live PDF annotations, clickable and exportable to Bluebeam or Acrobat &middot;
Original 200 AutoCAD layers preserved untouched &middot; Advisory only — a licensed design professional remains
responsible for code compliance, and this review is not a substitute for review by the authority having jurisdiction.</p>
""")

doc.set_metadata({"title":"Sculpted Hot Pilates — Independent FBC Code Review v5",
 "author":AUTH,"subject":"Advisory pre-submittal code review, 2023 FBC 8th Edition",
 "keywords":"FBC, code review, egress, ventilation, accessibility"})
doc.save(OUT, garbage=4, deflate=True, deflate_images=True, deflate_fonts=True, clean=True, use_objstms=1)
c=pymupdf.open(OUT)
import os
print(f"\nsaved {OUT}")
print(f"pages {c.page_count} | annots {sum(len(list(p.annots())) for p in c)} | "
      f"OCGs {len(c.get_ocgs())} | {os.path.getsize(OUT)/1e6:.1f} MB")
