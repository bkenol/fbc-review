#!/usr/bin/env bash
#
# Set up Firebase sign-in for a deployment with no Cloud Billing account.
#
#   bash scripts/setup-auth.sh
#
# Safe to re-run: every step checks for what it creates and skips if it is
# already there, so a failure halfway through is fixed by running it again.
#
# This is the free subset of scripts/provision.sh. It touches nothing that needs
# billing — no Cloud Run, no bucket, no Hosting deploy — because Firebase
# Authentication, Hosting and Firestore are all free on the Spark plan and only
# Cloud Storage and Cloud Run are not. See docs/DEPLOYMENT.md section 0d.
#
# What it does:
#   1. adds Firebase to the Google Cloud project, if it is not there already
#   2. creates a web app, if there is not one already
#   3. writes the real web config into web/src/app/core/firebase-config.ts
#   4. creates the token-verifying service account and one key
#
# What it deliberately leaves to you, because neither has a CLI:
#   - enabling the Google sign-in provider
#   - authorising the hostname you will serve from
#
# Runs in Git Bash on Windows, or any POSIX shell.

set -euo pipefail

# ── settings ──────────────────────────────────────────────────────────────
# Defaulted, not required. The project already exists — `provision.sh` uses the
# same default — and an unset variable interpolated into a service account
# address produces `fbc-auth@.iam.gserviceaccount.com`, which fails as an
# unexplained INVALID_ARGUMENT rather than as anything you could act on.
PROJECT_ID="${FBC_PROJECT_ID:-fbc-reviewer}"
SA_NAME="${FBC_SA_NAME:-fbc-auth}"
DOMAIN="${FBC_DOMAIN:-fbc.omniflexfitness.com}"

# Every path is resolved against the repository, not the working directory, so
# this works from anywhere — including a home directory, which is where a
# pasted `python scripts/...` most often gets run by accident.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
KEY_PATH="${FBC_SA_KEY:-$ROOT/secrets/firebase-sa.json}"

bold()  { printf '\n\033[1m%s\033[0m\n' "$*"; }
info()  { printf '  %s\n' "$*"; }
ok()    { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn()  { printf '  \033[33m!\033[0m %s\n' "$*"; }
die()   { printf '\n\033[31m✗ %s\033[0m\n\n' "$*" >&2; exit 1; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# ── 0. preflight ──────────────────────────────────────────────────────────
bold "0. Preflight"

for tool in gcloud firebase python; do
  command -v "$tool" >/dev/null || die "$tool is not on PATH."
done
ok "gcloud, firebase, python present"

ACCOUNT="$(gcloud config get-value account 2>/dev/null || true)"
case "$ACCOUNT" in
  *gserviceaccount.com)
    die "gcloud is authenticated as the service account $ACCOUNT.
     Run:  gcloud auth login" ;;
  "") die "gcloud has no active account. Run: gcloud auth login" ;;
esac
gcloud auth print-access-token >/dev/null 2>&1 \
  || die "The credential for $ACCOUNT has expired. Run: gcloud auth login"
ok "gcloud authenticated as $ACCOUNT"

# The Firebase CLI reads .firebaserc from the working directory on every
# command, so this probe runs somewhere empty — the same trick provision.sh
# uses, and for the same reason.
FB_PROBE="$TMP/probe"
mkdir -p "$FB_PROBE"
FB_LIST="$( cd "$FB_PROBE" && firebase login:list 2>/dev/null || true )"
case "$FB_LIST" in
  *"No authorized accounts"*|"") die "The Firebase CLI is not authenticated. Run: firebase login" ;;
esac
ok "firebase CLI authenticated"

gcloud projects describe "$PROJECT_ID" >/dev/null 2>&1 \
  || die "No Google Cloud project '$PROJECT_ID' that this account can see.
     Set FBC_PROJECT_ID if it is called something else, or create it:
       gcloud projects create $PROJECT_ID --name='FBC Reviewer'"
ok "project $PROJECT_ID"

# ── 1. Firebase on the project ────────────────────────────────────────────
bold "1. Firebase"

FB_PROJECTS="$(firebase projects:list 2>/dev/null || true)"
case "$FB_PROJECTS" in
  *"$PROJECT_ID"*) ok "Firebase already enabled" ;;
  *)
    firebase projects:addfirebase "$PROJECT_ID"
    ok "Firebase enabled on $PROJECT_ID" ;;
esac

APP_ID="$(firebase apps:list WEB --project "$PROJECT_ID" 2>/dev/null \
          | grep -oE '1:[0-9]+:web:[a-z0-9]+' | head -1 || true)"
