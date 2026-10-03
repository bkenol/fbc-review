---
title: Structural Consult module - plan against the code as it is
type: plan
status: awaiting owner decisions
created: 2026-10-03
prompt: docs/FEATURE-PROMPT-structural-consult.md
---

# Structural Consult module — the plan, mapped onto the repo

`docs/FEATURE-PROMPT-structural-consult.md` is the brief. This file is what reading the repo
against it turned up: where each section of the brief lands in the code as it actually is, every
conflict found, and the decisions that are the owner's to make. **Phases 2 onward wait on the
answers in §3.**

Written 2026-10-03 against `main` at `05fd0d0` (PR #29 merged).

---

## 0. What this session did

| Phase | State | Where |
| --- | --- | --- |
| 0 — this plan and the project skill | **done** | this branch, `claude/structural-consult-plan` |
| 1 — `STRUCT.WIND_STANDARD` fix (brief §2) | **done, separate PR** | branch `claude/wind-standard-unrecorded-asce7` |
| 2 – 8 | **not started** — waiting on §3 | — |

Phase 1 went in its own pull request on purpose. It is a defect in the reviewer with a hard date
(2026-12-31), it does not depend on any DECIDE item, and it should merge whether or not the
consult module is ever built. Holding it behind the plan's decisions would have tied a live bug
to a design discussion.

Test gate: `pytest tests/ -q` on `main` was 1223 passed, 43 skipped. With the Phase 1 fix it is
1234 passed, 43 skipped (11 new tests). Phase 0 adds no code.

---

## 1. Section by section — the brief against the repo

| Brief § | What it assumes | What the repo actually has | Consequence |
| --- | --- | --- | --- |
| §1.4 | engine v2 may still be on PR #27 | PR #27 merged (`76a22f5`), then PR #29 (`05fd0d0`). `main` carries v2. | Base is `main`. No decision needed. |
| §1.5 | deployment may be unfinished | `docs/DEPLOYMENT.md` §0: Cloud Run **not deployed** (no billing account); Hosting and CI phases "not yet run"; the domain is live **through a Cloudflare tunnel from the workstation**. | **D1 is real.** See C1. |
| §2 | defect in `r_structural.py` | Reproduced exactly: an 8th Edition set reviewed as of 2027-01-15 gets *"Wind design is to ASCE 7-22, which 9th Edition replaced … adopts None … Re-read Vult … from None"*. | Fixed in Phase 1 (see §4). |
| §3 | `webapp/struct/` with Firestore persistence | The service runs on either Firestore + GCS **or** the filesystem stand-ins in `webapp/devbackend.py` (`FBC_BACKEND`). Without billing there is no bucket. | Every store needs both backends. See C3. |
| §3 | `fbcstruct` may import `fbcreview.pipeline` | `pipeline.py:445` imports `fbcreview.ai.grounding` inside a function; the import walker in `tests/test_ai_guardrails.py` counts function bodies. | Importing `pipeline` reaches `fbcreview.ai`. See C7. |
| §4 | corpus rows carry `transcribed_by`, `verified_by`, `verified_on` | `fbcreview/codes/fbc2023.py` rows are bare constants with provenance in module comments; none carry per-row fields. Table 1604.5 is already partly there as `RISK_III_ASSEMBLY_OL = 300`, used by `XSHEET.RISK_CATEGORY`. | New row type for structural rows; one source for 1604.5. See C6. |
| §5 | prefill via `build_facts()` | `webapp/prefill.py` already does exactly this for the declaration (`build_facts` → `drawn_declaration` → suggestions with source). The catalog reads `risk_category`, `exposure_category`, `wind_speed_mph`, `internal_pressure_gcpi`, `flood_zone`, `height_ft`, `stories`, `occupancy_group`, `occupant_load`, `code_edition`. Not read: address/parcel, mean roof height, roof type/slope, plan dimensions, enclosure. | Reuse the prefill path; the unread fields are always requester-entered. |
| §8 | "add role claims server-side" | The only role today is owner, from `FBC_OWNER_EMAILS` (`webapp/config.py:128`, `current_owner` in `server.py:224`). No Firebase custom claims anywhere. | Follow the repo's pattern, not custom claims. See C5. |
| §8 | approval record "follows the shape of" the feedback decision | `admin_decide_feedback` stamps `decided_by=owner.email`, `decided_at`, `decision_note`. | Same three fields, plus uid, engineer name, licence number and the hash. |
| §8 | Angular pages | `web/` is Angular 22, standalone, with an **OpenAPI-generated client** (`web/src/app/api/`, `openapi.json`, `scripts/dump_openapi.py`). | New routes regenerate `openapi.json` and the client; the pages use the generated services. |
| §10 | "reuse the reader's model configuration" | `fbcreview/ai/reader.py`: `ReaderConfig.from_env()`, `FBC_AI_MODEL`, default model constant there. | `fbcstruct/ai` reads `ReaderConfig`; no model name in `fbcstruct`. |
| §3, §13.2 | new top-level packages `hazards/`, `fbcstruct/` | `Dockerfile` copies only `fbcreview/`, `webapp/`, `run.py`. `.gitignore` ignores `*.zip` (and `*.dxf`). | Dockerfile gains two `COPY` lines; FGDL zips cannot be committed as they stand. See C8. |

---

## 2. Conflicts found

Numbered so the answers can refer to them. Each says what the brief says, what the repo says, and
what I would do.

### C1 — the deployment task is unfinished (→ D1)

`CLAUDE.md` names `docs/DEPLOYMENT-PROMPT.md` as the current task. Phases 5 (Hosting and domain)
and 6 (CI/CD) are marked *not yet run*; Cloud Run is blocked on billing; the service is published
from the workstation through a Cloudflare tunnel. I did not edit the *Current task* section.

**Recommendation:** run this module **after** the deployment reaches a state where engineers can
sign in from outside the workstation with their own identity (C4). Phases 2–4 (`hazards/`,
corpus scaffolding, SK-01..05 with templates) are pure Python with no service surface and can run
in parallel with the deployment; phase 5 (approval workflow) should not start until D1 is answered.

### C2 — base branch (resolved)

PR #27 is merged. `main` carries engine v2 and the CAD adapter. Branch from `main`.

### C3 — persistence is not only Firestore

The brief puts requests, the approval log, the practice library index, exemplars and client gold
items in Firestore and Cloud Storage. The live service may be on `FBC_BACKEND=filesystem`, and with
no billing account there is no bucket at all.

**Recommendation:** one `StructStore` interface with a Firestore implementation and a filesystem
one in `webapp/devbackend.py`, mirroring `FeedbackStore` / `LocalFeedbackStore`. Library content
and client gold items on the filesystem backend live under the existing git-ignored `.devdata/`.
The tests run against the filesystem backend, as the feedback tests do.

### C4 — an approval under `FBC_DEV_UNSAFE_AUTH` records nobody

`docs/DEPLOYMENT.md` §0d layer 1 runs the service with `FBC_DEV_UNSAFE_AUTH=1` behind Cloudflare
Access: every request arrives as uid `dev-local`. An engineer's approval made that way would be
stamped with a uid that is every user at once — exactly the "decision made by nobody" that
`CLAUDE.md` warns about for Firestore writes.

**Recommendation (an invariant, enforced by a test):** every `/api/struct/*` endpoint that
records an approval, a rejection or an engineer profile refuses with 503 while
`dev_unsafe_auth` is on. Requests and drafts may still be created, so the module is usable locally;
nothing can be approved or released without a real Firebase identity (§0d layer 2 or Cloud Run).

### C5 — roles: server-side lists, not custom claims

The brief says "add role claims server-side". The repo has no custom claims; the owner gate is the
`FBC_OWNER_EMAILS` list checked on every request. Custom claims live inside the ID token, so a
revoked engineer keeps the role until the token refreshes (up to an hour) unless every check also
re-reads the user record.

**Recommendation:** an engineer is an **engineer profile record** — email, uid once they first sign
in, name, Florida licence number, `active`, `added_by`, `added_at` — written by the owner through an
owner-only endpoint, and re-read on every engineer request. Deactivating is immediate. The
requester role is "on the allowlist" (D6 decides who that is). No change to Firebase custom claims.
The owner checks each licence against the state lookup by hand, as the brief says.

### C6 — corpus row shape, and two transcriptions of Table 1604.5

Existing rows (`fbc2023.py`) carry provenance in module docstrings; the brief requires per-row
`edition`, `section`, `value`, `source_locator`, `transcribed_by`, `verified_by`, `verified_on`.

**Recommendation:**

- A frozen `CorpusRow` dataclass in a new `fbcreview/codes/structural.py` (and per-table modules
  beside it as they grow). Only the new structural rows use it. Existing rows are not retrofitted
  in this work — that is a separate decision about the reviewer's corpus.
- A row with no `verified_by` loads as `UNVERIFIED`; the lookup returns a typed
  `Unverified(row_id)` rather than a value, so a skill cannot read the number without handling the
  abstention.
- `tests/test_struct_corpus.py` asserts every row has every provenance field and that no `value`
  is filled while `transcribed_by` is empty.
- **Table 1604.5 must have one source.** `fbc2023.RISK_III_ASSEMBLY_OL = 300` already drives
  `XSHEET.RISK_CATEGORY`. Once KN-02 is transcribed and verified, the reviewer constant should read
  from it, and until then a test asserts the two agree. Re-pointing the reviewer is a reviewer
  change and goes through the regression gate with its result explained.

### C7 — `fbcreview.pipeline` reaches `fbcreview.ai`

The brief allows `fbcstruct → fbcreview.pipeline` and forbids `fbcstruct/skills` and
`fbcstruct/engines` from reaching `fbcstruct/ai` or `anthropic`. `pipeline` lazily imports
`fbcreview.ai.grounding`, which the AST walker sees.

**Recommendation:** stricter than the brief, in `tests/test_struct_boundaries.py`:

- only `fbcstruct/intake.py` (prefill) may import `fbcreview.pipeline`;
- the import closure of `fbcstruct/skills`, `fbcstruct/engines`, `fbcstruct/package.py`,
  `fbcstruct/validation` and `hazards` contains no `fbcstruct.ai`, no `fbcreview.ai`, no
  `fbcreview.pipeline` and no `anthropic`;
- `fbcreview` never reaches `fbcstruct` or `webapp.struct`; `hazards` never reaches `fbcreview` or
  `fbcstruct`.

Skills then take typed inputs only — a skill can never see a sheet, so it can never be argued into
a number by one.

### C8 — packaging and large data

- `Dockerfile`: add `COPY hazards/ ./hazards/` and `COPY fbcstruct/ ./fbcstruct/`.
- Geometry: point-in-polygon and coastline distance need `shapely` and `pyproj` (and `pyshp` or
  `fiona` to read the layers). Pinned in `requirements.txt` with the reason, as every other pin
  there is. Wheel sizes go in the phase 2 commit message.
- `.gitignore` ignores `*.zip`; the FGDL layers ship zipped. The ingestion script downloads to
  `.devdata/hazards/`, records URL, retrieval date and SHA-256 in a committed manifest, and
  converts to a compact form. Whether the converted layers are committed depends on size — D4.

### C9 — the time-bombed tests (brief §2, last bullet)

Checked two ways:

1. `grep` for every test that runs the rule set without `facts.meta["as_of"]`.
2. The whole suite run with every rule's `today()` moved to 2027-01-15 (a pytest plugin outside the
   repo patches `r_structural.dt` and `r_code.dt`): **1223 passed, 43 skipped — the same as
   today, and the same with the defect still in place.** Nothing committed would have caught it.

