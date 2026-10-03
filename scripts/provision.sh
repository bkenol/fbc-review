#!/usr/bin/env bash
#
# Stand up the FBC Reviewer on Google Cloud, from nothing to a live URL.
#
#   bash scripts/provision.sh
#
# Safe to re-run: every step checks for what it creates and skips if it is
# already there, so a failure halfway through is fixed by running it again.
#
# It will stop and tell you what to do if it needs a decision that is yours —
# principally which billing account to attach.
#
# Runs in Git Bash on Windows, or any POSIX shell.

set -euo pipefail

# ── settings ──────────────────────────────────────────────────────────────
PROJECT_ID="${FBC_PROJECT_ID:-fbc-reviewer}"
PROJECT_NAME="${FBC_PROJECT_NAME:-FBC Reviewer}"
REGION="${FBC_REGION:-us-east1}"
SERVICE="fbc-review"
REPO="fbc"
BUCKET="${FBC_BUCKET:-${PROJECT_ID}-artefacts}"
DOMAIN="${FBC_DOMAIN:-fbc.omniflexfitness.com}"
ALLOWED_EMAILS="${FBC_ALLOWED_EMAILS:-bertin.kenol@omniflexfitness.com}"
BILLING_ACCOUNT="${FBC_BILLING_ACCOUNT:-}"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

bold()  { printf '\n\033[1m%s\033[0m\n' "$*"; }
info()  { printf '  %s\n' "$*"; }
ok()    { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn()  { printf '  \033[33m!\033[0m %s\n' "$*"; }
die()   { printf '\n\033[31m✗ %s\033[0m\n\n' "$*" >&2; exit 1; }

# ── 0. preflight ──────────────────────────────────────────────────────────
bold "0. Preflight"

for tool in gcloud firebase docker git; do
  command -v "$tool" >/dev/null || die "$tool is not on PATH."
done
ok "gcloud, firebase, docker, git present"

ACCOUNT="$(gcloud config get-value account 2>/dev/null || true)"
case "$ACCOUNT" in
  *gserviceaccount.com)
    die "gcloud is authenticated as the service account $ACCOUNT.
     That is a CI identity for another project — provisioning with it would
     create these resources in the wrong place.

     Run:  gcloud auth login
     then: gcloud config set account you@yourdomain.com" ;;
  "") die "gcloud has no active account. Run: gcloud auth login" ;;
esac

gcloud auth print-access-token >/dev/null 2>&1 \
  || die "The credential for $ACCOUNT has expired. Run: gcloud auth login"
ok "authenticated as $ACCOUNT"

# Two traps in one check, both of which report a good login as a bad one.
#
# Run it from an empty directory: the Firebase CLI reads .firebaserc on *every*
# command, so a project that does not exist yet makes even `login:list` fail
# with "Invalid project id" and look like an auth problem.
#
# And capture, then match — never pipe into `grep -q`. Under `set -o pipefail`
# grep -q exits on the first match and closes the pipe, the CLI upstream dies of
# SIGPIPE, and the pipeline reports failure on a command that succeeded.
FB_PROBE="$(mktemp -d)"
FB_LIST="$( cd "$FB_PROBE" && firebase login:list 2>/dev/null || true )"
rm -rf "$FB_PROBE"
case "$FB_LIST" in
  *[Ll]"ogged in as"*) ;;
  *) die "The Firebase CLI is not authenticated. Run: firebase login" ;;
esac
FB_USER="$(printf '%s' "$FB_LIST" | grep -oiE '[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+' | head -1 || true)"
ok "Firebase CLI authenticated as ${FB_USER:-unknown}"

# ── 1. project ────────────────────────────────────────────────────────────
bold "1. Project"

if gcloud projects describe "$PROJECT_ID" >/dev/null 2>&1; then
  ok "project $PROJECT_ID already exists"
else
  info "creating $PROJECT_ID …"
  gcloud projects create "$PROJECT_ID" --name="$PROJECT_NAME" \
    || die "Could not create $PROJECT_ID. Project ids are globally unique —
     if it is taken, re-run with:  FBC_PROJECT_ID=something-else bash scripts/provision.sh"
  ok "created $PROJECT_ID"
fi
gcloud config set project "$PROJECT_ID" >/dev/null
PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"
ok "project number $PROJECT_NUMBER"

