---
title: The Project Declaration
type: design-note
tags:
  - florida-building-code
  - reconciliation
  - source/claude-code
status: shipped
created: 2026-08-23
---

# The Project Declaration

A short questionnaire the user answers **before** uploading a permit set. Fifteen fields,
every one optional.

The obvious reading of this feature is "the user tells us the answers, so we search for
less." That is the smaller half. The answers are a **second independent source of truth
alongside the drawings**, and the interesting output is what happens when the two sources
disagree — a category the product did not previously have.

## The five states

Every comparable field lands in exactly one of these, and each has a different consequence.

| State | Meaning | Consequence |
| --- | --- | --- |
| `CORROBORATED` | declared and drawn agree | the resulting `Evidence` is upgraded to `HIGH` — two independent sources beat either alone — and a `VERIFIED` finding records it |
| `CONFLICT` | declared and drawn disagree | a `CONFLICT` finding naming both values and both sources, and the whole rule set is evaluated twice |
| `DECLARED_ONLY` | drawings silent, user answered | rules run, and every finding resting on the value is tagged `basis: declaration` |
| `DRAWN_ONLY` | user skipped, drawings readable | unchanged from before this feature |
| `UNKNOWN` | neither | abstain |

`UNKNOWN` abstains. **A blank field is not permission to guess**, and nothing in this
feature softens that: there is no sentinel "unknown" value, only `None`, so a rule cannot
mistake a placeholder for an answer.

## Dual evaluation — exactly two scenarios

When any field is in `CONFLICT`, `run_all` evaluates the corpus twice: once against
`as_drawn()`, once against `as_declared()`. **Five conflicting fields produce two runs, not
thirty-two.** One reading in which every declared value wins, one in which every drawn
value wins. A matrix would answer a question nobody asked: a permit set is submitted as a
whole, so the two things worth comparing are the whole set as drawn and the whole set as
described.

Findings are deduplicated on `(rule_id, sheet, anchor, fid)` and carry a `scenario`:

- `both` — identical either way. The common case; most rules never touch a conflicting field.
- `as_drawn` — appeared only against the drawings.
- `as_declared` — appeared only against the declaration.

**The drawings govern the sheet markup.** The permit is issued against what was submitted
and the AHJ reviews the sheet, so an `as_declared`-only finding gets no on-sheet marker; a
divergence gets a dashed marker and the detail goes in the register.

## What it costs and what it buys — the ITEC Alico Park measurement

`docs/reference/ITEC Alico Park Findings.md` records the baseline: **0 findings and 12
reasoned abstentions**, because that set was plotted from AutoCAD LT 2022 with no preserved
OCG layers and its code-analysis tables pasted in as pictures. Nothing to parse.

Measured against a stand-in built from the documented G-002, A-101 and S-001 values
(`tests/fixtures/permit_sets.py` — the real PDF is a client file and is never committed):

| Run | Findings | Abstentions | of the original 12 rules |
| --- | --- | --- | --- |
| As plotted, no declaration | 2 | 28 | **12** — the documented baseline, reproduced |
| As plotted, with declaration | 13 | 17 | 10 |
| Code tables recovered, no declaration | 7 | 23 | 12 |
| **Code tables recovered, with declaration** | **20** | **10** | 10 |

Two of the hand-authored findings now fall out mechanically:

- **H-01** — the declaration says "not a mixed occupancy" while G-002 cites
  `SEPARATED PER TABLE 508.4`, which applies only to a mixed-occupancy building.
  `DECL.MIXED_OCCUPANCY`, `HIGH`, `CONFLICT`.
- **H-02** — occupant load recomputed at 150 gross from `Table 1004.5` gives **107** against
  the **152** stated, which is what an `OCCUPANT FACTOR` column full of unit numbers looks
  like. `EGRESS.OCCUPANT_LOAD_COMPUTED`.

Plus `CODE.EDITION_CURRENT` (7th Edition superseded — finding C-01) and
`STRUCT.WIND_STANDARD` (ASCE 7-16 replaced by 7-22 — part of H-03).

## Which answers are worth most

By the number of checks each unlocks:

| Field | Checks | |
| --- | --- | --- |
| `occupancy_group` | 8 | selects the row in Tables 504.3, 504.4, 506.2, 1004.5 and most of Chapter 10 |
| `sprinkler_system` | 7 | moves the column in every one of those tables |
| `construction_type` | 5 | the whole of Chapter 5 plus Table 601 |
| `building_area_sf` | 4 | Table 506.2 and the occupant load |
| `risk_category`, `code_edition` | 3 each | |

The first three answers buy most of the value. The form does not say so — it shows a live
count of what is still standing down, which is the honest version of the same message.

## Where the numbers live

Thresholds are data in `fbcreview/codes/`, keyed by cited section, never literals in a rule
body. Edition effective dates are keyed by edition in `fbcreview/codes/editions.py`, and
"today" is an argument rather than something a rule reaches for — so the behaviour is
testable on both sides of an adoption date and December 2026 does not break it.

Where this build does not carry a row — Table 504.3 for Groups H, I and R, Table 1004.5 for
occupancies whose factor is use-specific — the lookup returns `None` and the rule abstains
naming what was missing. A wrong allowable area is worse than no allowable area.

## The normaliser

Almost every false conflict this feature could produce would come from formatting rather
than from disagreement, and a tool that reports a conflict about a comma is switched off
before it reports a real one. `fbcreview/reconcile.py` normalises before comparing, and the
normaliser is a pure function with a table-driven test:

```
II-B == IIB == "Type II-B" == "type ii-b" == "TYPE 2B"
26'-4" == 26.33          "Yes" == True          "BUSINESS" == "Group B" == "B"
"Lee County, FL" == "LEE COUNTY, FLORIDA"       "7th Edition" == fbc2020
```

Numeric fields carry a tolerance — `max(50 sf, 2 %)` on areas, 0.5 ft on height, 1 mph on
wind speed, exact on storeys. The reasoning for each is in `declaration_schema.Tolerance`.

## What did not change

A review submitted with no declaration produces exactly what it produced before this
feature existed. The rules that predate it read declaration-sourced values only where the
declaration participates, so a text sweep can never quietly feed an old rule an input it
never had. That is checked against a baseline captured by running the pre-change engine —
`tests/baseline_no_declaration.json` — rather than against a hand-written expectation.
