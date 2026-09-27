---
title: Claude Code Prompt — Deploy FBC Reviewer to fbc.omniflexfitness.com
type: runbook
tags:
  - code-review
  - deployment
  - google-cloud
  - angular
  - typescript
  - source/cowork
status: draft
created: 2026-08-21
source-session: Meridian Drafting
---

# Claude Code prompt — deploy the FBC Reviewer to `fbc.omniflexfitness.com`

> **Hostname.** This prompt originally said `review.omniflexfitness.com`. Nothing was
> ever built against that name — `cors.json`, the scripts, the tunnel config and
> `docs/DEPLOYMENT.md` have always used `fbc.omniflexfitness.com`, which is what
> answers. The name is settled in `CLAUDE.md` and corrected throughout below.

Open Claude Code in `C:\Antigravity\fbc-review` and paste everything below the line.
The repo is already unzipped there and `CLAUDE.md` in that folder carries the standing
rules — this prompt is the one-time kickoff.

---

## Context

You are deploying an existing, working Python service and building a new Angular
frontend for it. **Do not redesign the backend engine.** The review engine is finished and
regression-tested; your job is to make it safe to expose, give it a real client, put it on
Google Cloud, and wire a custom domain. Read `ARCHITECTURE.md` and `webapp/README.md`
before touching anything.

**What the service does:** a user uploads a multi-sheet architectural permit set as a PDF
and chooses review parameters (code edition, occupancy group, sprinklered yes/no, minimum
severity, whether to include verified and measured items). The service extracts facts from
the PDF's vector geometry and text — schedules, code-analysis blocks, drawing scale, door
sizes — runs a deterministic rule corpus against the Florida Building Code, and renders a
marked-up PDF: every original sheet with a review margin added on the left containing
finding cards, colour-coded markers anchored on the drawing, plus a summary register
section appended at the back. It also emits `findings.json`.

> **Superseded 2026-09-27.** The zero-LLM property below was withdrawn by the owner. The
> standing rule is now "AI reads; rules decide" in `CLAUDE.md`: an optional AI sheet reader
> may propose values, every proposal is grounded against the sheet, and rules and the code
> corpus stay pure Python. `CLAUDE.md` outranks this prompt wherever they differ.

**Critical property to preserve: the review path makes zero LLM calls.** It is pure Python
over PyMuPDF. A 35-sheet set takes about two seconds of CPU and produces a 16 MB PDF. Do
not add an AI call anywhere in the request path. If you find yourself reaching for one, you
have misread the problem.

**Current shape of the repo:**

```
fbc-review/
  CLAUDE.md              standing rules — read first, they outrank this prompt
  docs/
    DEPLOYMENT-PROMPT.md this document
    DEPLOYMENT.md        the runbook you will write
    reference/           review findings from the two test sets, for context
  web/                   empty — you build the Angular app here (phase 3B)
  samples/               put test permit sets here; git-ignored
fbcreview/
  confidence.py        Evidence / Abstention primitives
  facts.py             ProjectFacts, Sheet, CodeDatum, Door, Schedule, PageGeometry
  options.py           ReviewOptions dataclass — the review parameters
  pipeline.py          build_facts(pdf_path) -> ProjectFacts
  codes/fbc2023.py     FBC thresholds as data, keyed by cited section
  extract/             scale.py, document.py, schedules.py, blocks.py, geometry.py
  rules/               12 rules; run_all(facts, options) -> (findings, abstentions)
  render/markup.py     Finding objects -> marked-up PDF
webapp/
  server.py            FastAPI: POST /api/review, GET /api/jobs/{id}, /markup.pdf,
                       /findings.json, /api/config, /healthz
  mailer.py            SMTP, inert unless configured
  static/index.html    single self-contained page — being replaced, see phase 3B
tests/test_regression.py
Dockerfile             python:3.12-slim, no GPU
run.py                 CLI entry point
```

`webapp/server.py` currently keeps jobs in an in-process dict, writes output to a temp
directory, has no authentication, and sweeps files after 24 hours. That is correct for a
laptop and wrong for the internet. Fixing it is most of this task.

## Architecture — decided, do not relitigate

