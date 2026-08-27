---
title: Claude Code Prompt — Project Declaration Questionnaire
type: runbook
tags:
  - code-review
  - florida-building-code
  - feature-spec
  - angular
  - source/cowork
status: draft
created: 2026-08-21
source-session: Meridian Drafting
---

# Claude Code prompt — the Project Declaration questionnaire

Open Claude Code in `C:\Antigravity\fbc-review` and paste everything below the line.
`CLAUDE.md` carries the standing rules and outranks anything here that contradicts it.

---

## What you are building

A **Project Declaration** — a short questionnaire the user answers before uploading a
permit set, giving the engine the building's code data directly instead of making it parse
that data off the drawings.

Twelve fields, all optional. The answers feed the rule engine as a **second independent
source of truth alongside the drawings.**

## Read this first — the design principle everything else follows from

The obvious reading of this feature is "the user tells us the answers, so we search for
less." **That is the smaller half and building only that half wastes the feature.**

The declaration is a second source. Every field therefore lands in one of five states, and
each state has a different consequence:

| State | Meaning | Consequence |
| --- | --- | --- |
| `CORROBORATED` | declared and drawn agree | rule fires at **HIGH** confidence instead of MEDIUM — two independent sources |
| `CONFLICT` | declared and drawn disagree | **emit a finding**, and evaluate both scenarios (see below) |
| `DECLARED_ONLY` | drawings silent, user answered | rule runs, but every finding is tagged as resting on user input, not drawing evidence |
| `DRAWN_ONLY` | user skipped, drawings readable | current behaviour, unchanged |
| `UNKNOWN` | neither | **abstain**, exactly as now |

`CONFLICT` is the highest-value output this feature unlocks and the product does not
currently have that category at all. Two real examples from the ITEC Alico Park test set in
`docs/reference/`:

- **H-01** — the user declares "not a mixed occupancy," while G-002 cites
  `OCCUPANCY SEPARATION RATING PROVIDED = MULTIPLE - SEPARATED PER TABLE 508.4`.
  Table 508.4 only applies to a mixed-occupancy building. That contradiction currently
  requires a human to read the sheet. With a declaration it falls out mechanically.
- **H-02** — the user declares Business at 150 gross; the `OCCUPANT FACTOR` column on A-101
  reads `100, 101, 102, 103, 104, 105, 106, 104`, which is the unit number, not a code
  factor. Conflict, immediately.

`UNKNOWN` must keep abstaining. **A blank field is not permission to guess.** That rule is
in `CLAUDE.md` and this feature does not soften it.

## The measurable success criterion

`docs/reference/ITEC Alico Park Findings.md` records the baseline: on that set the engine
returns **0 findings and 12 reasoned abstentions**, because it was plotted from AutoCAD LT
2022 with zero preserved OCG layers and raster code-analysis tables — nothing to parse.

That is precisely the failure mode this feature fixes. **You are not done until you have run
the ITEC set with a declaration built from its own G-002 values and reported the
before-and-after abstention count.** A substantial drop is the point of the work. If
abstentions do not fall, something is wired wrong — say so rather than shipping it.

## Decisions already made — do not relitigate

1. **The questionnaire comes before upload**, as one continuous run. Do not build a
   two-phase extract-then-confirm job model.
2. **On conflict, evaluate both scenarios and report both outcomes.**
3. **Twelve fields** — the nine the client named plus construction type, jurisdiction and
   code edition.
4. **Two vocabulary modes**, pro and simple, user-toggleable.

## Phase 1 — the data model

### 1.1 `fbcreview/declaration.py` (new)

```python
@dataclass
class ProjectDeclaration:
    # occupancy
    occupancy_group: Optional[str] = None        # "A-3", "B", "M", "S-1", ...
    mixed_occupancy: Optional[bool] = None
    separation_method: Optional[str] = None      # "separated" (508.4) | "nonseparated" (508.3)
    # construction and size
    construction_type: Optional[str] = None      # "I-A" ... "V-B"
    building_area_sf: Optional[float] = None     # largest floor, gross
    total_area_sf: Optional[float] = None
    height_ft: Optional[float] = None
    stories: Optional[int] = None
    # fire protection
    sprinkler_system: Optional[str] = None       # "none" | "nfpa13" | "nfpa13r" | "nfpa13d"
    # structural / wind
    wind_speed_mph: Optional[float] = None       # Vult, ultimate
    exposure_category: Optional[str] = None      # "B" | "C" | "D"
    risk_category: Optional[str] = None          # "I" | "II" | "III" | "IV"
    # context
    zoning: Optional[str] = None                 # free text, jurisdiction-specific
    jurisdiction: Optional[str] = None           # "Lee County, FL"
    code_edition: Optional[str] = None           # "fbc2020" | "fbc2023" | "fbc2026"
```

