---
title: Claude Code Prompt — Reading Raster Code Tables
type: runbook
tags:
  - code-review
  - florida-building-code
  - ocr
  - pdf-pipeline
  - source/cowork
status: draft
created: 2026-08-25
source-session: Meridian Drafting
---

# Claude Code prompt — make the engine read raster code tables

Open Claude Code in `C:\Antigravity\fbc-review` and paste everything below the line.
`CLAUDE.md` carries the standing rules and outranks anything here that contradicts it.
Reference implementations are in `docs/reference/ocr/` — read them before writing code.

---

## The problem, measured

The deployed engine was run against the ITEC Alico Park set (`docs/reference/ITEC Alico
Park Findings.md`) with `convert_raster` on. Measured result:

```
webapp/pdfkind.py     correctly classifies 11 sheets as carrying raster regions
webapp/convert.py     OCRs them and recovers 27,826 characters
fbcreview             produces 3 findings, 28 abstentions
                      facts.code_data  = 0 rows
                      facts.schedules  = 0
```

**27,826 characters were recovered and essentially all of them were thrown away.** OCR is
not the bottleneck. The bottleneck is that the recovered text is not usable by any spatial
extractor, and the extractors that do not need geometry are looking for a table shape this
office does not draw.

For comparison, a prototype that fixes the three causes below produces **7 findings and 12
abstentions on the same file**, including the two that matter most on this set — the
occupant-factor column error and the single-occupancy/Table 508.4 contradiction.

## Root cause 1 — `pdfocr_tobytes()` does not preserve line geometry

This is the big one and it is measurable in one command.

`webapp/convert.py::_ocr_region` renders the region, calls `Pixmap.pdfocr_tobytes()`, then
maps the resulting page's line bboxes back onto the sheet. The mapping arithmetic is
correct — measured `sx = 0.9999`, `sy = 0.9998`. The input to it is not:

```
region  x 858..2351, y 707..1362   (1493 x 655 pt)
render  8298 x 3640 px at 400 dpi
ocr_page.rect  1493.6 x 655.2      <- correct size

OCR line bboxes coming back:
  [6, 0, 21, 8]      'Saw'
  [27, 0, 28, 8]     '2'
  [38, 0, 181, 8]    'Me YJSZY AINE Dar'
  [136, 0, 172, 6]   'ae 8 Wee'
```

**Every one of the 45 lines comes back with `y0 = 0` and `y1 ≈ 6-8`, inside a region 655 pt
tall.** The x positions survive; the y positions are gone. `pdfocr_tobytes()` builds a
searchable-text overlay for full-text search, where line geometry does not matter — it is
the wrong tool for structured extraction.

Consequence, measured on the converted file: of the 988 words on the rebuilt G-002, **one**
falls in the code block's x-range. The recovered code text piles into roughly
`x 66..230, y 1609..1698` — a corner of the sheet. `blocks._rows()` clusters rows by y.
With every line at the same y there are no rows, so `code_data_block()` returns 0 and
`find_schedule()` returns nothing.

Prototype, same page, using `pytesseract.image_to_data` instead:

```
558 words, 122 distinct y positions, spanning 91% of the region's height
```

**Fix: stop using `pdfocr_tobytes()` for extraction.** Use `pytesseract.image_to_data`
(`output_type=Output.DICT`), which returns per-word `left/top/width/height/conf`. Emit
words as PyMuPDF's own tuple shape — `(x0, y0, x1, y1, text, block, line, word)` — in
displayed page coordinates. Then every existing extractor works on a raster sheet with no
branch and no duplicated logic. **OCR becomes a text source, not a second pipeline.**

Keep `pdfocr_tobytes()` only if you also want a human-searchable output PDF. It must not
feed the fact model.

## Root cause 2 — the region is OCR'd as one block, and it is two tables

