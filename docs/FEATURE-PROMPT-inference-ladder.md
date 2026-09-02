---
title: Claude Code Prompt — The inference ladder, and measuring off layerless geometry
type: runbook
tags:
  - code-review
  - florida-building-code
  - extraction
  - geometry
  - pdf-pipeline
  - source/cowork
status: accepted
created: 2026-09-02
source-session: Meridian Drafting
---

# Claude Code prompt — stop abstaining on values the sheet is holding up in front of us

`CLAUDE.md` carries the standing rules and outranks anything here that contradicts
it. Read *Refining the engine* there before starting: it names the five
properties that have to survive this work.

**Status.** Written as a proposal, because `fbcreview/` was outside the editable
boundary. That boundary was lifted on 2026-09-02 and this is now the standing
plan for the extraction and geometry work — §11 is the order to build it in.
None of it is implemented yet.

The one rule that did not move: **the review path makes zero LLM calls.** See §0.

---

## 0. On "the AI model"

The request that prompted this asked for updates to "the AI model". There is no
model in the review path and this proposal does not add one. That is not
pedantry about wording — it is the constraint that makes everything below
*harder* and also makes it worth having:

- A model that reads a drawing and says "about 4,200 sf" cannot show its work,
  cannot be regression-tested against a golden file, and cannot be defended to a
  plans examiner who asks where the number came from.
- Everything proposed here produces a number **with a derivation**: these paths,
  at this scale, closed into this polygon, area by the shoelace formula. That is
  a claim somebody can check on the sheet in thirty seconds.

There are two places a model legitimately could sit, and neither is the review
path: authoring the lexicons in §4 offline (a human still commits the file), and
the feedback assist that already exists in `webapp/assist.py`. If the zero-model
property is ever to be relitigated, that is a separate decision and belongs in
`CLAUDE.md`, not smuggled in through an extractor.

---

## 1. The problem, measured

The diagnosis printed on the workspace today —

> `DECL.BUILDING_AREA` — neither the drawings nor the declaration state this

— is, on real sets, frequently **false**. The drawings state it. The extractor
cannot see it. Three reproductions, run against the current `main`:

### 1.1 The area is on the sheet, in a table

`SUB1- JSP_Naples,FL_MEP.pdf` sheet M.001 prints an `OCCUPANCY CALCULATION`
table containing `LOBBY/RECEPTION 400 SQ. FT.` and `STUDIO AREA 1300 SQ. FT.`
Against that text:

```
building_area_sf   -> NO MATCH
total_area_sf      -> NO MATCH
```

`fbcreview/reconcile.py::_PATTERNS["building_area_sf"]` requires the literal
phrase `BUILDING AREA`, `AREA PER FLOOR` or `SQUARE FOOTAGE PER FLOOR`. A room
schedule whose rows sum to the tenant area matches none of them. Nothing in the
engine sums a table.

### 1.2 The code edition is on the sheet, twice

The same sheet prints `VENTILATION REQUIREMENTS PER 2023 FBC-MECHANICAL 8TH
EDITION` and `ALL WORK SHALL COMPLY WITH APPLICABLE SECTIONS OF 2023 FBC 8TH
EDITION`. Against `_PATTERNS["code_edition"]`:

```
MISS   <- VENTILATION REQUIREMENTS PER 2023 FBC-MECHANICAL 8TH EDITION, TABLE …
MISS   <- ALL WORK SHALL COMPLY WITH APPLICABLE SECTIONS OF 2023 FBC 8TH EDITION
MATCH  <- FLORIDA BUILDING CODE 2023, 8TH EDITION
```

The patterns require the words *Florida Building Code* spelled out. `FBC` — the
abbreviation in this product's own name — is not in the vocabulary. So
`CODE.EDITION_CURRENT` stood down on a set that states the edition twice, and
the register told the reviewer the set was silent.

### 1.3 On a single-discipline submittal, the area read never runs at all

`fbcreview/pipeline.py` scopes its area read to the general series:

```python
general = [s for s in facts.sheets if _series(s.code).endswith("G")]
facts.meta["area_g0_sf"] = _first_match(general, text, r"AREA:?\s*([\d,]+)\s*SF", …)
```

An MEP-only submittal — `M.001`, `M.101`, `M.201` … — has no sheet whose series
ends in `G`. `general` is empty, the search never executes, and the abstention
that follows is indistinguishable from one where the search ran and found
nothing.

### 1.4 Geometry is unreachable without a layer name somebody guessed

`fbcreview/rules/r_geometry.py` opens with `EGRESS_LAYER = "egress path"` and
`fbcreview/facts.py::PageGeometry` retains, per page, only
`{layer name: path count}` and a scale. **No geometry is kept.** A set plotted
without optional content groups — which is most sets that have been through a
plot-to-PDF or a flatten — reaches the rules as a page with zero layers and no
paths, and every measured rule abstains. The workspace already says this out
loud: *"this set carries no CAD layers at all"*.

That message ends with "Say what the layer is actually called in your standard",
which today is advice nobody can act on: there is no mechanism to say it.

---

## 2. What the abstentions are actually telling us

Ranked by how many rules each accounts for on the two reference sets, the causes
are not "the set is silent":

| Cause | Rules affected | Fixable how |
| --- | --- | --- |
| The value is stated in a phrasing not in `_PATTERNS` | most `DECL.*`, `CODE.EDITION_CURRENT` | §4 — lexicon instead of literals |
| The value is in a table, not a sentence | `DECL.BUILDING_AREA`, occupancy loads | §5 — read and sum tables |
| The search was scoped to a discipline the set does not carry | area, risk category | §4.4 — scope by content, not by sheet code |
| The sheet is vector but carries no layers | every `MEASURE.*` | §6 — classify geometry, don't ask for a layer name |
| The sheet prints more than one scale | every `MEASURE.*` on multi-view sheets | §7 — per-view scale attribution |
| The set genuinely does not say | a real minority | nothing. Abstain, correctly. |

The last row is the only one where today's message is honest. The proposal is to
shrink the other five until the last row is most of what is left, and to make the
message for each of the others say what is actually true.

---

## 3. The principle: a resolution ladder

Every declaration field is resolved by the **cheapest source that can support
it**, and the tier that answered is carried with the value forever.

| Tier | Source | Confidence | Exists today |
| --- | --- | --- | --- |
| **T0** | Declared by the applicant | as asserted | yes |
| **T1** | Stated on a sheet, explicitly labelled | HIGH | yes — `_PATTERNS` |
| **T2** | Stated on a sheet, semantically labelled | HIGH | **no** |
| **T3** | Tabulated — a table whose rows sum to it | MEDIUM | **no** |
| **T4** | Measured off vector geometry | LOW, banded | partly, layers only |
| **T5** | Measured off a rebuilt raster | LOW, banded | no — deferred, §9 |

**"After first assessing whether it is necessary" is the whole design.** A tier
only runs when every tier above it returned nothing for that field. Concretely:

- If the sheet says `BUILDING AREA: 4,200 SF`, T1 answers and nothing else runs.
  No polygon is traced, no table is summed, and the finding cites the sheet.
- If nothing says it, T4 measures the footprint and the finding says *measured*,
  with a band, and names the paths it traced.

This ordering is not just an optimisation. It is what stops a measured estimate
from ever displacing a stated fact — which is the failure mode that would make
this feature worse than the abstention it replaces.

### 3.1 Basis is a first-class property, and it never launders

`fbcreview/confidence.py::Evidence` already carries `source`, `confidence` and
`note`. Add one field:

```python
BASIS = ("declared", "stated", "tabulated", "measured")

@dataclass
class Evidence:
    value: Any
    source: str
    confidence: str = HIGH
    note: str = ""
    page: Optional[int] = None
    basis: str = "stated"          # NEW
    band: Optional[Tuple[float, float]] = None   # NEW — measured values only
```

Then, as a hard rule enforced by a test:

1. A finding whose inputs include a `measured` value **may not** report at
   `CRITICAL` or `HIGH`. It reports at `MEASURED`, or it reports the
   disagreement as a question rather than a violation.
2. A finding whose inputs include a `measured` value **must** print the band and
   the derivation in its `result` text. "4,180–4,320 sf, traced from the
   footprint on M.101 at 1/4" = 1'-0"" — never a bare `4,250 sf`.
3. `reconcile.py` must never treat a `measured` value as one of the two
   independent sources in a declared-vs-drawn comparison. Measuring the drawing
   and then congratulating the drawing on agreeing with the measurement is
   circular; `DECL.*` rules keep abstaining, correctly, and say *why* — "the
   area was estimated from the geometry rather than read, so there is nothing
   independent to check the declaration against."

Point 3 is worth dwelling on. It means this feature deliberately does **not**
close `DECL.BUILDING_AREA` by measurement. What it closes is
`HEIGHT_AREA.TABLE_506_AREA` and `EGRESS.OCCUPANT_LOAD_COMPUTED` — the rules that
*consume* an area — which is where the value was actually wanted.

---

## 4. T2 — lexicon-driven extraction, replacing the literal patterns

### 4.1 The shape

`_PATTERNS` becomes one matcher over a data-file field spec. Roughly:

```python
@dataclass(frozen=True)
class FieldSpec:
    key: str
    heads: Tuple[str, ...]        # AREA, FOOTAGE, SF
    qualifiers: Tuple[str, ...]   # BUILDING, FLOOR, GROSS, OVERALL, TENANT, SUITE
    disqualifiers: Tuple[str, ...]  # SITE, LOT, PARCEL, PARKING, IMPERVIOUS,
                                    # LANDSCAPE, OPEN SPACE, SETBACK
    units: Tuple[str, ...]        # SF, S.F., SQ FT, SQ. FT., SQUARE FEET, FT2
    shape: str                    # "number" | "enum" | "dimension" | "roman"
    window: int = 6               # tokens between the label and the value
    value_first_ok: bool = True   # "4,200 SF BUILDING AREA"
```

The matcher tokenises the page text once (it is already extracted), then for each
candidate number carrying one of `units`, scores the surrounding window:

```
score = 2·(qualifier hits) + 1·(head hits) − 4·(disqualifier hits) − distance/window
```

Candidates below a floor are discarded. The **best** candidate is returned with
its margin over the runner-up. A narrow margin is itself information: two
plausible readings that disagree is a `CONFLICT`, not a coin toss, and should
abstain with "the sheet states two different areas — 4,200 sf and 15,376 sf" and
name both. That is a far better abstention than today's.

### 4.2 Vocabulary is data, not Python

Move the whole thing to `fbcreview/codes/lexicon.py` alongside `fbc2023.py`, in
the same hand-transcribed, reviewed style the code corpus already uses. The
corpus is the moat; the lexicon is part of it. It is committed, diffable and
tested — not learned, not fetched, not inferred at runtime.

The `code_edition` entry alone fixes §1.2:

```python
heads      = ("EDITION", "FBC", "FLORIDA BUILDING CODE")
qualifiers = ("FLORIDA", "BUILDING", "MECHANICAL", "PLUMBING", "FUEL GAS",
              "EXISTING BUILDING", "ACCESSIBILITY")
```

so `2023 FBC-MECHANICAL 8TH EDITION` resolves, and does so citing the sheet.

### 4.3 An office alias table, which the diagnosis already promises

The same file carries layer aliases:

```python
LAYER_ALIASES = {
    "egress_path": ("egress path", "e-path", "a-egrs", "life safety",
                    "ls-path", "travel", "a-flor-evac"),
}
```

and `webapp/` gains a way for an office to extend it for their own standard —
which is the thing `abstentions.py::diagnose` already tells people to do and
which they currently cannot. That is a `webapp/` change and therefore squarely
in scope for this repo's own ownership boundaries.

### 4.4 Scope by content, not by sheet code

