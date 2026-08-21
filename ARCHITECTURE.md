# Can this leave the chat?

**Short answer: yes for roughly 80 percent of it, and the 80 percent includes every
finding that mattered on the test set. The remaining 20 percent needs a model, but it
needs one *per new drawing vendor*, not per request — and it can be a cheap one.**

This document is the reasoning. The repo beside it is the proof: `run.py` opens the raw
permit set and reproduces six of the fourteen open findings and five of the verified
items with **zero model calls**, including the CRITICAL one.

```
$ python run.py "SCULPTED HOT PILATES - PERMIT SET 8.18.2026.pdf"

sheets      24  (G-0, G-1, G-2, G-3, A-1, A-2, A-3, A-4 …)
cad layers  200 preserved as PDF optional content
code data   6 cited rows extracted
schedules   DOOR AND FRAME SCHEDULE, ROOF TOP UNIT SCHEDULE, AIR BALANCE,
            OCCUPANT DENSITY, ELECTRICAL LOAD CALCULATIONS, PANEL SCHEDULE
doors       5
rules       11 registered, 0 model calls
scale       resolved on 13/24 pages (12 at high confidence)

── FINDINGS (12) ──
  CRITICAL  C-01    M-1   Outdoor-air capacity 531 CFM below this sheet's own requirement
  HIGH      H-02    G-0   Common path requirement understated on G-0
  HIGH      H-03    A-2   Door 104 is a 2'-8" leaf — under the 32 in. clear width the set requires
  MEDIUM    M-01    G-0   0.15 in/occupant egress factor is not supported by the documents
  MEDIUM    M-03    G-0   Risk Category III does not match the stated occupant load
  MEDIUM    M-04    G-0   Building area disagrees between sheets
  MEDIUM    M-03b   A-2   Door 105 is a 2'-8" leaf — under the 32 in. clear width the set requires
  VERIFIED  V-01    G-0   Travel distance limit — correct
  VERIFIED  V-02    G-0   Dead-end limit 20 LF — correct for Group A
  VERIFIED  V-03    G-0   Corridor width and the 1020.3 citation are both correct
  VERIFIED  V-DW    A-2   1 of 3 new doors meet the 32 in. clear opening
  VERIFIED  V-30    E-3   Connected load recomputed — the panel has substantial headroom

── ABSTENTIONS (1) ──
  EGRESS.EXIT_COUNT   exit count row not found in a code data block
```

---

## 1. The thing to be clear about first

What came out of the chat is **not a trained model and not "trained output."** There is no
weight file, no fine-tune, nothing that has to be re-derived by asking again. What was
produced was four artefacts, and all four are ordinary files:

| Artefact | Where it lives now | Nature |
|---|---|---|
| The **fact model** — what a permit set is, typed | `fbcreview/facts.py` | code |
| The **rule corpus** — ~50 checks with verified citations | `fbcreview/rules/*.py` | code |
| The **code corpus** — FBC requirements as structured data | `fbcreview/codes/fbc2023.py` | data |
| The **renderer** — markup, rails, registers | `/tmp/v5.py` → `fbcreview/render/` | code |

The expensive part of this engagement was *deciding what to check and confirming the
citations*. That is authoring work. It happened once. It does not repeat per request any
more than writing a compiler repeats per compilation.

---

## 2. The three tiers

### Tier A — pure computation, no model, per request

Everything here is already in this repo and already runs.

- **Scale resolution.** Two independent sources (`/Measure` viewport dictionaries and the
  printed `1/4" = 1'-0"` labels), intersected. See §3 — this one is more interesting than
  it looks.
- **Geometry.** Layer-filtered path extraction off the preserved CAD layers, polyline
  length in feet. The four measured egress paths.
- **Schedule extraction.** `find_tables` recovers the door schedule, RTU schedule, panel
  schedule and load calculations cleanly and completely.
- **Code-data-block extraction.** Positional row clustering for the unruled blocks.
- **Arithmetic re-computation.** Outdoor air line by line; panel kVA resummed to amps;
  fixture ratios; egress width factors; door clear width from leaf width.
