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
| Container | Built and verified locally, incl. OCR inside the image. The LibreDWG stage and font added on 2026-10-03 for drawing uploads are written, not yet built (§3) |
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

Then put the console on the Desktop and use that from here on:

```powershell
& ".\Rebuild Console.cmd" app-shortcut
```

Pull, Rebuild and Publish become buttons, their output streams into the page,
and a line says whether the running container is on the commit in your working
tree. `bash scripts/rebuild-console.sh` is the same thing from Git Bash.

That shortcut opens the console as **its own Chrome window** — no tab strip, no
address bar, its own taskbar button and Chrome's icon — because the console is a
control panel and a control panel that lives in a tab gets lost among thirty
others. It targets `pythonw.exe` directly rather than the `.cmd`, so no console
window flashes on launch. Edge and Brave work too; with none of them installed
the page opens in the default browser instead. `& ".\Rebuild Console.cmd" app`
does the same thing once, without writing a shortcut, and
`& ".\Rebuild Console.cmd" shortcut` still writes the older default-browser one.

To confirm a machine is running what you think it is, read the version in the
masthead and the footer of the page itself — locally it carries the commit and
says `.dirty` when the tree has uncommitted changes. §5a explains the scheme.

### Docker Desktop has to be actually running

Not merely installed. Docker Desktop leaves `docker.exe` on PATH whether or not
its engine is up, so every check short of asking the daemon passes with the
engine stopped — and a Rebuild then builds the whole client before dying on:

```
ERROR: failed to connect to the docker API at npipe:////./pipe/dockerDesktopLinuxEngine
```

That is a stopped program, not a broken checkout. Both run scripts now ask
`docker info` before they build anything, so it fails in a second with the fix
in it, and the console's guidance panel names it rather than going on advising
the Rebuild that just failed. **Doctor** reports the daemon separately from the
CLI.

To stop it recurring, turn on *Settings → General → "Start Docker Desktop when
you sign in"*. A container started with `-Persistent` restarts with Docker, so
that setting is what makes the local deployment survive a reboot.

### One hostname, one machine

Every machine can run the app locally on `127.0.0.1:8060`; only one can serve
`fbc.omniflexfitness.com`. The named tunnel belongs to the Cloudflare account,
but the credentials file it needs sits on whichever machine created it, so
`tunnel.sh` on a second machine stops with

> The tunnel exists in the account but this machine holds no credentials for it.

which is the intended behaviour, not a fault — it refuses rather than quietly
competing with the machine already serving the hostname. Two connectors on one
tunnel would have Cloudflare hand requests to whichever answered, so the same
URL would sometimes reach the laptop and sometimes the desktop.

For a second machine that needs a public URL, give it one of its own with
`tailscale funnel 8060`; each machine gets its own hostname.

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
API serving the client so there is a single origin and no CORS. Tailscale Funnel
puts that on a public HTTPS URL with no DNS record to create, no certificate to
install and nothing opened on the router — which makes it the right answer for a
**second** machine, since only one machine can serve `fbc.omniflexfitness.com`
(§0c).

**First run on a machine.** Four things have to be true before Funnel works, and
three of them are tailnet-wide, so a machine joining an already-configured
tailnet only has to do the first:

| Requirement | Where |
| --- | --- |
| Tailscale ≥ 1.38.3, signed in (`tailscale up`) | this machine |
| **MagicDNS** enabled | admin console → DNS |
| **HTTPS certificates** enabled | admin console → DNS → HTTPS Certificates |
| `funnel` node attribute in the tailnet policy | granted automatically the first time you enable Funnel from the CLI |

On Windows: `winget install --id Tailscale.Tailscale`, then open a **new**
terminal — winget updates PATH for new processes only.

**Running it.** Start the app first, then:

```bash
tailscale funnel 8060          # foreground, Ctrl-C to stop
tailscale funnel --bg 8060     # background; survives reboots and `tailscale up`
```

The number is the **local** port to proxy to, not the public one. Funnel itself
can only listen on 443, 8443 and 10000, and defaults to 443 — so the URL has no
port in it. The first run opens a browser to approve enabling Funnel for the
tailnet; after that it prints the hostname, which is
`<machine-name>.<tailnet-name>.ts.net`. Each machine gets its own, which is
exactly why this composes where the named tunnel does not.

```bash
tailscale funnel status        # what is being served
tailscale funnel off           # stop a --bg funnel
tailscale funnel reset         # clear the configuration
docker rm -f fbc-test          # stop the app itself
```

**`listener already exists for port 443`.** The node already has a Serve or
Funnel configuration holding that port. Serve (tailnet-only) and Funnel
(public) cannot both hold one port, so an existing Serve on 443 blocks Funnel
there — and a machine that has been used for anything else over Tailscale may
well have one. Look before clearing, because whatever is there is presumably
wanted by something:

```bash
tailscale serve status         # everything on this node, Serve and Funnel
tailscale serve reset          # clear all of it
```

Or leave it alone and take one of the other two ports Funnel allows:

```bash
tailscale funnel --https=8443 8060
```

The hostname then carries the port — `https://<machine>.<tailnet>.ts.net:8443`
— which is fine for a test URL and avoids disturbing whatever already owns 443.

For the real hostname rather than a throwaway one, see **0c** below. Both modes
run the same container and both have authentication off — see **Exposure**. A
`.ts.net` hostname is unlisted rather than secret, so the same caveat applies:
close it with `share.ps1 -Authenticated` (§0d) if it will be up for long.

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
- with AI sheet reading off (the default), the engine makes no model calls, so
  an abusive upload costs CPU, not tokens. **With `FBC_AI_READING=on`, every
  upload is up to `FBC_AI_MAX_SHEETS` paid API requests** — leave it off on an
  unauthenticated tunnel, where anyone holding the URL would be spending the key

`tunnel.sh` prints a warning naming this every run, read from `/healthz`'s
`auth_required`, so it cannot be forgotten quietly.

Both remedies named here are now available, and section 0d is how to use them.
`FBC_DEV_UNSAFE_AUTH` has been split from the backend selection, so
`bash scripts/share.sh --authenticated` — or `share.ps1 -Authenticated`, which
takes the same environment and passes the same container settings — runs the
same filesystem stores with real Firebase sign-in and the server-side
allowlist. The Rebuild Console exposes it as the **Require sign-in** checkbox.
A Cloudflare Access policy on the hostname remains the zero-code option and
composes with it.

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

## 0d. Running it with no Cloud Billing account at all

Cloud Storage and Cloud Run are the only two pieces of this stack that require
an open billing account. Firebase **Authentication**, **Hosting** and
**Firestore** are all free on the Spark plan with no card attached — so the
absence of billing costs you the bucket and the managed container, and nothing
else. Since 3 Feb 2026 Cloud Storage for Firebase follows the standard Cloud
Storage rules and needs a linked billing account even inside the Always Free
tier, so there is no way around the bucket other than not using one.

This section is what to run instead. Two layers; use either or both.

### Layer 1 — Cloudflare Access, no code change

The tunnel from section 0c publishes the app at a hostname. Access puts an
identity check in front of that hostname at Cloudflare's edge, before anything
reaches the machine. Free to 50 users.