A code-analysis block is two or three tables printed side by side. G-002 is two. Handing
tesseract the whole 1493-pt-wide region gives it a layout it cannot resolve, which is why
the recovered words above are `'Saw'` and `'Me YJSZY AINE Dar'` rather than text.

Even with perfect geometry, row clustering across the full width pairs `OCCUPANCY:` from
the left table with `CONSTRUCTION TYPE: TYPE II-B` from the right one — every label against
the wrong value.

**Whitespace-gutter detection does not work here.** The tables are ruled: every column of
pixels contains border ink. Measured on G-002, the minimum column ink never approaches
zero. The separator is the opposite signal — a nearly full-height vertical rule:

```python
ink  = (grey < 160).sum(axis=0)
tall = np.where(ink > 0.55 * h)[0]     # major column separators
```

On G-002 that finds the divider at x-fraction 0.460-0.469 and yields two clean strips.
Run structure detection at 100 dpi; only the OCR pass needs 300.

Carry the strip index in field 5 of the word tuple — where PyMuPDF puts its own block
number, which is useless here — and cluster rows on `(strip, y)`. Two tables at the same y
are two rows, not one.

## Root cause 3 — the classifier hands over tiles, not tables

`pdfkind` reports **3 regions** on G-002 and 2 on A-101. Those are not three tables; they
are the horizontal bands AutoCAD's PDF driver sliced one plotted region into. OCRing each
band independently cuts words at the seams and stops any row from spanning the table.

**Fix: coalesce touching region rects before OCR.** On G-002 the three tiles merge into one
`[858, 52, 2351, 1694]`; on A-101 the tiles merge into two real tables.

## Root cause 4 — the parsers expect a table shape this office does not draw

Separate from OCR, and it would still bite on a vector sheet.

- `blocks.code_data_block()` requires a parenthesised section number **on every row** —
  `MAX TRAVEL DISTANCE (1017.2): 250 LF`. Phoenix Associates put the citation in a **banner
  above each table**: `TABLES 504.3, 504.4 & 506.2 < > FLORIDA BUILDING CODE 7TH EDITION`,
  then bare `LABEL: value` rows underneath. The citation is present and is still the most
  stable token on the sheet — it is one level up. Nothing reads it today.
- `blocks.labelled_values()` takes the value to be the trailing run of numeric / `YES` /
  `NO` / dimensional tokens. On this block most values are words: `BUSINESS`,
  `TYPE II-B NON-COMBUSTIBLE NON-RATED`, `MULTIPLE - SEPARATED PER TABLE 508.4`. It returns
  nothing for all of them.
- One visual row often carries **two** label/value pairs:
  `OCCUPANCY:  BUSINESS      MIXED OCCUPANCY?  NO`. Splitting at the single widest gap
  yields `OCCUPANCY = BUSINESS MIXED OCCUPANCY?` — second answer lost, first corrupted.

## What to build

### Phase 1 — a faithful OCR word source

New `fbcreview/extract/ocr.py`. A working implementation is in `docs/reference/ocr/ocr.py`;
port it rather than reinventing it, and keep its comments — they record why each decision
is what it is.

- `raster_regions(page)` — placed images in displayed coordinates, above a size floor,
  **merged** (root cause 3).
- `column_strips(page, clip)` — split at full-height vertical rules (root cause 2).
- `ocr_page(page, dpi=300, min_conf=40)` — per-word tuples in page coordinates, strip index
  in field 5, tesseract confidence carried alongside.
- **Confidence gating.** Words below `min_conf` are dropped, never guessed at. OCR-derived
  values are never HIGH confidence. A smudged value must produce an abstention exactly as a
  missing value would — that is the never-guess rule applied to a new input, not an
  exception to it.
- **Disk cache keyed on the document's SHA-256, page, dpi and threshold.** Measured: 395 s
  for 14 raster sheets cold, 23 s warm. Re-running a review or re-rendering markup after a
  rule change has to be cheap. `FBC_OCR_CACHE` for the path, `FBC_NO_OCR=1` to disable the
  whole raster path for A/B measurement.