- **Code lookups.** Every threshold comes from the code corpus, never from a literal in a
  rule.
- **Cross-sheet consistency.** Same quantity, different sheets, compared.
- **Rendering.** 100 percent deterministic. The 34-page marked-up PDF is a pure function
  of the findings list.

That is the bulk of the value, and it is the part that produced the CRITICAL finding.

### Tier B — small model, once per document, cacheable

One job only: **normalising an unfamiliar sheet into the fact model.** Concretely:

- Repairing merged table rows. The outdoor-air table on M-1 collapses two source rows into
  one cell (`'RECEPTION MAT STUDIO' | '164 994'`). `split_merged_row()` handles the clean
  case and returns `None` when the alignment is a guess — that `None` is where a model
  belongs.
- Mapping a drafter's idiosyncratic block heading to a known block type. "EGRESS",
  "LIFE SAFETY DATA", "CODE SUMMARY" and "BUILDING CODE ANALYSIS" are the same thing.
- Spatial association: which room does this door tag serve. Deterministically solvable
  (nearest room polygon), but the polygons have to be recovered first.
- Deciding an occupancy classification from a room name when the drawing does not state one.

This is structured-output work against a schema, with validation and retry. Haiku-class.
And critically it is **per document, not per check** — one normalisation pass, then 50+
rules run against the result for free. Cache on a content hash of the PDF and a repeat
review costs nothing at all.

### Tier C — frontier model, rare, offline

Never in the request path:

- **Authoring new rules** when a code edition changes or a new AHJ amendment lands.
- **Adjudicating genuinely ambiguous drawings** — flagged for human or frontier review by
  the abstention mechanism, not silently guessed at.
- **Adversarial QA** of the rule corpus: given this rule, construct a drawing that makes it
  wrong. That is worth real money and should run in CI, not in production.

---

## 3. Why the scale routine is the honest example

The obvious plan was: read the `/Measure` dictionary, done. The test file destroys that
plan. **Every page carries the same 125 viewports at 23 different conversion factors**,
because AutoCAD writes the whole layout's viewport set to each sheet. Reverse-iterating
the `/VP` array as ISO 32000 §12.9 suggests returns 71.99 pt/ft — the sheet viewport —
which is wrong by a factor of four. Picking the smallest viewport that *contains* the
egress geometry returns 1.5 pt/ft, which is wrong by a factor of twelve.

What works is intersecting two independent sources:

```python
labels = label_candidates(page_text)      # from '1/4" = 1'-0"'  ->  18.0 pt/ft
meas   = measure_candidates(doc, page)    # 23 values, one of which is 18.005
if len(labels) == 1 and any(close(m, labels[0]) for m in meas):
    return Evidence(labels[0], confidence=HIGH)     # two sources agree
if len(labels) == 1:
    return Evidence(labels[0], confidence=MEDIUM)   # one source only
return Evidence.abstain(...)                        # several scales, no way to attribute
```

On the test set that resolves 13 of 24 pages, 12 at high confidence, and abstains on the
detail sheets that print several scales at once. **Abstaining on 11 pages is the correct
answer**, not a failure — those pages get no geometric rules rather than measurements at a
guessed scale.

Generalise the lesson: **key everything on the most stable token available.** Code data
blocks are keyed by the cited section number, not the label text, because labels wrap and
get abbreviated but `(1006.2.1)` does not. That one decision is why `EGRESS.COMMON_PATH`
fires on this drafter's sheets and will fire on the next drafter's.

---

## 4. The code corpus is the moat, and it is data

Note what `fbcreview/codes/fbc2023.py` does with dead ends:

```python
_DEADEND_BASE = 20
_DEADEND_50FT_GROUPS = {"B", "E", "F", "I-1", "M", "R-1", "R-2", "S", "U"}
```

