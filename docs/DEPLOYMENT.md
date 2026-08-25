# Deployment runbook — `fbc.omniflexfitness.com`

Every command needed to stand this service up, and every decision taken while
building it.

> **Status.** There are two routes to `fbc.omniflexfitness.com` and the domain
> is served by the second one.
>
> **The Google Cloud route (sections 3–5) is blocked at billing.** Phases 0–3B
> are built, tested and pushed, and provisioning has been run as far as it can
> go: the GCP project **`fbc-reviewer`** (number 983366817143) exists under the
> `omniflexfitness.com` organisation, and `.firebaserc` points at it. Both
> billing accounts — `OmniFlex Billing` and `OmniFlex Fitness Billing` — are
> **closed**, and a closed account can be attached to a project while paying
> for nothing: enabling Cloud Run returns *"Billing account for project
> '983366817143' is not open."* Nothing past that point has run. Open a billing
> account and re-run the script; everything already done is skipped.
>
> **The Cloudflare Tunnel route (section 0c) needs no billing account and is
> what serves the domain today.** `omniflexfitness.com` is on Cloudflare, so
> the hostname is a CNAME into a tunnel that terminates at the container
> running on the workstation. Same domain, same client, same engine — the
> difference is that the machine has to be on, and that **authentication is
> off**, which was a deliberate choice recorded under Exposure below.

---

## 0. What exists today

| Piece | State |
| --- | --- |
| Review engine (`fbcreview/`) | Unchanged from the baseline. Never modified. |
| API (`webapp/`) | Hardened, typed, authenticated, Firestore + GCS backed |
| Client (`web/`) | Angular 22, built, tested, exercised in a browser |
| Container | Built and verified locally, incl. OCR inside the image |
| GitHub repo | `bkenol/fbc-review` (private), `main` pushed |
| GCP project | `fbc-reviewer` (983366817143) exists; no open billing account |
| Cloud Run service | Not deployed — blocked on billing |
| Cloudflare Tunnel | `scripts/tunnel.sh`, serving `fbc.omniflexfitness.com` from the workstation |
| Custom domain | Live via the tunnel; **not** on Firebase Hosting |

Test status on the current commit:

```
pytest tests/ -q          175 passed, 3 skipped  (Python 3.12, no Tesseract)
                          skips: 2 x Tesseract absent, 1 x FBC_TEST_PDF unset
test_regression.py        OK  (FBC_TEST_PDF = the real Sculpted permit set)
pytest inside the image   with Tesseract present, the two OCR skips run
ng build                  clean, 0 template type errors
ng test --watch=false     8 passed (Vitest)
```

---

## 0a. Deploying it — the short version

Everything in sections 3 to 5 is automated by `scripts/provision.sh`. It is
idempotent, so a failure halfway through is fixed by running it again.

```bash
gcloud auth login          # your own account, not a service account
firebase login
```

Then, from anywhere:

```bash
powershell -ExecutionPolicy Bypass -File scripts\provision.ps1
```

or, from the repository root:

```bash
bash scripts/provision.sh
```

It stops and asks only where a decision is yours — which billing account to
attach. Re-run it with that account:

```bash
FBC_BILLING_ACCOUNT=0X0X0X-0X0X0X-0X0X0X bash scripts/provision.sh
```

Overridable settings: `FBC_PROJECT_ID` (default `fbc-reviewer`), `FBC_REGION`
(`us-east1`), `FBC_BUCKET`, `FBC_DOMAIN` (`fbc.omniflexfitness.com`),
`FBC_ALLOWED_EMAILS`.

The script refuses to run as a service account, because gcloud on this
workstation was left authenticated as a CI identity for an unrelated project and
provisioning with it would create everything in the wrong place.

Two things it cannot do for you, both needing a browser: enabling the Google
sign-in provider, and adding the custom domain plus its DNS records. It prints
the console links for both when it finishes.

## 0b. Running it on another machine

Everything that matters is committed, so moving between machines is a clone plus
one script.

```bash
git clone https://github.com/bkenol/fbc-review.git
cd fbc-review
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
```

It checks prerequisites first and names what is missing rather than failing
halfway through, then creates the virtualenv, installs both dependency trees,
builds the client and runs the tests.

Deliberately not in git, and what to do about each:

| Not committed | Why | How to get it |
| --- | --- | --- |
| `samples/*.pdf` | Client drawings never go in a repository | Re-copy from the Drive folder `Meridian/Building Codes/Unreviewed Plans` |
| `.venv/`, `web/node_modules/` | Machine-specific | `scripts\setup.ps1` |
| `web/dist/` | Build output | `scripts\setup.ps1` |
| `.devdata/` | Local job scratch for the dev backend | Recreated on demand; disposable |

Nothing else is machine-specific. There is no state on the laptop worth moving:
no deployed service, no cloud credentials in the repo, and job records live in
`.devdata`, which is throwaway.

### Sharing it over a throwaway URL

`scripts/share.ps1` runs the whole app as one container on one port, with the
API serving the client so there is a single origin and no CORS. For an unlisted
URL that needs no DNS at all:

```bash
tailscale funnel 8060
```

Funnel is enabled once per tailnet; the CLI prints the approval link if it is
not. Each machine gets its own hostname, so the desktop's URL differs from the
laptop's, and only one machine serves a given hostname.

```bash
tailscale funnel reset; docker rm -f fbc-test
```

For the real hostname rather than a throwaway one, see **0c** below. Both modes
run the same container and both have authentication off — see **Exposure**.

## 0c. Publishing it at `fbc.omniflexfitness.com` — Cloudflare Tunnel

This is the route that serves the domain today. It needs no Google Cloud
billing account, no Cloud Run service and no Firebase Hosting site: the
hostname points into a tunnel that ends at the container on the workstation.

```bash
powershell -ExecutionPolicy Bypass -File scripts\share.ps1     # the app
powershell -ExecutionPolicy Bypass -File scripts\tunnel.ps1    # the hostname
```

or, from any POSIX shell with the app already running:

```bash
bash scripts/tunnel.sh
```

`tunnel.sh` is idempotent in the same way `provision.sh` is — every step checks
for what it creates and skips it — so a failure halfway through is fixed by
running it again. Overridable settings: `FBC_DOMAIN`
(default `fbc.omniflexfitness.com`), `FBC_TUNNEL` (`fbc-review`), `FBC_PORT`
(`8060`).

It never deletes or overwrites a DNS record. `cloudflared` has an
`--overwrite-dns` flag, but it is not in the documentation this was written
against and it destroys a record in a live zone, so the script does not use it.
If Cloudflare reports the hostname is already taken, the script says so and
carries on — the usual cause is a previous run of this same script — and the
record is checked or corrected by hand in **DNS > Records**.

### What it sets up

| Step | What happens |
| --- | --- |
| Preflight | Refuses to continue unless `cloudflared` is installed **and** the app answers on `127.0.0.1:8060/healthz`. Publishing a hostname that fronts nothing is the slow way to find out the app is down. |
| `cloudflared tunnel login` | Browser, once per machine. Writes `~/.cloudflared/cert.pem`. Pick the `omniflexfitness.com` zone. |
| `cloudflared tunnel create fbc-review` | Registers a named tunnel and writes its credentials JSON to `~/.cloudflared/<UUID>.json`. The UUID is read back by matching the UUID *shape* in `cloudflared tunnel list`, not by parsing a named JSON field — see below. |
| Config | Written to `~/.cloudflared/fbc-review.yml` — **not** `config.yml`. `cloudflared` reads `config.yml` by default and clobbering it would silently break any other tunnel on the machine. |
| `cloudflared tunnel route dns` | Creates the DNS record below, in the Cloudflare zone, over the API, authorised by `cert.pem`. No record is typed by hand and no API token is stored in the repo. |
| `cloudflared tunnel run` | Foreground. The hostname is live while it runs. |

### Reading the UUID back

Worth recording, because it cost a live debugging round. The first version of
`tunnel.sh` read `cloudflared tunnel list --output json` and skipped any row
carrying a `deleted_at`. cloudflared is written in Go, and Go marshals a zero
timestamp as `"0001-01-01T00:00:00Z"` rather than `null` — a non-empty string.
Every live tunnel therefore looked deleted, and a freshly created one failed
with *"Created the tunnel but could not read its UUID back"*.

It now matches the UUID by its shape in the plain `cloudflared tunnel list`
output, which carries no deleted rows to begin with (`-d` is what includes
them). That is insensitive to column order, to JSON field renames, and to Go's
zero values. `tests/test_tunnel_script.py` pins it against a listing captured
verbatim from a real cloudflared 2026.8.2 — the previous stub was written from
imagination, which is precisely how the bug survived to a live run.