# ── 2. billing ────────────────────────────────────────────────────────────
bold "2. Billing"
# Nothing else can be enabled until billing is attached AND the account behind
# it is open. Choosing which account to bill is not a decision this script
# should make for you.

# Billing API calls are charged against a quota project, and gcloud defaults to
# whatever `gcloud config` last pointed at — which may be a project that has
# never enabled the API. Enable it here and pass --billing-project explicitly.
gcloud services enable cloudbilling.googleapis.com --project="$PROJECT_ID" >/dev/null 2>&1 || true
BQ=(--billing-project="$PROJECT_ID")

billing_state() {
  gcloud beta billing projects describe "$PROJECT_ID" "${BQ[@]}"     --format='value(billingEnabled)' 2>/dev/null || true
}

if [ "$(billing_state)" = "True" ]; then
  ok "billing enabled"
else
  if [ -n "$BILLING_ACCOUNT" ]; then
    info "attaching billing account $BILLING_ACCOUNT …"
    gcloud beta billing projects link "$PROJECT_ID"       --billing-account="$BILLING_ACCOUNT" "${BQ[@]}" >/dev/null || true
  fi

  if [ "$(billing_state)" != "True" ]; then
    # Distinguish "no account attached" from "attached but the account is
    # closed". They look the same in a generic failure and need different fixes.
    printf '
  Billing accounts on this login:

'
    gcloud beta billing accounts list "${BQ[@]}" 2>/dev/null || true

    OPEN_COUNT="$(gcloud beta billing accounts list "${BQ[@]}"       --filter='open=true' --format='value(name)' 2>/dev/null | wc -l | tr -d ' ')"

    if [ "${OPEN_COUNT:-0}" -eq 0 ]; then
      die "None of your billing accounts is open, so nothing can be provisioned.

     A closed account can be attached to a project but pays for nothing, which
     is why enabling Cloud Run fails with \"Billing account ... is not open\".

     Reopen one, or create a new one, here:
       https://console.cloud.google.com/billing

     Then re-run this script. Everything up to this point is already done and
     will be skipped."
    fi

    die "Billing is not enabled on $PROJECT_ID.
     Pick an open account from the list above and re-run:

       FBC_BILLING_ACCOUNT=XXXXXX-XXXXXX-XXXXXX bash scripts/provision.sh"
  fi
  ok "billing enabled"
fi

# ── 3. APIs ───────────────────────────────────────────────────────────────
bold "3. APIs"
gcloud services enable \
  run.googleapis.com \
  artifactregistry.googleapis.com \
  firestore.googleapis.com \
  storage.googleapis.com \
  iamcredentials.googleapis.com \
  firebase.googleapis.com \
  firebasehosting.googleapis.com \
  identitytoolkit.googleapis.com \
  cloudbuild.googleapis.com \
  --project="$PROJECT_ID"
ok "APIs enabled"

# ── 4. Artifact Registry ──────────────────────────────────────────────────
bold "4. Artifact Registry"
if gcloud artifacts repositories describe "$REPO" --location="$REGION" >/dev/null 2>&1; then
  ok "repository $REPO exists"
else
  gcloud artifacts repositories create "$REPO" \
    --repository-format=docker --location="$REGION" \
    --description="FBC Reviewer images"
  ok "created $REPO"
fi

# ── 5. Firestore ──────────────────────────────────────────────────────────
bold "5. Firestore"
# Native mode is a one-time, irreversible per-project choice.
if gcloud firestore databases describe --database='(default)' >/dev/null 2>&1; then
  ok "Firestore database exists"
else
  gcloud firestore databases create --location=nam5 --type=firestore-native
  ok "created Firestore in Native mode"
fi

# ── 6. Bucket ─────────────────────────────────────────────────────────────
bold "6. Cloud Storage"
if gcloud storage buckets describe "gs://${BUCKET}" >/dev/null 2>&1; then
  ok "bucket gs://${BUCKET} exists"
else
  gcloud storage buckets create "gs://${BUCKET}" \
    --location="$REGION" \
    --uniform-bucket-level-access \
    --public-access-prevention
  ok "created gs://${BUCKET} (private, uniform access)"