1. Cloudflare dashboard → **Zero Trust** → Access → Applications → **Add an
   application** → **Self-hosted**.
2. Application domain: `fbc.omniflexfitness.com` (the tunnel hostname; leave
   the path empty so it covers everything).
3. Add a policy: Action **Allow**, Include → **Emails** → the addresses that
   may in. Or **Emails ending in** `@omniflexfitness.com` for the whole domain.
4. Identity provider: the built-in **One-time PIN** needs no setup and emails a
   code. Google is a few more clicks and is nicer to use.

That is the whole change, and the app never learns it happened. What it does
**not** do is give the app per-user identity: with `FBC_DEV_UNSAFE_AUTH=1`
every request still arrives as the same `dev-local` user, so the rate limits
stay global and **everyone signed in through Access shares one review
history**. For a single operator that is fine. For clients it is not, which is
what layer 2 is for.

### Layer 2 — real sign-in on the filesystem backend

`FBC_BACKEND` and `FBC_DEV_UNSAFE_AUTH` are separate settings. The first
chooses Firestore + Cloud Storage or the filesystem stand-ins in
`webapp/devbackend.py`; the second turns authentication off. Choosing the
filesystem no longer means giving up sign-in.

**One-time setup.** One script, and none of it needs billing.

```bash
cd /path/to/fbc-review          # the script resolves its own paths, but
bash scripts/setup-auth.sh      # `firebase` writes into the working directory
```

It adds Firebase to the project, creates a web app, writes the real config into
`web/src/app/core/firebase-config.ts`, and creates the token-verifying service
account and its key under `secrets/`. Every step checks for what it creates and
skips if it is already there, so a half-finished run is fixed by running it
again.

`FBC_PROJECT_ID` defaults to **`fbc-reviewer`** — the project that already
exists, and the same default `provision.sh` uses. Override it only if you are
deliberately standing up a second project.

It stops and tells you about the two steps that have no CLI: enabling the Google
sign-in provider, and adding your tunnel hostname under **Authentication →
Settings → Authorised domains**. `localhost` is authorised out of the box, so
local testing passes before you do the second one and Google sign-in then fails
on the tunnel hostname and nowhere else. Do it while you are in the console.

<details>
<summary>What the script is doing, if you would rather run it by hand</summary>

Three things went wrong the first time these were run loose, and all three are
worth knowing about because they fail in unhelpful ways:

1. **Run it from the repository.** `python scripts/write_firebase_config.py`
   from a home directory is `No such file or directory`. The script resolves
   every path against the repository root, so it works from anywhere.
2. **`FBC_PROJECT_ID` must be set before it is interpolated.** Unset, the
   service account address becomes `fbc-auth@.iam.gserviceaccount.com` and
   `gcloud` answers `INVALID_ARGUMENT: Unknown error`, which names nothing. The
   script defaults it instead of requiring it.
3. **The service account may already exist**, in which case `create` fails with
   a conflict and stops a `&&` chain dead. The script checks with `describe`
   first.

```bash
PROJECT_ID=fbc-reviewer
APP_ID="$(firebase apps:list WEB --project "$PROJECT_ID" | grep -oE '1:[0-9]+:web:[a-z0-9]+' | head -1)"
firebase apps:sdkconfig WEB "$APP_ID" --project "$PROJECT_ID" --json > sdk.json
python scripts/write_firebase_config.py sdk.json && rm sdk.json

gcloud iam service-accounts describe "fbc-auth@${PROJECT_ID}.iam.gserviceaccount.com" \
  || gcloud iam service-accounts create fbc-auth --display-name="FBC token verifier"
mkdir -p secrets
gcloud iam service-accounts keys create secrets/firebase-sa.json \
  --iam-account="fbc-auth@${PROJECT_ID}.iam.gserviceaccount.com"
```

</details>

**If key creation is refused.** `constraints/iam.disableServiceAccountKeyCreation`
blocks it outright at the organisation level, and the error does not say so:

```bash
gcloud resource-manager org-policies describe \
  constraints/iam.disableServiceAccountKeyCreation --project=fbc-reviewer --effective
```

Enforced and unliftable, the options are to run the service somewhere with a
Google identity of its own, or to accept `check_revoked=False` — a revoked
session then keeps working until its token expires, which is at most an hour.

**Why a key here, when `CLAUDE.md` says never to download one.** That rule is
about CI, where Workload Identity Federation is the right answer and a key is
laziness. This is different: `webapp/auth.py` verifies tokens with
`check_revoked=True`, and that check calls the Firebase Auth backend to ask
whether the session has been revoked or the user disabled. Signature
verification alone needs only Google's public certificates, but the revocation
check needs credentials — and a container on your own hardware has no ambient
identity and no OIDC issuer to federate from. The alternatives are worse:
dropping to `check_revoked=False` means a revoked session keeps working until
the token expires. Keep the key out of the repository (`secrets/` is ignored),
mount it read-only, and rotate it if the machine is ever shared.

**Running it.**

```bash
export FBC_PROJECT_ID=fbc-reviewer
export FBC_ALLOWED_EMAILS=you@example.com,someone@example.com
bash scripts/share.sh --authenticated --persistent
bash scripts/tunnel.sh          # in a second shell
```

Or, from PowerShell:

```powershell
$env:FBC_PROJECT_ID = 'fbc-reviewer'
$env:FBC_ALLOWED_EMAILS = 'you@example.com,someone@example.com'
powershell -ExecutionPolicy Bypass -File scripts\share.ps1 -Authenticated -Persistent
powershell -ExecutionPolicy Bypass -File scripts\tunnel.ps1   # in a second shell
```

Or tick **Require sign-in** in the Rebuild Console, having set those two
variables in the environment it was launched from.

`--authenticated` / `-Authenticated` sets `FBC_BACKEND=local` and leaves
`FBC_DEV_UNSAFE_AUTH` unset, mounts the key, and generates a stable artefact
signing key at `.devdata/artefact.secret`. `/healthz` will report
`auth_required: true`, and the script says so rather than warning.

The two scripts pass an identical set of container settings and share the one
`.devdata/artefact.secret`, so a machine can move between Git Bash and
PowerShell without invalidating outstanding download links.

### How artefacts are served without a bucket

Cloud Storage hands the browser a V4 signed URL and the download never touches
the app. The filesystem backend needs the same shape for a reason worth stating,
because it is the thing that makes this mode possible at all: **a download is a
navigation, and a navigation carries no `Authorization` header.** The Angular
interceptor attaches the Firebase token to XHR, which covers `findings.json`;
it cannot cover the click that downloads a 17 MB marked-up set.

So `webapp/storage_urls.py` mints the local equivalent — a path, an expiry and
an HMAC over both, served by `GET /api/artefacts/{blob}`. The signature is the
authorisation, exactly as it is for GCS, and it is only minted after ownership
of the job has been checked. This replaced the old `/_dev/blob` route, which
served any blob under the root to anyone who asked and existed only when
authentication was off.

The signing key comes from `FBC_ARTEFACT_SECRET`. Unset, the service generates
one per process: safe, but every outstanding link stops working when the
container restarts, and links minted by one uvicorn worker are not valid at
another. `share.sh` writes a stable one for you.

