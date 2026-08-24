#!/usr/bin/env bash
#
# Publish the locally-running FBC Reviewer at fbc.omniflexfitness.com through a
# Cloudflare Tunnel.
#
#   bash scripts/tunnel.sh
#
# Safe to re-run: every step checks for what it creates and skips if it is
# already there, so a failure halfway through is fixed by running it again.
#
# This assumes the app is already listening locally — start it first with
# scripts/share.ps1, which runs the client and the API as one container on one
# port. This script does not start or stop the app.
#
# Runs in Git Bash on Windows, or any POSIX shell.
#
# ── what this actually does ───────────────────────────────────────────────
# A named tunnel is an outbound-only connection from this machine to
# Cloudflare's edge. Nothing is opened on the router, no port is forwarded and
# the machine's IP address is never published. `cloudflared` dials out, holds
# the connection open, and Cloudflare hands it requests for the hostname.
#
# The DNS record it creates is a CNAME from the hostname to
# <TUNNEL-UUID>.cfargotunnel.com, and it MUST stay proxied (orange cloud).
# cfargotunnel.com does not resolve for anyone but Cloudflare's own edge, so a
# DNS-only record there is a hostname that resolves to nothing.

set -euo pipefail

# ── settings ──────────────────────────────────────────────────────────────
TUNNEL="${FBC_TUNNEL:-fbc-review}"
DOMAIN="${FBC_DOMAIN:-fbc.omniflexfitness.com}"
PORT="${FBC_PORT:-8060}"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