Parsing the listing is also the only thing the script needed an interpreter
for, so that dependency is gone: it is now plain `grep`.

### Paths inside the config, under Git Bash

The second thing that only showed up on a real machine. `cloudflared` is a
native Windows binary, and MSYS rewrites POSIX-looking paths in *arguments*
before a native binary sees them — which is why `--config /c/Users/...` works
untouched. It never looks inside a file, so the `/c/Users/...` credentials path
the script wrote into the YAML arrived verbatim:

```
Tunnel credentials file '/c/Users/.../<uuid>.json' doesn't exist or is not a file
```

...while the script's own `[ -f ]` on the same string passed, because bash
understands that form and the native binary does not. Everything up to and
including the DNS record succeeded; only the final `run` failed.

The script now converts with `cygpath -w` before writing, and single-quotes the
result so YAML keeps the backslashes literal rather than reading them as
escapes. Where there is no `cygpath` — Linux, macOS — the path is used as is.

The general rule, worth remembering for anything else that hands a path to a
native Windows tool from Git Bash: **arguments are translated, file contents are
not.**

### The DNS record

| Type | Name | Value | Proxy | TTL |
| --- | --- | --- | --- | --- |
| CNAME | `fbc` | `<TUNNEL-UUID>.cfargotunnel.com` | **Proxied (orange)** | Auto |

The UUID is printed by the script and by `cloudflared tunnel list`.

**The proxy must stay on.** `cfargotunnel.com` does not resolve for anyone but
Cloudflare's own edge, so switching the record to DNS-only leaves a hostname
that resolves to nothing. This is the opposite of the usual advice for an
origin behind Cloudflare, and it is the mistake to expect here.

TLS is Cloudflare's universal certificate, which already covers a single-label
subdomain of `omniflexfitness.com`. Nothing is issued, installed or renewed on
the workstation, and no port is opened on the router — the tunnel is an
outbound connection, so the machine's IP address is never published.

### The 100 MB ceiling — why the upload limit moved

Cloudflare rejects any proxied request body over **100 MB** on the Free and Pro
plans, with its own 413, at the edge, before the request reaches the tunnel.
The app's own limit defaulted to 120 MB, so a set between 100 and 120 MB would
have been refused by Cloudflare with an opaque error while the app's typed,
explanatory error never ran.

So `share.ps1` now passes `FBC_MAX_UPLOAD_MB=95`. The app refuses the file
first, with its real message, and the client shows the right number because
`/api/config` feeds the browser-side check. Raising it past 95 only makes sense
off the Cloudflare path.

Two related limits that do **not** bind, worth recording so nobody re-derives
them:

- **Cloudflare's 100-second origin timeout (error 524)** never triggers, because
  the async job model means no request ever waits for a review. `POST
  /api/review` returns `202` immediately and the client polls. This is the
  second time that design has paid for itself — Firebase Hosting's rewrite
  timeout was the first.
- **Response size** is not capped by Cloudflare, so the 16–19 MB marked-up PDF
  streams back through the tunnel without special handling. In this mode it is
  served by the app from `.devdata` over the dev-only `/_dev/blob/...` route,
  not from Cloud Storage — there are no signed URLs in play, and no CORS
  configuration to get wrong, because everything is one origin.

### Exposure

**Authentication is off in this mode, deliberately.** `share.ps1` sets
`FBC_DEV_UNSAFE_AUTH=1`, and the tunnel makes the result reachable by anyone who
finds `fbc.omniflexfitness.com`. That was chosen knowingly on 2026-08-24 over
putting Cloudflare Access in front of it; recorded here so it reads as a
decision rather than an oversight.

What limits the damage:

- the 95 MB and 300-page upload caps, enforced while streaming
- the rate limits, which with one shared identity become a **global** 3
  concurrent and 10 reviews an hour rather than per-person
- no Cloud Storage bucket and no Firestore in this mode — artefacts are
  files under `.devdata` on the workstation
- the engine makes zero LLM calls, so an abusive upload costs CPU, not tokens

`tunnel.sh` prints a warning naming this every run, read from `/healthz`'s
`auth_required`, so it cannot be forgotten quietly.