### What this mode costs you

Stated plainly, because it is not free of trade-offs:

| | Cloud Run + GCS | This |
| --- | --- | --- |
| Artefact download | direct from GCS | streamed through the app |
| Availability | Google's | your machine's |
| Scale to zero | yes | the container runs continuously |
| Retention | bucket lifecycle rule | `.devdata` until you delete it |
| Jobs survive a restart | yes | yes, they are files |
| Cost | pennies a month, needs billing | nothing |

The download path is the one real regression: a 17 MB PDF now occupies a worker
thread for the length of the transfer. On Cloud Run that was worth avoiding
because it pins a billable instance. On a machine you already own it is a
streaming file read, and `FBC_WORKERS` is the knob if it ever matters.

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

### The CAD adapter in the image (2026-10-03)

The Dockerfile gained a first stage, `libredwg`, that downloads the GNU
LibreDWG 0.14 release, checks its SHA-256, and builds the library and its
programs with the flags the reference drawing was converted with. Only
`dwg2dxf` and `libredwg.so.0`, stripped, are copied into the runtime image, with
the licence and a note of the source (§9a has why that matters). The runtime
stage also installs `fonts-dejavu-core` — ezdxf draws a plotted sheet's text
with system fonts, and the slim image has none — and builds ezdxf's font cache
as the `fbc` user. Two checks run during the build, so a broken image fails
there rather than on the first drawing: `dwg2dxf --version`, and that ezdxf
finds `DejaVuSans.ttf`.

**Not yet built with these changes.** The session that wrote them had no Docker
daemon. The `libredwg` stage's commands were run outside Docker on Ubuntu 24.04
against the same tarball — checksum verified, the same configure flags, `make -C
src`, `make -C programs`, install, strip — in 356 s on 4 cores. The stripped
`dwg2dxf` plus `libredwg.so.0` come to 20 MB (the library is 75 MB before
stripping), `dwg2dxf --version` prints `dwg2dxf 0.14` and exits 0, and it
converted the reference DWG in 3.7 s to a DXF **byte-identical** to the one
every number in §9a was measured on. The image itself, its size and the
in-Docker build time are unmeasured. On the first build:

```bash
docker build -t fbc-review:dev .
docker run --rm fbc-review:dev dwg2dxf --version
# dwg2dxf 0.14
docker run --rm fbc-review:dev python -c "import ezdxf; from ezdxf.addons.drawing import pymupdf; print(ezdxf.__version__)"
# 1.4.4
docker run --rm fbc-review:dev python -c "from fbcreview.cad import convert; print(convert.version())"
# dwg2dxf 0.14
```

and record the image size here beside the 893 MB above. Expect it to grow by
the 20 MB of LibreDWG, the font package, and the ezdxf, fontTools and Pillow
wheels; the compiler stays in the discarded stage.

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
  --memory=4Gi --cpu=2 \
  --concurrency=4 \
  --timeout=900 \
  --min-instances=0 --max-instances=5 \
  --service-account="$SA" \
  --set-env-vars="FBC_BUCKET=${BUCKET},FBC_PROJECT_ID=${PROJECT_ID},FBC_ALLOWED_EMAILS=bertin.kenol@omniflexfitness.com,FBC_SIGNER_SA=${SA},FBC_CAD_CONCURRENCY=1" \
  --no-allow-unauthenticated
