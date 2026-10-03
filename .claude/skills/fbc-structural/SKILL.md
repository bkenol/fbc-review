---
name: fbc-structural
description: Rules for the structural consult module. Load when working in fbcstruct/ or hazards/, on structural code-corpus rows in fbcreview/codes/, on the engineer approval workflow (webapp/struct/, /api/struct/*, the Angular struct pages), on skill validation, gold sets or the Wilson scorecard, or on STRUCT.WIND_STANDARD and the ASCE 7 edition mapping.
---

# fbc-structural — the structural consult module

The brief is `docs/FEATURE-PROMPT-structural-consult.md`. How it maps onto the code, the conflicts
found and the owner decisions still open are in `docs/STRUCT-PLAN.md` — **read §2 and §3 there
before starting any phase.** `CLAUDE.md` outranks both.

Full registries (every KN and SK row, endpoint list, reason codes) are in
[reference.md](reference.md).

## The principle

**AI drafts. Engines compute. The corpus holds every threshold. The engineer approves the result.**

- A model never produces a number, threshold, citation, driver, routing decision or scope decision.
- Engineers spend time in exactly two places: verifying the corpus rows a skill cites (once, before
  launch, with its gold answers) and approving or rejecting the final package (per request).
  **No per-rule approvals, ever.**
- No model training or fine-tuning. "Training" a skill = encode its knowledge, build its procedure,
  validate it against engineer-approved answers until it clears the launch gate.

## Status — check before you build

Phase 0 (plan, this skill) and phase 1 (`STRUCT.WIND_STANDARD` abstains when the in-force edition
has no ASCE 7 recorded) are done. **Phases 2–8 wait on owner decisions D1–D12** in
`docs/STRUCT-PLAN.md` §3. If a decision your phase needs is unanswered, stop and ask — do not
invent it.

## Layout

```text
hazards/          shared read-only site data (wind layers, flood client, coastline)
fbcreview/codes/  the ONE hand-transcribed corpus; structural rows go here
fbcstruct/        intake.py · skills/ · engines/ · package.py · validation/ · knowledge/ · ai/
webapp/struct/    routes, approval workflow, store (Firestore AND filesystem backend)
web/              Angular pages: request, status, engineer queue + card, released result
```

## Import boundaries — `tests/test_struct_boundaries.py`

Walk the import graph the way `tests/test_ai_guardrails.py` does (function bodies included).

- `fbcreview` never reaches `fbcstruct` or `webapp.struct`.
- `hazards` never reaches `fbcreview` or `fbcstruct`.
- Only `fbcstruct/intake.py` imports `fbcreview.pipeline` (for prefill) — `pipeline` lazily imports
  `fbcreview.ai.grounding`, so anything else importing it reaches the model layer.
- `fbcstruct/skills`, `engines`, `package.py`, `validation` and `fbcreview/codes` never reach
  `fbcstruct.ai`, `fbcreview.ai`, `fbcreview.pipeline` or `anthropic`. Skills take typed inputs.

## The corpus rule

Never fill a corpus value, map value or coefficient from memory — not "approximately", not "to get
the tests going". Transcription is human work from licensed text. You build:

1. the typed table and its loader (`CorpusRow` in `fbcreview/codes/structural.py`),
2. a provenance test,
3. a worksheet at `docs/struct-transcription/<table>.md` for a person to fill.

Every structural row carries `edition`, `section`, `value`, `source_locator`, `transcribed_by`,
`verified_by`, `verified_on`. A row with no `verified_by` is UNVERIFIED and yields no value; a
skill that needs it abstains with `corpus row <id> not verified`. Tests that need a value supply a
**test-local fixture row**, never a corpus edit (`monkeypatch.setitem`, as
`tests/test_wind_standard.py` does for `ASCE7["fbc2026"]`).

Table 1604.5 already exists in part as `fbc2023.RISK_III_ASSEMBLY_OL`; keep one source (STRUCT-PLAN C6).

## Approval-gate invariants

- States: `draft → awaiting_review → approved | rejected`; `approved → released` is immutable.
  A resubmission after rejection is a new version linked to the prior one.
- Approve carries the `content_hash` the engineer saw; the server refuses a mismatch. The record
  stores engineer uid, email, name, licence number, timestamp and hash. Release serves exactly the
  hashed bytes; any byte change voids the approval (tested field by field).
- Reject requires a reason code and a note; nothing is released.
- An engineer is an owner-entered profile re-read on every request — not a Firebase custom claim.
- **No decision is recorded while `FBC_DEV_UNSAFE_AUTH` is on** — every caller is `dev-local`
  then, and an approval by everybody is an approval by nobody.
- The audit log is append-only and never holds request content, prompts or model output.
- Drivers' effect text is built from `base_value`/`alt_value`, never by a model.

## AI drafting

Off unless `FBC_STRUCT_AI=on` and a key; templates otherwise, and the module is complete without a
model. Reuse `fbcreview.ai.reader.ReaderConfig` for the model — no model name in `fbcstruct`.
Grounding: every number and section number in the prose must appear in `answer_structured` or
`citations`, else discard and use the template. Cache by request hash, skill version, prompt
version, model. No test makes a network call.

## Validation

Wilson lower bound, z = 1.96, on engineers' "would approve as-is" marks must reach
`STRUCT_GATE_LB` (default 0.90 → 35 clean runs). Any change to a skill's rules, engine or cited
rows bumps `version` and returns it to draft. You build schema, runner, scorecard — **you never
write gold answers**. Synthetic gold items only in `tests/struct/gold/`.

## Do not

- commit client data, library content, client gold items, FGDL zips or credentials;
- edit a test to make a phase pass;
- alter or suppress a reviewer finding to suit the module;
- guess Firebase, Google Cloud or Angular syntax — check the current docs.