Delete the `endswith("G")` filter. Search every sheet; prefer a hit on a general
sheet when there is one, by scoring, not by exclusion. §1.3 disappears.

### 4.5 What this must not do

It must not become a fuzzy matcher that finds an area on every sheet. The
disqualifier list is the safety mechanism and it is load-bearing: `SITE AREA`,
`LOT AREA`, `PARKING AREA` and `IMPERVIOUS AREA` are all printed on real permit
sets, all in square feet, and all wrong. A test fixture should carry every one of
them on one page and assert the extractor returns the building area and not the
site area.

---

## 5. T3 — read the tables that are already being parsed

`page.find_tables()` is already used by `fbcreview/extract/schedules.py` for the
door, RTU and panel schedules. Nothing uses it for area or occupancy.

Add a table reader that, for a table whose header row matches a `FieldSpec`'s
unit vocabulary:

1. identifies the value column by header (`AREA`, `SQ. FT.`, `SF`),
2. identifies the label column as the first non-numeric column,
3. sums the value column, excluding rows whose label hits a disqualifier
   (`TOTAL`, `SUBTOTAL` — to avoid double counting — and any site-area row),
4. returns `Evidence(sum, basis="tabulated", confidence=MEDIUM)` **naming every
   row it summed**.

On the JSP sheet that yields `400 + 1300 = 1,700 sf`, sourced as "sum of the
OCCUPANCY CALCULATION table on M.001: LOBBY/RECEPTION 400, STUDIO AREA 1300".
That is checkable in five seconds by a human looking at the sheet, which is the
standard every number in this product is held to.

**Deliberately MEDIUM, never HIGH.** A room schedule sums to net area, not gross;
whether that is the area a code table wants depends on the table. Rules consuming
a tabulated area must say which they were given.

---

## 6. T4 — measuring a footprint with no layers

This is the substantial engineering, and it is worth being honest that it is the
part most likely to be wrong.

### 6.1 What a layerless path still carries

Confirmed against `get_drawings()` on a real page — every path dict carries:

```
items, rect, layer, width, color, fill, fill_opacity, stroke_opacity,
dashes, closePath, even_odd, lineCap, lineJoin, seqno, type
```

Layer is the only one that is `None` on a flattened plot. **Stroke width, colour,
dash pattern, fill and draw order all survive**, and architectural drafting
encodes meaning in every one of them. That is the opening.

### 6.2 The pipeline

1. **Segment the sheet into views.** Cluster path bounding boxes (DBSCAN-ish on
   centres, or a simpler gap-based split, which is enough). Discard the title
   block: it is the cluster containing the sheet-number text, and it hugs a page
   edge. Discard the sheet border: a rectangle within a few points of the
   MediaBox. What remains are the drawn views.

2. **Pick the plan view.** The largest remaining cluster whose aspect ratio is
   sane and which contains the most geometry. On a sheet with several, prefer the
   one whose nearest text label matches `FLOOR PLAN` / `LIFE SAFETY PLAN`. If two
   are equally plausible, **abstain** — do not pick.

3. **Find the wall weight.** Histogram `width` across the view. Architectural
   convention puts exterior walls at the heaviest weight; the histogram is
   usually strongly multi-modal. Take the heaviest mode holding at least a
   threshold share of total path length. **If the histogram is unimodal — as it
   is on a plot where every line is 1.0 pt — this signal is absent and the tier
   abstains saying exactly that.** Do not proceed on a guess.

4. **Collapse wall pairs to centrelines.** A wall is two near-parallel segments
   separated by a plausible wall thickness (4–14 in at the view's scale). Pair
   them, take the midline. This is the standard floor-plan vectorisation step and
   it is where most of the tuning will live.

5. **Build a planar graph and take the outer face.** Snap endpoints within
   tolerance, build the graph, extract faces, take the one with the largest area
   that is not the whole view. Area by the shoelace formula, divided by
   `scale²`.

6. **Gate it, hard.** Return nothing unless *all* hold:
   - the page scale is `HIGH` confidence (§7),
   - the polygon has ≥ 4 vertices and is simple,
   - its area is between 2 % and 60 % of the view's bounding box (below is a
     detail, above is the border),
   - its aspect ratio is under ~8:1,
   - it does not coincide with the sheet border or the title block,
   - the closed loop used ≥ 80 % heavy-weight segments.

