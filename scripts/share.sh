#!/usr/bin/env bash
#
# Run the whole app as one container, ready to put behind a tunnel — the Git
# Bash equivalent of scripts/share.ps1.
#
#   bash scripts/share.sh [--port 8060] [--max-upload-mb 95] [--skip-build]
#                        [--persistent] [--authenticated]
#
# Builds the client, then runs the API with FBC_STATIC_DIR pointed at the bundle
# so a single origin serves both. That removes CORS from the picture and means a
# tunnel only has to forward one port.
#
# WARNING — by default this runs with FBC_DEV_UNSAFE_AUTH=1, which turns
# authentication off entirely. Anyone who has the URL can upload a permit set
# and spend your CPU. It is for a short, unlisted test, not something to leave
# running.
#
# --authenticated is the answer to that, and needs no Cloud Billing account.
# It runs the same filesystem backend with real Firebase sign-in and the
# server-side allowlist, because FBC_BACKEND and FBC_DEV_UNSAFE_AUTH are now
# separate settings. Firebase Authentication is free on the Spark plan; only
# Cloud Storage and Cloud Run need billing, and this mode uses neither. It
# needs three things in the environment:
#
#   FBC_PROJECT_ID        the Firebase project id
#   FBC_ALLOWED_EMAILS    who may sign in, comma-separated
#   FBC_SA_KEY            path to a service account key JSON (default
#                         ./secrets/firebase-sa.json), mounted read-only
#
# The key is needed because verifying an ID token with check_revoked=True calls
# the Firebase Auth backend, which off-GCP has no ambient identity to use. Keep
# it outside the repository; docs/DEPLOYMENT.md section 0d says how to make one.
#
# The per-user rate limits still apply and, with authentication off, every
# request shares one identity — so 3 concurrent and 10 reviews an hour become a
# global cap rather than a per-person one. That is the main thing keeping a
# shared link from being abused.
#
# Storage is the local filesystem under .devdata, not Cloud Storage, and it is
# wiped whenever you delete that directory.
#
# ── the two things Git Bash gets wrong, and how this handles them ─────────
# MSYS rewrites anything that looks like a POSIX path in an argument. Without
# MSYS_NO_PATHCONV the container-side `/app/client` becomes a Windows path and
# the client silently 404s — share.ps1 carries a comment about exactly this.
# And the host side of a -v needs a Windows path, which is what `cygpath -m`
# produces (forward slashes, drive letter — the form Docker takes without
# quoting games).

set -euo pipefail

PORT="${FBC_PORT:-8060}"
# Below Cloudflare's 100 MB request-body ceiling, which applies to every proxied
# request including tunnel traffic. Over it the edge returns its own opaque 413
# and the upload never reaches the app, so the app's limit is set under the
# edge's and a too-large set gets the typed error instead. Raising this past 95
# only makes sense off the Cloudflare path.
MAX_UPLOAD_MB="${FBC_MAX_UPLOAD_MB:-95}"
SKIP_BUILD=0
PERSISTENT=0
AUTHENTICATED=0

while [ $# -gt 0 ]; do
  case "$1" in
    --port)           PORT="$2"; shift 2 ;;
    --max-upload-mb)  MAX_UPLOAD_MB="$2"; shift 2 ;;
    --skip-build)     SKIP_BUILD=1; shift ;;
    --persistent)     PERSISTENT=1; shift ;;
    --authenticated)  AUTHENTICATED=1; shift ;;
    -h|--help)        sed -n '2,48p' "$0"; exit 0 ;;
    *)                printf 'Unknown option: %s\n' "$1" >&2; exit 2 ;;
  esac
done

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