Tests that run the full rule set unpinned on an 8th Edition fixture with a stated Vult:

| Test | Fixture | After 2026-12-31 | Assertion affected? |
| --- | --- | --- | --- |
| `tests/test_reference_sets.py` (module fixture `sculpted`) | real Sculpted set, 8th Ed., Vult 170 — **skipped unless `FBC_TEST_PDF`** | `STRUCT.WIND_STANDARD` V-WIND → abstention (with the fix; H-WIND without it); `CODE.EDITION_CURRENT` V-ED → C-ED CRITICAL | **No.** No assertion names either rule; `scripts/scorecard.py` grades neither; "every rule fires or abstains" still holds. |
| `tests/test_regression.py` `main()` | same | same | No — and pytest collects no test from it. |

The other unpinned `run_all` calls (`test_factstore.py`, `test_ai_review.py`,
`test_declaration.py:135`, `test_ai_guardrails.py`) use fixtures with no code edition or no wind
speed, or compare two runs on the same day.

**Not pinned, and why:** pinning `as_of` in the Sculpted fixture is a test edit, and nothing it
asserts changes. I would still pin it — a regression gate whose output depends on the calendar is
one surprise away from somebody re-baselining a real change — but that is a `tests/` edit and the
owner's call (listed in §5).

### C10 — `CODE.EDITION_CURRENT` on 8th → 9th (→ D11)

