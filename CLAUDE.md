# FBC Reviewer — standing rules

Read this before doing anything. These rules persist across every session in this repo and
outrank any one-off prompt that contradicts them.

## What this is

A deterministic Florida Building Code plan-review service. A user uploads a multi-sheet
architectural permit set as a PDF; the service extracts facts from the PDF's vector
geometry and text, runs a rule corpus against the FBC, and renders a marked-up PDF plus a
`findings.json`.

Read `ARCHITECTURE.md` before changing anything. `docs/reference/` has the review findings
from the two test sets and explains what the output is supposed to look like.

## The one non-negotiable property

**The review path makes zero LLM calls.** It is pure Python over PyMuPDF — about two
seconds of CPU for a 35-sheet set. This is the product's core claim, not an implementation
detail. Do not add a model call anywhere in the request path. If you find yourself reaching
for one, you have misread the problem.

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
- **`docs/FEATURE-PROMPT-inference-ladder.md`** is the standing plan for the extraction and
  geometry work. Six phases, each independently shippable. Work it in order unless you have
  a better reason than convenience.

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
- Never log PDF contents or full file paths.
- Authenticate with Workload Identity Federation in CI, never a downloaded key JSON.
- The email allowlist is checked server-side. A client-side check is decoration.

## Working discipline

- Run `pytest tests/ -v` before every commit. It is the regression gate.
- One commit per phase, message naming what changed and why.
- Do not guess at Google Cloud, Firebase, or Angular CLI syntax — those docs move. Check
  the current page before writing a command.
- When something is ambiguous, say so and ask. Do not invent a decision and bury it.

## Current task

`docs/DEPLOYMENT-PROMPT.md` — deploy this to `review.omniflexfitness.com`. Six phases.
Write the runbook to `docs/DEPLOYMENT.md` as you go.