| Layer | Decision |
| --- | --- |
| Frontend | **Angular 22**, standalone + signals + zoneless, static build, in `web/` |
| API | **Python + FastAPI + PyMuPDF** — unchanged, becomes API-only |
| Compute | **Cloud Run**, service `fbc-review`, region `us-east1`, gen2, min instances 0 |
| Job records | **Firestore** (Native mode), collection `reviews` |
| Uploads and outputs | **Cloud Storage**, one bucket, lifecycle delete at 30 days |
| Auth | **Firebase Authentication**, Google provider, server-side email allowlist |
| Domain | **Firebase Hosting** at `fbc.omniflexfitness.com`, `/api/**` rewriting to Cloud Run |
| CI/CD | GitHub Actions → Artifact Registry → Cloud Run, via Workload Identity Federation |
| Registry | Artifact Registry, `us-east1-docker.pkg.dev` |

Four decisions that need justifying so you do not "improve" them:

- **The backend stays Python and that is not negotiable.** The engine's value is in
  PyMuPDF's `get_drawings()`, `find_tables()`, optional-content-group access and
  annotation writing. There is no TypeScript equivalent with that fidelity — `pdf.js`
  renders and `pdf-lib` writes, but neither gives you per-path vector geometry with layer
  names, table reconstruction, and the `/Measure` viewport dictionaries the scale
  resolution depends on. The split is deliberate: **TypeScript owns the client, Python owns
  the PDF.**
- **Angular, not React, and not Next.js.** Nothing here needs server rendering — this is
  one authenticated tool page talking to a JSON API, so a static build on a CDN is the
  whole deployment. Angular is chosen over React for three concrete reasons specific to
  this app, not on general merit: an `HttpInterceptorFn` attaches the Firebase ID token to
  every request in one place; RxJS makes the polling loop with backoff and cancellation
  correct by construction; and `openapi-generator`'s `typescript-angular` generator emits
  injectable, `HttpClient`-typed services that the interceptor covers automatically. Those
  are the three things this client actually does.
- **Firebase Hosting, not Cloud Run domain mapping.** Cloud Run's own custom domain mapping
  is still a pre-GA preview feature that Google explicitly documents as not
  production-ready, with a restricted region list. Firebase Hosting fronting Cloud Run via
  a `run` rewrite is GA and gives automatic managed TLS. Verify the current `firebase.json`
  rewrite syntax against the Firebase Hosting configuration reference before writing it.
- **Downloads go straight from Cloud Storage via V4 signed URLs, never through the app.**
  A marked-up set is 16-19 MB. Streaming that through Hosting and Cloud Run wastes egress,
  risks whatever response-size ceiling Hosting imposes, and holds a Cloud Run instance open
  for the duration. The API returns a signed URL; the browser fetches from GCS.

The **async job model already in `server.py` is load-bearing** — `POST` returns `202` with
an id, the client polls. Keep it. Firebase Hosting applies a request timeout to rewrites;
because no request ever waits for the review, that ceiling never binds. Do not "simplify"
this into a synchronous endpoint.

## Phase 0 — repo bootstrap

1. You are already in `C:\Antigravity\fbc-review`. The engine, the web service, the
   tests, the Dockerfile and a `.venv` are already present, and `docs/`, `web/` and
   `samples/` are scaffolded but empty. Verify the tree matches the listing above before
   doing anything else. Read `CLAUDE.md` — its rules outrank anything in this prompt that
   contradicts them.
2. `git init`. A `.gitignore` is already in place; check it covers everything you add.
3. Create a **private** GitHub repo `bkenol/fbc-review` and push `main`.
4. Create a venv, `pip install -r requirements.txt`, and **run the regression test before
   changing a line**: `python -m pytest tests/ -v`. It must pass. If it does not, stop and
   report — do not proceed onto a broken baseline.
5. Run the service locally (`uvicorn webapp.server:app --port 8000`) and confirm the
   existing page loads and a review completes end to end. You need a working baseline to
   compare against after every phase below.

**Commit discipline:** one commit per phase, each with a message naming what changed and
why. Run the regression test before every commit. If a phase breaks it, fix the phase — do
not edit the test.

## Phase 1 — harden the API

All of this is inside `webapp/`. Do not touch `fbcreview/`.

1. **Upload validation.** Currently `FBC_MAX_UPLOAD_MB` defaults to 120 and is checked
   loosely. Enforce it as a hard limit *while streaming*, before the file lands on disk.
   Reject anything whose first bytes are not `%PDF-`. Reject encrypted PDFs
   (`doc.needs_pass`) with a clear message rather than a traceback. Cap page count at 300.