7. **Report a band, not a number.** Wall centreline vs face-of-stud vs face-of-
   finish differ by the wall thickness all the way round: on a 60 × 70 ft
   building with 8 in walls that is roughly ± 1.5 %. Add scale-label rounding.
   Publish `(low, high)` and let the rules reason about the band. A rule that
   would fire on the midpoint and not on the low end must not fire.

### 6.3 Keeping the geometry at all

`PageGeometry` currently discards paths. It needs to retain, per view, the
segments that survived step 4 — or the pipeline must re-open the document, which
`r_geometry.py` already does (`pymupdf.open(f.source_path)` inside the rule) and
which is the wrong shape. Extraction should extract; rules should be pure over
facts. Fixing that is a prerequisite and is a real cost.

### 6.4 Honest expectation

On a clean CAD plot with a line-weight hierarchy this should land within a few
percent. On a plot that flattened every weight to 1.0 pt it will correctly refuse
at step 3. On a heavily-hatched plan, or one where the exterior wall is drawn as
a filled poché rather than two strokes, step 4 will need a fill-aware branch.

**Estimate roughly half of real vector sets, and refuse cleanly on the rest.**
Anything claiming better than that before it has been run against the two
reference sets and a dozen more should be disbelieved.

---

## 7. Per-view scale attribution — a prerequisite, and independently worth it

`fbcreview/extract/scale.py` abstains for the whole page whenever the sheet
prints more than one scale label:

```python
return Evidence.abstain(src, f"sheet prints {len(labels)} different scales …
    geometry cannot be attributed to one without view-boundary analysis")
```

The docstring names the missing piece. §6.1 builds it. Once views are segmented,
each printed scale label attaches to the view it sits under or beside, and each
view carries its own scale.

This is the single highest-leverage change in this document. A typical
architectural sheet prints three or four scales, so today's rule abstains on
almost every real multi-view sheet — including for the egress measurement that
already works. Do this first; it improves `MEASURE.EGRESS_EXTENT` on its own,
before any of §6 exists.

---

## 8. Where else the ladder applies

| Field | T2 gains | T3 gains | T4 gains |
| --- | --- | --- | --- |
| `building_area_sf` | `GROSS FLOOR AREA`, `TENANT AREA`, value-first | sum a room/occupancy table | trace the footprint |
| `total_area_sf` | `OVERALL`, `AGGREGATE` | sum across storeys | sum per-floor footprints |
| `code_edition` | `FBC`, discipline-qualified editions | — | — |
| `stories` | `X STORIES ABOVE GRADE` | count storey rows in a table | count distinct floor-plan views |
| `height_ft` | `MEAN ROOF HT`, `T.O. PARAPET` | — | measure an elevation view against its own scale |
| `occupant_load` | — | sum the occupancy table's people column | area ÷ the code's load factor |
| egress travel | — | — | §6 paths without a layer name; §7 alone helps today |
| corridor width | — | — | perpendicular distance between paired wall centrelines |

Occupant load is the quiet win: FBC Table 1004.5 gives a load factor per
occupancy, so a tabulated or measured area yields a computed load that can be
compared against the one printed on the sheet. That is a genuine independent
check — the sort of thing that justifies the whole product — and it needs an
area, which is what all of this is about.

---

## 9. Explicitly deferred: T5, raster

Measuring off a rebuilt scan is possible — `webapp/convert.py` already traces
linework — but traced lines carry no weight hierarchy, so step 3 of §6 has
nothing to work with, and everything downstream degrades. Do not attempt it until
§6 is proven on vector sets. The honest message for a scanned set stays "supply a
set plotted from CAD".

---

## 10. Acceptance — hard numbers, not impressions

