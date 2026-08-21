---
title: FBC Code Review Markup v5 — Method and Findings
type: note
tags:
  - code-review
  - florida-building-code
  - pdf-pipeline
  - source/cowork
  - status/needs-triage
status: draft
created: 2026-08-20
source-session: Meridian Drafting
---

# FBC Code Review Markup v5 — Method and Findings

Test case: **Sculpted Hot Pilates**, 9985 Estero Oaks Drive, Fort Myers, FL 33967. Permit Set
8/18/2026, project 214-2026, GS Drafting Solutions. 24 sheets, 2023 FBC 8th Edition.
Deliverable: `SCULPTED HOT PILATES - CODE REVIEW v5 - MARKUP.pdf` — 34 pages, 18.5 MB,
114 live annotations, 200 original AutoCAD layers preserved.

Routing note: written to `Unsorted Captures` because this is FBC code-reviewer product work
(the UpCodes competitor), not the Meridian Drafting client engagement the session is attached to.
Retag when a home folder exists for the code-review platform.

## What v5 changed

- Every one of the 24 drawing sheets now carries markup. Sheets that previously had no findings
  carry **green VERIFIED markers** — an item checked against the code text and found sufficient.
  No blank drawing page remains.
- A **10-page register section** was inserted in front of the drawings: cover/executive summary,
  how-to-read, sheet index + document map, open findings register (2 pp), verified register (3 pp),
  cross-discipline reconciliation, method and scope.
- Counts: **1 CRITICAL, 3 HIGH, 8 MEDIUM, 2 LOW, 36 VERIFIED, 4 MEASURED**.

## New findings introduced at v5

### H-03 — Door 104 is a 2'-8" leaf against a 32 in. clear minimum

- A-2 Door and Frame Schedule: door `104` = Style A, single-panel solid core wood, hollow metal
  frame, **2'-8" x 7'-0" x 1-3/4"**, Hardware Set 1, no "existing" remark.
- Door tag `104` sits at `(1238.2, 313.1)` on A-2, which is the entry to **Unisex Restroom 106**.
  (Door `103` = 3'-0" serves accessible Restroom 105; door `105` = 2'-8" Type B bifold serves
  Closet 104.)
- Clear width: 32 − 1.75 (leaf) − ~0.125 (stop) ≈ **30-1/8 in.**
- `FBC-B 1010.1.1` requires 32 in. clear, measured face-of-door to stop at 90°. Its exceptions cover
  Group I-3 sleeping units, revolving/power-operated doors, and **storage closets under 10 sq ft** —
  none reaches a toilet room.
- The set contradicts itself: G-0's code data block states 32 in. required and provided; G-3's own
  doorway-approach detail is drawn as `36" MIN DOOR / 32" MIN. CLR.`
- `FBC-A 213.2 Exception 4` (50 percent of clustered single-user toilet rooms) may exempt Restroom
  106 from `FBC-A 404.2.3`, but `1010.1.1` still applies.
- Secondary: door `105`, the 2'-8" bifold to Closet 104 — confirm the closet is under 10 sq ft or the
  1010.1.1 exception does not apply. M-1's ventilation row computes it at about 13 SF.

### M-06 — interior finish classes called for but not assigned (A-4)

A-4 general note 5 requires flame-spread and smoke-development ratings, but the finish schedule
assigns no Class A/B/C. `FBC-B Table 803.13` / `803.1.1`.

### M-07 — accessible reception counter not dimensioned (A-9)

A-5 mark 13 is a "CUSTOM BUILT-IN ACCESSIBLE RECEPTION COUNTER"; the A-9 detail shows construction
but does not identify or dimension the accessible segment. `FBC-A 904.4` / `904.4.1` — 36 in. max
high, 36 in. min length.

### M-08 — trap seal protection not shown at the two restroom floor drains (P-1)

`FBC-P 1002.4.1` — "Trap seals of emergency floor drain traps and trap seals subject to evaporation
shall be protected by one of the methods in Sections 1002.4.1.1 through 1002.4.1.4." Section number
verified against UpCodes. No primer, primer line, or barrier device appears in the P-1 legend, on the
plan, or in the P-3 riser.

## Accessibility verification — the v4 scope gap, now closed

Checked value by value against FBC-Accessibility. **Every value on G-2 and G-3 is correct.**

| Section | Requirement | Drawing |
|---|---|---|
| 304.3.1 | turning circle 60 in. | 60" |
| 305.3 | clear floor space 30 x 48 in. | 30" / 48" |
| 305.7 | alcove: >15 in. parallel → 60 in.; >24 in. forward → 36 in. | X>15→60", X>24→36" |
| 307.2 | protruding objects 27–80 in. AFF limited to 4 in. | X>27, X>80, 4" MAX |
| 308.2.2 | forward obstructed: 48 in. at ≤20 in. deep; 44 in. at 20–25 in. | 48"/20", 44"/20"-25" |
| 308.3.2 | side obstructed: 48 in. at ≤10 in. deep; **46 in. at 10–24 in.** | 48"/10", 46"/10"-24" |
| 604.2 | WC centerline 16–18 in. | **18" — at the maximum** |
| 604.3.1 | clearance 60 in. side / 56 in. rear | 60" / 56" |
| 604.4 | seat 17–19 in. | 17"-19" |
| 604.5.1 | side grab bar 42 in. min, 12 in. max from rear wall, 54 in. min extent | 42" / 12" / 54" |
| 604.5.2 | rear grab bar 36 in. min, 12 in. and 24 in. legs | 36" / 12" / 24" |
| 604.7 | dispenser 7–9 in. in front of bowl, 15–48 in. AFF | 7"-9", 15", 48" |
| 606.3 / 306.3 / 306.2 | lav rim 34 in.; knee 27 in. / 8 in.; toe 9 in. / 17–25 in. | all match |
| 603.3 | mirror bottom 40 in. max AFF | 40" MAX |
| 602.4 / 602.5 | DF spout 36 in. max AFF, 5 in. max from front edge | 36" / 5" |
| Table 404.2.4.1 | all six approach conditions + the "+12 in. closer and latch" rule | **exact match** |
| 703.2.5 / 703.3.2 / 703.4.1 | 5/8–2 in. characters; Braille 3/8 in. below; 48–60 in. AFF | all match |
| 303.2 / 303.3 | ≤1/4 in. vertical; 1/4–1/2 in. beveled 1:2 | 1/4", 1/2", 1:2 |