bold()  { printf '\n\033[1m%s\033[0m\n' "$*"; }
info()  { printf '  %s\n' "$*"; }
ok()    { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn()  { printf '  \033[33m!\033[0m %s\n' "$*"; }
die()   { printf '\n\033[31m✗ %s\033[0m\n\n' "$*" >&2; exit 1; }

command -v docker >/dev/null 2>&1 || die "docker is not on PATH. Is Docker Desktop running?"

# Windows-form paths for the -v arguments. On a non-Windows shell cygpath does
# not exist and the POSIX path is already correct.
winpath() {
  if command -v cygpath >/dev/null 2>&1; then cygpath -m "$1"; else printf '%s' "$1"; fi
}

# ── node ──────────────────────────────────────────────────────────────────
# Angular 22 needs Node >= 22.22.3 / 24.15. The machine default may be older, so
# prefer an nvm-installed 24 and fall back to whatever is on PATH.
if [ -n "${LOCALAPPDATA:-}" ]; then
  if command -v cygpath >/dev/null 2>&1; then
    NODE24="$(cygpath -u "$LOCALAPPDATA")/nvm/v24.19.0"
  else
    NODE24="$LOCALAPPDATA/nvm/v24.19.0"
  fi
  if [ -d "$NODE24" ]; then
    PATH="$NODE24:$PATH"
    info "Using Node from $NODE24"
  fi
fi

# ── version ───────────────────────────────────────────────────────────────
# Stamped on the host, where the checkout is. The image carries VERSION but no
# .git, so a container left to work it out alone can only say `1.0.0-alpha` —
# true, and not enough to tell one local build from the next.
PYTHON="$ROOT/.venv/Scripts/python.exe"
[ -x "$PYTHON" ] || PYTHON="$ROOT/.venv/bin/python"
[ -x "$PYTHON" ] || PYTHON="python"
VERSION="$("$PYTHON" -m webapp.version 2>/dev/null | head -1 | tr -d '\r' || true)"
if [ -n "$VERSION" ]; then info "Version $VERSION"; fi

# ── client ────────────────────────────────────────────────────────────────
BUNDLE="$ROOT/web/dist/fbc-review/browser"
if [ "$SKIP_BUILD" -eq 0 ] || [ ! -d "$BUNDLE" ]; then
  # Install the client dependencies when they are missing or stale. Going
  # straight to `ng build` fails on a machine that has never been set up and —
  # far more often — the first time you build a branch that added a dependency:
  # "Cannot find module 'pdfjs-dist'". npm writes
  # node_modules/.package-lock.json when it installs, so comparing that against
  # the real lockfile catches both cases without an npm ci on every run.
  NPM_STAMP="$ROOT/web/node_modules/.package-lock.json"
  if [ ! -f "$NPM_STAMP" ] || [ "$ROOT/web/package-lock.json" -nt "$NPM_STAMP" ]; then
    bold 'Installing client dependencies...'
    ( cd "$ROOT/web" && npm ci --no-audit --no-fund )
  fi
  bold 'Building the client...'
  ( cd "$ROOT/web" && npx ng build )
fi
[ -d "$BUNDLE" ] || die "Client bundle not found at $BUNDLE"

# ── image ─────────────────────────────────────────────────────────────────
bold 'Building the image...'
docker build -q -t fbc-review:dev "$(winpath "$ROOT")" >/dev/null

docker rm -f fbc-test >/dev/null 2>&1 || true
mkdir -p "$ROOT/.devdata"

# ── the artefact signing key ──────────────────────────────────────────────
# Kept beside the blobs it authorises, so links survive a restart. Without a
# stable key the service generates one per process, which is safe but means
# every outstanding download link stops working when the container is replaced.
SECRET_FILE="$ROOT/.devdata/artefact.secret"
if [ ! -s "$SECRET_FILE" ]; then
  ( umask 077; head -c 32 /dev/urandom | base64 | tr -d '\n=' > "$SECRET_FILE" )
  info "Generated an artefact signing key at .devdata/artefact.secret"
fi
ARTEFACT_SECRET="$(cat "$SECRET_FILE")"

# ── authenticated mode preflight ──────────────────────────────────────────
# Checked before the container starts rather than after: a missing key here
# fails as an unreadable stack trace three seconds into startup, which is a
# much worse way to learn about it.
if [ "$AUTHENTICATED" -eq 1 ]; then
  SA_KEY="${FBC_SA_KEY:-$ROOT/secrets/firebase-sa.json}"
  [ -n "${FBC_PROJECT_ID:-}" ] || die "--authenticated needs FBC_PROJECT_ID (the Firebase project id)."
  [ -n "${FBC_ALLOWED_EMAILS:-}" ] || die "--authenticated needs FBC_ALLOWED_EMAILS. Empty means nobody gets in."
  [ -f "$SA_KEY" ] || die "No service account key at $SA_KEY. See docs/DEPLOYMENT.md section 0d, or set FBC_SA_KEY."
  if grep -q 'REPLACE_ME' "$ROOT/web/src/app/core/firebase-config.ts" 2>/dev/null; then
    warn 'web/src/app/core/firebase-config.ts still has placeholders — sign-in will not work.'
    warn 'Run: firebase apps:sdkconfig web > sdk.json && python scripts/write_firebase_config.py sdk.json'
    warn 'then rebuild the client.'
  fi
fi

bold 'Starting...'
LIFECYCLE='--rm'
if [ "$PERSISTENT" -eq 1 ]; then LIFECYCLE='--restart=unless-stopped'; fi

# Built as an array so an empty version contributes no argument at all rather
# than an empty one.
RUN_ARGS=(
  run "$LIFECYCLE" -d --name fbc-test
  -p "${PORT}:8080"
  -e FBC_BACKEND=local
  -e FBC_STATIC_DIR=/app/client
  -e "FBC_MAX_UPLOAD_MB=${MAX_UPLOAD_MB}"
  -e FBC_WORKERS=2
  -e "FBC_ARTEFACT_SECRET=${ARTEFACT_SECRET}"
  -v "$(winpath "$BUNDLE"):/app/client:ro"
  -v "$(winpath "$ROOT/.devdata"):/app/.devdata"
)

if [ "$AUTHENTICATED" -eq 1 ]; then
  RUN_ARGS+=(
    -e "FBC_PROJECT_ID=${FBC_PROJECT_ID}"
    -e "FBC_ALLOWED_EMAILS=${FBC_ALLOWED_EMAILS}"
    -e "FBC_BUCKET=${FBC_BUCKET:-fbc-local}"
    -e GOOGLE_APPLICATION_CREDENTIALS=/app/secrets/firebase-sa.json
    -v "$(winpath "$SA_KEY"):/app/secrets/firebase-sa.json:ro"
  )
else
  RUN_ARGS+=(
    -e FBC_DEV_UNSAFE_AUTH=1
    -e FBC_BUCKET=fbc-dev-local
    -e FBC_PROJECT_ID=fbc-dev-local
  )
fi
if [ -n "$VERSION" ]; then RUN_ARGS+=(-e "FBC_VERSION=${VERSION}"); fi
RUN_ARGS+=(fbc-review:dev)

MSYS_NO_PATHCONV=1 docker "${RUN_ARGS[@]}" >/dev/null

# ── wait for it rather than guessing ──────────────────────────────────────
HEALTH=''
for _ in $(seq 1 180); do
  HEALTH="$(curl -fsS --max-time 2 "http://127.0.0.1:${PORT}/healthz" 2>/dev/null || true)"
  [ -n "$HEALTH" ] && break
  sleep 0.5
done

if [ -z "$HEALTH" ]; then
  docker logs fbc-test || true
  die 'The service did not come up.'
fi

# Read the two fields without needing jq, which Git Bash does not ship.
field() { printf '%s' "$HEALTH" | sed -n "s/.*\"$1\"[[:space:]]*:[[:space:]]*\"\{0,1\}\([^,\"}]*\)\"\{0,1\}.*/\1/p"; }

bold 'Running'
ok   "http://127.0.0.1:${PORT}/"
ok   "Version: $(field version)"
if [ "$(field auth_required)" = 'true' ]; then
  ok   'Sign-in required: true'
else
  warn 'Sign-in required: false — anyone with the URL can use this'
  warn 'Close it with --authenticated, or a Cloudflare Access policy'
  warn 'on the hostname (docs/DEPLOYMENT.md section 0d).'
fi
info ''
info "Upload limit: ${MAX_UPLOAD_MB} MB"
info ''
info 'Publish it at fbc.omniflexfitness.com with:'
info '    bash scripts/tunnel.sh'
info ''
info 'Or, for a throwaway unlisted URL:'
info "    tailscale funnel ${PORT}"
info ''
info 'Stop everything with:'
info '    docker rm -f fbc-test      # plus Ctrl-C in the tunnel window,'
info '                               # or: tailscale funnel reset'
info ''
if [ "$PERSISTENT" -eq 1 ]; then
  info 'Persistent: the container restarts with Docker, so it survives a reboot'
  info 'provided Docker Desktop is set to start with Windows. The tunnel does'
  info 'not: scripts/tunnel.sh runs in the foreground and stops with the shell.'
  info ''
fi