Every field is `Optional` and `None` means unanswered. There is no sentinel "unknown"
string — `None` is the only representation, so a rule cannot mistake a placeholder for an
answer.

**Refactor required.** `ReviewOptions` currently carries `occupancy_group` and
`sprinklered`. Those are building facts, not review settings, and they now belong to the
declaration. Move them. `ReviewOptions` keeps only output and behaviour knobs — edition
selection stays there **only** if the declaration's `code_edition` is unset; the declaration
wins when both are present. Update every caller, `run.py` and `webapp/server.py` included.
The regression test must stay green through this refactor — if it does not, you changed
behaviour, not just structure.

### 1.2 Field metadata as data, not as UI

`fbcreview/declaration_schema.py` — one module that is the **single source of truth** for
field definitions, and which the API serves to the frontend:

```python
FIELDS = [
  Field(
    key="construction_type",
    kind="enum",
    choices=["I-A","I-B","II-A","II-B","III-A","III-B","IV","V-A","V-B"],
    pro_label="Construction Type",
    pro_help="Type per FBC-B Table 601.",
    simple_label="How is the building built?",
    simple_help="Steel or concrete frame, protected or unprotected; wood frame; masonry.",
    group="construction",
    unlocks=["HEIGHT_AREA.TABLE_504", "FIRE.TABLE_601", "FIRE.TABLE_602"],
    tolerance=None,
  ),
  ...
]
```

Both label sets live here. **The Angular app must not hard-code a single enum value or
label** — adding an occupancy group in Python has to reach the UI with no frontend change.
`unlocks` names the rules a field enables; the UI uses it to tell the user what answering
buys them.

Numeric fields carry a `tolerance` used by conflict detection:

- `building_area_sf`, `total_area_sf` — `max(50.0, 0.02 * value)`
- `height_ft` — `0.5`
- `wind_speed_mph` — `1.0`
- `stories` — exact
- all categorical fields — exact string match after normalisation

Rounding on a drawing is not a conflict. Pick tolerances that ignore rounding and catch
real disagreement, and put the reasoning in a comment.

## Phase 2 — reconciliation

`fbcreview/reconcile.py` (new). This is the heart of the feature.

```python
@dataclass
class Reconciled:
    field: str
    declared: Optional[Evidence]   # source="declaration"
    drawn: Optional[Evidence]      # source=<sheet code>
    state: str                     # CORROBORATED | CONFLICT | DECLARED_ONLY | DRAWN_ONLY | UNKNOWN

@dataclass
class ReconciledFacts:
    facts: ProjectFacts
    declaration: ProjectDeclaration
    fields: Dict[str, Reconciled]
    def as_drawn(self) -> ProjectFacts: ...
    def as_declared(self) -> ProjectFacts: ...
    def conflicts(self) -> List[Reconciled]: ...
```

Rules:

- Reuse the existing `Evidence` primitive from `fbcreview/confidence.py`. Add
  `source="declaration"`. Do not invent a parallel provenance type.
- `CORROBORATED` upgrades the resulting `Evidence.confidence` to `HIGH` and its `note`
  records both sources. That upgrade is the reward for answering and should be visible in
  the register.
- Comparison is normalised before it is compared: case, whitespace, `II-B` vs `IIB` vs
  `Type II-B`, `"yes"`/`True`, `26'-4"` vs `26.33`. Write the normaliser as a tested pure
  function — most false conflicts will come from formatting, and a tool that cries wolf on
  `II-B` versus `Type IIB` will be switched off within a day.

### Dual evaluation

When any field is in `CONFLICT`, run the full rule set **twice**: once against
`as_drawn()`, once against `as_declared()`.

**Exactly two scenarios. Never a combinatorial product.** Five conflicting fields produce
two runs, not thirty-two: one where every declared value wins, one where every drawn value
wins. State that in a comment so nobody later "improves" it into a matrix.

`Finding` gains `scenario: str`:

- `"both"` — the finding appeared identically in both runs. This is the common case; most
  rules never touch a conflicting field. Report once, exactly as today.
- `"as_drawn"` — appeared only when evaluating the drawings. Renders as
  *"Fails as drawn. Passes as you described it."*
- `"as_declared"` — the reverse. *"Passes as drawn. Fails as you described it."*

Deduplicate on `(rule_id, sheet, anchor)` so a finding present in both runs is not printed
twice. Get this wrong and every register doubles in length.

**The drawings govern the sheet markup.** On-sheet markers show the `as_drawn` outcome,
because the permit is issued against what was submitted and the AHJ reviews the sheet. A
divergence gets a marker indicating that the two scenarios disagree, with the detail in the
register.

## Phase 3 — conflict rules

