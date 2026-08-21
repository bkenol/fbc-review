# Deployment runbook — `review.omniflexfitness.com`

Every command needed to stand this service up, and every decision taken while
building it.

> **Status.** Phases 0–3B are built, tested and pushed. Phases 4–6 are **written
> but not executed**: no GCP project existed for this service when the work was
> done, and creating one plus attaching billing is an account-level, cost-
> incurring action. Every command below marked **[not yet run]** is exactly what
> to run, in order — but it has not been run, so nothing in this document should
> be read as describing a live system. The one exception is the container, which
> was built and exercised locally; see [Phase 4](#phase-4--container).

---

## 0. What exists today

| Piece | State |
| --- | --- |
| Review engine (`fbcreview/`) | Unchanged from the baseline. Never modified. |
| API (`webapp/`) | Hardened, typed, authenticated, Firestore + GCS backed |
| Client (`web/`) | Angular 22, built, tested, exercised in a browser |
| Container | Built and verified locally, incl. OCR inside the image |
| GitHub repo | `bkenol/fbc-review` (private), `main` pushed |
| GCP project | **Does not exist yet** |
| Cloud Run service | Not deployed |
| Custom domain | Not configured |

Test status on the current commit:

```
pytest tests/ -v          62 passed, 1 skipped   (locally, no Tesseract)
pytest inside the image   63 passed              (Python 3.12, with Tesseract)
ng build                  clean, 0 template type errors
ng test --watch=false     8 passed (Vitest)
```

---

## 1. Prerequisites

| Tool | Version used | Note |
| --- | --- | --- |
| Python | 3.12 in the container | Local dev used 3.14; CI pins **3.12** to match the image |
| Node | 24.19.0 | Angular 22 requires `^22.22.3 \|\| ^24.15.0 \|\| >=26`. Installed via nvm as `nvm install 24.19.0`; the machine default (20.19.2) was left alone and Node 24 is invoked by absolute path. |
| Java | 21+ (25 used) | `openapi-generator-cli` is a Java tool |
| gcloud | 580.0.0 | |
| firebase-tools | 15.3.1 | **Credentials were expired.** Run `firebase login --reauth` before Phase 5. |
| Docker | 29.6.2 | `buildx` for `linux/amd64` |

**Before anything else**, switch gcloud off the service account it was left on:

```bash
gcloud auth list          # was: github-actions@omnitask-475422.iam.gserviceaccount.com
gcloud config set account bertin.kenol@omniflexfitness.com
gcloud auth login         # if needed
```

Deploying while that service account is active would create everything inside
the unrelated **OmniTask** project.

---

## 2. Local development

The client needs the API. From the repository root:

```bash
FBC_DEV_UNSAFE_AUTH=1 FBC_BUCKET=fbc-dev-local FBC_PROJECT_ID=fbc-dev-local \
  FBC_ALLOWED_EMAILS=bertin.kenol@omniflexfitness.com \
  .venv/Scripts/python.exe -m uvicorn webapp.server:app --port 8060
```

```bash
cd web && npm start        # ng serve, proxying /api and /_dev to :8060
```

`FBC_DEV_UNSAFE_AUTH=1` swaps Firestore and Cloud Storage for filesystem
stand-ins (`webapp/devbackend.py`) and accepts unauthenticated requests, so the
whole client works with no GCP project.

**It cannot be switched on in production.** `webapp/config.py` refuses the flag
whenever `K_SERVICE` is set, and Cloud Run always sets `K_SERVICE`. There is no
environment variable that turns authentication off in a deployed service. A test
pins this (`test_dev_auth_bypass_cannot_activate_on_cloud_run`).

**Port 8060, not 8000.** On this Windows machine 8000 falls inside a reserved
TCP exclusion range (7952–8051) and cannot be bound. Check with
`netsh interface ipv4 show excludedportrange protocol=tcp`.

---

## 3. Phase 4 — container

**Built and verified.** Not yet pushed to a registry.

```bash
docker build -t fbc-review:dev .
docker run --rm fbc-review:dev python -c "from webapp import convert; print(convert.support())"
# Support(ocr=True, vectorise=True, detail='Tesseract available; OpenCV available')
```

Verified inside the image:

- Tesseract present, `TESSDATA_PREFIX=/usr/share/tesseract-ocr/5/tessdata`
  (resolved during the build rather than hard-coded blindly, then asserted)
- OpenCV present
- Runs as `uid=10001(fbc)`, not root
- No `webapp/static` — the image serves the API only, and no route returns HTML
- `pytest tests/ -v` → **63 passed** on Python 3.12
- `python scripts/verify_ocr.py` recovers the printed scale label (→ 18.0 pt/ft)
  and both cited section numbers from a flattened sheet

Image size is **893 MB**, dominated by Tesseract and OpenCV. Those are needed
only for the opt-in raster rebuild; a vector set never imports `cv2`. If the
size becomes a problem, the honest fix is a second image without them and a
`convert_raster` request routed to it — not trimming the deterministic path.

`--platform linux/amd64` is explicit in CI. Cloud Run runs amd64 and an
accidentally-arm64 image fails at start rather than at build.

### Deploy [not yet run]

```bash
PROJECT_ID=fbc-review-prod        # chosen below; must be globally unique
REGION=us-east1

gcloud projects create "$PROJECT_ID" --name="FBC Reviewer"
gcloud config set project "$PROJECT_ID"

# Billing must be attached before any API will enable. List accounts, then link.
gcloud billing accounts list
gcloud billing projects link "$PROJECT_ID" --billing-account=BILLING_ACCOUNT_ID

gcloud services enable \
  run.googleapis.com \
  artifactregistry.googleapis.com \
  firestore.googleapis.com \
  storage.googleapis.com \
  iamcredentials.googleapis.com \
  firebase.googleapis.com \
  identitytoolkit.googleapis.com \
  cloudbuild.googleapis.com

gcloud artifacts repositories create fbc \
  --repository-format=docker --location="$REGION" \
  --description="FBC Reviewer images"

gcloud auth configure-docker "${REGION}-docker.pkg.dev"

TAG=$(git rev-parse HEAD)
IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/fbc/fbc-review:${TAG}"
docker buildx build --platform linux/amd64 -t "$IMAGE" --push .
```

### Firestore and the bucket [not yet run]

```bash
# Native mode. This is a ONE-TIME, IRREVERSIBLE per-project choice.
gcloud firestore databases create --location="nam5" --type=firestore-native

BUCKET=omniflex-fbc-review          # adjust if taken
gcloud storage buckets create "gs://${BUCKET}" \
  --location="$REGION" \
  --uniform-bucket-level-access \
  --public-access-prevention

# Delete artefacts after 30 days.
cat > lifecycle.json <<'JSON'
{"rule":[{"action":{"type":"Delete"},"condition":{"age":30}}]}
JSON
gcloud storage buckets update "gs://${BUCKET}" --lifecycle-file=lifecycle.json

# CORS. Not optional, and easy to miss: the results view fetches findings.json
# from a signed URL with XHR. A signed URL authorises the request but does NOT
# produce CORS headers — GCS only sends them if the bucket says so. Download
# *links* are navigations and work without this, so a click-test passes while
# the findings table stays empty forever.
#   Replace REPLACE_PROJECT_ID in cors.json first.
gcloud storage buckets update "gs://${BUCKET}" --cors-file=cors.json
```

### Service account and IAM [not yet run]

```bash
SA="fbc-review-sa@${PROJECT_ID}.iam.gserviceaccount.com"
gcloud iam service-accounts create fbc-review-sa \
  --display-name="FBC Reviewer runtime"

# Firestore
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${SA}" --role="roles/datastore.user"

# Storage, scoped to the one bucket rather than the project
gcloud storage buckets add-iam-policy-binding "gs://${BUCKET}" \
  --member="serviceAccount:${SA}" --role="roles/storage.objectAdmin"

# Signing. THIS IS THE ONE MOST OFTEN MISSED.
# Cloud Run's credentials carry no private key, so V4 signing goes through the
# IAM Credentials signBlob API — which requires the service account to be able
# to impersonate ITSELF. Without this, every download URL fails to generate and
# the job looks finished with broken links.
gcloud iam service-accounts add-iam-policy-binding "$SA" \
  --member="serviceAccount:${SA}" \
  --role="roles/iam.serviceAccountTokenCreator"
```

Nothing else. Not the default compute service account.

**Verify signing before moving on** — this is the step that fails silently:

```bash
gcloud run services proxy fbc-review --region="$REGION" &
# then, signed in, request a finished job and open its markup_pdf URL in a
# browser with no session. If it 403s, the token-creator binding is missing.
```

### Deploy the service [not yet run]

```bash
gcloud run deploy fbc-review \
  --image="$IMAGE" \
  --region="$REGION" \
  --memory=2Gi --cpu=2 \
  --concurrency=4 \
  --timeout=900 \
  --min-instances=0 --max-instances=5 \
  --service-account="$SA" \
  --set-env-vars="FBC_BUCKET=${BUCKET},FBC_PROJECT_ID=${PROJECT_ID},FBC_ALLOWED_EMAILS=bertin.kenol@omniflexfitness.com,FBC_SIGNER_SA=${SA}" \
  --no-allow-unauthenticated
```

Sizing, so it is not changed blindly:

- **2 GiB** — PyMuPDF holds the whole document plus the rendered output in
  memory; a 300-page raster-heavy set is the worst case.
- **concurrency 4** — the review is CPU-bound, not IO-bound. The default 80
  would let one instance thrash.
- **max-instances 5** — a cost guard. Raise deliberately, not reactively.
- **timeout 900** — never binds for a vector review (the request returns 202
  immediately), but the opt-in raster rebuild runs on the instance and can take
  minutes.

Startup probe on `/healthz` (Cloud Run ignores Docker's `HEALTHCHECK`, which is
why the Dockerfile has none):

```bash
gcloud run services update fbc-review --region="$REGION" \
  --startup-probe=httpGet.path=/healthz,initialDelaySeconds=5,periodSeconds=5,failureThreshold=6
```

---

## 4. Phase 5 — Hosting and the domain [not yet run]

```bash
firebase login --reauth            # credentials were expired
firebase projects:addfirebase "$PROJECT_ID"
```

Set the project id in `.firebaserc` (replace `REPLACE_PROJECT_ID`), and in the
`frame-src` of the CSP in `firebase.json`, and in `cors.json`.

```bash
cd web && npm ci && npm run build && cd ..
firebase deploy --only hosting,firestore --project "$PROJECT_ID"
```

`firebase.json` points `public` at **`web/dist/fbc-review/browser`**. That path
was read off an actual build, not assumed — recent Angular emits a `browser/`
subdirectory.

**Confirm the `*.web.app` URL works end to end before touching DNS.**

### Invoker identity

The service is deployed `--no-allow-unauthenticated`, so the Hosting rewrite
needs an identity with `roles/run.invoker`. The current Firebase Hosting +
Cloud Run documentation did not state which service agent that is in the pages
retrieved while writing this, so **verify it against the live docs and the
project's IAM page** rather than trusting a remembered email format.

If the wiring resists, `--allow-unauthenticated` is acceptable — but as a
**conscious, documented choice**, not an accident. It is defensible here only
because the application authenticates every route itself: `webapp/auth.py`
verifies the Firebase ID token and checks the allowlist on everything except
`/healthz`, which returns a static object and touches no data. Record the date
and the reason here if you take it.

### Custom domain

Add `review.omniflexfitness.com` in the Firebase Hosting console. It will print
the exact records. **They go into the registrar by hand — this runbook does not
touch DNS and no registrar credentials should be shared.**

Record what was actually entered:

| Type | Host | Value | TTL |
| --- | --- | --- | --- |
| _(fill in from the console)_ | | | |

Then:

1. Wait for propagation, then confirm the TLS certificate is valid.
2. Confirm HTTP redirects to HTTPS.
3. **Add `review.omniflexfitness.com` to Firebase Auth → Settings → Authorised
   domains.** Google sign-in works on `*.web.app` and fails on the custom domain
   without this. It catches everyone once.
4. Add the custom domain to `cors.json` and re-apply it to the bucket.

---

## 5. Phase 6 — CI/CD [not yet run]

`.github/workflows/deploy.yml` runs on every push and PR to `main`:

1. `pytest tests/ -v` (with Tesseract installed, so the OCR test runs rather
   than skips)
2. `npm run build` and `npm run test:ci` in `web/`
3. **Client drift check** — regenerates `openapi.json` and `web/src/app/api/`
   and fails if either differs from what is committed. This is what keeps the
   typed client honest; without it the whole point of generating it is lost.
4. On `main` only: build and push the image tagged with the commit SHA, deploy
   to Cloud Run, smoke-test `/healthz` on the new revision
5. Deploy Hosting when `web/**`, `firebase.json` or `firestore.*` changed

### Workload Identity Federation [not yet run]

No downloaded key JSON, ever.

```bash
POOL=github
PROVIDER=fbc-review
REPO=bkenol/fbc-review
PROJECT_NUMBER=$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')

gcloud iam workload-identity-pools create "$POOL" \
  --location=global --display-name="GitHub Actions"

gcloud iam workload-identity-pools providers create-oidc "$PROVIDER" \
  --location=global --workload-identity-pool="$POOL" \
  --display-name="fbc-review" \
  --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository" \
  --attribute-condition="assertion.repository == '${REPO}'" \
  --issuer-uri="https://token.actions.githubusercontent.com"

# A deploy identity, separate from the runtime identity.
DEPLOYER="fbc-deployer@${PROJECT_ID}.iam.gserviceaccount.com"
gcloud iam service-accounts create fbc-deployer --display-name="CI deployer"

for ROLE in roles/run.admin roles/artifactregistry.writer roles/firebasehosting.admin; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:${DEPLOYER}" --role="$ROLE"
done

# The deployer must be able to act as the runtime service account to deploy a
# service that runs as it.
gcloud iam service-accounts add-iam-policy-binding "$SA" \
  --member="serviceAccount:${DEPLOYER}" --role="roles/iam.serviceAccountUser"

# Let the repo impersonate the deployer.
gcloud iam service-accounts add-iam-policy-binding "$DEPLOYER" \
  --role="roles/iam.workloadIdentityUser" \
  --member="principalSet://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL}/attributeSet/repository/${REPO}"
```

> The `--member` principal format for a repository attribute has changed more
> than once. Check the current `google-github-actions/auth` README before
> running that last command; the action itself is pinned at `v3`, with
> `permissions: id-token: write` on the job, which was confirmed against the
> README while writing this.

Then set these as **repository variables** (not secrets — none is sensitive):

| Variable | Value |
| --- | --- |
| `GCP_PROJECT_ID` | the project id |
| `WIF_PROVIDER` | `projects/<number>/locations/global/workloadIdentityPools/github/providers/fbc-review` |
| `DEPLOY_SERVICE_ACCOUNT` | `fbc-deployer@<project>.iam.gserviceaccount.com` |
| `FBC_BUCKET` | the bucket name |
| `FBC_ALLOWED_EMAILS` | comma-separated allowlist |

---

## 6. Environment variables the service reads

All are read once at startup by `webapp/config.py`.

| Variable | Default | Meaning |
| --- | --- | --- |
| `FBC_BUCKET` | — | Cloud Storage bucket. Required. |
| `FBC_PROJECT_ID` | `GOOGLE_CLOUD_PROJECT` | GCP project |
| `FBC_ALLOWED_EMAILS` | empty | Comma-separated allowlist. **Empty means nobody gets in.** |
| `FBC_SIGNER_SA` | ambient | Service account used for V4 signing |
| `FBC_MAX_UPLOAD_MB` | 120 | Hard limit, enforced while streaming |
| `FBC_MAX_PAGES` | 300 | Page cap |
| `FBC_RETAIN_DAYS` | 30 | Reported to the client; enforced by bucket lifecycle |
| `FBC_SIGNED_URL_TTL` | 3600 | Signed URL lifetime, seconds |
| `FBC_RATE_PER_HOUR` | 10 | Reviews per user per hour |
| `FBC_RATE_CONCURRENT` | 3 | Concurrent reviews per user |
| `FBC_STALE_RUNNING_MINUTES` | 15 | A `running` job older than this is failed at startup |
| `FBC_WORKERS` | 2 | Worker threads per instance |
| `FBC_COLLECTION` | `reviews` | Firestore collection |
| `FBC_LOG_LEVEL` | `INFO` | |
| `FBC_DEV_UNSAFE_AUTH` | unset | **Local only.** Ignored whenever `K_SERVICE` is set. |
| `FBC_SMTP_*`, `FBC_MAIL_FROM` | unset | Email stays inert unless all are set |

---

## 7. Operations

### Add or remove someone from the allowlist

The allowlist is an environment variable, so changing it deploys a new revision.

```bash
gcloud run services update fbc-review --region=us-east1 \
  --update-env-vars="FBC_ALLOWED_EMAILS=bertin.kenol@omniflexfitness.com,someone@example.com"
```

Removing an address takes effect on the next revision; any ID token already
issued keeps working against the API only until the next request, because the
allowlist is checked per request, not at sign-in.

An address that is not on the list gets a `403` naming the address and telling
the person to contact the administrator — not a generic error.

### Roll back

```bash
gcloud run revisions list --service=fbc-review --region=us-east1
gcloud run services update-traffic fbc-review --region=us-east1 \
  --to-revisions=fbc-review-00007-abc=100
```

To roll back the client, redeploy Hosting from the previous commit, or use
`firebase hosting:rollback`.

### Logs

Structured JSON on stdout, so Cloud Logging parses the fields directly:

```bash
gcloud run services logs read fbc-review --region=us-east1 --limit=100
# or, by job:
gcloud logging read 'resource.type="cloud_run_revision" AND jsonPayload.job_id="abc123def456"' --limit=50
```

Each line carries `severity`, `message`, `job_id`, `uid`, `email`, `pages` and
`elapsed_seconds`. **The uploaded file's path, basename and contents are never
logged** — a permit set's filename is usually the client's project name, so the
job id is the join key instead.

### Billing alert [not yet run]

Threshold chosen: **$25/month**.

```bash
gcloud billing budgets create \
  --billing-account=BILLING_ACCOUNT_ID \
  --display-name="FBC Reviewer" \
  --budget-amount=25USD \
  --threshold-rule=percent=50 \
  --threshold-rule=percent=90 \
  --threshold-rule=percent=100
```

---

## 8. Cost estimate at 50 reviews/month

Estimated, not measured — nothing is deployed. Order-of-magnitude, us-east1.

| Item | Basis | Monthly |
| --- | --- | --- |
| Cloud Run CPU | 50 × ~30 s wall × 2 vCPU | ~$0.07 |
| Cloud Run memory | same × 2 GiB | ~$0.02 |
| Cloud Run requests | ~2 k, first 2 M free | $0 |
| Cloud Storage | ~1.8 GB held, 30-day lifecycle | ~$0.04 |
| Egress | 50 × ~18 MB downloaded | ~$0.11 |
| Firestore | ~2 k reads, ~500 writes; free tier is 50 k reads/day | $0 |
| Firebase Hosting | well inside the free 10 GB | $0 |
| Artifact Registry | 893 MB image × a few tags | ~$0.30 |
| **Total** | | **well under $1** |

The dominant line is the container image, not the compute. The $25 budget is a
runaway alarm rather than a forecast.

**Caveat.** The opt-in raster rebuild changes the shape: OCR plus Hough
transform on a large sheet is minutes of CPU, not seconds. Fifty *scanned* sets
would still land under a dollar of CPU, but it is the one thing that could move
the number, and `max-instances=5` is what bounds it.

### Cold start

**Not measured — nothing is deployed.** Measure it once the service is up and
record it here:

```bash
gcloud run services update fbc-review --region=us-east1 --min-instances=0
# then, after several minutes idle:
curl -s -o /dev/null -w '%{time_total}\n' https://review.omniflexfitness.com/healthz
```

Expect it to be poor by web standards: the image is 893 MB and importing
PyMuPDF, OpenCV and the Google client libraries is not free. If it is
unacceptable, `--min-instances=1` costs roughly $13/month for an always-warm
2 GiB / 2 vCPU instance and should be weighed against that.

---

## 9. Verification checklist

Ticked only where actually verified. See the report for what is blocked and why.

- [x] `pytest tests/ -v` green on the deployed commit (62 local, 63 in-image)
- [x] `ng build` clean with `strict` and `strictTemplates` — zero template type
      errors, and `strictTemplates` proven enforcing by deliberately breaking it
- [x] Anonymous request to `/api/review` returns `401`, not a review
- [x] A signed-in address not on the allowlist gets `403` with the intended
      message (unit-tested against `auth._authorise`)
- [x] Upload of a non-PDF, an encrypted PDF and an over-limit file each fail
      cleanly with a typed error and no traceback in the response
- [x] A scanned set is refused with a measured diagnosis rather than reviewed
      to a misleading zero findings
- [x] A second session cannot read the first session's job by guessing its id
      (reported as `404`, so ids cannot be probed)
- [x] The marked-up PDF was opened and looked at — review margin added left,
      original sheet untouched, register listing all twelve abstentions
- [x] Keyboard-operable: real focusable `<input type=file>`, no unlabelled
      controls, `aria-live` on job status
- [x] Light and dark both correct, following the OS setting
- [x] Polling stops on a terminal state — measured, zero further `getJob`
      requests over 12 s idle after a job finished
- [x] Cloud Logging output is structured JSON with job ids, and carries no PDF
      content or filenames
- [ ] A real 35-sheet permit set completes end to end — **blocked**: no permit
      set was available on disk
- [ ] The signed download URL works from a browser with no session, and expires
      — **blocked**: needs a real bucket
- [ ] An expired signed URL triggers the re-fetch path — code written and
      reviewed, not exercised against real expiry
- [ ] Cold start latency measured — **blocked**: not deployed
- [ ] Billing alert set — **blocked**: no project
- [ ] Custom domain, TLS, sign-in on the custom domain — **blocked**

---

## 10. Decisions the brief did not cover

**Node 24.19.0 installed via nvm.** Angular 22 requires Node `^22.22.3`,
`^24.15.0` or `>=26`; the machine had 20.19.2 active and 22.14.0 available, both
too old. Node 24.19.0 was installed into the existing nvm and invoked by
absolute path, so the machine's default Node was not changed.

**The multipart field is `review_options`, not `options`.**
`openapi-generator`'s `typescript-angular` services already take a parameter
called `options` for per-request HttpClient settings. A form field of the same
name generates a method with two parameters called `options`, which does not
compile. The `Authorization` header is likewise hidden from the schema, so the
generated client has no `authorization` argument to pass by hand — that is the
interceptor's job.

**Findings live in the bucket, not in the job document.** Firestore caps a
document at 1 MiB and a large set's finding bodies can approach it. The client
fetches `findings.json` from the signed URL, and `FindingsDocument` is published
into the OpenAPI schema so that fetch is typed rather than hand-written.

**Rate limits are derived by query, never counters.** A counter incremented at
start and decremented at finish leaks a slot permanently whenever Cloud Run
kills an instance mid-review, locking the user out with no way to clear it.
Derived counts need the composite indexes in `firestore.indexes.json`; without
them the query returns a 400 in production that never appears locally.

**`GET /` was removed and `webapp/static/index.html` deleted.** No route returns
HTML. The reference page remains in history at `780f163`.

**Email stays inert.** `webapp/mailer.py` is unchanged and does nothing unless
SMTP is configured. The new client does not surface `email_to`: attaching a
17 MB PDF over SMTP contradicts "downloads never go through the app", and
sending mail on someone's behalf is an outward-facing action that should be an
explicit decision rather than a side effect of a checkbox.

**A local development backend exists** (`webapp/devbackend.py`). Firestore needs
a project and Cloud Storage has no emulator, so without it the client could only
be reviewed by reading it. It is gated on the same `K_SERVICE` check as the auth
bypass.

### Raster rebuild — what it does and does not recover

Added at request, opt-in per review (`convert_raster`).

OCR recovers text, and text is where eleven of the twelve rules get their
inputs — schedules, code-analysis blocks, and the printed scale label. That part
works: `scripts/verify_ocr.py` flattens a sheet to an image, rebuilds it, and the
scale label comes back and resolves to 18.0 pt/ft with both cited section
numbers intact.

Vectorisation recovers *lines*. It cannot recover what they mean, because that
lived in the CAD layer name and the scan does not have it.

This matters more than it sounds. `MEASURE.EGRESS_EXTENT` selects geometry by
matching the layer name against `"egress path"` and reports the longest straight
run as an exit access travel distance — at CRITICAL severity, with a citation.
If the traced layer were named to match, the longest straight run on a scanned
sheet, very often the title-block border, would be reported as a travel
distance, confidently and wrongly. That is worse than not measuring.

So traced linework goes to `traced linework (unclassified)`, the egress rule
abstains as it should, and a test pins it
(`test_traced_layer_must_never_impersonate_the_egress_layer`). Recovering those
semantics needs room-polygon recovery (`ARCHITECTURE.md` §6.2), not a better
tracer.

Known limitation, observed in the verification run: OCR read `SHEET G-0` as
`SHEET G-O0`. Sheet codes are short and O/0 confusion is the classic OCR
failure, so sheet-code matching on a rebuilt set is less reliable than on a
plotted one.

### Vectorisation changes no finding today

Worth stating plainly rather than leaving in the detail above: the OCR half of
the rebuild unlocks most of the rule corpus, and the vectorisation half unlocks
**none of the twelve current rules**. It makes the file a genuine vector PDF and
it enables future rules, but no finding changes because of it today — the only
geometric rule needs a semantic layer name that tracing cannot recover.

**No model call was added anywhere.** OCR is Tesseract and vectorisation is a
Hough transform; both are deterministic. The review path still makes zero LLM
calls.
