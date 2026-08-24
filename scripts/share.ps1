<#
.SYNOPSIS
    Run the whole app as one container, ready to put behind a tunnel.

.DESCRIPTION
    Builds the client, then runs the API with FBC_STATIC_DIR pointed at the
    bundle so a single origin serves both. That removes CORS from the picture
    and means a tunnel only has to forward one port.

    WARNING — this runs with FBC_DEV_UNSAFE_AUTH=1, which turns authentication
    off entirely. Anyone who has the URL can upload a permit set and spend your
    CPU. It is for a short, unlisted test, not something to leave running.

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
    [switch]$Persistent
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
Push-Location $repo

try {
    $node24 = Join-Path $env:LOCALAPPDATA 'nvm\v24.19.0'
    if (Test-Path $node24) { $env:PATH = "$node24;$env:PATH" }

    $bundle = Join-Path $repo 'web\dist\fbc-review\browser'
    if (-not $SkipBuild -or -not (Test-Path $bundle)) {
        Write-Host 'Building the client...' -ForegroundColor Cyan
        Push-Location (Join-Path $repo 'web')
        try { & npx ng build } finally { Pop-Location }
    }
    if (-not (Test-Path $bundle)) { throw "Client bundle not found at $bundle" }

    Write-Host 'Building the image...' -ForegroundColor Cyan
    & docker build -q -t fbc-review:dev $repo | Out-Null

    & docker rm -f fbc-test 2>$null | Out-Null
    New-Item -ItemType Directory -Force -Path (Join-Path $repo '.devdata') | Out-Null

    Write-Host 'Starting...' -ForegroundColor Cyan
    # MSYS_NO_PATHCONV is irrelevant here (PowerShell, not Git Bash) but the
    # same command run from Git Bash needs it, or /app/client is rewritten to a
    # Windows path and the client silently 404s.
    $lifecycle = if ($Persistent) { '--restart=unless-stopped' } else { '--rm' }
    & docker run $lifecycle -d --name fbc-test `
        -p "${Port}:8080" `
        -e FBC_DEV_UNSAFE_AUTH=1 `
        -e FBC_BUCKET=fbc-dev-local `
        -e FBC_PROJECT_ID=fbc-dev-local `
        -e FBC_STATIC_DIR=/app/client `
        -e FBC_MAX_UPLOAD_MB=$MaxUploadMb `
        -e FBC_WORKERS=2 `
        -v "${bundle}:/app/client:ro" `
        -v "$(Join-Path $repo '.devdata'):/app/.devdata" `
        fbc-review:dev | Out-Null

    $deadline = (Get-Date).AddSeconds(90)
    do {
        Start-Sleep -Milliseconds 500
        try { $health = Invoke-RestMethod "http://127.0.0.1:$Port/healthz" -TimeoutSec 2 } catch { $health = $null }
    } while (-not $health -and (Get-Date) -lt $deadline)

    if (-not $health) { & docker logs fbc-test; throw 'The service did not come up.' }

    Write-Host ''
    Write-Host "  Running on http://127.0.0.1:$Port/" -ForegroundColor Green
    Write-Host "  Sign-in required: $($health.auth_required)" -ForegroundColor Yellow
    Write-Host ''
    Write-Host "  Upload limit: $MaxUploadMb MB" -ForegroundColor DarkGray
    Write-Host ''
    Write-Host '  Publish it at fbc.omniflexfitness.com with:' -ForegroundColor Cyan
    Write-Host '      powershell -ExecutionPolicy Bypass -File scripts\tunnel.ps1'
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
