---
title: Claude Code Prompt - Structural Consult Module (Knowledge, Skills, Approval Gate)
type: runbook
tags:
  - code-review
  - florida-building-code
  - structural
  - claude-code
  - source/cowork
status: draft
created: 2026-10-02
source-session: Meridian Drafting
---

# Claude Code prompt - Structural Consult module: knowledge, skills and the approval gate

> Committed to the repo on 2026-10-03 as the owner wrote it, so that
> `docs/STRUCT-PLAN.md` and `.claude/skills/fbc-structural/` can point at it.
> `docs/STRUCT-PLAN.md` records how it maps onto the code, the conflicts found,
> and the DECIDE answers still needed. Where the two disagree, the plan says why.

Open Claude Code in `C:\Antigravity\fbc-review` and paste everything below the line, or point it
at this file. `CLAUDE.md` outranks anything here that conflicts with it. This prompt adds two
packages on purpose - `fbcstruct/` and the shared `hazards/` (section 3). If any other conflict
remains after you read the repo, stop and ask.

---

## 0. What you are building

A structural consult module that sits beside the reviewer, not inside it.

- A requester who is not a structural engineer (architect, drafter, contractor, owner) asks a
  question that needs a structural engineer's knowledge.
- The module answers it end to end - facts, rules, calculations, a drafted explanation - and
  produces exactly one result package.
- A licensed Florida structural engineer working with Meridian reviews only that final package
  and approves or rejects it. Only approved packages reach the requester.
- Engineers never review individual rule applications during a request. Rule correctness is
  established before a skill goes live (section 9) and re-established when a rule changes.

Governing principle, extended from the reviewer's "AI reads; rules decide":

**AI drafts. Engines compute. The corpus holds every threshold. The engineer approves the result.**

There is no model training or fine-tuning anywhere in this prompt. "Training" a skill means
encoding its knowledge (section 4), building its procedure (section 6), and validating it against
engineer-approved answers until it clears the launch gate (section 9). Approved results then become
drafting exemplars (section 10). Every number stays traceable to a corpus row, a dataset or an
engine.

Engineer time is spent in exactly two places:
1. Once per skill, upfront: verifying the structural corpus rows it cites and writing its gold answers.
2. Per request: approve or reject the final package. Nothing else.

## 1. Read before writing anything

1. `CLAUDE.md`, `docs/ARCHITECTURE-V2.md`, `docs/ENGINE-TEARDOWN.md`, `docs/TRAINING-MODE.md`.
2. `fbcreview/codes/` (`editions.py`, `fbc2023.py`), `fbcreview/read/catalog.py`,
   `fbcreview/rules/r_structural.py`, `fbcreview/pipeline.py`.
3. `webapp/feedback_schema.py`, `webapp/triage.py`, the auth/allowlist code, and the admin
   decision endpoint (`POST /api/admin/feedback/{id}/decision` stamps decided_by, decided_at and
   decision_note - the approval record here follows the same shape).
4. Confirm which branch carries engine v2. PR #27 was open as of 2026-09-28. Branch from the right
   base; ask if unclear.
5. `CLAUDE.md` names `docs/DEPLOYMENT-PROMPT.md` as the current task. If that task is unfinished,
   ask the owner whether this prompt runs before or after it. Do not edit the "Current task"
   section without approval.

Then write `docs/STRUCT-PLAN.md`: how this prompt maps onto the code as it actually is, every
conflict found, and the DECIDE items (section 12) that need an owner answer. Stop for those answers.

## 2. Prerequisite - fix STRUCT.WIND_STANDARD before 2026-12-31

The defect:
- `fbcreview/codes/editions.py` maps `ASCE7["fbc2026"]` to `None`.
- `fbcreview/rules/r_structural.py` sets `superseded` from effective dates alone.
- From 2026-12-31, every 8th Edition set (ASCE 7-22) gets H-WIND, claiming the 9th Edition replaced
  ASCE 7-22. The finding text reads "adopts None" and tells the user to re-read Vult "from the None maps".