Encoding the *list of groups the exception covers* rather than a boolean is what prevents
the single most common egress error in the industry — "the building is sprinklered so dead
ends are 50 feet," which is false for Group A. A rule that hard-codes `20` is wrong for a
retail tenant; a rule that hard-codes `50` is wrong here. Neither hard-codes anything.

A new code edition is then a new data file, and a bitemporal corpus (the FRBR/effects model
from the earlier build plan) lets one deployment review a 2023 permit and a 2026 permit
correctly on the same day. That corpus is the largest single build item in the whole
programme and it is worth more than the rules on top of it.

---

## 5. What this means for deployment

```
┌──────────┐   upload    ┌───────────────┐   facts JSON   ┌────────────┐
│  Next.js │ ──────────► │ FastAPI +     │ ─────────────► │ rule engine│
│  client  │             │ worker queue  │                │ (pure fn)  │
└──────────┘             └───────┬───────┘                └─────┬──────┘
                                 │ Tier B only when the                │
                                 │ deterministic parse abstains        │
                                 ▼                                     ▼
                         ┌───────────────┐                    ┌────────────────┐
                         │ small model,  │                    │ PyMuPDF render │
                         │ structured out│                    │ marked-up PDF  │
                         └───────────────┘                    └────────────────┘
```

- Container is Python + PyMuPDF. No GPU. A 24-sheet set parses in about two seconds.
- The rule engine is a pure function, so it is trivially horizontally scalable and testable.
- Model spend is per *document*, not per check, and is cached on the file hash.
- The free tier can be pure Tier A. Paid unlocks the full rule corpus, the marked-up PDF and
  the register — which matches the freemium split already decided.

**Cost shape.** Frontier tokens today: every review. After this refactor: zero for a set
whose sheets parse cleanly, one small structured-output call per unfamiliar document
otherwise, and a frontier call only when authoring rules or when the system explicitly
says it cannot tell.

---

## 6. Where it genuinely breaks, stated plainly

1. **A-12 is a raster.** 42 images, ~206 megapixels, 68 words of live text. No parser
   reaches inside it. That is a vision-model job or a "we could not read this sheet"
   declaration — the current code chooses the declaration.
2. **Spatial association is not implemented.** Knowing that door tag `104` at (1238, 313)
   serves Unisex Restroom 106 is what turns "a 2'-8" door exists" into "a 2'-8" door serves
   a toilet room." That inference was made by hand here. It is deterministic — point-in-
   polygon against room boundaries recovered from the wall layers — but it is real work.
3. **The narrative prose is templated, and reads like it.** The finding bodies in the v5 PDF
   are better written than what `Finding.body` produces from slots. Closing that gap is
   either more template craft or a cheap generation pass over an already-decided finding —
   which is a very different, much safer use of a model than deciding the finding.
4. **Rule coverage is partial.** Eleven rules here against roughly fifty checks performed by
   hand. The accessibility corpus (G-2/G-3 value-by-value) is the largest missing block and
   is also the most mechanical — every one of those is a scalar comparison against a table.
5. **`EGRESS.EXIT_COUNT` abstains**, because that row on G-0 carries no section citation and
   the label wrapped. That is the citation-keying strategy failing honestly rather than
   guessing, and it is exactly the case Tier B exists to close.

---

## 7. Build order

1. **Finish Tier A rule coverage** — accessibility scalars first, then plumbing fixture
   counts, then finish classes. Highest value per hour; no model involved.
2. **Room polygon recovery + spatial association.** Unlocks a whole class of findings and
   removes the largest remaining hand step.
3. **The code corpus as a real bitemporal database**, not a Python module. This is the moat.
4. **Tier B normaliser** with a strict schema, validation and retry, cached on file hash.
5. **Adversarial rule QA in CI** — a frontier model trying to break each rule, run on
   merge, not on request.
6. **Wire the v5 renderer to `Finding`** so the marked-up PDF is generated from the rule
   output rather than a hand-built register.

Steps 1, 2 and 6 alone would let a deployed container produce a v5-quality PDF for a
cleanly-plotted vector set with no model call at all.