From 2026-12-31 every 8th Edition set gets C-ED **CRITICAL** with: *"…it is a re-analysis rather
than a cover-sheet edit: 2026 Florida Building Code, 9th Edition adopts a different referenced
standard set in place of ASCE 7-22."* With the corpus as it is, that sentence asserts a standard
change nobody has recorded; if D2 records ASCE 7-22, it is false.

`tests/test_code_edition.py` locks the 7th → 8th sentences at `AS_OF = 2026-08-25`; it does not
lock the 8th → 9th branch, so a change there would not break it — which is exactly why it needs the
owner's eye rather than a quiet edit.

**Proposal (not implemented):** branch on what the corpus knows.

- `current.asce7` differs from `cited.asce7` → unchanged (the confirmed wording).
- `current.asce7` equals `cited.asce7` → keep C-ED OPEN, but drop the "re-analysis … adopts X in
  place of Y" clause and say instead that the wind standard is unchanged and the referenced
  standards in Chapter 35 must be checked edition by edition. Severity: **HIGH rather than
  CRITICAL** — the reason the reviewer gave for CRITICAL ("the referenced standard moved, so the
  wind numbers move with it") does not hold.
- `current.asce7` is `None` → same as above but "the corpus does not yet record what the
  9th Edition adopts", and severity HIGH.

### C11 — what an approved package *is*, legally (→ D10)

The brief shows the requester *"Reviewed and approved by <name>, PE <license>, <date>"*, delivers
engineering answers to people who are not engineers, and lists Rule 61G15-18.011 (responsible
charge) in Appendix B without using it anywhere. Open questions the code cannot answer:

- Is a released package an engineering document that must be signed and sealed (Rule 61G15-23)?
  If so, the approval click is not the seal, and the release format has to carry one.
- Does offering it require a certificate of authorization for the business (F.S. 471.023), and
  whose — OmniFlex's, Meridian's or the engineer's firm?
- Does approving a package the engineer did not prepare satisfy *responsible charge* under
  61G15-18.011, given the module drafted it?

I am not giving a legal answer. **Recommendation:** get a Florida licensing attorney's (or FBPE's)
read before D6 opens requests to anyone outside Meridian, and before the released-result wording
is fixed. Build phases 2–4 meanwhile; they produce nothing a requester sees.