- The 9th Edition takes effect 2026-12-31 and continues the ASCE 7-22 framework.

Fix (adapt to the v2 code if it moved):

```python
# in wind_standard(), replacing the `superseded = ...` line
if current is not None and current.effective > cited.effective and current.asce7 is None:
    # A newer edition is in force but its ASCE 7 adoption is not in the corpus. Never guess.
    out.abstentions.append(Abstention(
        "STRUCT.WIND_STANDARD",
        "the edition in force has no ASCE 7 adoption recorded",
        detail=current.key))
    return
# Superseded for wind only when the in-force edition adopts a different ASCE 7.
superseded = (current is not None and current.effective > cited.effective
              and current.asce7 != cited.asce7)
```

Tests, at `AS_OF = 2027-01-15`:
- With `ASCE7["fbc2026"] = None`: the rule abstains for every set and never claims a replacement.
- With `ASCE7["fbc2026"] = "ASCE 7-22"`, via a test-local patch and not a corpus edit: a 7th Edition set
  fires H-WIND naming ASCE 7-22, and an 8th Edition set gets V-WIND.
- Every review dated before 2026-12-31 is unchanged. Prove it with the existing suite.
- `tests/conftest.py` pins no date. List any test that runs the full rule set on an 8th Edition
  fixture with a stated Vult; those change on 2026-12-31 with no code change. Pin them, or explain.

The corpus edit `ASCE7["fbc2026"] = "ASCE 7-22"` is DECIDE D2: the owner confirms it against
Chapter 35 of the published 9th Edition, including which supplements are referenced.

Also note: `CODE.EDITION_CURRENT` justifies its CRITICAL "re-analysis rather than a cover-sheet
edit" wording by the wind standard moving. That reason does not hold for 8th to 9th. Propose the
change in STRUCT-PLAN.md; do not change the confirmed sentences that `tests/test_code_edition.py`
locks without owner approval.

## 3. Boundaries and layout

```text
hazards/              NEW shared, read-only site data: wind layers, flood client, jurisdiction lists
fbcreview/            the reviewer - unchanged except section 2 and new corpus rows
  codes/              the ONE hand-transcribed corpus; structural tables are added here
fbcstruct/            NEW consult module
  intake.py           requester facts, follow-up questions, prefill from an uploaded set
  skills/             one module per skill (section 6) - pure functions over typed inputs
  engines/            deterministic calculators (ASCE 7-22 C and C first)
  package.py          result package, driver ranking, canonical JSON, content hash
  validation/         gold-set schema, runner, Wilson scorecard
  knowledge/          loaders for practice-library items and exemplars (no content in git)
  ai/                 drafting layer: prompt files, cache, grounding check
webapp/struct/        routes, approval workflow, Firestore persistence
web/                  Angular pages: request, status, engineer queue, released result
.claude/skills/fbc-structural/   project skill for future Claude Code sessions (section 11)
```

Import rules, enforced by `tests/test_struct_boundaries.py`. Walk the import graph the way
`tests/test_ai_guardrails.py` does.
- `fbcreview` never imports `fbcstruct` or `webapp.struct`.
- `fbcstruct` may import `fbcreview.codes`, `fbcreview.read`, `fbcreview.pipeline` and `hazards`.
- `fbcstruct/skills`, `fbcstruct/engines` and `fbcreview/codes` never import `fbcstruct/ai` or `anthropic`.
- `hazards` imports nothing from `fbcreview` or `fbcstruct`.

Independence: if the reviewer reviews a set that the module helped produce for the same account,
the review report says so. Never suppress or alter a reviewer finding to suit the module.

## 4. Knowledge base - what the module must know, and how each piece is encoded

Four encodings. Each knowledge item uses exactly one primary encoding.

