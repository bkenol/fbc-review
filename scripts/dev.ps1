<#
.SYNOPSIS
    Start the FBC Reviewer locally — API plus the Angular client.

.DESCRIPTION
    Runs the API with filesystem stand-ins for Firestore and Cloud Storage and
    the authentication bypass, so the whole thing works with no GCP project.

    That bypass cannot be switched on in a deployed service: webapp/config.py
    refuses the flag whenever K_SERVICE is set, and Cloud Run always sets it.

    Port 8060 rather than 8000 because on this machine 8000 falls inside a
    reserved Windows TCP exclusion range and cannot be bound. Check yours with
        netsh interface ipv4 show excludedportrange protocol=tcp

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\dev.ps1
#>
[CmdletBinding()]
param(
    [int]$ApiPort = 8060,
    [int]$WebPort = 4300,
    [string]$AllowedEmails = 'bertin.kenol@omniflexfitness.com'
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot

# Angular 22 needs Node >= 22.22.3 / 24.15. The machine default may be older,
# so prefer an nvm-installed 24 and fall back to whatever is on PATH.
$node24 = Join-Path $env:LOCALAPPDATA 'nvm\v24.19.0'
if (Test-Path $node24) {
    $env:PATH = "$node24;$env:PATH"
    Write-Host "Using Node from $node24" -ForegroundColor DarkGray
}

$python = Join-Path $repo '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) { $python = 'python' }

$env:FBC_DEV_UNSAFE_AUTH = '1'
$env:FBC_BUCKET          = 'fbc-dev-local'
$env:FBC_PROJECT_ID      = 'fbc-dev-local'
$env:FBC_ALLOWED_EMAILS  = $AllowedEmails
$env:PYTHONIOENCODING    = 'utf-8'

Write-Host ''
Write-Host 'Starting the API...' -ForegroundColor Cyan
$api = Start-Process -PassThru -NoNewWindow -FilePath $python `
    -ArgumentList @('-m', 'uvicorn', 'webapp.server:app',
                    '--host', '127.0.0.1', '--port', $ApiPort, '--log-level', 'warning') `
    -WorkingDirectory $repo

# Wait for it rather than guessing.
$deadline = (Get-Date).AddSeconds(60)
do {
    Start-Sleep -Milliseconds 500
    try {
        $health = Invoke-RestMethod "http://127.0.0.1:$ApiPort/healthz" -TimeoutSec 2
    } catch { $health = $null }
} while (-not $health -and (Get-Date) -lt $deadline)

if (-not $health) {
    Write-Host 'The API did not come up. Is the port free?' -ForegroundColor Red
    if (-not $api.HasExited) { Stop-Process -Id $api.Id -Force }
    exit 1
}
Write-Host "  API ready on http://127.0.0.1:$ApiPort  (v$($health.version))" -ForegroundColor Green

Write-Host ''
Write-Host 'Starting the client (first build takes ~20s)...' -ForegroundColor Cyan
Write-Host ''
Write-Host "  Open  http://127.0.0.1:$WebPort/" -ForegroundColor Yellow
Write-Host ''
Write-Host '  Ctrl+C stops the client; the API is stopped with it.' -ForegroundColor DarkGray
Write-Host ''

try {
    Push-Location (Join-Path $repo 'web')
    & npx ng serve --host 127.0.0.1 --port $WebPort --proxy-config proxy.conf.json
} finally {
    Pop-Location
    if ($api -and -not $api.HasExited) {
        Stop-Process -Id $api.Id -Force
        Write-Host 'API stopped.' -ForegroundColor DarkGray
    }
}