if [ -z "$APP_ID" ]; then
  firebase apps:create WEB "FBC Reviewer" --project "$PROJECT_ID" >/dev/null
  APP_ID="$(firebase apps:list WEB --project "$PROJECT_ID" 2>/dev/null \
            | grep -oE '1:[0-9]+:web:[a-z0-9]+' | head -1)"
  ok "web app created"
fi
[ -n "$APP_ID" ] || die "Could not find or create a Firebase web app."
ok "web app $APP_ID"

# ── 2. the web config, into the client ────────────────────────────────────
bold "2. Client config"
# Public by design: these values identify the project and authorise nothing.
# Access is decided server-side by verifying the ID token against the allowlist.
firebase apps:sdkconfig WEB "$APP_ID" --project "$PROJECT_ID" --json > "$TMP/sdk.json" 2>/dev/null \
  || firebase apps:sdkconfig WEB "$APP_ID" --project "$PROJECT_ID" > "$TMP/sdk.json"
python scripts/write_firebase_config.py "$TMP/sdk.json"
ok "web/src/app/core/firebase-config.ts written"

if [ ! -f "$ROOT/.firebaserc" ] || ! grep -q "$PROJECT_ID" "$ROOT/.firebaserc"; then
  printf '{\n  "projects": {\n    "default": "%s"\n  }\n}\n' "$PROJECT_ID" > "$ROOT/.firebaserc"
  ok ".firebaserc points at $PROJECT_ID"
fi

# ── 3. the token-verifying service account ────────────────────────────────
bold "3. Service account"
# Needed because webapp/auth.py verifies with check_revoked=True, and that check
# calls the Firebase Auth backend to ask whether the session was revoked or the
# user disabled. Signature verification alone needs only Google's public
# certificates; the revocation check needs a credential, and a container on your
# own hardware has no ambient identity to use.

if gcloud iam service-accounts describe "$SA_EMAIL" --project "$PROJECT_ID" >/dev/null 2>&1; then
  ok "service account $SA_EMAIL already exists"
else
  gcloud iam service-accounts create "$SA_NAME" \
    --project "$PROJECT_ID" --display-name="FBC token verifier" >/dev/null
  ok "service account $SA_EMAIL created"
fi

# Verifying tokens needs no project role at all — the Admin SDK authenticates as
# the service account and reads its own project's users. Nothing is granted here
# deliberately; if a later change needs a role, add the narrowest one then.

mkdir -p "$(dirname "$KEY_PATH")"
if [ -s "$KEY_PATH" ]; then
  ok "key already present at ${KEY_PATH#$ROOT/}"
else
  if gcloud iam service-accounts keys create "$KEY_PATH" \
       --iam-account="$SA_EMAIL" --project "$PROJECT_ID" 2>"$TMP/keyerr"; then
    chmod 600 "$KEY_PATH" 2>/dev/null || true
    ok "key written to ${KEY_PATH#$ROOT/}  (git-ignored)"
  else
    cat "$TMP/keyerr" >&2
    die "Could not create a key for $SA_EMAIL.

     The usual cause is the organisation policy
     constraints/iam.disableServiceAccountKeyCreation, which blocks key
     creation outright. Check with:

       gcloud resource-manager org-policies describe \\
         constraints/iam.disableServiceAccountKeyCreation \\
         --project=$PROJECT_ID --effective

     If it is enforced and you cannot lift it, the fallback is to run the
     service somewhere with a Google identity of its own, or to accept
     check_revoked=False — see docs/DEPLOYMENT.md section 0d."
  fi
fi

# ── done ──────────────────────────────────────────────────────────────────
bold "Done"
cat <<EOF

  Project:     $PROJECT_ID
  Web app:     $APP_ID
  Key:         ${KEY_PATH#$ROOT/}

  Two things left, both needing a browser, neither with a CLI:

  1. Enable Google sign-in.
     https://console.firebase.google.com/project/$PROJECT_ID/authentication/providers

  2. Authorise the hostname you will serve from. Authentication -> Settings ->
     Authorised domains -> add:  $DOMAIN
     https://console.firebase.google.com/project/$PROJECT_ID/authentication/settings

     localhost is authorised out of the box, so local testing works before you
     do this. Google sign-in then fails on the tunnel hostname and nowhere
     else, which is a confusing way to find out — so do it now.

  Then run it:

    export FBC_PROJECT_ID=$PROJECT_ID
    export FBC_ALLOWED_EMAILS=you@example.com
    bash scripts/share.sh --authenticated --persistent
    bash scripts/tunnel.sh          # in a second shell

EOF