2. **Structured logging.** Replace prints with the standard `logging` module emitting JSON
   lines to stdout, so Cloud Logging parses them. Log the job id, the authenticated email,
   the page count, and the elapsed time. **Never log the filename's full path or the file
   contents.**
3. **Error surface.** Every handler returns a typed JSON error
   `{"error": {"code","message"}}`. No stack trace ever reaches the client. Full tracebacks
   go to the log with the job id.
4. **Rate limiting.** Per authenticated user: 10 reviews per hour, 3 concurrent. Enforce it
   against Firestore (phase 2) so it survives instance churn, not an in-memory counter.
5. **CORS.** In production the frontend is same-origin behind the Hosting rewrite, so no
   CORS is needed. For local development `ng serve`'s proxy config sends `/api` to
   `localhost:8000`, which is also same-origin from the browser's point of view. **Do not
   add a permissive CORS middleware.** If you think you need one, the dev proxy is
   misconfigured.
6. **Keep `/healthz` unauthenticated** and make it cheap — no Firestore or GCS call. Cloud
   Run's startup probe hits it.
7. **Tighten the OpenAPI schema.** FastAPI generates `/openapi.json`; the frontend's types
   are generated from it in phase 3B. That means every request and response body needs an
   explicit Pydantic model with real field types — no bare `dict` returns, no `Any`. This
   is the step that makes the TypeScript client worth having, so do it properly.

## Phase 2 — Cloud Storage and Firestore

Replace the in-process `_jobs` dict and the temp directory.

**Bucket:** `omniflex-fbc-review` (adjust if taken), region `us-east1`, uniform bucket-level
access **on**, public access prevention **enforced**, and a lifecycle rule deleting objects
after 30 days. Layout:

```
uploads/{job_id}/{original_filename}
outputs/{job_id}/markup.pdf
outputs/{job_id}/findings.json
```

**Firestore** collection `reviews`, document id = job id:

```
{ id, uid, email, filename, bytes, pages,
  state,            // queued | running | done | error
  stage,            // 0-4
  options,          // the ReviewOptions dict as submitted
  summary,          // counts by severity, sheet count, abstention count
  error,
  created_at, started_at, finished_at }
```

Rules for this phase:

- The worker downloads the upload from GCS to the instance's local `/tmp`, runs the
  existing pipeline unchanged, uploads the two outputs, then deletes the local copies in a
  `finally` block. `fbcreview/` keeps working on local paths — do not make the engine
  GCS-aware.
- `GET /api/jobs/{id}` reads Firestore and returns **V4 signed URLs** with a 1-hour expiry
  for the two outputs. Cloud Run's default service account can sign via the IAM Credentials
  API — grant the service account `roles/iam.serviceAccountTokenCreator` **on itself**.
  This is the step that most often gets missed; verify signing works before moving on.
- A job document may only be read by the uid that created it. Enforce this **server-side**,
  in the handler. Do not rely on Firestore security rules — the client never talks to
  Firestore directly.
- On startup, mark any `running` job older than 15 minutes as `error` with "interrupted by
  a restart". Cloud Run scales to zero; jobs will be orphaned.

## Phase 3A — authentication, backend

Firebase Authentication, Google provider only.

- `firebase-admin`, `auth.verify_id_token(token)` in a FastAPI dependency applied to every
  route except `/healthz`. Check the current `firebase_admin.auth` documentation for the
  correct call and the exceptions it raises — handle expired and revoked tokens distinctly
  from malformed ones.
- Allowlist: environment variable `FBC_ALLOWED_EMAILS`, comma-separated, checked against
  the verified `email` claim **and** `email_verified == true`. An address not on the list
  gets `403` with a message saying to contact the administrator — not a generic error.
  Seed it with `bertin.kenol@omniflexfitness.com`.
- The allowlist check is server-side only. A client-side check is decoration.

## Phase 3B — the frontend

Build a new `web/` — **Angular 22**, standalone components, signals, zoneless change
detection, strict mode. Delete `webapp/static/index.html` once the new client reaches
parity; until then keep it, because it is the reference for what the UI must do.

### Read this before you write a line of Angular

Angular changed substantially between v16 and v22 and **models reliably generate the old
version of it** — the Angular team called this out publicly in 2026. Everything below is
mandatory, and you must verify each against `angular.dev` rather than from memory:

- **No `NgModule`.** Standalone components only. `bootstrapApplication`, not
  `platformBrowserDynamic().bootstrapModule()`.
- **No `zone.js`.** Zoneless is the default from v21 and zone.js is no longer bundled. If
  you find yourself adding it to `angular.json` polyfills, stop — you have generated a v16
  app.