### C12 — who operates this (→ D12)

The brief speaks of "engineers working with Meridian" and "Meridian's work"; the repository is
OmniFlex's FBC reviewer at `fbc.omniflexfitness.com` (`CLAUDE.md`: the only hostname that
answers). Same service, same allowlist, same owner? Same hostname? The "independence" clause in
brief §3 ("the same account") also needs a definition — the repo's account is an email, so I would
define *same account* as same uid or same email, and say so on the review.

### C13 — smaller things, decided in the plan unless the owner objects

- **Abstention vocabulary.** Structural abstentions ("corpus row … not verified", "out of scope:
  …") reuse the reviewer's `Abstention` shape so `webapp/abstentions.py` can classify them.
- **Reason codes → triage.** `webapp/triage.py` folds feedback-schema verdicts; rejection codes map
  onto its ladder as `WRONG_CODE_BASIS → escalate` (citations never auto-tune, as for feedback),
  `CALC_ERROR → needs_component`, `WRONG_ASSUMPTION → needs_component`,
  `INSUFFICIENT_INFO → needs_component` (an intake question is missing),
  `OUT_OF_SCOPE_NEEDS_ENGINEER → escalate`, `OTHER → escalate`. Nothing auto-tunes.
- **Content hash.** Canonical JSON = `json.dumps(sort_keys=True, separators=(",", ":"),
  ensure_ascii=False)` over every field but `content_hash`, floats as `repr` round-trips; the
  test mutates one byte of every field in turn and asserts the stored approval no longer matches.
- **Exemplars and gold items** are never committed; `tests/struct/gold/` carries synthetic items
  only, each with `"synthetic": true` asserted by a test.
- **The brief's arithmetic and Wilson vectors check out** (computed this session): qh = 73.984 psf;
  enclosure delta 23.268 psf strength, 13.961 psf ASD; Wilson LB (98,100) 0.92999,
  (49,50) 0.89504, (50,50) 0.92865, (30,30) 0.88648, (100,100) 0.96301; 35 clean runs give
  LB 0.9011 (34 give 0.8985); 73 give 0.95001 (72 give 0.9493).

---

## 3. DECIDE — what I need from the owner

The brief's D1–D9, plus three the repo raised (D10–D12). Each has my recommendation; overrule any
of them.

| # | Question | Recommendation | Blocks |
| --- | --- | --- | --- |
| **D1** | Run order vs the deployment; base branch | Base `main` (settled). Phases 2–4 now, in parallel with the deployment; phase 5 onward after engineers can sign in with real identities (C1, C4). | phase 5+ |
| **D2** | `ASCE7["fbc2026"]` | Leave `None` until you have read Chapter 35 of the published 9th Edition, then set it with the supplement list in the row comment. The Phase 1 fix makes `None` safe. | the wind rule's 9th Ed. answers; SK-03 for 9th Ed. sets |
| **D3** | Geocoding source | U.S. Census Geocoder (free, no key) for address → point; county property-appraiser parcel layers for parcel IDs, county by county as Meridian needs them. Recorded responses for tests. | SK-01 |
| **D4** | Where FGDL layers live | Decide once phase 2 measures the converted size: under ~20 MB committed (with manifest and SHA-256); over that, `.devdata/` locally and the bucket when billing exists, fetched at build time. | phase 2 |
| **D5** | `STRUCT_GATE_LB`; monitoring thresholds | 0.90 to launch (35 clean runs), 0.95 for any skill that outputs a design pressure (SK-05, SK-06). Flag at approval ≥ 99 % with median review < 30 s, as the brief says. | phase 6 |
| **D6** | Who may submit at launch | Meridian staff only, until D10 is answered. | phase 5 |
| **D7** | SK-11 engine | Defer; nothing before SK-06 needs it. | v3 |
| **D8** | The engineers: names, licence numbers | Owner enters them through the profile endpoint (C5). | first approval |
| **D9** | Licensed ASCE 7-22 source and terms for KN-13 | Needed before any KN-13 transcription starts. | phase 8 |
| **D10** *(new)* | Legal status of a released package; seal; responsible charge; certificate of authorization (C11) | Attorney / FBPE read before clients can submit. | D6 widening; release wording |
| **D11** *(new)* | `CODE.EDITION_CURRENT` wording and severity for 8th → 9th (C10) | Adopt the C10 proposal before 2026-12-31. | reviewer output from 2026-12-31 |
| **D12** *(new)* | Operator, hostname and account boundary: OmniFlex or Meridian; `fbc.omniflexfitness.com` or elsewhere; what "same account" means (C12) | Same service and hostname; *same account* = same uid or email. | phase 5 |

---

## 4. Phase 1 as done

Separate PR, branch `claude/wind-standard-unrecorded-asce7`, one commit.

- `fbcreview/rules/r_structural.py` — the brief's fix, verbatim in substance: abstain with
  *"the edition in force has no ASCE 7 adoption recorded"* (detail = the in-force edition's key)
  when a newer edition is in force and the corpus records no ASCE 7 for it; *superseded for wind*
  only when the in-force edition adopts a different ASCE 7.
- `webapp/abstentions.py` — the new reason classified as a corpus gap
  (`tests/test_abstentions.py` requires every emitted reason to be classified).
- `tests/test_wind_standard.py` — 11 tests: abstains for 7th and 8th Edition sets at 2027-01-15;
  no finding in the whole review prints "adopts None" / "from None" / "→ None"; with the 9th
  Edition patched to ASCE 7-22 **inside the test** (`monkeypatch.setitem`, never the corpus) a 7th
  Edition set gets H-WIND naming ASCE 7-22 and an 8th Edition set gets V-WIND; before 2026-12-31
  nothing changes; the boundary is the effective date. Six of them fail on the old rule.
- Behaviour change to note: after 2026-12-31, **7th** Edition sets also abstain instead of getting
  H-WIND, until D2 is answered. That is the brief's "abstains for every set", and it is honest —
  but it is a lost finding, and D2 restores it.

---

## 5. Phases 2–8 against the files they will touch

One commit per phase, `pytest tests/ -v` green before each.

| Phase | Files | Tests | Waits on |
| --- | --- | --- | --- |
| 2 `hazards/` | `hazards/__init__.py`, `wind.py` (FGDL point lookup RC I–IV), `flood.py` (NFHL client, replayed responses), `coast.py` (distance to mean high-water line), `manifest.json`, `scripts/fetch_hazards.py`; `requirements.txt` pins; `Dockerfile` COPY | `tests/hazards/` — manifest SHA-256 matches; lookups on recorded fixtures; no network (a socket guard in the test module) | D3, D4 |
| 3 corpus scaffolding | `fbcreview/codes/structural.py` (`CorpusRow`, `Unverified`, loaders) and one module per table for KN-02, 03, 05, 06, 07, 08, 09, 10, 15, 16 — every `value` empty; `docs/struct-transcription/<table>.md` worksheets | `tests/test_struct_corpus.py` — provenance fields, unverified rows refuse to yield a value, 1604.5 agrees with `RISK_III_ASSEMBLY_OL` | nothing (no values are filled) |
| 4 SK-01..05 | `fbcstruct/{__init__,spec,intake}.py`, `fbcstruct/skills/sk01..sk05.py`, `fbcstruct/templates/` | `tests/struct/` — every skill abstains with "corpus row … not verified" today; with test-local verified rows (fixtures, never corpus), the arithmetic and the driver ranking; `tests/test_struct_boundaries.py` | phase 3 |
| 5 package + approval + pages | `fbcstruct/package.py`; `webapp/struct/{routes,store,models}.py`; `webapp/devbackend.py` local store; `server.py` include; `openapi.json` + generated client; `web/src/app/struct/` (request, status, queue + card, released result) | hash voids approval on any byte change; approve refuses on hash mismatch; release is immutable; dev-unsafe-auth refuses decisions (C4); engineer profile deactivation is immediate; Vitest for the pages | D1, D6, D8, D10, D12 |
| 6 validation | `fbcstruct/validation/{gold_schema,runner,wilson}.py`, `scripts/struct_scorecard.py`, `tests/struct/gold/` (synthetic only) | the Wilson vectors; gate pass/fail at `STRUCT_GATE_LB`; synthetic-only assertion | D5 |
| 7 AI drafting | `fbcstruct/ai/{draft,grounding,cache}.py`, `fbcstruct/ai/prompts/draft_answer_v1.md` (Appendix A verbatim) | off by default; grounding discards any number or section not in the inputs; replay from cache; no network | — |
| 8 SK-06 engine | `fbcstruct/engines/asce7_22_cc.py`, `fbcstruct/skills/sk06.py` | the brief's arithmetic vectors with fixture coefficients; engineer-supplied worked examples | D9, KN-13 transcribed and verified |

Proposed but **not** done, because they edit `tests/`: pin `as_of` in the Sculpted fixture of
`tests/test_reference_sets.py` (C9).

---

## 6. Corpus status per KN item

Nothing has been transcribed for this module. "Rows" means rows in the structural row format
(C6); the reviewer's existing constants are noted where they overlap.

| KN | Rows | Verified | Unverified | Note |
| --- | --- | --- | --- | --- |
| KN-01 editions, ASCE 7 per edition | 4 editions in `editions.py` (old format) | — | — | `fbc2026` ASCE 7 = `None` (D2) |
| KN-02 Table 1604.5 | 0 | 0 | 0 | `fbc2023.RISK_III_ASSEMBLY_OL = 300` exists (C6) |
| KN-03 1603.1 | 0 | 0 | 0 | |
| KN-04 FGDL wind layers | DATA, not fetched | — | — | D4 |
| KN-05 Vult → Vasd | 0 | 0 | 0 | |
| KN-06 HPR / WBDR | 0 | 0 | 0 | |
| KN-07 HVHZ | 0 | 0 | 0 | |
| KN-08 product approval | 0 | 0 | 0 | |
| KN-09 threshold building | 0 | 0 | 0 | |
| KN-10 special inspections | 0 | 0 | 0 | |
| KN-11 flood | DATA + CORPUS, nothing yet | — | — | |
| KN-12 exposure | 0 | 0 | 0 | |
| KN-13 C&C coefficients | 0 | 0 | 0 | D9 first |
| KN-14 load combinations | 0 | 0 | 0 | |
| KN-15 61G15-31 | 0 | 0 | 0 | |
| KN-16 chickee exemption | 0 | 0 | 0 | from the chaptered 2026 law |
| KN-17 FBC-EB triggers | 0 | 0 | 0 | |
| KN-18 Chapter 35 editions | 0 | 0 | 0 | D2 reads the same chapter |
| KN-19 library | — | — | — | engineers |
| KN-20 exemplars | — | — | — | owner-promoted |

## 7. Scorecard per skill

No skill exists yet; `scripts/struct_scorecard.py` is phase 6. Every skill SK-01..SK-11 is
*not started* (n = 0, gate not evaluated).

## 8. What remains, in order

1. Owner answers D1, D2, D10, D11, D12 (the ones that block or have a date).
2. Merge the Phase 1 PR before 2026-12-31; decide D11 before the same date.
3. Phase 2 `hazards/` (after D3, D4).
4. Phase 3 corpus scaffolding and worksheets; people transcribe; engineers verify.
5. Phase 4 SK-01..SK-05, templates, AI off.
6. Phase 5 package, approval, pages (after D1, D6, D8, D10, D12).
7. Phase 6 validation harness; engineers write gold answers; skills clear the gate one by one.
8. Phase 7 AI drafting.
9. Phase 8 SK-06 after KN-13 (D9).
10. Later: SK-07, SK-09, SK-10, then SK-08 and SK-11 (D7).
