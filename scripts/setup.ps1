<#
.SYNOPSIS
    Set this repository up on a fresh Windows machine.

.DESCRIPTION
    Everything that matters is in git, so moving between machines is a clone
    plus this script. What is deliberately NOT in git, and what to do about it:

      .venv/            recreated here
      web/node_modules/ recreated here
      web/dist/         rebuilt here
      .devdata/         local job scratch, disposable
      samples/*.pdf     permit sets. Git-ignored on purpose — never commit a
                        client's drawings. Re-copy them from Google Drive:
                        "G:\Shared drives\Meridian\Building Codes\Unreviewed Plans"

    It checks prerequisites first and tells you exactly what is missing rather
    than failing halfway through an install.

.EXAMPLE
    git clone https://github.com/bkenol/fbc-review.git
    cd fbc-review
    powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
#>
[CmdletBinding()]
param(
    [switch]$SkipTests
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
Push-Location $repo

function Section($t) { Write-Host ''; Write-Host $t -ForegroundColor Cyan }
function Ok($t)      { Write-Host "  [ok] $t" -ForegroundColor Green }
function Miss($t)    { Write-Host "  [--] $t" -ForegroundColor Yellow }

try {
    Section '1. Prerequisites'
    $missing = @()

    # --- Python: 3.12+ ---------------------------------------------------
    $python = $null
    foreach ($candidate in @('python', 'py')) {
        $cmd = Get-Command $candidate -ErrorAction SilentlyContinue
        if ($cmd) {
            $v = & $cmd.Source -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
            if ($v -and [version]$v -ge [version]'3.12') { $python = $cmd.Source; Ok "Python $v"; break }
        }
    }
    if (-not $python) { Miss 'Python 3.12+'; $missing += 'Python 3.12 or newer: https://www.python.org/downloads/' }

    # --- Node: Angular 22 needs ^22.22.3 || ^24.15 || >=26 ---------------
    $node = $null
    $nvm24 = Join-Path $env:LOCALAPPDATA 'nvm\v24.19.0\node.exe'
    if (Test-Path $nvm24) {
        $node = Split-Path -Parent $nvm24
        Ok "Node 24.19.0 (nvm)"
    } else {
        $cmd = Get-Command node -ErrorAction SilentlyContinue
        if ($cmd) {
            $v = (& $cmd.Source --version).TrimStart('v')
            if ([version]$v -ge [version]'22.22.3') { $node = Split-Path -Parent $cmd.Source; Ok "Node $v" }
            else { Miss "Node $v is too old for Angular 22"; $missing += 'Node 24: nvm install 24.19.0   (or https://nodejs.org)' }
        } else { Miss 'Node'; $missing += 'Node 24: https://nodejs.org  (or nvm install 24.19.0)' }
    }
    if ($node) { $env:PATH = "$node;$env:PATH" }

    # --- Docker: needed to run the real image, incl. Tesseract -----------
    if (Get-Command docker -ErrorAction SilentlyContinue) {
        try { & docker info --format '{{.ServerVersion}}' 2>$null | Out-Null; Ok 'Docker running' }
        catch { Miss 'Docker installed but not running'; $missing += 'Start Docker Desktop' }
    } else { Miss 'Docker'; $missing += 'Docker Desktop: https://www.docker.com/products/docker-desktop/' }

    # --- Optional ---------------------------------------------------------
    foreach ($opt in @(
        @{ n = 'java';      why = 'only to regenerate the API client (npm run api:refresh)' },
        @{ n = 'gcloud';    why = 'only to deploy to Google Cloud' },
        @{ n = 'firebase';  why = 'only to deploy to Google Cloud' },
        @{ n = 'tailscale'; why = 'only to share the app over a public URL' }
    )) {
        if (Get-Command $opt.n -ErrorAction SilentlyContinue) { Ok "$($opt.n) (optional)" }
        else { Miss "$($opt.n) — $($opt.why)" }
    }

    if ($missing.Count) {
        Write-Host ''
        Write-Host 'Install these first:' -ForegroundColor Red
        $missing | ForEach-Object { Write-Host "  - $_" }
        Write-Host ''
        throw 'Missing prerequisites.'
    }

    # --- Python environment ----------------------------------------------
    Section '2. Python environment'
    if (-not (Test-Path (Join-Path $repo '.venv'))) {
        & $python -m venv (Join-Path $repo '.venv')
        Ok 'created .venv'
    } else { Ok '.venv exists' }
    $venvPy = Join-Path $repo '.venv\Scripts\python.exe'
    & $venvPy -m pip install --quiet --upgrade pip
    & $venvPy -m pip install --quiet -r (Join-Path $repo 'requirements-dev.txt')
    Ok 'dependencies installed'

    # --- Client -----------------------------------------------------------
    Section '3. Client'
    Push-Location (Join-Path $repo 'web')
    try {
        & npm ci --no-audit --no-fund
        Ok 'npm ci'
        & npx ng build
        Ok 'client built'
    } finally { Pop-Location }

    # --- Verify -----------------------------------------------------------
    if (-not $SkipTests) {
        Section '4. Verify'
        & $venvPy -m pytest (Join-Path $repo 'tests') -q
        if ($LASTEXITCODE -ne 0) { throw 'Python tests failed.' }
        Ok 'Python tests pass'

        Push-Location (Join-Path $repo 'web')
        try {
            & npx ng test --watch=false
            if ($LASTEXITCODE -ne 0) { throw 'Angular tests failed.' }
            Ok 'Angular tests pass'
        } finally { Pop-Location }
    }

    # --- Done -------------------------------------------------------------
    Section 'Ready'
    $samples = Get-ChildItem (Join-Path $repo 'samples') -Filter *.pdf -ErrorAction SilentlyContinue
    if (-not $samples) {
        Write-Host '  No permit sets in samples/ — they are git-ignored on purpose.' -ForegroundColor Yellow
        Write-Host '  Copy them from:' -ForegroundColor Yellow
        Write-Host '    "G:\Shared drives\Meridian\Building Codes\Unreviewed Plans"'
        Write-Host ''
    } else {
        Ok "$($samples.Count) permit set(s) in samples/"
    }

    Write-Host @'
  START HERE — put the Rebuild Console on the Desktop, then use that:
      & ".\Rebuild Console.cmd" shortcut

  It opens a page with Pull, Rebuild and Publish as buttons, their output
  streaming into it, and a line saying whether the running container is on the
  commit in your working tree. From Git Bash instead:
      bash scripts/rebuild-console.sh

  What the buttons run, if you would rather run it by hand:
      powershell -ExecutionPolicy Bypass -File scripts\share.ps1
      bash scripts/share.sh                     # the same, from Git Bash

  Develop with hot reload instead (two processes, no container):
      powershell -ExecutionPolicy Bypass -File scripts\dev.ps1

  Both of those leave authentication OFF - anyone with the URL can use it. To
  require real sign-in without a Cloud Billing account, set FBC_PROJECT_ID and
  FBC_ALLOWED_EMAILS, run scripts\setup-auth.sh once, then add -Authenticated
  (or tick "Require sign-in" in the console). See section 0d of
  docs/DEPLOYMENT.md.

  PUBLISHING FROM A SECOND MACHINE — read this before running tunnel.ps1.
  fbc.omniflexfitness.com is served by one machine at a time. The named tunnel
  belongs to the Cloudflare account, but its credentials file sits on whichever
  machine created it, so tunnel.ps1 here will stop and say so rather than
  quietly fighting the other one. For a second machine use a hostname of its
  own:
      tailscale funnel 8060

  Deploy to Google Cloud (needs an open billing account):
      gcloud auth login; firebase login
      powershell -ExecutionPolicy Bypass -File scripts\provision.ps1

'@
} finally {
    Pop-Location
}