If it should be closed later, the cheapest fix is a Cloudflare Access policy on
the hostname — Zero Trust, allowlist by email, free to 50 users, enforced at the
edge with no change to the app. The alternative is decoupling
`FBC_DEV_UNSAFE_AUTH` so real Firebase sign-in can run against the filesystem
backend; Firebase Authentication itself is free-tier and needs no open billing
account. Neither is done.

### Persistence

`tunnel.sh` runs in the foreground and the hostname stops resolving to anything
useful when it exits — Cloudflare then returns error 1033. `share.ps1
-Persistent` already keeps the container across reboots; to match that for the
tunnel, install `cloudflared` as a Windows service:

```bat
mkdir C:\Cloudflared\bin
:: copy cloudflared.exe there, then, as administrator:
cd C:\Cloudflared\bin
cloudflared.exe service install
mkdir C:\Windows\System32\config\systemprofile\.cloudflared
copy %USERPROFILE%\.cloudflared\cert.pem C:\Windows\System32\config\systemprofile\.cloudflared\
copy %USERPROFILE%\.cloudflared\<TUNNEL-UUID>.json C:\Windows\System32\config\systemprofile\.cloudflared\
copy %USERPROFILE%\.cloudflared\fbc-review.yml C:\Windows\System32\config\systemprofile\.cloudflared\config.yml
```

The service reads `config.yml` from the system profile, which is why the file is
copied under that name rather than the per-tunnel one. Then point the service at
it — in `regedit`, under
`HKEY_LOCAL_MACHINE\SYSTEM\CurrentControlSet\Services\Cloudflared`, set
`ImagePath` to:

```
C:\Cloudflared\bin\cloudflared.exe --config=C:\Windows\System32\config\systemprofile\.cloudflared\config.yml tunnel run
```

