# FBC Reviewer — standing rules

Read this before doing anything. These rules persist across every session in this repo and
outrank any one-off prompt that contradicts them.

## What this is

A Florida Building Code plan-review service. A user uploads a multi-sheet architectural
permit set as a PDF; the service lays out each sheet, reads facts off it — a deterministic
reader and, when configured, an AI reader whose every value is verified against the sheet —
runs a rule corpus against the FBC, and renders a marked-up PDF plus a `findings.json`.

Read `docs/ARCHITECTURE-V2.md` before changing anything, and `docs/ENGINE-TEARDOWN.md` for
why it is shaped that way. `ARCHITECTURE.md` is the original three-tier reasoning.
`docs/reference/` has the review findings from the two test sets and explains what the
output is supposed to look like.

## AI reads; rules decide

The review path may call a model — decided by the owner on 2026-09-27, replacing the old
"zero LLM calls" rule. What it may do is narrow, and every line below is enforced by a test:

1. **The reader's output can only become a claim** — "this value is printed here on this
   sheet". Never a finding, a severity, a code threshold, a citation or an abstention
   reason. The first decision on compliance is made by pure-Python rules over the fact
   store and the hand-verified code corpus; the reviewer in rule 7 may then change it.
2. **Grounded or discarded.** Every AI-proposed value carries a verbatim quote, and
   `fbcreview/ai/grounding.py` must find that quote on that page's text (live or OCR) and
   re-derive the value from it with the catalog parser. A proposal that fails is kept for
   audit and never reaches a rule.
3. **Deterministic floor.** With AI reading off, unconfigured, refused or failing, the
   review completes on the deterministic reader alone. An AI failure makes a smaller
   review, never a failed one.
4. **Replayable.** Readings are cached by file hash, page, model and prompt version, and
   stored with the job. Tests and re-runs replay them. No test makes a network call.
5. **Provenance is visible.** A value the model located says so on the finding.
6. **`fbcreview/rules` and `fbcreview/codes` never import `fbcreview/ai` or `anthropic`.**
7. **AI reviews and corrects the result.** Decided by the owner on 2026-09-28, and amended
   the same day to let the reviewer change findings directly. After the rules run, a
   reviewer model checks the result against what the user asked for and the sheets, and
   may **revise** a finding (severity, status, title, result, remedy, citation), **add** one
   the rules missed, or **withdraw** one — as well as send sheets back to be read again and
   leave notes. Pass 1 checks, pass 2 actively edits, pass 3 verifies: at most **three
   passes**, however it is configured. What holds for every edit, and is enforced in
   `fbcreview/ai/review.py` and by tests:
   - **Labelled.** Every applied edit is marked on the finding — its result says it was
     revised or raised by the AI review and why, and `findings.json` carries `ai_revision`.
     An AI-edited value never passes for a rule's.
   - **Evidenced.** Adding or withdrawing a finding, or changing a severity or status, needs
     a quote printed on the sheet; an edit whose quote is not found is not applied.
   - **Withdrawn is not passed.** A withdrawn finding becomes an abstention saying so.
   - **Replayable, with a floor.** The trace is stored with the job (`ai_review.json`) and
     replayed like readings; a failed pass keeps the last good state.
   - **The code corpus is untouched.** The reviewer changes findings, never
     `fbcreview/codes/`, and a citation it adds is its own, not a corpus row.

If you find yourself letting the *reader* decide whether something complies, or letting any
model write a threshold into the code corpus, you have misread the problem. The reviewer is
the one place a model may change a finding, and only in the labelled, evidenced way above.

## Ownership boundaries

| Directory | Rule |
| --- | --- |
| `fbcreview/` | Engine, rules, code corpus, renderer. **Yours to refine** — see *Refining the engine* below for what has to survive the refinement. |
| `webapp/` | The FastAPI service. Yours to harden and extend. |
| `web/` | The Angular client. Yours to build. |
| `tests/` | **Do not edit a test to make a change pass.** Fix the change. |
| `samples/` | Test permit sets. Git-ignored. Never commit a client PDF. |