`fbcreview/rules/r_declaration.py` (new). One rule per comparable field:

```
DECL.OCCUPANCY        DECL.MIXED_OCCUPANCY   DECL.CONSTRUCTION_TYPE
DECL.BUILDING_AREA    DECL.HEIGHT            DECL.STORIES
DECL.SPRINKLER        DECL.WIND_SPEED        DECL.EXPOSURE
DECL.RISK_CATEGORY    DECL.CODE_EDITION
```

Each fires only in state `CONFLICT`, and each must name **both values and both sources** in
the finding text. "Occupancy mismatch" is useless. This is the shape:

> You declared Group **B**. Sheet **G-002** states `OCCUPANCY: BUSINESS` but also
> `OCCUPANCY SEPARATION RATING PROVIDED: MULTIPLE - SEPARATED PER TABLE 508.4`, which
> applies only to a mixed-occupancy building. `FBC-B 508.4`

Severity by consequence, not by field:

- **HIGH** — the conflict changes which code threshold applies (occupancy, construction
  type, sprinkler status, risk category, code edition)
- **MEDIUM** — the conflict changes a computed value but not which rule governs (area,
  height, stories, wind speed)
- **LOW** — cosmetic or unit-level disagreement inside tolerance drift

`status = "CONFLICT"` — a new status alongside the existing `OPEN` and `PASS`. It is not a
severity. Update anything that switches on status, the renderer and the register included.

## Phase 4 — rules the declaration unlocks

This is where the abstention count actually falls. Each of these is deterministic
arithmetic against a table, needs no drawing parsing, and currently cannot run when the code
block is raster. Add them to `fbcreview/codes/fbc2023.py` as data and to
`fbcreview/rules/` as rules.

| New rule | Inputs from the declaration | Code |
| --- | --- | --- |
| `HEIGHT_AREA.TABLE_504_HEIGHT` | construction type, occupancy, sprinklered, height | `FBC-B Table 504.3` |
| `HEIGHT_AREA.TABLE_504_STORIES` | + stories | `Table 504.4` |
| `HEIGHT_AREA.TABLE_506_AREA` | + area per floor | `Table 506.2` |
| `FIRE.TABLE_601` | construction type | `Table 601` |
| `EGRESS.OCCUPANT_LOAD_COMPUTED` | occupancy, area | `Table 1004.5` |
| `EGRESS.COMMON_PATH` | occupancy, sprinklered | `Table 1006.2.1` |
| `EGRESS.TRAVEL_DISTANCE` | occupancy, sprinklered | `Table 1017.2` |
| `EGRESS.DEAD_END` | occupancy, sprinklered | `1020.4` |
| `STRUCT.WIND_STANDARD` | code edition, wind speed | `1609`, ASCE 7 |
| `CODE.EDITION_CURRENT` | code edition, today's date | see below |

Two of these deserve specific attention:

**`EGRESS.OCCUPANT_LOAD_COMPUTED`** computes occupant load from occupancy plus area with no
drawing parsing whatsoever, then compares it to whatever the drawings state. On ITEC that
alone reproduces finding H-02. Business is **150 gross**; do not hard-code it — read it from
the `Table 1004.5` data in `codes/`, which already exists.

**`CODE.EDITION_CURRENT`** makes the ITEC C-01 finding automatic. The FBC 8th Edition (2023)
took effect 31 December 2023; the 9th Edition takes effect on or about 31 December 2026.
A set declared to the 7th Edition (2020) is superseded today. Encode the effective dates as
**data in `codes/`, keyed by edition** — never as a literal in the rule, or this breaks in
December.

Respect the existing corpus discipline: thresholds live in `codes/`, keyed by cited section
number, never as literals in rule bodies. `_DEADEND_50FT_GROUPS` in `fbc2023.py` is the
pattern to follow — the exception is encoded as the **group list it covers**, not as a
boolean, because "sprinklered, so dead ends are 50 ft" is false for Group A.

## Phase 5 — API

- `POST /api/review` accepts a `declaration` JSON part alongside the existing `options`.
  Both stay optional; omitting the declaration must reproduce today's behaviour byte for
  byte. Prove that with a test.
- Extend `GET /api/config` to serve the full field schema from
  `fbcreview/declaration_schema.py` — keys, kinds, choices, both label sets, both help
  texts, groups, tolerances, `unlocks`. The frontend renders whatever this returns.
- Validate server-side against the schema. An enum value not in `choices` is a `400` with
  the offending field named. Do not silently drop it.
- Persist the declaration on the Firestore job record. It is part of the audit trail and the
  register prints it.
- Add `GET /api/jobs/{id}/declaration` returning what was submitted, for support.

## Phase 6 — frontend