- **Signals, not `BehaviorSubject`, for component state.** `signal()`, `computed()`,
  `input()`, `output()`, `viewChild()`. RxJS stays for the polling stream, where it earns
  its place.
- **Built-in control flow.** `@if` / `@for` / `@switch` in templates. Not `*ngIf` /
  `*ngFor`.
- **`inject()`, not constructor parameter injection.**
- **`provideHttpClient(withInterceptors([...]))`** with functional `HttpInterceptorFn`.
  Not the class-based `HTTP_INTERCEPTORS` multi-provider.
- **Vitest** is the default test runner from v21, not Karma.
- Turn on `"strict": true` and `"strictTemplates": true`. Non-negotiable.

Scaffold with `ng new` from the pinned CLI version so you inherit the current defaults
rather than reconstructing them.

### Read the existing page too

`webapp/static/index.html` is a deliberate, finished design, not a placeholder: a real
`<input type="file">` behind a drop zone (so keyboard and screen-reader users can still
upload), native `<progress>` for the five named stages, `color-scheme: light dark` with
`accent-color` so it follows the OS theme without a theme toggle, and no external
dependencies. **Port that behaviour and that restraint.** Do not introduce Angular
Material, Tailwind, or any component library — this is one page, and the existing CSS is
about 200 lines.

### Requirements

1. **Typed API client generated from the backend.** Use `@openapitools/openapi-generator-cli`
   with the **`typescript-angular`** generator against the running server's
   `/openapi.json`, output to `web/src/app/api/`. It emits `@Injectable()` services typed
   against `HttpClient`, which means the auth interceptor applies to them with no extra
   wiring. Commit the generated output and add an npm script to regenerate it. **This is
   the whole reason for a typed frontend** — the client's types cannot drift from the
   server's contract, and a backend field rename becomes a compile error rather than a
   runtime `undefined`.