bold()  { printf '\n\033[1m%s\033[0m\n' "$*"; }
info()  { printf '  %s\n' "$*"; }
ok()    { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn()  { printf '  \033[33m!\033[0m %s\n' "$*"; }
die()   { printf '\n\033[31m✗ %s\033[0m\n\n' "$*" >&2; exit 1; }

# Config lives beside the credentials, under its own name rather than as
# config.yml. `cloudflared` reads config.yml by default, and clobbering that
# would silently break any other tunnel already set up on this machine.
CF_DIR="${HOME}/.cloudflared"
CONFIG="${CF_DIR}/${TUNNEL}.yml"

# A tunnel UUID, by shape. Matching on the shape rather than on a column
# position or a JSON field name is deliberate: `cloudflared`'s JSON field names
# have changed across versions, and its zero values are Go's rather than JSON's
# — a live tunnel's deleted_at marshals to "0001-01-01T00:00:00Z", not null.
# Reading the listing as JSON and filtering on that field is what made the first
# version of this script mistake a freshly created tunnel for a deleted one and
# report no UUID at all.
UUID_RE='[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}'

# cloudflared is a native Windows binary, so any path it reads must be a Windows
# path. Under Git Bash, MSYS rewrites POSIX-looking paths in *arguments*
# automatically — which is why `--config /c/Users/...` works untouched — but it
# never sees inside a file. An MSYS path written into the YAML therefore reaches
# cloudflared verbatim, and it reports the credentials file as missing while
# bash's own `[ -f ]` on the same string succeeds. Convert before writing.
win_path() {
  if command -v cygpath >/dev/null 2>&1; then
    cygpath -w "$1"
  else
    printf '%s' "$1"
  fi
}

# ── 0. preflight ──────────────────────────────────────────────────────────
bold "0. Preflight"

if ! command -v cloudflared >/dev/null 2>&1; then
  die "cloudflared is not on PATH.

     Windows:  winget install --id Cloudflare.cloudflared
     macOS:    brew install cloudflared
     Linux:    see https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/

     Then re-run this script."
fi
ok "cloudflared $(cloudflared --version 2>&1 | head -1 | awk '{print $3}')"

# The tunnel is useless if there is nothing behind it, and finding that out
# after DNS is live is the slow way round.
if ! curl -fsS --max-time 5 "http://127.0.0.1:${PORT}/healthz" >/dev/null 2>&1; then
  die "Nothing is answering on http://127.0.0.1:${PORT}/healthz

     Start the app first:
       powershell -ExecutionPolicy Bypass -File scripts\\share.ps1

     Then re-run this script. Override the port with FBC_PORT if you changed it."
fi
ok "the app is answering on 127.0.0.1:${PORT}"

if ! curl -fsS "http://127.0.0.1:${PORT}/healthz" 2>/dev/null \
     | grep -qE '"auth_required"[[:space:]]*:[[:space:]]*true'; then
  warn "This service has authentication OFF (share.ps1 sets FBC_DEV_UNSAFE_AUTH=1)."
  warn "Once DNS is live, anyone who finds ${DOMAIN} can upload a permit set."
  warn "What limits it: the upload size and page caps, and the rate limits,"
  warn "which with one shared identity become a global 3 concurrent and 10/hour."
  warn "That was a deliberate choice; see 'Exposure' in docs/DEPLOYMENT.md."
fi

# ── 1. authenticate ───────────────────────────────────────────────────────
bold "1. Cloudflare account"

if [ ! -f "${CF_DIR}/cert.pem" ]; then
  info "No certificate yet. A browser will open — pick the omniflexfitness.com zone."
  cloudflared tunnel login
  [ -f "${CF_DIR}/cert.pem" ] || die "Login did not produce ${CF_DIR}/cert.pem."
fi
ok "authorised (${CF_DIR}/cert.pem)"

# ── 2. the tunnel ─────────────────────────────────────────────────────────
bold "2. Named tunnel '${TUNNEL}'"

# `tunnel list` without -d lists only live tunnels, so there is no deleted
# state to filter here — the row carrying the name carries the id we want.
tunnel_uuid() {
  cloudflared tunnel list 2>/dev/null \
    | grep -E "(^|[[:space:]])${TUNNEL}([[:space:]]|\$)" \
    | grep -oE "$UUID_RE" \
    | head -1
}

UUID="$(tunnel_uuid || true)"
if [ -z "$UUID" ]; then
  info "Creating it..."
  set +e
  CREATE_OUT="$(cloudflared tunnel create "$TUNNEL" 2>&1)"
  CREATE_RC=$?
  set -e

  # create prints the new id; prefer that over listing again.
  UUID="$(printf '%s' "$CREATE_OUT" | grep -oE "$UUID_RE" | tail -1)"
  [ -n "$UUID" ] || UUID="$(tunnel_uuid || true)"

  if [ -z "$UUID" ]; then
    printf '\n%s\n\n' "$CREATE_OUT"
    if [ $CREATE_RC -ne 0 ]; then
      die "cloudflared could not create the tunnel '${TUNNEL}'."
    fi
    die "Created the tunnel but could not read its UUID back.

     Check what cloudflared reports:  cloudflared tunnel list
     If '${TUNNEL}' is listed there, this script failed to parse it — report
     the listing rather than creating a second tunnel."
  fi
  ok "created — $UUID"
else
  ok "already exists — $UUID"
fi

CREDENTIALS="${CF_DIR}/${UUID}.json"
[ -f "$CREDENTIALS" ] || die "Credentials file missing: ${CREDENTIALS}

     The tunnel exists in the account but this machine holds no credentials for
     it. Either run this from the machine that created it, or delete and
     recreate it:  cloudflared tunnel delete ${TUNNEL}"
ok "credentials present"

# ── 3. configuration ──────────────────────────────────────────────────────
bold "3. Ingress"

# Written every run, so a changed port or hostname takes effect without anyone
# having to remember to edit it. Single-quoted so a Windows path's backslashes
# stay literal — YAML only processes escapes inside double quotes.
CREDENTIALS_FOR_CONFIG="$(win_path "$CREDENTIALS")"

cat > "$CONFIG" <<YAML
# Generated by scripts/tunnel.sh — edits are overwritten on the next run.
tunnel: ${UUID}
credentials-file: '${CREDENTIALS_FOR_CONFIG}'

originRequest:
  # The origin is this machine. The review itself never holds a request open —
  # POST /api/review returns 202 immediately and the client polls — so the only
  # slow request is the upload of the set itself.
  connectTimeout: 30s

ingress:
  - hostname: ${DOMAIN}
    service: http://127.0.0.1:${PORT}
  # Anything that reaches the tunnel without matching above is not ours.
  - service: http_status:404
YAML
ok "wrote ${CONFIG}"

cloudflared tunnel --config "$CONFIG" ingress validate >/dev/null \
  || die "cloudflared rejected the ingress rules in ${CONFIG}."
ok "ingress rules valid"

# ── 4. DNS ────────────────────────────────────────────────────────────────
bold "4. DNS for ${DOMAIN}"

# There is deliberately no --overwrite-dns here. Deleting a record in a live
# zone should be a considered action taken in the dashboard, not a flag on a
# script that also gets re-run routinely.
set +e
ROUTE_OUT="$(cloudflared tunnel route dns "$TUNNEL" "$DOMAIN" 2>&1)"
ROUTE_RC=$?
set -e

if [ $ROUTE_RC -eq 0 ]; then
  ok "CNAME ${DOMAIN} → ${UUID}.cfargotunnel.com (proxied)"
elif printf '%s' "$ROUTE_OUT" | grep -qi "already exists"; then
  # Two very different causes, and cloudflared reports them identically.
  # Carrying on is right for the common one — this script having already run —
  # and the warning is what covers the other.
  warn "Cloudflare says a record already holds ${DOMAIN}:"
  printf '\n%s\n\n' "$ROUTE_OUT"
  warn "If a previous run of this script created it, that is expected and the"
  warn "hostname already points at tunnel ${UUID} — carrying on."
  warn ""
  warn "If it is some other record, this tunnel will NOT serve the hostname."
  warn "Check it in the dashboard: DNS > Records > ${DOMAIN}. It should be a"
  warn "CNAME to ${UUID}.cfargotunnel.com, proxied. Delete or"
  warn "correct it there, then re-run this script."
else
  printf '\n%s\n' "$ROUTE_OUT"
  die "cloudflared could not create the DNS record."
fi

# ── 5. run ────────────────────────────────────────────────────────────────
bold "5. Running"

info "https://${DOMAIN}  →  127.0.0.1:${PORT}"
info ""
info "TLS is Cloudflare's universal certificate; there is nothing to install."
info "The hostname stays up only while this process runs and the app is up."
info "To keep it running across reboots, install cloudflared as a service —"
info "see 'Persistence' in docs/DEPLOYMENT.md."
info ""
info "Stop serving with Ctrl-C — the hostname then returns Cloudflare's"
info "error 1033 until this runs again. To take it down for good, delete the"
info "CNAME for ${DOMAIN} in the Cloudflare dashboard, then:"
info "    cloudflared tunnel delete ${TUNNEL}"
info ""

exec cloudflared tunnel --config "$CONFIG" run "$TUNNEL"