fi

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
printf '{"rule":[{"action":{"type":"Delete"},"condition":{"age":30}}]}\n' > "$TMP/lifecycle.json"
gcloud storage buckets update "gs://${BUCKET}" --lifecycle-file="$TMP/lifecycle.json" >/dev/null
ok "lifecycle: artefacts deleted after 30 days"

# CORS is not optional. The results view fetches findings.json from a signed URL
# with XHR; a signed URL authorises the request but does not produce CORS
# headers. Download *links* are navigations and work without this, so a casual
# click-test passes while the findings table stays empty forever.
sed "s/REPLACE_PROJECT_ID/${PROJECT_ID}/g" cors.json > "$TMP/cors.json"
gcloud storage buckets update "gs://${BUCKET}" --cors-file="$TMP/cors.json" >/dev/null
ok "CORS set for the Hosting origins and $DOMAIN"

# ── 7. Runtime service account ────────────────────────────────────────────
bold "7. Service account and IAM"
SA="${SERVICE}-sa@${PROJECT_ID}.iam.gserviceaccount.com"
if gcloud iam service-accounts describe "$SA" >/dev/null 2>&1; then
  ok "service account exists"
else
  gcloud iam service-accounts create "${SERVICE}-sa" --display-name="FBC Reviewer runtime"
  ok "created ${SERVICE}-sa"
fi

gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${SA}" --role="roles/datastore.user" \
  --condition=None >/dev/null
ok "Firestore access"

gcloud storage buckets add-iam-policy-binding "gs://${BUCKET}" \
  --member="serviceAccount:${SA}" --role="roles/storage.objectAdmin" >/dev/null
ok "bucket access, scoped to gs://${BUCKET} only"

# The binding this deployment most often forgets. Cloud Run's credentials carry
# no private key, so V4 signing goes through the IAM Credentials signBlob API —
# which needs the service account to be able to impersonate ITSELF. Without it
# every download URL fails to generate and jobs finish with broken links.
gcloud iam service-accounts add-iam-policy-binding "$SA" \
  --member="serviceAccount:${SA}" \
  --role="roles/iam.serviceAccountTokenCreator" >/dev/null
ok "signBlob self-impersonation (V4 signed URLs)"

# ── 8. Image ──────────────────────────────────────────────────────────────
bold "8. Container image"
TAG="$(git rev-parse --short HEAD 2>/dev/null || date +%s)"
IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO}/${SERVICE}:${TAG}"
gcloud auth configure-docker "${REGION}-docker.pkg.dev" --quiet >/dev/null 2>&1
info "building and pushing $IMAGE"
info "(first build pulls Tesseract and OpenCV and compiles LibreDWG — several minutes)"
# linux/amd64 explicitly: Cloud Run runs amd64, and an accidentally-arm64 image
# fails at start rather than at build.
docker buildx build --platform linux/amd64 -t "$IMAGE" --push .
ok "pushed $IMAGE"

# ── 9. Cloud Run ──────────────────────────────────────────────────────────
bold "9. Cloud Run"
# 4Gi, not 2Gi: a drawing review reads the DWG in a subprocess beside the
# service, and on the reference drawing (23 MB DWG, 8 sheets) that subprocess
# peaks at 1.13 GB, while the 170 MB DXF it converts to sits on Cloud Run's
# in-memory disk, which counts against the same limit. FBC_CAD_CONCURRENCY=1
# lets one drawing at a time per instance do that; a second waits for the slot
# rather than doubling the peak (webapp/cadjob.py). 4Gi needs at least 1 vCPU,
# so --cpu=2 stands.
gcloud run deploy "$SERVICE" \
  --image="$IMAGE" \
  --region="$REGION" \
  --platform=managed \
  --memory=4Gi --cpu=2 \
  --concurrency=4 \
  --timeout=900 \
  --min-instances=0 --max-instances=5 \
  --service-account="$SA" \
  --set-env-vars="FBC_BUCKET=${BUCKET},FBC_PROJECT_ID=${PROJECT_ID},FBC_ALLOWED_EMAILS=${ALLOWED_EMAILS},FBC_SIGNER_SA=${SA},FBC_CAD_CONCURRENCY=1" \
  --allow-unauthenticated \
  --quiet
