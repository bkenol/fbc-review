<#
.SYNOPSIS
    Windows wrapper for scripts/provision.sh.

.DESCRIPTION
    Runs the provisioning script through Git Bash from the repository root,
    whatever directory you happen to be in. `bash scripts/provision.sh` only
    works if the shell is already inside the repo, which is an easy and
    unhelpful way to get "No such file or directory".

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\provision.ps1

.EXAMPLE
    # With a billing account, once you have an open one:
    powershell -ExecutionPolicy Bypass -File scripts\provision.ps1 -BillingAccount 0X0X0X-0X0X0X-0X0X0X
#>
[CmdletBinding()]
param(
    [string]$BillingAccount,
    [string]$ProjectId,
    [string]$Domain,
    [string]$AllowedEmails
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
if (-not $bash) { throw "Git Bash not found. Install Git for Windows, or run scripts/provision.sh from any POSIX shell." }

# Passed through as environment variables, which is what the script reads.
if ($BillingAccount) { $env:FBC_BILLING_ACCOUNT = $BillingAccount }
if ($ProjectId)      { $env:FBC_PROJECT_ID      = $ProjectId }
if ($Domain)         { $env:FBC_DOMAIN          = $Domain }
if ($AllowedEmails)  { $env:FBC_ALLOWED_EMAILS  = $AllowedEmails }

Write-Host "Repository: $repo" -ForegroundColor DarkGray
Push-Location $repo
try {
    & $bash "./scripts/provision.sh"
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
