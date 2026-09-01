<#
.SYNOPSIS
  Create secrets\local.env, and say what is still missing.

.DESCRIPTION
  Mail and the comment assist are both inert without their keys, and both are
  inert *quietly* - a review still runs, feedback still queues, and the only
  place that says otherwise is /admin. This is the thing you run to find out
  where you actually stand, before wondering why no mail arrived.

  It reads the file to see which names have values. It never prints a value.

.PARAMETER Check
  Report only. Creates nothing and changes nothing.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\setup-secrets.ps1
#>
[CmdletBinding()]
param([switch]$Check)

$ErrorActionPreference = 'Stop'

$repo = Split-Path -Parent $PSScriptRoot
$envFile = Join-Path $repo 'secrets\local.env'
$template = Join-Path $repo 'secrets\local.env.example'

function Write-Bold($text) { Write-Host $text -ForegroundColor White }
function Write-Ok($text) { Write-Host "  ok    $text" -ForegroundColor Green }
function Write-Miss($text) { Write-Host "  miss  $text" -ForegroundColor Yellow }
function Write-Info($text) { Write-Host "        $text" -ForegroundColor DarkGray }

# ── the file ──────────────────────────────────────────────────────────────
if (-not (Test-Path $envFile)) {
    if ($Check) {
        Write-Bold 'No secrets\local.env'
        Write-Info 'Run scripts\setup-secrets.ps1 (without -Check) to create it.'
        exit 1
    }
    if (-not (Test-Path $template)) { throw "No template at $template" }
    New-Item -ItemType Directory -Force -Path (Split-Path $envFile) | Out-Null
    Copy-Item $template $envFile
    Write-Bold 'Created secrets\local.env from the template.'
    Write-Info "Open it and fill in the blanks: $envFile"
    Write-Host ''
}

$lines = Get-Content $envFile

# Read with a regex rather than dot-sourced: this file is not a script, a value
# is literal, and running one would execute whatever a paste happened to hold.
function Get-Value($name) {
    $match = $lines |
        Where-Object { $_ -match "^\s*(export\s+)?$name=" } |
        Select-Object -Last 1
    if (-not $match) { return '' }
    return ($match -replace "^\s*(export\s+)?$name=", '').Trim()
}

function Test-Set($name) { return -not [string]::IsNullOrWhiteSpace((Get-Value $name)) }

Write-Bold 'secrets\local.env'
Write-Info $envFile
Write-Host ''

Write-Bold 'Mail'
$mailOk = $true
foreach ($name in @('FBC_SMTP_HOST', 'FBC_SMTP_USER', 'FBC_SMTP_PASS', 'FBC_MAIL_FROM')) {
    if (Test-Set $name) { Write-Ok $name } else { Write-Miss $name; $mailOk = $false }
}
if (Test-Set 'FBC_OWNER_EMAILS') {
    Write-Ok 'FBC_OWNER_EMAILS'
} else {
    Write-Miss 'FBC_OWNER_EMAILS'
    Write-Info 'Empty means nobody gets an escalation or the digest, and nobody can'
    Write-Info 'approve a calibration change. Mail can be configured without it, and'
    Write-Info 'then has nowhere to go.'
}
if ($mailOk) {
    Write-Info 'Mail will be live. Send yourself the digest from /admin to prove it.'
} else {
    Write-Info 'Mail stays inert: the review still runs and feedback still queues.'
    Write-Info 'For Google Workspace you need an App Password, not the account'
    Write-Info 'password: https://myaccount.google.com/apppasswords'
}
Write-Host ''

Write-Bold 'Comment assist'
if (Test-Set 'ANTHROPIC_API_KEY') {
    Write-Ok 'ANTHROPIC_API_KEY'
    Write-Info 'Free-text feedback comments will be summarised before they reach the'
    Write-Info 'queue. The review path is unaffected and still makes zero model calls.'
} else {
    Write-Miss 'ANTHROPIC_API_KEY'
    Write-Info 'Comments route to a person unread, which is what they did before the'
    Write-Info 'assist existed. Keys: https://console.anthropic.com/settings/keys'
}
Write-Host ''

Write-Bold 'Issues from escalated feedback'
if ((Test-Set 'FBC_GITHUB_REPO') -and (Test-Set 'FBC_GITHUB_TOKEN')) {
    Write-Ok 'FBC_GITHUB_REPO and FBC_GITHUB_TOKEN'
} else {
    Write-Miss 'FBC_GITHUB_REPO / FBC_GITHUB_TOKEN - optional'
}

$quoted = $lines | Where-Object { $_ -match '^\s*[A-Z_]+=(".*"|''.*'')\s*$' }
if ($quoted) {
    Write-Host ''
    Write-Bold 'Quoted values - these will not work'
    Write-Info 'Docker takes the quotes literally, so the value arrives with them'
    Write-Info 'attached. Remove the quotes; a value with spaces needs none.'
    foreach ($line in $quoted) { Write-Info (($line -replace '=.*', '=...')) }
}

Write-Host ''
Write-Bold 'Next'
Write-Info 'Restart the container so it reads the file:'
Write-Info "    powershell -File $repo\scripts\share.ps1"
Write-Info 'Then check what the server itself thinks at /admin.'