Then `sc start cloudflared`. Only one `cloudflared` service may run per machine.
Verify against the current
[Windows service page](https://developers.cloudflare.com/tunnel/advanced/local-management/as-a-service/windows/)
before running it — these steps were taken from that page and it has changed
before.

Note that the credentials JSON and `cert.pem` are account credentials. They live
in `~/.cloudflared` and must never be copied into the repository; `.gitignore`
carries a `.cloudflared/` entry as a backstop.

### Teardown

```bash
docker rm -f fbc-test              # the app
# Ctrl-C the tunnel window, then, to give up the hostname entirely:
# delete the CNAME for fbc.omniflexfitness.com in the Cloudflare dashboard
cloudflared tunnel delete fbc-review
```

Deleting the tunnel without deleting the CNAME leaves the record pointing at a
UUID that no longer exists, which is error 1033 forever rather than a clean
NXDOMAIN. Delete the record first.

### When billing opens

This route and the Google Cloud route are not exclusive, but they cannot both
hold the hostname. To move to Cloud Run and Firebase Hosting later: run
`scripts/provision.sh`, confirm the `*.web.app` URL end to end, then delete the
tunnel's CNAME and add the custom domain in the Firebase console, which prints
its own records (section 4). Cloudflare stays the DNS provider either way — the
records it holds are what changes. The Firebase records must be **DNS-only
(grey cloud)**: Firebase issues and serves its own certificate, and proxying the
record puts Cloudflare's certificate in front of a host that is not expecting
it. That is the mirror image of the tunnel's requirement above, and mixing the
two up is the single easiest way to break either.

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
| cloudflared | any current | Only for section 0c. `winget install --id Cloudflare.cloudflared`, or `brew install cloudflared`. |

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

**The hostname is currently held by the Cloudflare Tunnel (§0c).** Its CNAME
must be deleted before Firebase can take the name — two records cannot hold it,
and Firebase's verification fails against a record pointing at
`cfargotunnel.com`.

`omniflexfitness.com` is on **Cloudflare**, so the records go into the
Cloudflare dashboard rather than a registrar. Add `fbc.omniflexfitness.com` in
the Firebase Hosting console; it prints the exact records to enter.

**They must be DNS-only — grey cloud, proxy off.** Firebase issues and serves
its own certificate for the hostname, and a proxied record puts Cloudflare's
certificate in front of an origin that is not expecting it; it also breaks the
ACME challenge Firebase uses to issue in the first place. This is the exact
opposite of the tunnel's requirement in §0c, where the record *must* stay
proxied. Getting these the wrong way round breaks whichever route you are on,
and the symptom — a hostname that will not serve — looks the same either way.

Two more Cloudflare-specific things to check in the zone before waiting on
propagation:

- **CAA records.** If the zone has any, they must permit Google's CA
  (`pki.goog`) or Firebase's certificate will never issue. No CAA records at all
  is fine; a restrictive set is the failure that looks like slow propagation.
- **Universal SSL** covers `*.omniflexfitness.com` for Cloudflare-proxied
  traffic only, and is irrelevant to a grey-clouded Firebase record. Do not
  read a valid certificate on another subdomain as evidence this one will work.

Record what was actually entered:

| Type | Host | Value | Proxy | TTL |
| --- | --- | --- | --- | --- |
| _(fill in from the console)_ | | | DNS only | |

Then:

1. Wait for propagation, then confirm the TLS certificate is valid.
2. Confirm HTTP redirects to HTTPS.
3. **Add `fbc.omniflexfitness.com` to Firebase Auth → Settings → Authorised
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

Until all five are set, the `deployment configured?` job reports which are
missing and the **Cloud Run** and **Firebase Hosting** jobs are skipped. The
tests still gate every push — a skipped deploy is not a green light on a broken
build, it is the deploy declining to run against a deployment that does not
exist yet. Setting the variables is the only step needed to turn it on; nothing
in the workflow has to change.

Before that gate existed, an unconfigured repository failed
`google-github-actions/auth` on every push to main with *"the GitHub Action
workflow must specify exactly one of `workload_identity_provider` or
`credentials_json`"*. Every run since the workflow was written was red for that
reason while all three test jobs passed, which is the same failure mode the
review engine itself is built to avoid: a red that means nothing hides the one
that means something.

---

## 6. Environment variables the service reads

All are read once at startup by `webapp/config.py`.

| Variable | Default | Meaning |
| --- | --- | --- |
| `FBC_BUCKET` | — | Cloud Storage bucket. Required. |
| `FBC_PROJECT_ID` | `GOOGLE_CLOUD_PROJECT` | GCP project |
| `FBC_ALLOWED_EMAILS` | empty | Comma-separated allowlist. **Empty means nobody gets in.** |
| `FBC_SIGNER_SA` | ambient | Service account used for V4 signing |
| `FBC_MAX_UPLOAD_MB` | 120 | Hard limit, enforced while streaming. `share.ps1` passes **95** behind the tunnel — Cloudflare rejects a body over 100 MB at the edge (§0c). |
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
curl -s -o /dev/null -w '%{time_total}\n' https://fbc.omniflexfitness.com/healthz
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
- [x] A real permit set completes end to end. The 24-sheet Sculpted Hot Pilates
      set was uploaded through the browser client: 28 pages out, 200 CAD layers
      preserved, 34 live annotations, 13 markers placed, 17.4 MB, **14.6 s**.
      The marked-up PDF was opened and read.
- [x] **The regression gate passes against the real set** — the first time it
      has ever actually run (see below)
- [ ] The signed download URL works from a browser with no session, and expires
      — **blocked**: needs a real bucket
- [ ] An expired signed URL triggers the re-fetch path — code written and
      reviewed, not exercised against real expiry
- [ ] Cold start latency measured — **blocked**: not deployed
- [ ] Billing alert set — **blocked**: no project
- [ ] Custom domain, TLS, sign-in on the custom domain — **blocked**

---

## 9a. What the real permit sets showed

Four sets from `Building Codes/Unreviewed Plans` were run through the engine.

**Sculpted Hot Pilates (24 sheets, 17.8 MB)** reproduces the documented result
exactly — 1 CRITICAL, 2 HIGH, 4 MEDIUM, 5 VERIFIED, 1 MEASURED, 1 abstention,
matching `webapp/README.md` for A-3 sprinklered. 200 CAD layers, 6 cited code
rows, all six schedules, 5 doors, scale on 13/24 pages with 12 at high
confidence. `tests/test_regression.py` prints `OK` against it.

**The other three produce zero findings and twelve abstentions**, and the reason
is not what it looks like:

| Set | Sheets | Verdict | CAD layers | Live text | Cited sections |
| --- | --- | --- | --- | --- | --- |
| Sculpted Hot Pilates | 24 | vector | **200** | 111 k chars | **6** |
| ITEC Building Plans | 35 | vector | 0 | **223 k chars** | **0** |
| JSP Naples Arch | 14 | vector | 0 | plenty | 0 |
| JSP Naples MEP | 15 | vector | 0 | plenty | 0 |

All four are **already proper vector PDFs with live text**. None of them is a
scan, and the raster rebuild would do nothing for any of them — ITEC carries
twice Sculpted's text.

Two things are missing instead:

1. **No parenthesised section citations.** `ARCHITECTURE.md` §3 explains that
   everything is keyed on the cited section number because it is the most stable
   token available — `(1006.2.1)` does not wrap or get abbreviated the way a
   label does. Sculpted's drafter prints those citations; these drafters do not.
   With no citation to key on, every code-datum rule abstains, which is the
   citation-keying strategy failing honestly rather than guessing.
2. **No optional content groups.** Sculpted preserves 200 CAD layers; the others
   flatten them. `MEASURE.EGRESS_EXTENT` selects geometry by layer name, so it
   abstains on all three regardless of anything else.

### Why ITEC really produces nothing — three separate gaps, measured

Investigated properly rather than assumed. The three are independent, and
closing any one alone changes no finding.

**Gap 1 — the code tables were pixels. Closed.**
ITEC pastes its code-analysis tables onto G-002 and A-101 as images; its hand
review says so ("plotted from AutoCAD LT with no preserved layers and raster
code tables"). Those sheets are genuinely vector, so the whole-sheet raster
check never fired on them. `webapp/pdfkind.py` now measures raster *regions*
too, and `convert.read_regions()` OCRs just those and writes the words back as
an invisible text layer, leaving the vector content untouched.

Measured on ITEC: 11 sheets, 5 regions on the two that matter, **27,826
characters recovered — live text 223,422 → 252,995** in 92 s. `OCCUPANT LOAD`,
`DOOR SCHEDULE`, `PANEL SCHEDULE` and `LOAD CALCULATION` all go from absent to
present, and the rows behind the hand review's finding H-01 come back legibly.

**Gap 2 — nothing to key the recovered text on. Open.**
Extraction joins on the parenthesised section number because it is the most
stable token on a sheet (`ARCHITECTURE.md` §3). ITEC contains **zero** of them
across 253k characters. It writes `TABLE 508.4`, `TABLE 601`, `TABLE 705.8`
instead. So the text is now readable and still unkeyable, and code data rows stay
at 0. This is the Tier B normalisation case, and it is now clearly worth doing
because there is finally data to key.

**Gap 3 — the rule corpus does not cover what ITEC gets wrong. Open.**
Even after OCR, `TRAVEL DISTANCE`, `COMMON PATH`, `DEAD END`, `EGRESS WIDTH`,
`CORRIDOR WIDTH` and `OUTDOOR AIR` are simply **not stated anywhere on ITEC's
35 sheets**. Those rules abstain because the drawing is silent, which is correct
and no amount of extraction changes it.

ITEC's actual problems, per its hand review, are a superseded code edition
(7th vs 8th), a self-contradicting occupancy analysis, and an OCCUPANT FACTOR
column holding unit numbers instead of code factors. The twelve implemented
rules check none of those.

So: **ITEC needs new rules more than it needs better extraction.** Sequencing
the normaliser ahead of rule authoring would be building a key for a lock that
is not on this door.

Sheet-code recovery also degrades on these sets: `sheet_index` returns `p1, p2,
p3 …` rather than `G-0, A-1`, catching only the occasional `E-3` or `A-12`.

### DWG and RVT

Decided rather than deferred.

**RVT: not supported, deliberately.** There is no open-source Revit reader. The
only routes are Autodesk's Model Derivative API — which uploads clients' permit
sets to Autodesk — or a licensed Revit install on Windows, which Cloud Run
cannot be. Ask for a PDF or DWG export instead; that is what firms send for
permit review anyway.

**DWG: DXF only, when it comes up.** `accoreconsole.exe` ships with the AutoCAD
on this workstation and converts DWG headlessly, but it is Windows-only and
licence-bound and cannot run in the container. `ezdxf` reads DXF, not DWG. So
the supported path is DXF in, rendered to a layered PDF in-process, with no
external binary and no licensing question.

**Before any of that, check the cheap fix.** The Sculpted set's 200 optional
content groups are literally AutoCAD layer names — `A-Wall`, `A-Anno-Titl`,
`Life Safety|Egress Path`. That is AutoCAD's PDF export with "Include layer
information" enabled. ITEC and JSP simply exported without it. A one-line
instruction to the drafter produces exactly what CAD ingestion would, for no
code at all.

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