| Encoding | What it is | Where it lives | Who fills it |
| --- | --- | --- | --- |
| CORPUS | Hand-transcribed code rows with provenance | `fbcreview/codes/` | A person, from licensed code text; a licensed engineer verifies structural rows |
| DATA | Versioned public datasets with source URL, retrieval date and SHA-256 | `hazards/` (DECIDE D4 for large files) | Scripts you write; owner confirms the source |
| LIBRARY | Engineer practice material: standard notes, typical details, standard wind-data blocks, typical small-structure connections, firm assumptions | Cloud Storage plus Firestore index - never git | The engineers |
| EXEMPLAR | Approved result packages promoted to drafting examples; rejected ones kept with reason codes | Firestore - never git | Owner promotes; never automatic |

Corpus rule, restating CLAUDE.md: never fill a CORPUS row, map value or coefficient from model
memory. You build:
- the typed table and its loader
- a provenance test
- a transcription worksheet at `docs/struct-transcription/<table>.md`, which a person fills from the licensed text

Every structural row carries `edition`, `section`, `value`, `source_locator`, `transcribed_by`,
`verified_by` and `verified_on`. A row without `verified_by` loads as UNVERIFIED, and any skill that
needs it abstains with "corpus row <id> not verified".

### Knowledge registry

| ID | Knowledge | Source to transcribe or ingest | Enc. | Used by |
| --- | --- | --- | --- | --- |
| KN-01 | Code editions, effective dates, ASCE 7 per edition | `editions.py` (exists), FBC adoption schedule | CORPUS | all |
| KN-02 | Risk category by use and occupant load | FBC-B Table 1604.5 | CORPUS | SK-02, SK-03, SK-06 |
| KN-03 | Structural data required on construction documents | FBC-B 1603.1 to 1603.1.9, per edition. The 9th Edition residential volume deletes Vasd; check the building volume | CORPUS | SK-03 |
| KN-04 | Basic wind speed, Risk Categories I-IV | FGDL layers `WINDZONES_CAT1..CAT4_ASCE7_22_JUN21` (= FBC 8th Ed Figures 1609.3(1)-(4)); ASCE 7 Hazard Tool for human spot checks | DATA | SK-01, SK-03, SK-06 |
| KN-05 | Vult to Vasd conversion | FBC-B 1609.3.1, edition-keyed | CORPUS | SK-03 |
| KN-06 | Hurricane-prone region, wind-borne debris region, opening protection | FBC-B 202 and 1609.2; ASCE 7-22 26.2 and 26.12.3. WBDR as summarized for the 8th Edition: within 1 mile of the mean high-water line where Exposure D exists upwind at the waterline and Vult >= 130 mph, or anywhere Vult >= 140 mph. Transcribe the exact wording | CORPUS + DATA (coastline) | SK-01, SK-05 |
| KN-07 | High-Velocity Hurricane Zone | Miami-Dade and Broward; FBC-B Sections 1612-1626 (verify numbering); FBC Test Protocols (TAS); accepted approval types per jurisdiction | CORPUS | SK-01, SK-05 |
| KN-08 | Product approval | F.S. 553.842; Rule 61G20-3; permit data (FL number or NOA, expiration, design pressure); approvals publish ASD pressures; strength-to-ASD factor 0.6 | CORPUS | SK-05 |
| KN-09 | Threshold building | F.S. 553.71 definition; F.S. 553.79(5) structural inspection plan and special inspector | CORPUS | SK-04 |
| KN-10 | Special inspections | FBC-B Chapter 17: statement of special inspections and triggers by material and system. Transcribe the subset Meridian's work hits first | CORPUS | SK-04 |
| KN-11 | Flood | FBC-B 1612, ASCE 24; NFHL fields `FLD_ZONE`, `SFHA_TF`, `STATIC_BFE`, `V_DATUM`, `FIRM_PAN`; NGVD29 to NAVD88 from a published conversion service, never a constant | DATA + CORPUS | SK-01, SK-07 |
| KN-12 | Exposure categories and the upwind-fetch procedure | ASCE 7-22 26.7 (definitions and procedure only) | CORPUS | SK-01, SK-06 |
| KN-13 | ASCE 7-22 C and C coefficients, low-rise h <= 60 ft | Kd Table 26.6-1; Kz and terrain constants Tables 26.10-1, 26.11-1; Kzt 26.8; Ke Table 26.9-1; GCpi Table 26.13-1; GCp Figures 30.3-1 (walls), 30.3-2A to 30.3-2G (flat, gable, hip roofs); effective wind area; zone width a | CORPUS (licensed text, engineer-verified) | SK-06, SK-08 |
| KN-14 | Load combinations | FBC-B 1605; ASCE 7-22 Chapter 2 | CORPUS | SK-08 and later |
| KN-15 | Engineer of record and delegated engineering | FBPE Rule 61G15-31: .001 general, .002 definitions, .003 wood trusses, .005 precast and prestressed, .007 pre-engineered, .008 foundations, .009 structural steel, .010 cold-formed steel, .011 aluminum | CORPUS | SK-10 |
| KN-16 | Chickee exemption | F.S. 553.73(10)(i), amended in the 2026 session (HB 929, enrolled). Transcribe from the chaptered law with its effective date. Non-tribal builders are not exempt | CORPUS | SK-01, SK-08 |
| KN-17 | Existing buildings | FBC-EB structural triggers for alteration levels, additions, repairs, reroofing | CORPUS | SK-09 |
| KN-18 | Referenced standard editions | FBC-B Chapter 35 per edition (ACI 318, AISC 360, NDS, TMS 402/602, AISI S100, ASCE 24) | CORPUS | all design answers |
| KN-19 | Practice library | Engineer-provided | LIBRARY | all |
| KN-20 | Exemplars | Approved packages, owner-promoted | EXEMPLAR | AI drafting |