# --allow-unauthenticated is a considered choice, not laziness: the application
# authenticates every route itself (webapp/auth.py verifies the Firebase ID
# token and checks the allowlist on everything except /healthz, which returns a
# static object and touches no data). It also avoids depending on which service
# agent Firebase Hosting uses to invoke Cloud Run, which Google has changed.
RUN_URL="$(gcloud run services describe "$SERVICE" --region="$REGION" --format='value(status.url)')"
ok "deployed: $RUN_URL"

info "checking /healthz on the new revision …"
for _ in 1 2 3 4 5 6; do
  code="$(curl -s -o /dev/null -w '%{http_code}' "${RUN_URL}/healthz" || true)"
  [ "$code" = "200" ] && { ok "healthz 200"; break; }
  sleep 5
done
[ "${code:-}" = "200" ] || die "The service did not answer /healthz. Check: gcloud run services logs read $SERVICE --region=$REGION"

# ── 10. Firebase ──────────────────────────────────────────────────────────
bold "10. Firebase"
# Same SIGPIPE trap as the preflight check — capture, then match.
FB_PROJECTS="$(firebase projects:list 2>/dev/null || true)"
case "$FB_PROJECTS" in
  *"$PROJECT_ID"*) ok "Firebase already enabled on the project" ;;
  *)
    firebase projects:addfirebase "$PROJECT_ID"
    ok "Firebase enabled" ;;
esac

APP_ID="$(firebase apps:list WEB --project "$PROJECT_ID" 2>/dev/null | grep -oE '1:[0-9]+:web:[a-z0-9]+' | head -1 || true)"
if [ -z "$APP_ID" ]; then
  firebase apps:create WEB "FBC Reviewer" --project "$PROJECT_ID" >/dev/null
  APP_ID="$(firebase apps:list WEB --project "$PROJECT_ID" 2>/dev/null | grep -oE '1:[0-9]+:web:[a-z0-9]+' | head -1)"
fi
ok "web app $APP_ID"

# Write the real Firebase web config into the client. These values are public
# by design — they identify the project, they do not authorise anything; access
# is decided server-side by verifying the ID token against the allowlist.
firebase apps:sdkconfig WEB "$APP_ID" --project "$PROJECT_ID" --json > "$TMP/sdk.json" 2>/dev/null \
  || firebase apps:sdkconfig WEB "$APP_ID" --project "$PROJECT_ID" > "$TMP/sdk.json"
python scripts/write_firebase_config.py "$TMP/sdk.json"
ok "web/src/app/core/firebase-config.ts written"

printf '{\n  "projects": {\n    "default": "%s"\n  }\n}\n' "$PROJECT_ID" > .firebaserc
sed -i.bak "s/REPLACE_PROJECT_ID/${PROJECT_ID}/g" firebase.json && rm -f firebase.json.bak
ok ".firebaserc and firebase.json point at $PROJECT_ID"

# ── 11. Client + Hosting ──────────────────────────────────────────────────
bold "11. Client and Hosting"
( cd web && npm ci --no-audit --no-fund && npm run build )
ok "client built"

firebase deploy --only hosting,firestore --project "$PROJECT_ID" --non-interactive
HOST_URL="https://${PROJECT_ID}.web.app"
ok "hosting live: $HOST_URL"

# ── done ──────────────────────────────────────────────────────────────────
bold "Done"
cat <<EOF

  Live now:        $HOST_URL
  API:             $RUN_URL
  Allowlisted:     $ALLOWED_EMAILS

  Two things left, both needing a browser:

  1. Firebase console -> Authentication -> Sign-in method -> enable Google.
     https://console.firebase.google.com/project/$PROJECT_ID/authentication/providers

  2. Custom domain $DOMAIN:
     https://console.firebase.google.com/project/$PROJECT_ID/hosting/main
     Add the domain, then paste the DNS records it shows you into your
     registrar. Afterwards add $DOMAIN under Authentication -> Settings ->
     Authorised domains, or Google sign-in works on .web.app and fails on the
     custom domain. That one catches everyone once.

  Add someone to the allowlist later:

    gcloud run services update $SERVICE --region=$REGION \\
      --update-env-vars="FBC_ALLOWED_EMAILS=a@x.com,b@y.com"

  Roll back:

    gcloud run revisions list --service=$SERVICE --region=$REGION
    gcloud run services update-traffic $SERVICE --region=$REGION --to-revisions=REVISION=100

EOF
