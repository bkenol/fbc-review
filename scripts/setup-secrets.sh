#!/usr/bin/env bash
#
# Create secrets/local.env, and say what is still missing.
#
#     bash scripts/setup-secrets.sh            copy the template if absent, then check
#     bash scripts/setup-secrets.sh --check    check only, change nothing
#
# Mail and the comment assist are both inert without their keys, and both are
# inert *quietly* — a review still runs, feedback still queues, and the only
# place that says otherwise is /admin. This is the thing you run to find out
# where you actually stand, before wondering why no mail arrived.
#
# It reads the file to see which names have values. It never prints a value.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$ROOT/secrets/local.env"
TEMPLATE="$ROOT/secrets/local.env.example"

bold() { printf '\033[1m%s\033[0m\n' "$1"; }
ok()   { printf '  \033[32mok\033[0m    %s\n' "$1"; }
warn() { printf '  \033[33mmiss\033[0m  %s\n' "$1"; }
info() { printf '        %s\n' "$1"; }

CHECK_ONLY=0
[ "${1:-}" = '--check' ] && CHECK_ONLY=1

# ── the file ──────────────────────────────────────────────────────────────
if [ ! -f "$ENV_FILE" ]; then
  if [ "$CHECK_ONLY" -eq 1 ]; then
    bold 'No secrets/local.env'
    info 'Run scripts/setup-secrets.sh (without --check) to create it.'
    exit 1
  fi
  [ -f "$TEMPLATE" ] || { echo "No template at $TEMPLATE" >&2; exit 1; }
  # 600 from the moment it exists rather than after it is filled in: the
  # window in between is exactly when somebody pastes a key into it.
  ( umask 077; cp "$TEMPLATE" "$ENV_FILE" )
  bold 'Created secrets/local.env from the template.'
  info "Open it and fill in the blanks: $ENV_FILE"
  info ''
fi

chmod 600 "$ENV_FILE" 2>/dev/null || true

# ── what is set ───────────────────────────────────────────────────────────
# Read with grep rather than sourced: this file is not a shell script, a value
# is literal, and sourcing one would execute whatever a paste happened to
# contain.
value_of() {
  grep -E "^[[:space:]]*(export[[:space:]]+)?$1=" "$ENV_FILE" 2>/dev/null |
    tail -n 1 | sed -E "s/^[[:space:]]*(export[[:space:]]+)?$1=//"
}

has() { [ -n "$(value_of "$1")" ]; }

QUOTED="$(grep -nE '^[[:space:]]*[A-Z_]+=("|'"'"').*("|'"'"')[[:space:]]*$' "$ENV_FILE" || true)"

bold 'secrets/local.env'
info "$ENV_FILE"
echo

bold 'Mail'
MAIL_OK=1
for name in FBC_SMTP_HOST FBC_SMTP_USER FBC_SMTP_PASS FBC_MAIL_FROM; do
  if has "$name"; then ok "$name"; else warn "$name"; MAIL_OK=0; fi
done
if has FBC_OWNER_EMAILS; then ok 'FBC_OWNER_EMAILS'; else
  warn 'FBC_OWNER_EMAILS'
  info 'Empty means nobody gets an escalation or the digest, and nobody can'
  info 'approve a calibration change. Mail can be configured without it, and'
  info 'then has nowhere to go.'
fi
if [ "$MAIL_OK" -eq 1 ]; then
  info 'Mail will be live. Send yourself the digest from /admin to prove it.'
else
  info 'Mail stays inert: the review still runs and feedback still queues.'
  info 'For Google Workspace you need an App Password, not the account'
  info 'password: https://myaccount.google.com/apppasswords'
fi
echo

bold 'Comment assist'
if has ANTHROPIC_API_KEY; then
  ok 'ANTHROPIC_API_KEY'
  info 'Free-text feedback comments will be summarised before they reach the'
  info 'queue. The review path is unaffected and still makes zero model calls.'
  if has ANTHROPIC_WORKSPACE_ID; then
    ok 'ANTHROPIC_WORKSPACE_ID'
  else
    info ''
    info 'ANTHROPIC_WORKSPACE_ID is not set. That is correct for a workspace'
    info 'key and fatal for an identity-linked one, which the API refuses with'
    info '400 until the request names a workspace. Check the Type column at'
    info 'https://console.anthropic.com/settings/keys — if it does not say'
    info 'Workspace, set the wrkspc_ id here too.'
  fi
else
  warn 'ANTHROPIC_API_KEY'
  info 'Comments route to a person unread, which is what they did before the'
  info 'assist existed. Keys: https://console.anthropic.com/settings/keys'
fi
echo

bold 'Issues from escalated feedback'
if has FBC_GITHUB_REPO && has FBC_GITHUB_TOKEN; then
  ok 'FBC_GITHUB_REPO and FBC_GITHUB_TOKEN'
else
  warn 'FBC_GITHUB_REPO / FBC_GITHUB_TOKEN — optional'
fi

if [ -n "$QUOTED" ]; then
  echo
  bold 'Quoted values — these will not work'
  info 'Docker takes the quotes literally, so the value arrives with them'
  info 'attached. Remove the quotes; a value with spaces needs none.'
  printf '%s\n' "$QUOTED" | sed 's/=.*/=…/' | sed 's/^/        /'
fi

echo
bold 'Next'
info 'Restart the container so it reads the file:'
info "    bash \"$ROOT/scripts/share.sh\""
info 'Then check what the server itself thinks at /admin.'