```

Sizing, so it is not changed blindly:

- **4 GiB** (2 GiB until 2026-10-03) — PyMuPDF holds the whole document plus
  the rendered output in memory; a 300-page raster-heavy set is the worst case
  for a PDF. A drawing adds a subprocess that peaked at **1.13 GB** reading the
  23 MB reference DWG, and the 170 MB DXF it converts to sits on Cloud Run's
  local disk, which is memory: the docs list *"Writing files to the file
  system"* among what the limit has to cover
  (<https://docs.cloud.google.com/run/docs/configuring/services/memory-limits>).
  `FBC_CAD_CONCURRENCY=1` holds that to one drawing per instance, beside up to
  one PDF review on the other worker. 4 GiB needs at least 1 vCPU; 2 vCPU allows
  up to 8 GiB, so `--cpu=2` stands.
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

1. **Version** — reads `VERSION`, stamps the build number on, fails the run if
   `VERSION` is malformed (§5a)
2. `pytest tests/ -v` (with Tesseract installed, so the OCR test runs rather
   than skips)
3. `npm run build` and `npm run test:ci` in `web/`
4. **Client drift check** — regenerates `openapi.json` and `web/src/app/api/`
   and fails if either differs from what is committed. This is what keeps the
   typed client honest; without it the whole point of generating it is lost.
5. On `main` only: build and push the image tagged with both the commit SHA and
   the version, deploy to Cloud Run with `FBC_VERSION` set, then smoke-test
   `/healthz` on the new revision and check that the version it reports is the
   one just deployed
6. Deploy Hosting when `web/**`, `firebase.json` or `firestore.*` changed

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

## 5a. Versioning

### The scheme

`VERSION` at the repository root is the source of truth. It holds the release
triple and the channel, and nothing else:

```
1.0.0-alpha
```

The build number is **not** committed. It is stamped on per build, because a
number that has to be edited by hand is a number that stops being edited.

| Channel | `VERSION` says | A build is called |
| --- | --- | --- |
| **ALPHA** — where the app is now | `1.0.0-alpha` | `1.0.0-alpha.412+3f1c9ab` |
| **BETA** | `1.0.0-beta` | `1.0.0-beta.430+9ab21cd` |
| **PROD** | `1.0.0` | `1.0.0+build.450.771ee0f` |

That is SemVer 2.0.0 precedence, unmodified, and it orders the way the release
train runs:

```
1.0.0-alpha.9 < 1.0.0-alpha.10 < 1.0.0-beta.1 < 1.0.0
```

Two properties of SemVer are doing the work, and neither is decoration:

- A **numeric** pre-release identifier compares as a number. Build 10 outranks
  build 9 — a plain string comparison gets that backwards.
- Build metadata after `+` is **ignored** in precedence. The commit says *which*
  build it was, never *whether* it is newer.

A production release has no pre-release part to extend, and `1.0.0.450` would
not be SemVer at all, so on that channel the build number moves into the
metadata instead.

### Where the number comes from

The build number is `github.run_number` — the run count of
`.github/workflows/deploy.yml`. It increments once per run, does not reset when
a run is retried (that is `run_attempt`), and needs neither a commit back to the
repository nor `contents: write` on the workflow token. No tag is pushed and no
file is rewritten by CI; the only thing anyone edits is `VERSION`.

Outside CI there is no build number, so the slot carries the working tree
instead:

```
1.0.0-alpha+local.9f3c1ab          a clean checkout
1.0.0-alpha+local.9f3c1ab.dirty    uncommitted changes
```

That is the line to read after a `git pull` to confirm the deployment in front
of you is the code you just pulled. `scripts/share.ps1` resolves it on the host
and passes it into the container as `FBC_VERSION`, because the image carries
`VERSION` but no `.git`.

### Promoting a channel

One edit, one commit:

```bash
echo 1.0.0-beta > VERSION      # alpha -> beta
echo 1.0.0      > VERSION      # beta  -> production
echo 1.1.0-alpha > VERSION     # start the next release on alpha
pytest tests/test_version.py -v
```

`tests/test_version.py` rejects anything that is not `MAJOR.MINOR.PATCH` with an
optional `-alpha` or `-beta`, and so does the workflow's version job — on pull
requests as well, so a typo fails on the branch rather than on `main`.

### Where it shows up

| Surface | Shows | Why |
| --- | --- | --- |
| `GET /healthz`, `GET /api/healthz` | the full stamped version | what the running service is |
| The masthead and the footer | the same string | read from `/api/healthz` on load |
| OpenAPI `info.version` | `1.0.0` — release only | the API *contract*, which does not move when a build number does |
| Artifact Registry | a `1.0.0-alpha.412_3f1c9ab` tag beside the SHA tag | a Docker tag may not contain `+` |
| The Actions run summary | the version, channel, build and commit | so a run is identifiable without opening it |

`info.version` is deliberately the release triple alone. CI regenerates the
published schema and compares it byte-for-byte against what is committed, so
anything that varies by build or by machine cannot appear in it — a build number
in there would make the drift check fail on every push for a reason that has
nothing to do with the contract.

The client shows the version the **API** reports rather than one baked into the
bundle. Hosting serves the bundle from a CDN and rewrites `/api/**` to Cloud
Run, so one number from the service that is actually answering beats two numbers
that can disagree.

### The one rule the code and the workflow share

`webapp/version.py` states the scheme in Python; the workflow's version job
restates it in shell, because a workflow cannot import Python before it has
checked out and installed anything. `tests/test_version.py` runs that shell
against the Python and fails if they have drifted — so the duplication cannot
rot silently.

---

## 6. Environment variables the service reads

All are read once at startup by `webapp/config.py`, except the two version
variables, which `webapp/version.py` reads — build identity is not runtime
configuration, and `config.py` deliberately shells out to nothing.

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
| `FBC_STALE_RUNNING_MINUTES` | 45 | A `running` job older than this is failed at startup. 45, not 15, since drawings: one CAD job can spend two `FBC_CAD_TIMEOUT_S` steps plus a wait for the CAD slot, and marking a live one interrupted is worse than noticing a dead one late (`webapp/config.py`). Keep it above 4 × `FBC_CAD_TIMEOUT_S` in minutes. |
| `FBC_WORKERS` | 2 | Worker threads per instance |
| `FBC_DWG2DXF` | `/usr/local/bin/dwg2dxf` in the image; else `dwg2dxf` on `PATH` | LibreDWG's converter. Set by the Dockerfile. Unset and not on `PATH` (a local run without LibreDWG), a `.dwg` upload is refused with `cad_unavailable` and a `.dxf` or a zip of DXFs still works. |
| `FBC_DWG_TIMEOUT_S` | 300 | Seconds one DWG may take to convert before the converter is stopped. The 23 MB reference converts in 4–7 s. |
| `FBC_CAD_TIMEOUT_S` | 600 | Seconds the drawing ingest (convert, read, plot every layout), and separately the DXF markup, may run in their subprocess before it is killed. Measured: ingest 186 s, markup 85 s on the reference drawing. Minimum 30. |
| `FBC_CAD_CONCURRENCY` | 1 | Drawing subprocesses at once per instance. Each peaks at ~1.1 GB; a second drawing waits for the slot rather than doubling that. Both deploy paths set 1 explicitly, sized against `--memory=4Gi`. Read once at startup. |
| `FBC_CAD_MAX_DXF_MB` | 300 | DXF one drawing review may hold open, all of a zip's drawings together (a DWG counts at its converted size). Measured: 170 MB of DXF peaked at 1.13 GB, about 6.6×; 300 MB is ~2.3 GB beside the worker on `--memory=4Gi`, where the DXF itself also sits on the in-memory disk. Above it the job fails `payload_too_large` with the size in the message, before ezdxf reads anything — not an out-of-memory kill reported as an unreadable drawing. Raise it only with the memory (`fbcreview/cad/__init__.py`). |
| `FBC_BACKEND` | `gcp` | `gcp` for Firestore + Cloud Storage, `local` for the filesystem stand-ins. Independent of `FBC_DEV_UNSAFE_AUTH` — see section 0d. Forced to `gcp` whenever `K_SERVICE` is set, because Cloud Run's disk is ephemeral. |
| `FBC_ARTEFACT_SECRET` | generated per process | Signs local artefact URLs. Only read on the `local` backend. Unset means outstanding links break on restart; set it for a service that restarts often or runs more than one uvicorn worker. |
| `FBC_COLLECTION` | `reviews` | Firestore collection |
| `FBC_LOG_LEVEL` | `INFO` | |
| `FBC_VERSION` | stamped from `VERSION` | The version reported on `/healthz` and shown in the client. Set by CI; wins over anything computed locally. See §5a. |
| `FBC_BUILD` | unset | Build number, when handing one in without a full `FBC_VERSION`. |
| `FBC_DEV_UNSAFE_AUTH` | unset | **Local only.** Ignored whenever `K_SERVICE` is set. |
| `FBC_SMTP_*`, `FBC_MAIL_FROM` | unset | Email stays inert unless all are set. For a local run, `secrets/local.env` — see §6a. |
| `FBC_TRAINING_MODE` | unset | `1` turns training mode on. Unset, no feedback collection exists and no Firestore collection beyond `reviews` is touched — see §8. **`share.ps1` and `share.sh` set it for a local run**, along with `FBC_OWNER_EMAILS=dev@localhost` so the owner's queue is reachable under the dev bypass; `-NoTraining` / `--no-training` opts out. The deployed service is unaffected: this default lives in the local run scripts, not in `webapp/config.py`. |
| `FBC_OWNER_EMAILS` | empty | Who may read the feedback queue and promote a calibration profile. A second, independent list: **empty means nobody**, and being on `FBC_ALLOWED_EMAILS` does not put you on this one. |
| `FBC_FEEDBACK_COLLECTION` | `feedback` | Firestore collection for submitted feedback |
| `FBC_MARKUP_COLLECTION` | `markups` | Firestore collection for sheet markup |
| `FBC_CALIBRATION_COLLECTION` | `calibration` | Firestore collection for calibration profile versions |
| `ANTHROPIC_API_KEY` | unset | Two uses. Alone, it turns on summarising free-text feedback comments (`webapp/assist.py`), which runs after a review and never inside one — `tests/test_training.py` walks the import graph to keep that true; unset, comments route to a person unread. With `FBC_AI_READING=on` as well, it is also the AI sheet reader's credential. For a local run, `secrets/local.env` — see §6a. |
| `FBC_AI_READING` | off | `on` adds the AI sheet reader to every review, when `ANTHROPIC_API_KEY` is also set. The key alone is not consent to send drawings to an API. See §6a, *AI sheet reading*. |
| `FBC_AI_MODEL` | `claude-opus-5` | Model the sheet reader asks. |
| `FBC_AI_EFFORT` | `medium` | `low`, `medium`, `high`, `xhigh` or `max`; anything else falls back to `medium` rather than failing every sheet. |
| `FBC_AI_CONCURRENCY` | 6 | Sheets read in parallel. |
| `FBC_AI_MAX_SHEETS` | 60 | Sheets past this many are not AI-read; the deterministic reader still reads them. |
| `FBC_AI_TIMEOUT_S` / `FBC_AI_DEADLINE_S` | 240 / 900 | Seconds per sheet request, and for the whole set. Past the deadline the review carries on with what has been read. |
| `FBC_AI_CACHE_DIR` | under the system temp dir | Readings cached by file hash, model and prompt version, so the same PDF is read once per instance. Holds sheet text. |
| `FBC_AI_REVIEW` | on (with AI reading) | The result check: after the rules run, Claude checks the result and may send sheets back to be read again. `off` skips it. Never on without `FBC_AI_READING`. See §6a, *The result check*. |
| `FBC_AI_MAX_PASSES` | 3 | Runs of the rules, the first included. 1–3; anything higher is held at 3. |
| `FBC_AI_REVIEW_MODEL` / `FBC_AI_REVIEW_EFFORT` | the reader's model / `high` | Model and effort for the result check. |
| `FBC_GITHUB_REPO` | unset | `owner/repo` to open issues in from escalated feedback |
| `FBC_GITHUB_TOKEN` | unset | Token for the above. Issues stay unavailable unless both are set. |

### 6a. Configuring a local deployment: `secrets/local.env`

On Cloud Run these variables are set once on the service and forgotten. Locally
the service is a container `scripts/share.sh` starts from whatever shell is
open, and exporting five SMTP variables and an API key before every start is a
step that gets skipped. The failure is silent — mail and the comment assist are
both inert without their keys, a review still runs, feedback still queues, and
the only place that says otherwise is the queue on `/refine`.

So there is one git-ignored file, and both paths read it: the container gets it
as `docker run --env-file`, and a bare `uvicorn` gets it through
`webapp/envfile.py`.

```bash
bash scripts/setup-secrets.sh          # copy the template, then say what is missing
$EDITOR secrets/local.env              # fill in the blanks
bash scripts/share.sh                  # restart; the container picks it up
bash scripts/setup-secrets.sh --check  # what is set, without changing anything
```

On Windows: `powershell -ExecutionPolicy Bypass -File scripts\setup-secrets.ps1`.

Three rules, and the second one is the one that bites:

1. **Anything already set in the environment wins.** Cloud Run has no such file,
   and if one ever appeared in a checkout it could not override the real thing.
2. **Do not quote values.** Docker's `--env-file` takes quotes literally, so
   `FBC_SMTP_PASS="abcd efgh"` sends the quotes as part of the password.
   `webapp/envfile.py` deliberately parses no more cleverly than Docker does —
   a parser that stripped quotes would authenticate locally and fail in the
   container, which is worse than not supporting them. A password with spaces
   in it is written bare. `setup-secrets` flags any quoted line it finds.
3. **There is no `$VAR` expansion.** The value is the characters after the `=`.

#### Mail, on Google Workspace

`webapp/mailer.py` speaks SMTP with STARTTLS, which is `smtp.gmail.com` on 587.

- `FBC_SMTP_USER` is the full address.
- `FBC_SMTP_PASS` is a 16-character **App Password**, not the account password.
  Google rejects the account password for SMTP on any account with 2-step
  verification, which is every Workspace account worth having. Generate one at
  <https://myaccount.google.com/apppasswords> signed in as that account.
- `FBC_MAIL_FROM` must be the authenticated address or one of its verified
  aliases. Gmail rewrites or rejects a `From` it does not own.
- `FBC_OWNER_EMAILS` is who escalations and the digest go to. Empty means
  nobody, so mail can be fully configured and still have nowhere to go —
  `setup-secrets` calls that out.

Prove it end to end from the queue on `/refine`: **send the digest now** returns what the SMTP
server said, including the failure text when it said no.

#### The comment assist

`ANTHROPIC_API_KEY` turns on `webapp/assist.py`, which reads the free-text half
of a feedback submission so an escalation arrives with a sentence saying what
the person actually claimed. Keys are at
<https://console.anthropic.com/settings/keys>.

**It is not the review path and it cannot become the review path.** It runs
after a review has finished, on a background thread, against feedback a person
submitted. `tests/test_training.py::test_the_feedback_assist_stays_off_the_review_path`
walks the import graph from `webapp.worker` and `fbcreview` and fails if
`webapp.assist` is reachable from either, or if anything on the review path
other than the AI sheet reader imports `anthropic` — including through a lazy
import inside a function. Setting the key does not turn AI sheet reading on;
that takes `FBC_AI_READING=on` as well (below). Its opinion is advisory and
one-directional: it may raise a disposition and can never lower one
(`docs/TRAINING-MODE.md` §3.5).

For Cloud Run, put the key in Secret Manager and mount it rather than setting it
as a plain environment variable — see the block below §8.

##### Two kinds of key, and only one works unaided

The console issues both, and the difference is not cosmetic:

| Type | What it is | Needs |
| --- | --- | --- |
| **Workspace** | Bound to one workspace. Survives its creator leaving. | `ANTHROPIC_API_KEY` |
| **Identity-linked** | Belongs to the person or service account that made it, and can act in several workspaces. | `ANTHROPIC_API_KEY` **and** `ANTHROPIC_WORKSPACE_ID` |

An identity-linked key with no workspace named is refused outright:

```
400  anthropic-workspace-id is required when authenticating with an
     identity-linked API key
```

The SDK sends that header for a credentials-file profile and never for a key
read out of the environment, which is how this service authenticates — so
`webapp/assist.py` sets it as a default header when `ANTHROPIC_WORKSPACE_ID` is
present, and sends nothing when it is not. An empty header is a 400 of its own,
which is why "unset" and "empty" have to mean the same thing here.

The type is in the **Type** column at
<https://console.anthropic.com/settings/keys>. `bash scripts/setup-secrets.sh
--check` says whether you have set it, and warns when a key is present without
one.

Finding the id has one wrinkle worth knowing. A key created for **All
workspaces** shows its Workspace ID as `—`, because it is not bound to one —
which is exactly the key that needs the id supplied. Read it instead off any
workspace-scoped key in the same organisation, or from the workspace's own page
under **Organization settings → Workspaces**. It identifies the workspace, not
the key, so the same `wrkspc_` value serves every key acting in it.

#### AI sheet reading

Off unless `FBC_AI_READING=on` **and** `ANTHROPIC_API_KEY` are both set — the
key alone is not consent, because it may be there only for the comment assist.
With both, every review gains a stage, *Reading sheets with AI*, between
reading the PDF and building the facts:

1. `fbcreview/ai/reader.py` sends each sheet to Claude as one request — an
   overview image (long edge 1568 px), a crop of each pasted raster region, and
   the sheet's positioned text layer — and gets back, as structured output, the
   values the field catalog asks about, each with the verbatim text it read.
2. `fbcreview/ai/grounding.py` finds that quote on the sheet and parses the
   value out of it. A proposal it cannot place, or that the catalog says is some
   other quantity, is recorded as rejected and never reaches a rule.
3. What survives joins the deterministic reader's claims in the fact store,
   scored below an exact deterministic match: it fills gaps and corroborates,
   and cannot out-vote the deterministic reading or create a "set conflict".

Rules, thresholds and code citations are unchanged and stay pure Python.
`readings.json` is stored beside `findings.json`, and a re-run replays it rather
than paying for a second read. The pass never fails a review: a sheet that
errors, times out or is refused is simply not AI-read, and past
`FBC_AI_DEADLINE_S` the review carries on with what it has. `CLAUDE.md`, "AI
reads; rules decide", states the guardrails and `tests/test_ai_guardrails.py`
holds each one. The job record's `ai_reading` carries counts — sheets read,
proposals, accepted, rejected, token usage — and never sheet text.

**What leaves the machine.** The images and text of every sheet reviewed go to
the Anthropic API. Turn it on only where that is acceptable for the sets the
deployment reviews, and never on the unauthenticated tunnel (§ *Exposure*),
where anyone holding the URL would be spending the key.

**Refusal fallbacks are on.** Each request carries the
`server-side-fallback-2026-07-01` beta with `fallbacks: "default"`: if the
configured model declines a sheet on policy grounds, the API re-runs that one
request on the model Anthropic recommends for it. A sheet declined anyway is
recorded in `readings.json` as refused and is not AI-read.

**What it costs — estimated, not measured.** Built from the Sculpted set's own
requests, rendered locally with no API call: 24 sheets come to about 65 k image
tokens and 53 k text-layer tokens, plus a 1.7 k-token system prompt that is
cached after the first sheet — about 120 k input tokens, or ≈ $0.60 at Claude
Opus 5's $5 per million. Output is the uncertain half, because adaptive thinking
decides how much to spend: at 1–3 k tokens a sheet it is ≈ $0.60–1.80 at $25 per
million. So roughly **$1–3 per 24-sheet set**, which at 50 reviews a month is
the largest line in §8 by two orders of magnitude. The job record's
`ai_reading.usage` has the real numbers after the first live run; record them
here.

Locally, set both in `secrets/local.env` (the template documents every
`FBC_AI_*` variable) and restart; `bash scripts/setup-secrets.sh --check` says
whether it is on. From the command line, `python run.py set.pdf --ai
--save-readings readings.json` reads a set once and `--readings readings.json`
replays it with no API call. On Cloud Run, mount the key from Secret Manager —
the same pattern as the GitHub token in §8 — and set the switch:

```bash
printf '%s' "$ANTHROPIC_KEY" | gcloud secrets create anthropic-api-key --data-file=-
gcloud run services update fbc-review --region=us-east1 \
  --update-secrets="ANTHROPIC_API_KEY=anthropic-api-key:latest" \
  --update-env-vars="FBC_AI_READING=on"
```

The runtime service account (`${SA}`, as in §3) needs to read the secret:

```bash
gcloud secrets add-iam-policy-binding anthropic-api-key \
  --member="serviceAccount:${SA}" --role="roles/secretmanager.secretAccessor"
```

**Not yet run** against the deployed service — this session had no key.
Turning it off is `--remove-env-vars="FBC_AI_READING"`; reviews go back to the
deterministic reader at once, and stored readings stay with their jobs.

#### The result check

With AI reading on, every review also gains a stage after the rules, *Checking
the result with AI* (`FBC_AI_REVIEW=off` skips it). Since 2026-09-28 the
reviewer corrects the result directly:

1. `fbcreview/ai/reviewer.py` sends one request per pass: the pass's job, the
   review options and declaration (what was asked for), the findings with their
   keys, the abstentions, the facts the rules used, the AI values the sheet
   check rejected, earlier passes' notes and edits, and each sheet's text.
2. The answer (`ResultReview`) can revise, add or withdraw findings, ask for
   sheets to be read again, and leave notes.
3. `fbcreview/ai/review.py` validates and applies the edits. Adding or
   withdrawing a finding, or moving a severity or status, needs a quote printed
   on the sheet; anything that fails is recorded and not applied. Every applied
   edit is labelled on the finding — in its result text, so the PDF shows it,
   and as `ai_revision` in `findings.json`. A withdrawn finding becomes an
   abstention. Re-reads re-run the rules and the edits are re-applied on top.
4. **Pass 1 checks, pass 2 edits actively, pass 3 verifies** — three passes at
   most, whatever `FBC_AI_MAX_PASSES` says. After pass 1, a pass that changes
   nothing ends the loop. A failed pass keeps the last good state.
5. Calibration applies after, so a promoted profile keeps the last word.

`ai_review.json` is stored beside `findings.json` with every answer, edit,
re-read and note, and a re-run replays it with no call. The job's
`summary.ai_review` carries counts only — passes, findings revised, added and
withdrawn, edits not applied, sheets re-read, why it stopped — never the notes
or reasons.

**What this changes for the people reading a review.** Findings can now be
raised, re-levelled or withdrawn by a model, not only by the hand-verified rule
corpus. Each one says so on its card and in the PDF, and the weighty ones rest
on a quote from the sheet — but a citation the reviewer adds is its own, not a
corpus row. Turn it on knowing that.

**Cost and time.** One check per pass, plus a re-read of at most eight sheets
per extra pass: at most three checks and two partial re-reads on top of the
first read. Built locally from the Sculpted set with no API call, one check's
packet is 107 k characters — about 27 k tokens, plus a 1 k-token system prompt
— so ≈ $0.14 a check at Opus 5's $5 per million input, before output. Each
extra pass also re-runs the engine: about 11 s on the same set. **Estimated, not
measured** against the API; record `ai_review.usage` here after the first live
run.

#### Issues from escalated feedback

`FBC_GITHUB_REPO` and `FBC_GITHUB_TOKEN` together let the queue open an escalated
report as a GitHub issue — the same brief the prompt export produces, but
tracked. Both are needed; either alone leaves the button unavailable.

`webapp/notify.py` makes exactly one call, `POST /repos/{owner}/{repo}/issues`,
with a `Bearer` token. So the token needs one permission and no more.

**A fine-grained personal access token** (<https://github.com/settings/personal-access-tokens>):

1. **Generate new token**, and set **Resource owner** to the account or
   organisation that owns the repository. Getting this wrong is the usual
   reason a token 404s on a repository that plainly exists.
2. **Repository access → Only select repositories**, and pick the one you want
   the issues in.
3. **Repository permissions → Issues → Read and write.** Nothing else. Leave
   Contents at "No access": this token opens issues, it does not push code.
4. Set an expiry you will actually notice. The failure is quiet — the issue
   button stops working and the queue says the channel is unconfigured.
5. Copy the `github_pat_...` value once; GitHub will not show it again.

A classic token works too and needs the `repo` scope, but that scope also
grants read and write to your code on every repository you can reach. Prefer
fine-grained.

Then, in `secrets/local.env`:

```
FBC_GITHUB_REPO=owner/repo
FBC_GITHUB_TOKEN=github_pat_...
```

`FBC_GITHUB_REPO` is `owner/repo` — no URL, no `.git`, no leading slash. It is
interpolated straight into the API path.

Two things worth doing before you rely on it. The issue is created with the
labels `training-feedback` and `disposition/<disposition>`, where the
disposition is one of `confirmation`, `auto_tunable`, `needs_component` or
`escalate` — create those five labels in the repository first, so the first
escalation is not the thing that finds out whether GitHub minds. And note that
the token is never logged and a GitHub error is recorded by status code only,
deliberately: an error body can echo the request back, and the request carries
the report. That is the right trade for a service and a poor one for you
standing at a terminal wondering why nothing happened, which is what the check
below is for.

##### Checking the token without opening an issue

Send a create-issue request with a deliberately empty body. GitHub answers
**403** when the token may not write issues and **422** when it may but the
payload is wrong — and 422 creates nothing, because `title` is required:

```bash
curl -s -o /dev/null -w '%{http_code}\n' \
  -X POST https://api.github.com/repos/OWNER/REPO/issues \
  -H "Authorization: Bearer $FBC_GITHUB_TOKEN" \
  -H 'Accept: application/vnd.github+json' \
  -H 'X-GitHub-Api-Version: 2022-11-28' \
  -d '{}'
```

In PowerShell, on one line, with the token in a variable — `curl` there is an
alias for `Invoke-WebRequest`, which takes none of these flags, and `\` is not
a line continuation:

```powershell
$t = 'github_pat_...'
curl.exe -s -o NUL -w "%{http_code}\n" -X POST https://api.github.com/repos/OWNER/REPO/issues -H "Authorization: Bearer $t" -H "Accept: application/vnd.github+json" -H "X-GitHub-Api-Version: 2022-11-28" -d "{}"
```

Read the answer as a question about the *credential* first and the
*permission* second:

| Code | What it means |
| --- | --- |
| **422** | What you want. The permission is there; the empty payload was rejected and nothing was created. |
| **401** | GitHub did not accept the credential at all. Almost never the token's scopes — it is the token that arrived: a placeholder pasted literally, a truncated copy, or a shell variable that expanded to nothing. An unset `$FBC_GITHUB_TOKEN` sends `Bearer ` and reads exactly like a bad token. |
| **403** | Valid token, missing permission. Set Issues to Read and write. |
| **404** | Valid token that cannot see the repository — the **resource owner** is not the account that owns it, or it was not in the selected list. A fine-grained token says "not found" rather than "not allowed". |

Isolate a 401 before touching anything else, because this call answers only for
the credential:

```bash
curl -s -o /dev/null -w '%{http_code}\n' \
  -H "Authorization: Bearer $FBC_GITHUB_TOKEN" https://api.github.com/user
```

`200` means the token is fine and the problem was in the longer command;
`401` means the token itself never arrived intact.

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
| Cloud Run memory | same × 4 GiB (2 GiB before drawings were accepted) | ~$0.04 |
| Cloud Run requests | ~2 k, first 2 M free | $0 |
| Cloud Storage | ~1.8 GB held, 30-day lifecycle | ~$0.04 |
| Egress | 50 × ~18 MB downloaded | ~$0.11 |
| Firestore | ~2 k reads, ~500 writes; free tier is 50 k reads/day | $0 |
| Firebase Hosting | well inside the free 10 GB | $0 |
| Artifact Registry | 893 MB image × a few tags | ~$0.30 |
| **Total** | | **well under $1** |

The dominant line is the container image, not the compute. The $25 budget is a
runaway alarm rather than a forecast.

**AI sheet reading is not in this table.** Off by default; on, it is roughly
$1–3 per 24-sheet set in API charges — $50–150 at 50 reviews a month, which
would be nearly the whole bill. The estimate and its arithmetic are in §6a,
*AI sheet reading*.

**Caveat.** The opt-in raster rebuild changes the shape: OCR plus Hough
transform on a large sheet is minutes of CPU, not seconds. Fifty *scanned* sets
would still land under a dollar of CPU, but it is the one thing that could move
the number, and `max-instances=5` is what bounds it.

**Drawings change it the same way.** A DWG review measured about five minutes
of CPU on the reference drawing (ingest 186 s, markup 85 s), roughly ten PDF
reviews' worth, and keeps a 170 MB DXF on the instance while it runs. At this
volume that is still cents. If CPU stops being throttled outside requests
(§9a, *Not decided — CPU outside requests*), the basis changes from request
time to instance time and this table has to be redone.

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
2 GiB / 2 vCPU instance and should be weighed against that. That figure predates
the move to 4 GiB; the memory part of it doubles.

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
permit review anyway. *(2026-10-03: still true for `.rvt`. A Revit user exports
sheets to DWG today — `docs/CAD-INPUT.md` says how — and reading Revit's own
IFC export is planned as a separate path, `docs/FEATURE-PROMPT-revit-ifc.md`.)*

**DWG: DXF only, when it comes up.** *(Superseded 2026-10-03 — see below.)*
`accoreconsole.exe` ships with the AutoCAD on this workstation and converts DWG
headlessly, but it is Windows-only and licence-bound and cannot run in the
container. `ezdxf` reads DXF, not DWG. So the supported path is DXF in, rendered
to a layered PDF in-process, with no external binary and no licensing question.

#### 2026-10-03 — DWG is read directly, through LibreDWG

Decided by the owner on 2026-10-03, with a real drawing to test against
(`EVERGREEN_BLDG_1.dwg`: AutoCAD 2018 format, 23 MB, eight layouts). The upload
can now be a `.dwg`, a `.dxf`, or a `.zip` of drawings with their xrefs; the
engine plots each layout to the PDF it reviews and also returns the drawing
marked up. How it works is `docs/ARCHITECTURE-V2.md` §5; what a user can send
and get back is `docs/CAD-INPUT.md`.

**The converter is GNU LibreDWG's `dwg2dxf`**, chosen over the two
alternatives:

| Option | Why not |
| --- | --- |
| ODA File Converter | free to download, proprietary licence; use inside a hosted commercial service appears to need an ODA membership |
| Autodesk Platform Services (Model Derivative) | paid per translation, and every client drawing leaves the deployment for Autodesk's cloud |
| **LibreDWG `dwg2dxf`** | **chosen**: GPL-3.0-or-later, runs inside the container, converts the reference drawing in 4–7 s |

**How it is used, and why that matters for the licence.** The service runs
`dwg2dxf` as a separate, unmodified program — a subprocess that takes two file
paths and writes a DXF (`fbcreview/cad/convert.py`). Nothing links against
LibreDWG, and its source is not changed. Two consequences, from the licence
text itself (<https://www.gnu.org/licenses/gpl-3.0.html>):

- **Running the service is not distribution.** GPL-3.0 §0: *"Mere interaction
  with a user through a computer network, with no transfer of a copy, is not
  conveying."* (GPL-3.0 has no network clause; that is the AGPL.) Pushing the
  image to this project's own private Artifact Registry and running it on Cloud
  Run conveys nothing to anyone.
- **Calling it as a program keeps the rest of the code out of the GPL's scope.**
  The GNU FAQ treats pipes and command-line arguments between two programs as
  communication between separate works, not one combined program
  (<https://www.gnu.org/licenses/gpl-faq.html#MereAggregation>).

**If the container image is ever given to anyone** — a client, a contractor, a
public registry — that *is* conveying object code, and GPL-3.0 §6 then requires
offering the Corresponding Source for `dwg2dxf` and `libredwg.so.0`. It is
unmodified upstream source:

```
https://ftp.gnu.org/gnu/libredwg/libredwg-0.14.tar.xz
SHA-256 62ebb73b984f865960f20ed26619ea5f8789d5e3fd088fa40a2598384da81275
```

plus the build steps, which are the Dockerfile's `libredwg` stage. The image
carries both, at `/usr/local/share/doc/libredwg/COPYING` and `SOURCE`. Under
§6(d) the source may be offered from a different server than the image, with
directions next to the image saying where — but whoever distributes stays
responsible for it remaining available, so keep a copy of the tarball rather
than relying on GNU's mirror forever. None of this touches `fbcreview/` or
`webapp/`. `tests/test_container_cad.py` fails if the Dockerfile and this
paragraph name different tarballs.

**What it costs the deployment.** Measured on the reference drawing:

| Step | Measured |
| --- | --- |
| `dwg2dxf` conversion | 4–7 s; reports ~1 700 `ERROR` lines and exits 0 — the DXF reads back whole, so success is judged by the output, not the exit code |
| DXF size | 170 MB from a 23 MB DWG — ASCII DXF, because `dwg2dxf -b` (binary) truncates every text-style and linetype name to one character and drops all 1 088 block attributes |
| ezdxf read + bounding-box index | ~60 s + ~17 s |
| plotting | 3–15 s per sheet; 8 sheets in ~188 s end to end |
| peak memory | 1.13 GB, in the `python -m fbcreview.cad` subprocess |
| outputs | `rendered.pdf` 5 MB; marked-up DXF zip 14.5 MB |

So: Cloud Run memory goes from 2Gi to **4Gi** (§3, and both `scripts/provision.sh`
and `.github/workflows/deploy.yml`), CAD work is limited to one drawing at a
time per instance (`FBC_CAD_CONCURRENCY=1`), and the new variables are in §6.
The tunnel deployment (§0c) runs the same image on the workstation under Docker
Desktop, which sets no per-container limit but caps the whole Docker VM; the
same 4 GB has to fit inside that cap, so check it (Docker Desktop's resource
settings, or `.wslconfig` under the WSL 2 backend) before the first drawing
review there, and rebuild the image so it carries the converter.

**Upload size.** `FBC_MAX_UPLOAD_MB` (120) applies to drawings as it does to
PDFs, and DXF is the bulky form: the 23 MB DWG above is a 170 MB DXF. Upload
the DWG; if only a DXF is available, zip it — DXF text deflates about 10:1, and
a zip of drawings is accepted and unpacked with limits on member count, total
size and compression ratio (`fbcreview/cad/source.py`). Behind the Cloudflare
tunnel the 95 MB ceiling of §0c still applies.

**Not decided — CPU outside requests.** The review runs on a background thread
after `POST /api/review` has answered 202. Under Cloud Run's default
request-based billing, *"CPU is only allocated during request processing"*
(<https://docs.cloud.google.com/run/docs/configuring/billing-settings>), so that
thread runs at full speed only while some request — the client's status polls —
is in flight on the instance. A PDF review is ~30 s and has lived with it; a
drawing review is about five minutes of CPU (ingest ~186 s, markup ~85 s). The
fix is `--no-cpu-throttling` (instance-based billing), which changes what an
idle instance costs. That is a billing decision for the owner, so it is recorded
here and not made: neither deploy path sets it today.

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
Hough transform; both are deterministic. The raster rebuild still calls no
model; AI sheet reading, which does, is a separate switch (§6a).

---

## 8. Training mode

Off by default and off in the deployment described above. With
`FBC_TRAINING_MODE` unset, `webapp/feedback_store.py` never constructs a
Firestore client, `calibration.apply` runs as the identity function, and the
review a user gets is byte-for-byte the one this service produced before the
feature existed. `docs/TRAINING-MODE.md` explains what it does and why it is
shaped the way it is; this section is what to run.

### Turn it on

```bash
gcloud run services update fbc-review --region=us-east1 \
  --update-env-vars="FBC_TRAINING_MODE=1,FBC_OWNER_EMAILS=you@example.com"
```

`FBC_OWNER_EMAILS` is what makes the queue section of `/refine` visible and what gates every
`/api/admin/*` route. It is deliberately not derived from
`FBC_ALLOWED_EMAILS`: running a review and re-levelling a rule for every future
applicant are different privileges, and an unset variable must not grant the
second one. Leave it empty and training mode still collects and triages
feedback — nobody can approve anything, which is the safe direction to fail in.

### Firestore indexes

Three new collections and six composite indexes, already in
`firestore.indexes.json`. Deploy them before turning the feature on, or the
first queue query fails with a link to create one by hand:

```bash
firebase deploy --only firestore:indexes
```

`firestore.rules` still denies everything, and that now covers the new
collections too. A calibration profile decides what every future review
reports, so a client-writable path to one would be a way to change other
people's plan reviews from a browser console. There is no such path: promotion
happens in the API, behind `FBC_OWNER_EMAILS`.

### Optional channels

Each is inert unless configured and each says so on `/api/admin/overview`.

| Channel | Needs | Without it |
| --- | --- | --- |
| Immediate mail on an escalation, and the digest | `FBC_SMTP_*`, `FBC_MAIL_FROM`, `FBC_OWNER_EMAILS` | Feedback still queues; you read the queue on `/refine` |
| A GitHub issue from a report | `FBC_GITHUB_REPO`, `FBC_GITHUB_TOKEN` | The prompt export still works; copy it by hand |
| Free-text comments summarised before they reach you | `ANTHROPIC_API_KEY` | Any comment routes to you unread, which is what it did before |
| Sheets also read by Claude, every value grounded before use | `ANTHROPIC_API_KEY` and `FBC_AI_READING=on` | The deterministic reader alone, as before — see §6a |

Store the two secrets in Secret Manager and mount them, rather than setting
them as plain environment variables:

```bash
printf '%s' "$TOKEN" | gcloud secrets create fbc-github-token --data-file=-
gcloud run services update fbc-review --region=us-east1 \
  --update-secrets="FBC_GITHUB_TOKEN=fbc-github-token:latest"
```

The GitHub token needs `issues: write` on that repository and nothing else. For
the comment assist, the Anthropic key is billed per comment summarised — a
handful of calls a day rather than one per review; the test suite keeps the
assist off the review path. AI sheet reading is billed per sheet reviewed and is
off unless `FBC_AI_READING=on` (§6a).

### Turning it off again

```bash
gcloud run services update fbc-review --region=us-east1 \
  --remove-env-vars="FBC_TRAINING_MODE"
```

Reviews immediately go back to running uncalibrated. Nothing is deleted: the
collected feedback and every promoted profile version stay in Firestore, and
turning the flag back on resumes from the same active version.

**Note what this does not do.** Reviews run against the *active* profile while
training is on, so if you have promoted anything, switching training off also
switches those calibrations off. That is usually not what you want in an
incident — to back out one bad promotion while keeping the rest, promote a
correction instead. Versions are immutable and `/api/admin/calibration` lists
them, so you can always see what changed and when.