Nothing ships on "it seems better".

1. **No regression.** `tests/test_regression.py` on both reference sets: every
   finding present before is present after, at the same severity, with the same
   citation. A measured value must not change a stated finding.
2. **The three reproductions in §1 resolve**, each citing the sheet, with a
   fixture per case in `tests/`.
3. **Precision over recall on the disqualifiers.** A page carrying `SITE AREA`,
   `LOT AREA`, `PARKING AREA`, `IMPERVIOUS AREA` and `BUILDING AREA` returns the
   building area. This is the test that matters most: a confidently wrong area is
   worse than an abstention, because a reviewer will act on it.
4. **The estimator is calibrated against ground truth.** On every reference sheet
   that states an area *and* can be measured, report the error distribution.
   Ship only if the median absolute error is under 5 % and no sample exceeds
   15 %; otherwise the gates in §6.2 are too loose and the tier stays off by
   default behind a review option.
5. **Refusals stay clean.** On a set with a unimodal stroke-width histogram, T4
   returns nothing and the abstention reason names the reason — not a number.
6. **Basis is never laundered.** A test asserts no `CRITICAL` or `HIGH` finding
   has a `measured` input, and that `reconcile.py` never uses a `measured` value
   as the drawn half of a declared-vs-drawn comparison.
7. **The review path still makes zero model calls.**
   `tests/test_training.py::test_no_model_call_is_reachable_from_the_review_path`
   continues to pass unchanged.
8. **Runtime.** The two-second budget for a 35-sheet set is a product claim.
   §6 runs only on the pages a rule actually asks about, and only when tiers
   above returned nothing. Measure it; if the ladder pushes a 35-sheet set past
   ~5 s, cache per-page view segmentation and reconsider.

---

## 11. Phasing

Each phase is independently shippable and independently valuable. Stop after any
of them without leaving the engine half-built.

| Phase | Work | Unlocks |
| --- | --- | --- |
| **1** | §7 per-view scale attribution | `MEASURE.EGRESS_EXTENT` on multi-view sheets, today |
| **2** | §4 lexicon + §4.4 scoping | §1.1 partly, §1.2 and §1.3 fully. Pure text; no geometry risk |
| **3** | §5 table reading | §1.1 fully; occupant-load cross-check |
| **4** | §3.1 basis and band on `Evidence` | the safety rails, before anything can be measured |
| **5** | §6 footprint tracing, off by default behind a review option | the estimate |
| **6** | §4.3 office layer aliases in `webapp/` | makes the existing diagnosis actionable |

Phases 1–3 are the majority of the value and carry almost none of the risk.
Phase 5 is where the interesting work is and where it can go wrong.

---

## 12. Do not

- **Do not add a model call to the review path.** Not for extraction, not for
  "just this one hard field", not behind a flag. This is the one property
  `CLAUDE.md` still calls non-negotiable, and it survived the boundary change
  that made the rest of this document actionable.
- **Do not let a measured value reach a `CRITICAL` finding**, silently become a
  declared one, or serve as the drawn half of a declared-vs-drawn check.
- **Do not widen `_PATTERNS` in place** as a shortcut. The literals are the
  problem; adding a fourth literal to a list of three is the same bug with a
  longer list.
- **Do not weaken an abstention into a guess.** If the gates fail, abstain — but
  abstain with the *specific* reason, which is itself most of what this
  document buys.
- **Do not re-baseline the reference sets to make a change look clean.** A
  finding that moved severity or citation is a result to explain, not noise to
  absorb. `tests/` is the only thing that tells you a refinement did what you
  meant and nothing else.

---

## 13. Report back with

- The error distribution from §10.4, as a table, per reference sheet.
- The abstention count on both reference sets, before and after, broken down by
  the `webapp/abstentions.py` class — the interesting number is not the total but
  how many moved from `extraction` to `absent`, because that is the count of
  abstentions that were previously lying.
- The runtime delta on a 35-sheet set.
- Which of the six phases you stopped after, and why.