## Refining the engine

The engine is the product and it is not finished. Extraction is too literal, most
abstentions say "the set does not state this" about values the set is holding up in front
of us, and geometry is unreachable unless somebody guessed the CAD layer name correctly.
Refining that is wanted work, not a boundary violation.

**Go and improve it.** Specifically:

- **Feedback is the input.** What reviewers submit through Refine analysis, and what comes
  out of sessions like this one, is evidence about where the engine is wrong. Read
  `docs/TRAINING-MODE.md` §6 and the feedback queue; a recurring complaint about a rule is
  a reason to change the rule, not only a reason to move a calibration lever.
- **Reproduce before you change.** A fix begins with the failing input written down — the
  sheet text, the phrasing, the geometry — and a test carrying it. "It seems better" is not
  a reason to ship anything.
- **`docs/ARCHITECTURE-V2.md`** is the standing architecture: layout → readers → fact
  store → rules. `docs/FEATURE-PROMPT-inference-ladder.md` is still the plan for the
  measured rungs (tabulated, measured, footprint tracing); its §0 and its first "do not"
  bullet are superseded by the section above.
- **Measure with the scorecard.** `python scripts/scorecard.py <set.pdf>` reports how much
  of the hand-built Sculpted review the engine reproduces. A refinement that does not move
  it, or moves it down, needs a reason.

### What has to survive every refinement

These are not style preferences. Each one is the reason somebody can act on what this tool
says, and a refinement that breaks one has made the product worse however much it improves
the numbers.

1. **Abstention stays honest.** "Not checked" must never become indistinguishable from
   "checked and passed". Widening a rule's reach is good; widening it by lowering the bar
   for what counts as an answer is not.
2. **Every value carries its provenance.** `Evidence` records where a number came from and
   how much to trust it. A refinement that produces a value without a derivation somebody
   can check on the sheet in thirty seconds is not an improvement.
3. **An inferred value never masquerades as a stated one.** Estimation is welcome; laundering
   an estimate into a fact is not. Anything measured or inferred says so, in the finding.
4. **The code corpus stays hand-transcribed.** `fbcreview/codes/` is checked by hand rather
   than scraped, which is why it is trusted. Adding a row is deliberate work with a citation.
   Never generate it.
5. **The regression gate holds.** `tests/` on the reference sets is what tells you a change
   did what you meant and nothing else. A finding that changes severity or citation is a
   result to explain, not noise to re-baseline.

## Stack — decided, do not relitigate

- **Backend: Python + FastAPI + PyMuPDF.** Not portable to TypeScript — the engine depends
  on `get_drawings()`, `find_tables()`, optional-content-group access and `/Measure`
  viewport dictionaries. Nothing in the JS ecosystem matches that. Python owns the PDF.
- **Frontend: Angular 22**, standalone components, signals, zoneless, strict mode.
- **Hosting: Firebase Hosting** (static bundle + `/api/**` rewrite) in front of
  **Cloud Run** `us-east1`. Firestore for job records, Cloud Storage for artefacts.
- **Auth: Firebase Authentication**, Google provider, server-side email allowlist.
- **Hostname: `fbc.omniflexfitness.com`.** The canonical one, and the only one that
  answers. `review.omniflexfitness.com` appeared in this file and in
  `docs/DEPLOYMENT-PROMPT.md` and was never implemented — `cors.json`, every script's
  `FBC_DOMAIN` default, the Cloudflare tunnel config and `docs/DEPLOYMENT.md` have
  always said `fbc.`. Two names for one service sent a session chasing a network
  fault that did not exist. Do not reintroduce the other one.

## Angular — you will get this wrong from memory

Angular changed substantially between v16 and v22 and models reliably generate the old
version of it. Verify every API against `angular.dev` rather than recall. Specifically:

- **No `NgModule`.** Standalone components, `bootstrapApplication`.
- **No `zone.js`.** Zoneless is the default from v21 and zone.js is not bundled. Adding it
  to polyfills means you have generated a v16 app.
- **Signals** (`signal`, `computed`, `input`, `output`) for component state — not
  `BehaviorSubject`. RxJS stays for the polling stream, where it earns its place.
- **`@if` / `@for` / `@switch`**, not `*ngIf` / `*ngFor`.
- **`inject()`**, not constructor parameter injection.
- **`provideHttpClient(withInterceptors([...]))`** with functional `HttpInterceptorFn` —
  not class-based `HTTP_INTERCEPTORS`.
- **Vitest**, not Karma.
- `"strict": true` and `"strictTemplates": true` stay on.

No Angular Material, no Tailwind, no component library, no state management library. This
is one page and `webapp/static/index.html` is about 200 lines of CSS. Port that restraint.

## Security rules

- Never commit service account keys, `.env` files, or any client PDF.
- Never enable public access on the storage bucket. V4 signed URLs only.
- Never log PDF contents or full file paths — and that includes prompts, sheet images and
  model responses, which carry sheet content.
- The AI reader sends sheet images and text to the Anthropic API only when the deployment
  sets `FBC_AI_READING=on` and supplies a key. `ANTHROPIC_API_KEY` is a secret like any
  other: Secret Manager in production, `secrets/local.env` locally, never the repo.
- Authenticate with Workload Identity Federation in CI, never a downloaded key JSON.
- The email allowlist is checked server-side. A client-side check is decoration.

## Operating the live deployment

Running against the deployed service is **allowed and expected** — marking feedback
actioned, promoting a calibration profile, reading the queue, sending a digest. These are
ordinary operations, not a boundary to stop at, and needing a person to click a button in
a browser for every one of them is a bottleneck rather than a safeguard.

What holds instead of a prohibition:

- **Go through the API, not the database.** `POST /api/admin/feedback/{id}/decision` also
  stamps `decided_at`, `decided_by` and `decision_note`; a document written straight into
  Firestore records a decision made by nobody at no time. Reach for the collection only
  when the service itself is unreachable, and then write every field the endpoint would
  have.
- **Short-lived credentials only.** A Firebase ID token or `gcloud` ADC is fine. A
  downloaded service-account key is not — that is the same rule as the one above about CI,
  for the same reason.
- **A credential in a chat transcript is disclosed.** Treat anything pasted into a session
  as burned: use it, say so, and say it should be rotated. Never write one into the repo,
  a commit message, a log line or a test fixture.
- **Confirm before anything irreversible or outward-facing.** Promoting a profile changes
  what every future review tells a permit applicant; sending a digest mails people.
  Marking one piece of feedback actioned, after doing the work it asked for, does not need
  asking twice.
- **Say what you did.** Name the endpoint, the ids and the outcome, so the action is
  auditable from the conversation as well as from the record.

## Working discipline

- Run `pytest tests/ -v` before every commit. It is the regression gate.
- One commit per phase, message naming what changed and why.
- Do not guess at Google Cloud, Firebase, or Angular CLI syntax — those docs move. Check
  the current page before writing a command.
- When something is ambiguous, say so and ask. Do not invent a decision and bury it.
- **Name every branch after its topic**: `claude/<what-changes>` in kebab-case, e.g.
  `claude/cad-dwg-dxf-input` or `claude/no-billing-local-backend`. Never a generated name
  (`cloud-dev/relaxed-lamport-55sv0v`) and never a random suffix. A session handed a
  generated branch renames it before its first push (`git branch -m <old> <new>`, then
  `git push -u origin <new>`). This rule is the owner's standing permission to push under
  the topic name instead of the assigned one. Decided by the owner on 2026-10-03.

## Current task

`docs/DEPLOYMENT-PROMPT.md` — deploy this to `fbc.omniflexfitness.com`. Six phases.
Write the runbook to `docs/DEPLOYMENT.md` as you go.
