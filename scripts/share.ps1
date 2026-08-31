<#
.SYNOPSIS
    Run the whole app as one container, ready to put behind a tunnel.

.DESCRIPTION
    Builds the client, then runs the API with FBC_STATIC_DIR pointed at the
    bundle so a single origin serves both. That removes CORS from the picture
    and means a tunnel only has to forward one port.

    WARNING — by default this runs with FBC_DEV_UNSAFE_AUTH=1, which turns
    authentication off entirely. Anyone who has the URL can upload a permit set
    and spend your CPU. It is for a short, unlisted test, not something to leave
    running.

    -Authenticated is the answer to that, and needs no Cloud Billing account.
    It runs the same filesystem backend with real Firebase sign-in and the
    server-side allowlist, because FBC_BACKEND and FBC_DEV_UNSAFE_AUTH are now
    separate settings. Firebase Authentication is free on the Spark plan; only
    Cloud Storage and Cloud Run need billing, and this mode uses neither. It
    needs three things in the environment:

      FBC_PROJECT_ID        the Firebase project id
      FBC_ALLOWED_EMAILS    who may sign in, comma-separated
      FBC_SA_KEY            path to a service account key JSON (default
                            .\secrets\firebase-sa.json), mounted read-only

    The key is needed because verifying an ID token with check_revoked=True
    calls the Firebase Auth backend, which off-GCP has no ambient identity to
    use. Keep it outside the repository; docs/DEPLOYMENT.md section 0d says how
    to make one. This is the Windows twin of `share.sh --authenticated`.

    The per-user rate limits still apply and, with authentication off, every
    request shares one identity — so 3 concurrent and 10 reviews an hour become
    a global cap rather than a per-person one. That is the main thing keeping a
    shared link from being abused.

    Storage is the local filesystem under .devdata, not Cloud Storage, and it is
    wiped whenever you delete that directory.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\share.ps1
    # then, in another shell:
    powershell -ExecutionPolicy Bypass -File scripts\tunnel.ps1