Rotation: work entirely in displayed coordinates. `get_pixmap(clip=)`, `get_text("words")`
and `get_image_rects()` all agree there. Seven ITEC sheets carry `/Rotate 270` and none of
them is raster, so this path is currently untested against rotation — write the test.

### Phase 2 — cell-grid table reconstruction (the new work)

This is the part the prototype does **not** do, and it is what converts the remaining
abstentions into findings.

Detect the full rule grid, not just the column separators, and OCR **each cell
independently**. Measured feasibility on the A-101 egress table
(`region [1435, 40, 2211, 856]`, rendered at 150 dpi):

```
vertical rules   (>50% of height):  4  at x-fractions 0.001, 0.613, 0.822, 0.998
horizontal rules (>50% of width) : 33
=> a 32 row x 3 column cell grid is recoverable
```

Three columns at those fractions are `LABEL | PROVIDED | REQUIRED` — exactly the
`CodeDatum(label, provided_raw, required_raw)` shape the rule corpus already consumes.
Thirty-two rows of it.

Build `fbcreview/extract/grid.py`:

1. Render the merged region at `STRUCTURE_DPI` (100-150 is enough).
2. Ink profile along both axes; take runs above `RULE_MIN_FRACTION` of the perpendicular
   dimension as rules.
3. Build the cell matrix from consecutive rule pairs. Reject grids under 3 rows or 2
   columns — that is a border, not a table.
4. OCR **each cell** as its own image at 300 dpi with `--psm 7` (single line) or `--psm 6`.
   A cell is unambiguous: no layout analysis to get wrong, no neighbouring column to fuse
   with. This is what recovers the values the prototype lost — the `152` total occupant
   load in its narrow right-hand column, `MIXED OCCUPANCY? NO`, `HEIGHT 26'-4"`,
   `STORIES 1 / 4`.
5. Emit a `Schedule` when the grid has a header row, and `CodeDatum` rows when the columns
   resolve to label/provided/required.
6. Attach the citation from the **nearest banner above the grid** (root cause 4), so rows
   key on a section number even when the drafter did not put one on each line.

Cost control: cell-by-cell OCR is many more tesseract calls. Cache per region, run cells in
a thread pool, and skip cells whose ink fraction is under a floor — empty cells are common
and cost nothing to skip.

### Phase 3 — parsers that match the drawn shape

Port `docs/reference/ocr/formblocks.py`:

- `cluster_rows(words)` keyed on `(strip, y)`.
- `split_pairs(row)` — every `label: value` pair in a row, splitting at colons and the `?`
  drafters use in their place, with the widest gap between consecutive marks deciding where
  one value ends and the next label begins.
- **Keep the unanswered ones.** A mark on the last token counts. A label whose answer could
  not be read is not the same as a label that is not there: `MIXED OCCUPANCY` on G-002
  comes back with an empty value and `unanswered=True`, so a rule abstains with a precise
  reason instead of inferring an answer from the label text. **In the prototype this
  single change removed a false VERIFIED** — the first version inferred "mixed occupancy"
  from a polluted string and passed a sheet that should have failed. Treat that as the
  cautionary tale it is.
- `banners()` / `banner_for()` — citation banners per strip, so every row inherits the
  section its table is drawn under.
- `clean()` — strip the `|`, `[`, `]` punctuation tesseract hallucinates from ruled cell
  borders. `[BUSINESS` and `BUSINESS` are the same value, and a comparison that does not
  know that reports a phantom conflict against the project declaration.

Then port `docs/reference/ocr/building.py`, which turns form rows into typed `Evidence`,
and note two constraints it encodes the hard way:

- **Height only counts when it is the BUILDING height.** Sheets are full of rows labelled
  HEIGHT — door, parapet, mounting, wall. Taking the first match gave a confident wrong
  answer of 16 ft against an actual 26 ft 4 in. Accept it only under a Table 504 banner or
  a label containing BUILDING.