Before encoding KN-13, the owner confirms the licensed ASCE 7-22 source and its terms (DECIDE D9).

## 5. Facts the requester supplies

Typed intake in `fbcstruct/intake.py`:
- address or parcel ID; project type; scope (new, addition, alteration, repair)
- occupancy and use; occupant load; stories; building height and mean roof height
- roof type and slope; plan dimensions
- opening protection or enclosure status
- uploaded set (optional)

- Prefill: if a set is uploaded, call `fbcreview.pipeline.build_facts()` and offer the values for
  confirmation, marked "prefilled from sheet X". A prefilled value is never treated as confirmed
  until the requester confirms it.
- Missing required facts trigger a structured follow-up question to the requester. Never a default.
  The engineer never sees intake questions.
- v1 routing is an explicit question-type picker (one entry per live skill) plus free text. Model
  routing of free text is a later enhancement, and only with requester confirmation.
- Default out-of-scope rule (owner may change): any question that needs a site visit, inspection of
  existing conditions, or the capacity of an existing member is routed straight to
  "needs engineer-authored response". The tool does not attempt it.

## 6. Skills - what the module can do

Each skill is a module in `fbcstruct/skills/` with a declared spec:

```python
@dataclass(frozen=True)
class SkillSpec:
    key: str                         # "SK-03"
    title: str
    version: str                     # bump on any rule, engine or cited-corpus change
    required_facts: tuple[str, ...]
    optional_facts: tuple[str, ...]
    knowledge: tuple[str, ...]       # KN ids
    engine: str | None
    drivers: tuple[str, ...]         # judgment calls this skill may make
    out_of_scope: tuple[str, ...]    # conditions that force "needs engineer-authored response"
    status: str                      # "draft" | "validated" | "retired"
```

v1 - corpus and data, light computation:

| Key | Skill | Output | Typical drivers |
| --- | --- | --- | --- |
| SK-01 | Site Structural Brief | For an address: Vult by risk category, HVHZ yes or no, WBDR screen, flood zone with BFE and datum, threshold screen, product-approval regime, chickee-exemption applicability | Risk category; Exposure D upwind at the waterline (WBDR) |
| SK-02 | Risk Category Determination | Table 1604.5 category with the governing row | Governing use; occupant-load basis |
| SK-03 | Wind Design Data Block | The 1603.1.4 block for the drawings: Vult, Vasd per edition, risk category, exposure, GCpi, C and C reference | Risk category; exposure; enclosure |
| SK-04 | Threshold and Special Inspection Screen | Threshold yes or no, with the triggering criterion; the special-inspection categories triggered | Assembly classification; height basis |
| SK-05 | Product Approval Requirements and Design-Pressure Check | Per opening: required design pressure (from SK-06 or the engineer of record's table), approval type by jurisdiction, ASD conversion, impact requirement | Pressure basis (strength vs ASD); WBDR status |

v2 - engines:

| Key | Skill | Out of scope by default |
| --- | --- | --- |
| SK-06 | C and C wind pressure table, ASCE 7-22 Ch. 30 Part 1, enclosed or partially enclosed, h <= 60 ft | h > 60 ft, open buildings, roof shapes not transcribed, topographic Kzt cases, irregular plans |
| SK-07 | Flood design data and flood-resistant construction requirements by zone | Coastal structures needing site-specific analysis |
| SK-09 | Existing-building structural triggers | Anything needing existing-member capacity |
| SK-10 | Delegated engineering coordination (precast, trusses, cold-formed steel, aluminum) | Design of the delegated system itself |

v3 - later:
- SK-08: small-structure uplift screen for tiki and chickee huts, awnings and carports. The engine
  computes uplift demand per post or connection; the result always names the connection design as
  engineer-of-record work.
- SK-11: member checks (headers, beams, posts). DECIDE D7: build in-house, or call an external
  calculator API.

A skill ships alone: draft, then validated (section 9), then live. Only live skills appear in the
question-type picker.

## 7. The result package - the only thing the engineer reviews

```python
@dataclass(frozen=True)
class Driver:
    key: str                 # "enclosure"
    chosen: str              # "enclosed, GCpi 0.18"
    alternative: str         # "partially enclosed, GCpi 0.55"
    effect: str              # human text built from the numbers below - never from a model
    base_value: float
    alt_value: float
    magnitude: float         # abs(alt_value - base_value) / abs(base_value)

@dataclass(frozen=True)
class ResultPackage:
    request_id: str
    skill_key: str
    skill_version: str
    scope_flag: str                     # "validated" | "out-of-scope"
    question: str                       # the requester's words
    answer_structured: dict             # rule and engine output - the source of every number
    answer_text: str                    # template or grounded AI prose (section 10)
    drivers: tuple[Driver, ...]         # top 3-5 by magnitude
    unverified_inputs: tuple[str, ...]  # requester facts not confirmed, prefills not confirmed
    citations: tuple[str, ...]          # corpus row ids and section numbers
    derivation_ref: str                 # full rule log and calc trace (drill-down only)
    content_hash: str                   # SHA-256 of canonical JSON of every field above
```

Driver ranking:
- For each judgment call the skill made, re-run the skill with the alternative.
- Compare the governing output; `magnitude` is the relative change.
- Put the top 3-5 on the card. The rest stay in the drill-down. Effect text is generated from
  `base_value` and `alt_value`, never written by a model.

Arithmetic test vectors, with Kd and GCpi supplied as test fixtures and not corpus rows:
- qh = 0.00256 x Kh x Kzt x Ke x V^2, with Kh = Kzt = Ke = 1.0 and V = 170: 0.00256 x 28,900 = 73.984 psf.
- Enclosure driver, internal-pressure change = qh x Kd x (0.55 - 0.18) with Kd = 0.85: 73.984 x 0.85 x 0.37 = 23.268 psf strength.
- In ASD terms: 23.268 x 0.6 = 13.961 psf. Assert within 0.01.

Card layout (the engineer UI shows exactly this, plus an optional drill-down):

```text
REQUEST <id>  from: <role>  skill: <key title vX>  scope: VALIDATED | OUT-OF-SCOPE
QUESTION   <requester's words>
ANSWER     <answer_text>   (structured table below it)
DECISIONS THAT DRIVE THIS ANSWER    chosen -> alternative : effect
  1 ...
NOT VERIFIED   <unverified_inputs>
DRILL-DOWN     citations, derivation, full rule log
[ APPROVE ]   [ REJECT  reason: <code>  note: <text> ]
```

## 8. Approval workflow

- Roles: requester, engineer and owner. Reuse Firebase Auth and the server-side allowlist; add role
  claims server-side. Engineer profiles carry name and Florida license number, entered by the
  owner (DECIDE D8). The owner checks them against the state license lookup by hand; do not
  automate that lookup.
- States: `draft -> awaiting_review -> approved | rejected`, then `approved -> released`, which is
  immutable. A resubmission after rejection is a new version linked to the prior one.
- Approve: the request body must carry the `content_hash` the engineer saw. The server refuses if
  it differs from the current package.
  - It stores engineer uid, name, license number, timestamp and hash.
  - Release serves exactly the hashed content. Test that any byte change voids the approval.
- Reject: a reason code is required, plus a note. Nothing is released; the requester sees the
  engineer's note.
  - Codes: `WRONG_ASSUMPTION`, `WRONG_CODE_BASIS`, `CALC_ERROR`, `OUT_OF_SCOPE_NEEDS_ENGINEER`,
    `INSUFFICIENT_INFO`, `OTHER` (note required).
  - Reason codes feed `webapp/triage.py`, which sorts each fix into a setting, a code change or a person.
- No other review screens exist. There are no per-rule approvals anywhere.
- Audit: an append-only decision log per request in Firestore, visible to the owner. Never log request
  content, prompts or model output (CLAUDE.md security rules).
- Endpoint shapes (match the existing router conventions):
  - `POST /api/struct/requests`
  - `GET /api/struct/requests/{id}`
  - `POST /api/struct/requests/{id}/answers` - the requester answers follow-up questions
  - `GET /api/struct/queue` - engineer role
  - `POST /api/struct/results/{id}/decision` with `{decision, reason_code, note, content_hash}`
  - `GET /api/struct/results/{id}` - released results only, for the requester
- Angular pages follow CLAUDE.md exactly (standalone, signals, zoneless, `@if`/`@for`, `inject()`,
  functional interceptors, Vitest, no component library):
  - request form and status
  - engineer queue and card
  - released result, showing "Reviewed and approved by <name>, PE <license>, <date>"

## 9. Validation - how a skill is trained before it goes live

- Gold set per skill: questions with engineer-approved answers, written by the engineers.
  - You build the schema (`fbcstruct/validation/gold_schema.py`), the runner and the scorecard.
    You never write gold answers.
  - Gold items with client data stay in the private bucket. Synthetic items may live in
    `tests/struct/gold/`.
- Grading, two parts:
  - Structured outputs are compared automatically within a stated tolerance.
  - Engineers then mark each run "would approve as-is" or "would reject" with a reason code.
- Launch gate: the Wilson lower bound (z = 1.96) on the would-approve rate must reach
  `STRUCT_GATE_LB`, default 0.90 and owner-configurable (DECIDE D5).

```text
LB = (p + z^2/(2n) - z*sqrt(p(1-p)/n + z^2/(4n^2))) / (1 + z^2/n)
With zero misses, p = 1 and LB = 1 / (1 + z^2/n):
  LB >= 0.90 needs n >= 3.8416 x 9  = 34.6 -> 35 clean runs
  LB >= 0.95 needs n >= 3.8416 x 19 = 73.0 -> 73 clean runs
```

Implement `wilson_lower_bound(successes, n, z=1.96)` with these vectors, each within 1e-4:
(98,100) = 0.9300, (49,50) = 0.8950, (50,50) = 0.9286, (30,30) = 0.8865, (100,100) = 0.9630.

- Re-validation: any change to a skill's rules, engine, or the corpus rows it cites bumps
  `version` and returns the skill to draft until it re-passes.
- Live monitoring, which costs the engineers no extra work: approval rate, rejection reasons and
  review seconds per skill. Flag any skill with approval >= 99 percent and median review under
  30 seconds; the thresholds are DECIDE D5.
- `scripts/struct_scorecard.py` prints per skill: n, accepts, Wilson LB, gate pass or fail, and
  the top rejection reasons.

## 10. AI drafting layer

- Off by default, like the reader: on only with `FBC_STRUCT_AI=on` and a key. With it off,
  `answer_text` comes from per-skill templates. The module must be complete without a model.
- Reuse the reader's client, model configuration and cache pattern. Do not hard-code a model name
  in this module.
- The model writes prose only. Its inputs:
  - `answer_structured`, `drivers`, `citations`, `unverified_inputs`
  - the question
  - up to three owner-promoted exemplars for the same skill
- Grounding check (`fbcstruct/ai/grounding.py`): every number and every section number in the
  returned text must appear in `answer_structured` or `citations`.
  - Otherwise discard the text and use the template.
  - Record the discard in the audit log, without content.
- The model never computes, chooses a driver, adds a citation or decides scope.
- Replayable: cache by request hash, skill version, prompt version and model. No test makes a
  network call.
- Prompt file: `fbcstruct/ai/prompts/draft_answer_v1.md`, containing Appendix A verbatim. Bump the
  version on any edit.

## 11. Claude Code project skill for future sessions

Create `.claude/skills/fbc-structural/SKILL.md`. The directory name becomes `/fbc-structural`.
- Description: load when working in `fbcstruct/`, `hazards/`, structural corpus rows, the approval
  workflow, or validation.
- Contents:
  - the KN and SK registries (summary)
  - the import boundaries
  - the corpus transcription rule
  - the approval-gate invariants
  - pointers to this prompt and `docs/STRUCT-PLAN.md`
- Keep SKILL.md under 500 lines. Put the full registries in `reference.md` beside it.
- Project skills load after the workspace-trust prompt is accepted. Say so in the report.

## 12. DECIDE items - ask the owner, do not assume

- D1: Run order relative to the deployment task, and the base branch.
- D2: `ASCE7["fbc2026"]`, after the owner checks Chapter 35 of the published 9th Edition.
- D3: Address and parcel geocoding source.
- D4: Where the FGDL layers live (repo or bucket) once their size is known.
- D5: `STRUCT_GATE_LB` and the monitoring thresholds.
- D6: Who may submit requests at launch: Meridian staff only, or clients too.
- D7: SK-11 engine source.
- D8: The reviewing engineers, with names and license numbers, entered by the owner.
- D9: The licensed ASCE 7-22 source and terms for KN-13 transcription.

## 13. Phases - one commit per phase, `pytest tests/ -v` green before each

0. `docs/STRUCT-PLAN.md`; `.claude/skills/fbc-structural/`.
1. Section 2 fix and tests.
2. `hazards/`:
   - FGDL ingestion with SHA-256 and point lookup, Risk Category I-IV
   - NFHL client with recorded-response replay
   - coastline distance for the WBDR screen
   - pinned geometry dependencies
3. Corpus scaffolding for KN-02, 03, 05, 06, 07, 08, 09, 10, 15 and 16, plus transcription
   worksheets. Rows load UNVERIFIED until filled and verified.
4. SK-01 to SK-05 with templates; AI off.
5. Result package, approval workflow, Angular pages.
6. Validation harness, scorecard, gold intake.
7. AI drafting layer: grounded, replayable, off by default.
8. SK-06 engine, after KN-13 is transcribed and verified. Validate against engineer-supplied worked
   examples.
- Later: SK-07, SK-09, SK-10, then SK-08 and SK-11.

## 14. Do not

- Do not generate, infer or fill any corpus value, map value or coefficient from memory.
  Transcription is human work from licensed text; you build the slot and the check.
- Do not let a model produce a number, threshold, citation, driver, routing decision or scope decision.
- Do not add a review step anywhere except the final package. No per-rule approvals, ever.
- Do not release anything unapproved, or anything whose hash differs from the approved hash.
- Do not let `fbcreview` import `fbcstruct`. Do not alter reviewer findings for the module.
- Do not guess missing facts. Ask the requester. When a skill cannot answer, mark the request
  out of scope and route it to an engineer-authored response.
- Do not commit client data, practice-library content, client gold items or credentials.
- Do not edit a test to make a phase pass.
- Do not guess Firebase, Google Cloud or Angular syntax. Check the current docs (CLAUDE.md).

## 15. Report back with

- STRUCT-PLAN.md: conflicts found, and DECIDE answers still needed.
- Per phase: files changed, tests added, pytest summary.
- Corpus status per KN item: rows present, verified, unverified.
- Scorecard output per skill, even while every skill is draft.
- What remains, in order. That list is the roadmap and belongs in the product, not only the commit log.

## Appendix A - fbcstruct/ai/prompts/draft_answer_v1.md

```text
You write the explanation part of a structural engineering result for a person who is not a
structural engineer. A licensed structural engineer will approve or reject the complete result
before anyone else sees it.

You receive QUESTION, STRUCTURED_RESULT, DRIVERS, CITATIONS, UNVERIFIED_INPUTS, and EXAMPLES of
previously approved explanations for the same kind of question.

Write exactly four labeled parts:
Answer: two to four plain-language sentences that answer QUESTION directly.
What this depends on: one sentence per item in DRIVERS - what was assumed, the alternative, and
the effect, using the values given.
What we could not confirm: one line per item in UNVERIFIED_INPUTS, saying what the requester
should confirm.
Code basis: the items in CITATIONS, verbatim.

Rules:
- Use only numbers, units and section numbers that appear in STRUCTURED_RESULT, DRIVERS or
  CITATIONS. Do not round differently, convert units, or introduce any new value.
- Describe alternatives; never choose between them.
- Do not add requirements, recommendations or code sections that are not in the inputs.
- Do not say or imply that the result is approved. Approval is shown separately.
- If the inputs do not support an answer, output exactly: INSUFFICIENT_INPUTS
- Plain ASCII. No other headings.
```

## Appendix B - sources to verify, then transcribe or ingest

- FGDL wind layers (UF GeoPlan manual): https://www.geoplan.ufl.edu/content/pdfs/Project_WindSpeed_Manual.pdf
- ASCE Hazard Tool API: https://www.asce.org/publications-and-news/asce-hazard-tool/api
- FEMA NFHL MapServer: https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer
- Florida Product Approval search: https://www.floridabuilding.org
- FBPE Rule 61G15-31 definitions: https://www.law.cornell.edu/regulations/florida/Fla-Admin-Code-Ann-R-61G15-31-002
- Responsible charge, Rule 61G15-18.011: https://www.law.cornell.edu/regulations/florida/Fla-Admin-Code-Ann-R-61G15-18-011
- Chickee amendment, 2026 HB 929 (enrolled): https://flsenate.gov/Session/Bill/2026/929/BillText/er/PDF
- 9th Edition timing, FBC workplan (August 2026): https://www.floridabuilding.org/fbc/commission/FBC_0726/Commission/2026_FBC_Workplan_August_2026.pdf
- Claude Code skills: https://code.claude.com/docs/en/skills

## Related (vault)

- [[2026-10-02 - Structural Knowledge Module with Engineer Review]]
- [[2026-10-02 - FBC Code Reviewer - Structural Consulting Module Feasibility]]
- [[2026-10-02 - FBC Code Reviewer - Structural AI Landscape and Build Plan]]
