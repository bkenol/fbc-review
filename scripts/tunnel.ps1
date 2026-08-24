<#
.SYNOPSIS
    Windows wrapper for scripts/tunnel.sh.

.DESCRIPTION
    Publishes the locally-running app at fbc.omniflexfitness.com through a
    Cloudflare Tunnel. Runs the tunnel script through Git Bash from the
    repository root, whatever directory you happen to be in.

    Start the app first with scripts\share.ps1 - this script only publishes
    something that is already listening.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\share.ps1
    powershell -ExecutionPolicy Bypass -File scripts\tunnel.ps1

.EXAMPLE
    # A different port, if share.ps1 was started on one:
    powershell -ExecutionPolicy Bypass -File scripts\tunnel.ps1 -Port 8070
#>
[CmdletBinding()]
param(
    [string]$Domain,
    [string]$Tunnel,
    [int]$Port
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot

$bash = Get-Command bash -ErrorAction SilentlyContinue
if (-not $bash) {
    foreach ($candidate in @(
        "$env:ProgramFiles\Git\bin\bash.exe",
        "${env:ProgramFiles(x86)}\Git\bin\bash.exe",
        "$env:LOCALAPPDATA\Programs\Git\bin\bash.exe"
    )) {
        if (Test-Path $candidate) { $bash = $candidate; break }
    }
} else {
    $bash = $bash.Source
}
if (-not $bash) { throw "Git Bash not found. Install Git for Windows, or run scripts/tunnel.sh from any POSIX shell." }

# Passed through as environment variables, which is what the script reads.
if ($Domain)       { $env:FBC_DOMAIN        = $Domain }
if ($Tunnel)       { $env:FBC_TUNNEL        = $Tunnel }
if ($Port)         { $env:FBC_PORT          = "$Port" }

Write-Host "Repository: $repo" -ForegroundColor DarkGray
Push-Location $repo
try {
    & $bash "./scripts/tunnel.sh"
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