#>
[CmdletBinding()]
param(
    [int]$Port = 8060,
    [switch]$SkipBuild,
    # Below Cloudflare's 100 MB request-body ceiling, which applies to every
    # proxied request including tunnel traffic. Over it, the edge returns its
    # own opaque 413 and the upload never reaches the app - so the app's limit
    # is set under the edge's, and a too-large set gets the typed error and the
    # real message instead. Raising this past 95 only makes sense off the
    # Cloudflare path.
    [int]$MaxUploadMb = 95,
    # For a machine that stays powered on: restart the container with Docker,
    # and therefore across reboots, instead of vanishing on exit.
    [switch]$Persistent,
    # Real Firebase sign-in over the same filesystem stores. See the header and
    # docs/DEPLOYMENT.md section 0d.
    [switch]$Authenticated,
    # Training mode is ON by default for a local run. It writes feedback,
    # markups and calibration profiles, which on this backend are JSON files
    # under .devdata - throwaway and git-ignored. It does not change what a
    # review reports until someone promotes a calibration profile: with none
    # promoted, active_profile() is the same default CalibrationProfile() the
    # untrained path uses. This switch is the way back to the deployed
    # service's behaviour.
    [switch]$NoTraining
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
Push-Location $repo

try {
    $node24 = Join-Path $env:LOCALAPPDATA 'nvm\v24.19.0'
    if (Test-Path $node24) { $env:PATH = "$node24;$env:PATH" }

    # Stamp the version on the host, where the checkout is. The image carries
    # VERSION but no .git, so a container left to work it out alone can only say
    # `1.0.0-alpha` — true, and not enough to tell one local build from the next.
    # Resolved here it reads `1.0.0-alpha+local.9f3c1ab`, and `.dirty` when the
    # tree has uncommitted changes, which is what answers "is this the code I
    # just pulled?" from the page itself.
    $python = Join-Path $repo '.venv\Scripts\python.exe'
    if (-not (Test-Path $python)) { $python = 'python' }
    $version = ''
    try { $version = (& $python -m webapp.version 2>$null | Select-Object -First 1).Trim() } catch { }
    if ($version) { Write-Host "Version $version" -ForegroundColor DarkGray }

    $bundle = Join-Path $repo 'web\dist\fbc-review\browser'
    if (-not $SkipBuild -or -not (Test-Path $bundle)) {
        Push-Location (Join-Path $repo 'web')
        try {
            # Install the client dependencies when they are missing or stale.
            # This used to go straight to `ng build`, which fails on a machine
            # that has never run setup.ps1 and — far more often — the first time
            # you build a branch that added a dependency:
            #
            #   Cannot find module 'pdfjs-dist' or its corresponding type
            #   declarations
            #
            # npm writes node_modules/.package-lock.json when it installs, so
            # comparing that against the real lockfile catches both cases
            # without paying for an npm ci on every single run.
            #
            # -Force on both Get-Item calls, because the stamp is a dot-file:
            # Test-Path finds it and a bare Get-Item does not, which turns a
            # skippable install into a terminating error.
            $lock = Join-Path $repo 'web\package-lock.json'
            $stamp = Join-Path $repo 'web\node_modules\.package-lock.json'
            $stale = -not (Test-Path $stamp)
            if (-not $stale -and (Test-Path $lock)) {
                $lockAt = (Get-Item $lock -Force).LastWriteTimeUtc
                $stampAt = (Get-Item $stamp -Force).LastWriteTimeUtc
                $stale = $lockAt -gt $stampAt
            }
            if ($stale) {
                Write-Host 'Installing client dependencies...' -ForegroundColor Cyan
                & npm ci --no-audit --no-fund
                if ($LASTEXITCODE -ne 0) { throw 'npm ci failed - the client dependencies are not installed.' }
            }

            Write-Host 'Building the client...' -ForegroundColor Cyan
            & npx ng build
            # Checked, because it was not: a failed build printed its errors and
            # the script carried on to build an image around a stale bundle.
            if ($LASTEXITCODE -ne 0) { throw 'The client build failed - see the errors above.' }
        } finally { Pop-Location }
    }
    if (-not (Test-Path $bundle)) { throw "Client bundle not found at $bundle" }

    Write-Host 'Building the image...' -ForegroundColor Cyan
    & docker build -q -t fbc-review:dev $repo | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'docker build failed - see the errors above.' }

    # Asked rather than attempted. `docker rm` writes "No such container" to
    # stderr when there is nothing to remove, and PowerShell turns a native
    # command's *redirected* stderr into an ErrorRecord, which
    # $ErrorActionPreference='Stop' then makes terminating — so on a machine
    # that had never run this, the script died right here. Listing first
    # produces no stderr to trip over.
    $existing = & docker ps -aq --filter 'name=^fbc-test$'
    if ($existing) { & docker rm -f fbc-test | Out-Null }
    $devdata = Join-Path $repo '.devdata'
    New-Item -ItemType Directory -Force -Path $devdata | Out-Null

    # ── the artefact signing key ──────────────────────────────────────────
    # Kept beside the blobs it authorises, so links survive a restart. Without
    # a stable key the service generates one per process, which is safe but
    # means every outstanding download link stops working when the container is
    # replaced — and this script replaces it on every rebuild. Written in the
    # same place and the same format as share.sh, so the two are interchangeable
    # on one machine.
    $secretFile = Join-Path $devdata 'artefact.secret'
    if (-not (Test-Path $secretFile) -or (Get-Item $secretFile).Length -eq 0) {
        # RandomNumberGenerator::Create() exists on both Windows PowerShell 5.1
        # and PowerShell 7; ::Fill() does not exist on the former.
        $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
        try {
            $bytes = New-Object byte[] 32
            $rng.GetBytes($bytes)
        } finally { $rng.Dispose() }
        $generated = [Convert]::ToBase64String($bytes)
        $generated = $generated -replace '=+$', ''
        Set-Content -Path $secretFile -Value $generated -NoNewline -Encoding ascii
        Write-Host '  Generated an artefact signing key at .devdata\artefact.secret' -ForegroundColor DarkGray
    }
    $artefactSecret = (Get-Content -Path $secretFile -Raw).Trim()

    # ── authenticated mode preflight ──────────────────────────────────────
    # Checked before the container starts rather than after: a missing key here
    # fails as an unreadable stack trace three seconds into startup, which is a
    # much worse way to learn about it.
    $saKey = ''
    if ($Authenticated) {
        $saKey = $env:FBC_SA_KEY
        if (-not $saKey) { $saKey = Join-Path $repo 'secrets\firebase-sa.json' }
        if (-not $env:FBC_PROJECT_ID) {
            throw '-Authenticated needs FBC_PROJECT_ID (the Firebase project id).'
        }
        if (-not $env:FBC_ALLOWED_EMAILS) {
            throw '-Authenticated needs FBC_ALLOWED_EMAILS. Empty means nobody gets in.'
        }
        if (-not (Test-Path $saKey)) {
            throw "No service account key at $saKey. See docs/DEPLOYMENT.md section 0d, or set FBC_SA_KEY."
        }
        $fbConfig = Join-Path $repo 'web\src\app\core\firebase-config.ts'
        if ((Test-Path $fbConfig) -and (Select-String -Path $fbConfig -Pattern 'REPLACE_ME' -Quiet)) {
            Write-Host '  web/src/app/core/firebase-config.ts still has placeholders - sign-in will not work.' -ForegroundColor Yellow
            Write-Host '  Run: firebase apps:sdkconfig web > sdk.json' -ForegroundColor Yellow
            Write-Host '       python scripts\write_firebase_config.py sdk.json' -ForegroundColor Yellow
            Write-Host '  then rebuild the client.' -ForegroundColor Yellow
        }
    }

    Write-Host 'Starting...' -ForegroundColor Cyan
    # MSYS_NO_PATHCONV is irrelevant here (PowerShell, not Git Bash) but the
    # same command run from Git Bash needs it, or /app/client is rewritten to a
    # Windows path and the client silently 404s.
    $lifecycle = if ($Persistent) { '--restart=unless-stopped' } else { '--rm' }

    # Built as one array rather than a wall of backticks, so the two modes below
    # are a pair of appends instead of two near-identical copies of the call.
    $runArgs = @(
        'run', $lifecycle, '-d', '--name', 'fbc-test'
        '-p', "${Port}:8080"
        '-e', 'FBC_BACKEND=local'
        '-e', 'FBC_STATIC_DIR=/app/client'
        '-e', "FBC_MAX_UPLOAD_MB=$MaxUploadMb"
        '-e', 'FBC_WORKERS=2'
        '-e', "FBC_ARTEFACT_SECRET=$artefactSecret"
        '-v', "${bundle}:/app/client:ro"
        '-v', "${devdata}:/app/.devdata"
    )
    if ($Authenticated) {
        $runArgs += @(
            '-e', "FBC_PROJECT_ID=$($env:FBC_PROJECT_ID)"
            '-e', "FBC_ALLOWED_EMAILS=$($env:FBC_ALLOWED_EMAILS)"
            '-e', "FBC_BUCKET=$(if ($env:FBC_BUCKET) { $env:FBC_BUCKET } else { 'fbc-local' })"
            '-e', 'GOOGLE_APPLICATION_CREDENTIALS=/app/secrets/firebase-sa.json'
            '-v', "${saKey}:/app/secrets/firebase-sa.json:ro"
        )
    } else {
        $runArgs += @(
            '-e', 'FBC_DEV_UNSAFE_AUTH=1'
            '-e', 'FBC_BUCKET=fbc-dev-local'
            '-e', 'FBC_PROJECT_ID=fbc-dev-local'
        )
    }
    # ── training mode ─────────────────────────────────────────────────────
    # The owner list is separate from the allowlist on purpose - being allowed
    # to run a review is not being allowed to re-level a rule for everyone -
    # and an unset FBC_OWNER_EMAILS means nobody, so the owner's queue would
    # 404 with training otherwise on. With the dev bypass every request signs
    # in as dev@localhost, so that is who gets it locally. Anything already in
    # the environment wins, and authenticated mode never invents an owner.
    if (-not $NoTraining) {
        $runArgs += @('-e', 'FBC_TRAINING_MODE=1')
        $owners = $env:FBC_OWNER_EMAILS
        if (-not $owners -and -not $Authenticated) { $owners = 'dev@localhost' }
        if ($owners) { $runArgs += @('-e', "FBC_OWNER_EMAILS=$owners") }
    }

    if ($version) { $runArgs += @('-e', "FBC_VERSION=$version") }
    $runArgs += 'fbc-review:dev'

    & docker @runArgs | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'docker run failed - see the errors above.' }

    $deadline = (Get-Date).AddSeconds(90)
    do {
        Start-Sleep -Milliseconds 500
        try { $health = Invoke-RestMethod "http://127.0.0.1:$Port/healthz" -TimeoutSec 2 } catch { $health = $null }
    } while (-not $health -and (Get-Date) -lt $deadline)

    if (-not $health) { & docker logs fbc-test; throw 'The service did not come up.' }

    Write-Host ''
    Write-Host "  Running on http://127.0.0.1:$Port/" -ForegroundColor Green
    Write-Host "  Version: $($health.version)" -ForegroundColor Green
    if ($health.auth_required) {
        Write-Host '  Sign-in required: true' -ForegroundColor Green
    } else {
        Write-Host '  Sign-in required: false - anyone with the URL can use this' -ForegroundColor Yellow
        Write-Host '  Close it with -Authenticated, or a Cloudflare Access policy' -ForegroundColor Yellow
        Write-Host '  on the hostname (docs/DEPLOYMENT.md section 0d).' -ForegroundColor Yellow
    }
    if ($NoTraining) {
        Write-Host '  Training mode: off' -ForegroundColor DarkGray
    } else {
        Write-Host '  Training mode: on (-NoTraining turns it off)' -ForegroundColor Green
    }
    Write-Host ''
    Write-Host "  Upload limit: $MaxUploadMb MB" -ForegroundColor DarkGray
    Write-Host ''
    Write-Host '  Publish it at fbc.omniflexfitness.com with:' -ForegroundColor Cyan
    # Absolute, and quoted. Printed relative, this line only worked if you
    # happened to be sitting in the repository - and nobody is, because the
    # console and the Desktop shortcut both start elsewhere. Pasted from
    # C:\WINDOWS\system32 it fails with "the argument to the -File parameter
    # does not exist", which reads as a missing file rather than a wrong path.
    # Built by concatenation so the embedded quotes need no escaping.
    $tunnelPs1 = Join-Path $repo 'scripts\tunnel.ps1'
    Write-Host ('      powershell -ExecutionPolicy Bypass -File "' + $tunnelPs1 + '"')
    Write-Host ''
    Write-Host '  Or, for a throwaway unlisted URL:' -ForegroundColor Cyan
    Write-Host "      tailscale funnel $Port"
    Write-Host ''
    Write-Host '  Stop everything with:' -ForegroundColor Cyan
    Write-Host '      docker rm -f fbc-test      # plus Ctrl-C in the tunnel window,'
    Write-Host '                                 # or: tailscale funnel reset'
    Write-Host ''
    if ($Persistent) {
        Write-Host '  Persistent: the container restarts with Docker, so it survives a' -ForegroundColor DarkGray
        Write-Host '  reboot provided Docker Desktop is set to start with Windows.' -ForegroundColor DarkGray
        Write-Host '  The tunnel does not: scripts\tunnel.ps1 runs in the foreground and' -ForegroundColor DarkGray
        Write-Host '  stops with the window. See Persistence in docs/DEPLOYMENT.md to' -ForegroundColor DarkGray
        Write-Host '  install cloudflared as a Windows service. Funnel persists on its own.' -ForegroundColor DarkGray
        Write-Host ''
    }
} finally {
    Pop-Location
}