- **Reject occupant rows whose "use" is one token repeated three or more times.** That is
  the signature of two columns read as one, and it invented a phantom space that shifted
  the computed occupant load from 107 to 108.

### Phase 4 — anchoring markup on a raster sheet

There is no searchable text to box, so findings carry `anchor_rect` in page coordinates.

**Do not look the anchor up by label text.** The prototype first anchored the occupant-load
findings with a text match on `"UNIT 101"`, which hit the unit **tag on the floor plan** —
same words, wrong place, marker drawn across the middle of the building. Carry the rect on
the extracted row itself and union them.

### Phase 5 — reconcile with the declaration

`fbcreview/reconcile.py` already exists. OCR-derived values must enter it as
`confidence=MEDIUM` at best, and the `CONFLICT` state must weight a declared value above an
OCR value when they disagree by less than the OCR confidence justifies. A phantom conflict
caused by `[BUSINESS` versus `BUSINESS` is worse than no conflict at all — it trains the
user to ignore the conflict register.

## Acceptance — hard numbers, not impressions

Run all of these and report the measured results.

- [ ] `pytest tests/ -v` green throughout. **The Sculpted Hot Pilates set must produce the
      same findings it does today** — that set is vector and nothing here should touch it.
- [ ] ITEC, `FBC_NO_OCR=1`: records the baseline.
- [ ] ITEC, raster path on: **more than 7 findings and fewer than 12 abstentions.** The
      prototype hit 7/12 without cell-grid reconstruction; Phase 2 is what beats it. If you
      do not beat it, say so and explain why rather than shipping.
- [ ] `facts.code_data` is **non-empty** on ITEC. It is 0 today. The A-101 egress grid alone
      should yield on the order of 30 rows.
- [ ] These five abstentions become findings or passes: `EGRESS.COMMON_PATH`,
      `EGRESS.TRAVEL_DISTANCE`, `EGRESS.DEAD_END`, `EGRESS.CORRIDOR_WIDTH`,
      `EGRESS.EXIT_COUNT`. All five have their required and provided values printed in the
      A-101 table; all five abstain today only because nothing reads it.
- [ ] `TOTAL OCCUPANT LOAD = 152` is recovered, and
      `EGRESS.OCCUPANT_LOAD_COMPUTED` reports **107 computed against 152 stated** rather
      than "no stated total readable".
- [ ] `MIXED OCCUPANCY? NO` is recovered, and
      `OCCUPANCY.SEPARATION_CONTRADICTION` cites it explicitly.
- [ ] Open the rendered PDF and **look at it**. Every marker on a raster sheet lands on the
      table row it is about, not on the drawing.
- [ ] Timing recorded, cold and warm, with the cache in place.

## Do not

- Do not feed `pdfocr_tobytes()` output into the fact model. Measured: it discards line y
  geometry, which is the one thing the extractors need.
- Do not let OCR-derived values reach HIGH confidence, and do not let a low-confidence word
  become a value. An unreadable cell is an abstention.
- Do not name a traced or OCR-derived layer anything a rule matches on. `convert.py`'s
  existing docstring on `TRACED_LAYER` gets this exactly right — keep that discipline.
- Do not modify `fbcreview/rules/` behaviour to make numbers move. New inputs may make
  existing rules fire; rewriting a rule to fire is a different thing.
- Do not edit `tests/test_regression.py` to make a phase pass.
- Do not OCR every page. Gate on raster coverage — measured floor of 3% keeps 21 of ITEC's
  35 sheets out of the OCR path entirely.

## Report back with

The before-and-after finding and abstention counts on ITEC, `len(facts.code_data)` before
and after, which of the five egress rules came alive, cold and warm timings, and any cell
in the A-101 grid the reconstruction could not read — that last list is the next piece of
work and it should not be silently absent.