One nuance: `703.4.1` measures the 48 in. minimum to the baseline of the lowest **tactile character**;
the G-3 note measures it to the baseline of the lowest line of Braille, which sits lower — more
conservative, compliant either way.

## UL U465 verified against the listing text on A-11

A-11 is **live text, not a raster** (33 k characters), so W1 was checked item by item:

| U465 item | Requirement | A-2 / A-10 |
|---|---|---|
| Item 1 | runners 3-5/8 in. deep min, fasteners 24 in. OC max | pins @ 24" and 6" from ends |
| Item 2 | studs 3-5/8 in. deep min, 24 in. OC max, min 25 MSG | 6" studs @ 16" OC, 18/20 GA |
| Item 4 | gypsum board 5/8 in., one layer each side | 5/8" Type X each side |

Item 4 also sets screw spacing at **8 in. OC on edges, 12 in. OC in the field, joints vertical and
staggered** — not repeated on A-10, but A-2 general note 2 binds all rated assemblies to the listing.
Field-meeting item, not a plan correction.

A-12 is the raster sheet (42 images, ~206 MPx, 68 words of live text). The six firestop design
numbers were readable — `W-L-1054`, `W-L-2244`, `C-AJ-1421`, `BW-S-0006`, `HW-D-0256` — but the
parameters inside each system were not.

## Electrical recomputation (E-3)

Per-phase kVA column resummed:
`0.72 + 9.38 + 6.11 + 7.08 + 1.57 + 4.00 + 3.86 + 3.33 + 2.79 + 4.50 + 4.50 + 3.75 + 3.75 + 2.00 + 1.65 + 0.57 + 2.80 = 62.36 kVA`
→ `62,360 / (208 × 1.732) = 173 A` on a **250 A** MLO panel off a **1000 A / 65 kAIC** service. 69 percent loaded.

Circuit 14, "MAT STUDIO & EMERGENCY LIGHTING", reads like a violation and is not: `NEC 700.12`
requires battery unit equipment to share the branch circuit serving normal lighting in the same area,
ahead of local switching.

## Build pipeline notes (PyMuPDF 1.28.2)

- **`<colgroup>` is ignored** by the Story HTML engine. `table-layout:fixed` plus colgroup does
  nothing. Column widths only take effect as the **`width` attribute on `<th>`**:
  `<th width='47%'>`. `style='width:47%'` on `<th>` is also ignored.
  Fix applied as a regex post-processor that lifts colgroup widths onto the header cells:

```python
def fixtables(html):
    def repl(m):
        widths = re.findall(r"width:([\d.]+)%", m.group(1)); row = m.group(2); i = [0]
        def th(mm):
            w = widths[i[0]] if i[0] < len(widths) else None; i[0] += 1
            return f"<th width='{w}%'{mm.group(1)}>" if w else mm.group(0)
        return re.sub(r"<th([^>]*)>", th, row)
    return re.sub(r"<colgroup>(.*?)</colgroup>\s*(<tr>.*?</tr>)", repl, html, flags=re.S)
```

- `insert_htmlbox` returns `(spare_height, scale)`. **`scale < 1.0` means the content overflowed and
  was shrunk to fit.** Print it and repaginate rather than shipping silently scaled text — that is
  the only overflow signal available.
- Register-driven build: one Python list of finding dicts
  (`fid, pg, anchor, hit, disc, status, sev, title, checked, result, code, action, body`) drives the
  on-drawing markers, the per-sheet rail cards, and the summary register tables. Single source, no
  drift between the sheet markup and the register.
- Legend moved from a full 742 pt panel on every sheet to a **compact 233 pt strip**, with the full
  "how to read" content promoted to its own front-matter page. Without that change the G-0 rail
  (9 cards) overflowed the 1728 pt sheet height.
- Save flags that keep a 24-sheet vector set under the 20 MB device-commit ceiling:
  `save(garbage=4, deflate=True, deflate_images=True, deflate_fonts=True, clean=True, use_objstms=1)`
  → 18.5 MB.
- Sheet geometry unchanged from v4: `set_mediabox(Rect(-702, 0, 2592, 1728))` applied **before** any
  drawing, so all later coordinates shift by +702 automatically. Sheet becomes 3294 x 1728.
- Scale: `18.005 pt/ft` from the `/Measure` dictionary on the life-safety viewport (`C = 0.05554`
  ft/pt, 1/4" = 1'-0").

## Related

- [[2026-08-20 - UpCodes Output Teardown and Head-to-Head]]
- [[2026-08-20 - Build Plan - Code Sources, Vector PDF Pipeline, Frontend Stack]]
- [[2026-08-19 - FBC Code Reviewer - Platform and Architecture Analysis]]