Angular 22 — standalone, signals, zoneless, strict. Re-read the Angular section of
`CLAUDE.md` before writing a line; models reliably generate pre-v17 Angular from memory.

1. **A step before upload.** Questionnaire, then the drop zone. One continuous submit — do
   not split the job.
2. **Pro / simple toggle** in the form header, persisted in `localStorage`, defaulting to
   **pro**. Both label sets come from `/api/config`; the toggle picks which of the two the
   template binds. **Do not duplicate copy in the Angular app** — if a label exists in two
   places it will drift, and a wrong code label is a liability, not a typo.
3. **Every field optional**, with a prominent "Skip — read it from my drawings" that
   preserves today's zero-input path exactly.
4. **Show what answering buys.** A live line reading
   *"9 of 12 answered — 3 checks will abstain without these."* Compute it from the `unlocks`
   metadata. This makes abstention visible and is the honest way to motivate completion:
   the tool is telling the user what it will refuse to guess at.
5. Typed reactive forms with explicit generics. Signal Forms were still experimental at v21
   — confirm status before using them, otherwise typed reactive forms.
6. Group the fields — Occupancy, Construction & Size, Fire Protection, Structural, Context —
   and let each group collapse. Twelve fields in one flat column reads as a tax form.
7. `aria-live` on the answered-count line, labels tied to inputs, visible focus, keyboard
   operable throughout. Non-negotiable, same as the rest of the app.

## Phase 7 — output

- **Front matter gains a "Project Declaration as submitted" page.** Print every answer with
  its state — corroborated, conflicting, unanswered. This is the audit record of what the
  user asserted and it protects you as much as it informs them.
- **A new register section, "Declared versus drawn"**, listing each conflict with both
  values, both sources, and both scenario outcomes.
- **Findings resting on declared-only values must say so on the card.** Something in the
  shape of *"Based on the project declaration; the drawings do not state this."* The markup
  must never attribute to the drawings something the drawings do not say — that is a
  factual misstatement in a document a client may forward to a plans examiner.
- **Legend gains two entries** — the conflict marker and the declared-basis marker. The
  legend is already a compact 233 pt strip, so check it still fits after adding rows, and
  read the return value of `insert_textbox`: it returns negative when text does not fit and
  silently draws nothing.
- Update the "About this review margin" block to state that some findings may rest on
  user-supplied data.

## Testing

1. **Regression unchanged.** `pytest tests/ -v` green throughout. The Sculpted Hot Pilates
   set with no declaration must produce byte-identical findings to today. Add that as an
   explicit test — it is the guard on the whole refactor.
2. **The ITEC before-and-after**, the headline acceptance test. Build a declaration from
   ITEC's own G-002 values (Business, Type II-B, 15,376 SF, 1 story, 26 ft 4 in., NFPA 13,
   155 mph, Exposure B, Risk II, Lee County, FBC 7th Edition) and assert:
   - abstentions fall well below the 12 baseline
   - `CODE.EDITION_CURRENT` fires — 7th Edition is superseded
   - `EGRESS.OCCUPANT_LOAD_COMPUTED` computes 107 against the 152 stated, reproducing H-02
   - `DECL.*` conflict rules fire where the declaration disagrees with the sheets
3. **Normaliser unit tests.** `II-B` / `IIB` / `Type II-B` / `type ii-b` all equal.
   `26'-4"` equals `26.33`. `"Yes"` equals `True`. Table-drive it.
4. **Tolerance tests.** 15,376 against 15,380 is not a conflict; 15,376 against 18,000 is.
5. **Dual-evaluation tests.** One conflicting field produces exactly two rule runs; findings
   untouched by that field are marked `both` and appear once.
6. **API tests.** Declaration omitted reproduces current output. An invalid enum returns
   `400` naming the field.

## Do not

- Do not let an unanswered field become a default. `None` means abstain. A blank is not
  permission to guess, and this feature does not soften that rule.
- Do not let the declaration override the drawings in the sheet markup. The AHJ reviews the
  sheet.
- Do not expand dual evaluation into a combinatorial matrix. Exactly two scenarios.
- Do not hard-code enum values, labels or help text in the Angular app. The schema is served.
- Do not hard-code code thresholds or edition effective dates in rule bodies. They live in
  `codes/`.
- Do not modify anything under `fbcreview/extract/` or `fbcreview/render/markup.py`'s
  geometry handling. Adding finding types is fine; changing rotation or mediabox handling is
  not.
- Do not edit `tests/test_regression.py` to make a phase pass.

## Report back with

The before-and-after abstention count on ITEC, the list of new rules that fired because of
the declaration, which of the 12 fields turned out to unlock the most checks, any field
where drawing extraction and declaration disagreed so often that the normaliser or tolerance
needs another look, and anything you had to decide that this prompt did not cover.
