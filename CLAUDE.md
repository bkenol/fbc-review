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
| `fbcreview/` | **Do not modify.** Engine, rules, code corpus, renderer. If something here genuinely blocks you, stop and report it rather than editing it. |
| `webapp/` | The FastAPI service. Yours to harden and extend. |
| `web/` | The Angular client. Yours to build. |
| `tests/` | **Do not edit a test to make a change pass.** Fix the change. |
| `samples/` | Test permit sets. Git-ignored. Never commit a client PDF. |

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