2. **Auth.** Use the **modular Firebase JS SDK directly** (`firebase/auth`), not
   `@angular/fire`. AngularFire's stable line trails Angular's release train by a major
   version and Firebase documents it as maintained by Googlers but not a supported Firebase
   product — do not put the auth path on it. Check whether a release peered to Angular 22
   exists when you get here; if it does, using it is fine, but the plain SDK is the safe
   default.

   Wrap it in an `AuthService`:
   - `onAuthStateChanged` bridged into a `signal<User | null>`
   - `signInWithPopup` with `GoogleAuthProvider`
   - `getIdToken()` called **per request**, not cached — the SDK handles refresh
   - a `CanActivateFn` route guard for the tool route
   - three distinct states in the UI: signed out, signed in but **not on the allowlist**
     (the API's `403`), and signed in and authorized. The middle one needs its own message
     telling the user to contact the administrator.

3. **`authInterceptor`** — a functional `HttpInterceptorFn` that awaits `getIdToken()` and
   sets `Authorization: Bearer <token>` on every outbound request to `/api`. One place,
   ~15 lines. Do not attach tokens by hand at call sites, and do not attach the token to
   the GCS signed-URL fetches (they carry their own signature and a stray `Authorization`
   header will break them).

4. **Options form** — typed reactive forms (`FormGroup` with explicit generics), driven by
   `GET /api/config` rather than hard-coded, so adding an occupancy group to
   `fbcreview/options.py` needs no frontend change. Signal Forms are still experimental as
   of v21 — use typed reactive forms unless you confirm they have gone stable.

5. **Upload** at parity with the existing page: drag-and-drop over a real file input,
   client-side size and type check before the request (the server checks again — the client
   check is for feedback, not security).

6. **Job polling** — this is the one place RxJS is clearly the right tool, so use it
   properly:

```ts
timer(0, 1000).pipe(
  switchMap(() => this.api.getJob(id)),
  tap(j => this.job.set(j)),
  takeWhile(j => j.state === 'queued' || j.state === 'running', true),
  // widen to 3s after the first ~10 polls
  takeUntilDestroyed(this.destroyRef),
)
```

   Native `<progress>` bound to the five named stages, `aria-live="polite"` on the status
   text so a screen reader announces stage changes, and the poll must stop on terminal
   states and on navigation away. Do not poll a backgrounded tab at full rate.

7. **Results view.** Severity counts, the findings table rendered from `findings.json`
   (fetched from the signed URL directly), and download links pointing at the signed URLs.
   A signed URL expires after an hour — detect a `403` on download, re-fetch the job for a
   fresh URL, and retry once rather than showing a broken link.

8. **Accessibility is a requirement, not a nice-to-have.** Keyboard-operable throughout,
   labelled controls, visible focus, `aria-live` on job status. The existing page got this
   right; do not regress it.

9. `ng build` produces the static bundle. Confirm the actual output path — recent Angular
   emits `dist/<project>/browser/` — and use whatever `angular.json` reports, not what you
   remember. `ng serve` with a proxy config sending `/api` to `http://localhost:8000`.

**Definition of parity:** every capability of the current `index.html` works in the new
client, including the light/dark behaviour and the keyboard upload path. Verify by using
both, not by reading the code.

## Phase 4 — container and Cloud Run

The container is now **API-only**. It no longer serves HTML.

1. Update `requirements.txt`: add `firebase-admin`, `google-cloud-storage`,
   `google-cloud-firestore`. **Pin every dependency** — `pymupdf` in particular, because
   the extraction code depends on 1.26+ behaviour in `get_drawings()` and `find_tables()`.
2. Drop `webapp/static/` from the image. Run as a **non-root user** — add `USER` after
   installing.
3. Cloud Run injects `PORT`; bind to it:
   `CMD exec uvicorn webapp.server:app --host 0.0.0.0 --port ${PORT:-8000}`.
   The `HEALTHCHECK` line is a Docker feature Cloud Run ignores — remove it and configure a
   startup probe on `/healthz` in the service config instead.
4. Build for `linux/amd64` explicitly. If you are on Apple silicon this is not optional.
5. Create the Artifact Registry repo, push the image, deploy:

```
gcloud run deploy fbc-review \
  --image us-east1-docker.pkg.dev/PROJECT/fbc/fbc-review:TAG \
  --region us-east1 \
  --memory 2Gi --cpu 2 \
  --concurrency 4 \
  --timeout 900 \
  --min-instances 0 --max-instances 5 \
  --service-account fbc-review-sa@PROJECT.iam.gserviceaccount.com \
  --set-env-vars "FBC_BUCKET=...,FBC_ALLOWED_EMAILS=..." \
  --no-allow-unauthenticated
```

Sizing rationale, so you do not change it blindly: **2 GB** because PyMuPDF holds the whole
document plus the rendered output in memory and a 300-page raster-heavy set is the worst
case. **Concurrency 4** because the review is CPU-bound, not IO-bound — the default 80
would let one instance thrash. **max-instances 5** is a cost guard; raise it deliberately,
not reactively.

6. Create a dedicated service account `fbc-review-sa` with exactly:
   `roles/datastore.user`, `roles/storage.objectAdmin` scoped to the one bucket,
   `roles/iam.serviceAccountTokenCreator` on itself. Nothing else. Do not use the default
   compute service account.
7. `--no-allow-unauthenticated` plus a Firebase Hosting rewrite requires the Hosting
   service agent to be able to invoke the service. Check the current Firebase Hosting +
   Cloud Run documentation for which identity needs `roles/run.invoker`; if the wiring
   proves fragile, `--allow-unauthenticated` is acceptable **only because** the app enforces
   Firebase Auth on every route itself. Make that a conscious, documented choice in the
   runbook, not an accident.

## Phase 5 — domain

1. `firebase init hosting` in the repo. `firebase.json`:

```json
{
  "hosting": {
    "public": "web/dist/fbc-review/browser",
    "ignore": ["firebase.json", "**/.*", "**/node_modules/**"],
    "rewrites": [
      { "source": "/api/**", "run": { "serviceId": "fbc-review", "region": "us-east1" } },
      { "source": "**", "destination": "/index.html" }
    ],
    "headers": [
      { "source": "**", "headers": [
        { "key": "X-Content-Type-Options", "value": "nosniff" },
        { "key": "Referrer-Policy", "value": "strict-origin-when-cross-origin" },
        { "key": "Content-Security-Policy", "value": "..." }
      ] } ]
  }
}
```

   Confirm the `public` path against what `angular.json` actually emits — do not assume.
   The CSP has to permit Firebase Auth's endpoints and the GCS signed-URL host — work it
   out from what the app actually loads, do not paste a permissive one. Verify the whole
   file against the current Firebase Hosting configuration reference before deploying; the
   `run` rewrite's supported fields and regions have changed before.

2. `ng build`, then `firebase deploy --only hosting`. Confirm the default `*.web.app`
   URL works end to end **before** touching DNS.
3. Add the custom domain `fbc.omniflexfitness.com` in the Firebase Hosting console.
   **Print the exact DNS records for Bertin to paste into the registrar himself** — record
   type, host, value, TTL — and wait. Do not ask for registrar credentials and do not
   attempt to edit DNS.
4. After propagation, confirm: HTTPS certificate valid, HTTP redirects to HTTPS, the
   sign-in flow completes, an upload runs, and the signed download URL resolves.
5. Add `fbc.omniflexfitness.com` to Firebase Auth's authorized domains, or Google
   sign-in will fail on the custom domain while working on `*.web.app`. This bites everyone
   once.

## Phase 6 — CI/CD

GitHub Actions, on push to `main`:

1. `pytest tests/ -v`, `ng build` and `ng test --watch=false` (Vitest) — all three gate
   everything. A template type error must fail the build, so keep `strictTemplates` on.
2. Build and push the API image tagged with the commit SHA.
3. `gcloud run deploy` with that tag.
4. Deploy Hosting when `web/**` or `firebase.json` changed.
5. Authenticate with **Workload Identity Federation**, not a downloaded service account key
   JSON. Look up the current `google-github-actions/auth` configuration; do not paste a key
   into repository secrets.
6. Add a job that regenerates the `typescript-angular` client against the built container
   and fails if it differs from the committed output. That is what keeps the contract
   honest.

## Verification — run all of these before reporting done

- [ ] `pytest tests/ -v` green on the deployed commit
- [ ] `ng build` clean with `strict` and `strictTemplates` on — zero template type errors
- [ ] Anonymous request to `/api/review` returns `401`, not a review
- [ ] A signed-in address **not** on the allowlist gets `403` with the intended message
- [ ] Upload of a non-PDF, an encrypted PDF, and a 200 MB file each fail cleanly with a
      typed error and no traceback in the response
- [ ] A real 35-sheet set completes and the downloaded PDF opens with markup intact —
      **open it and look at it**, do not infer success from a 200
- [ ] The signed download URL works from a browser with no session, and expires
- [ ] An expired signed URL triggers the re-fetch path rather than a broken download
- [ ] A second browser session cannot read the first session's job by guessing its id
- [ ] Keyboard-only run through the whole flow: sign in, upload, download
- [ ] Light and dark both correct, following the OS setting
- [ ] Cold start latency measured and recorded in the runbook
- [ ] Cloud Logging shows structured JSON with job ids, and no PDF content or filenames
- [ ] Billing alert set on the project at a threshold Bertin picks
- [ ] `DEPLOYMENT.md` written: every command, every IAM binding, every env var, how to roll
      back to a previous Cloud Run revision, and how to add someone to the allowlist

## Do not

- Do not add an LLM call to the request path. The engine is deterministic by design and
  that is the product's core claim.
- Do not port the PDF work to TypeScript. Python owns the PDF.
- Do not modify anything under `fbcreview/` — engine, rules, code corpus or renderer. If
  something there genuinely blocks deployment, stop and report it rather than editing it.
- Do not edit `tests/test_regression.py` to make a phase pass.
- Do not add Angular Material, Tailwind, or any other UI or CSS framework.
- Do not write `NgModule`s, `*ngIf`/`*ngFor`, constructor injection, class-based HTTP
  interceptors, or add `zone.js`. If any of those appear, you have generated pre-v17
  Angular from memory instead of checking `angular.dev`.
- Do not commit service account keys, `.env` files, or any client PDF.
- Do not enable public access on the bucket. Signed URLs only.
- Do not guess at Google Cloud or Firebase CLI syntax. These docs move; check the current
  page before writing a command:
  - Cloud Run custom domains and their preview status
  - Firebase Hosting configuration reference, `run` rewrites
  - Firebase Admin SDK Python, `verify_id_token`
  - Firebase Auth web SDK, `signInWithPopup` and `getIdToken`
  - `angular.dev` for every Angular API you touch — signals, `httpResource`, functional
    interceptors, typed reactive forms, the zoneless guide, and the current `ng build`
    output path
  - Cloud Storage V4 signed URLs from a service account without a key file
  - `google-github-actions/auth` with Workload Identity Federation

## Report back with

The Cloud Run service URL, the custom domain status, the verification checklist with each
box actually ticked, the monthly cost estimate at 50 reviews, and anything you had to
decide that this prompt did not cover.
