<#
.SYNOPSIS
    Run the fbc-review Cloudflare tunnel as a Windows service, so
    fbc.omniflexfitness.com stays up with no window open and comes back with
    Windows.

.DESCRIPTION
    tunnel.ps1 serves the hostname only while its window is open. This installs
    the same tunnel as the Cloudflared Windows service instead, following
    Cloudflare's page for running a locally-managed tunnel as a Windows service:
    https://developers.cloudflare.com/tunnel/advanced/local-management/as-a-service/windows/

    Changing a service needs an administrator, and nothing else in the local
    deployment does. So this script runs as you, checks everything it can first,
    and only then asks Windows for approval (the usual UAC prompt) to do the one
    elevated step. Its output comes back into the window or console that ran it.
    Declining the prompt changes nothing.

    Install copies the tunnel's config and credentials from your
    .cloudflared folder into the system profile, where the service account can
    read them, points the copied config at -Port, and makes the service run
    `cloudflared --config=<that file> tunnel run` at startup. tunnel.ps1 has to
    have run once on this machine first: it writes <tunnel>.yml.

    Status needs no elevation and changes nothing.

.PARAMETER Action
    Install, Start, Stop, Uninstall or Status.

.PARAMETER Port
    The local port the app answers on. Install writes it into the service's
    config; run Install again after changing it.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\tunnel-service.ps1 -Action Install

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\tunnel-service.ps1 -Action Status
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('Install', 'Start', 'Stop', 'Uninstall', 'Status')]
    [string]$Action,
    [int]$Port = 8060,
    [string]$Tunnel = 'fbc-review',
    # Resolved before elevating, and passed through: an administrator approving
    # the prompt with another account's credentials has another USERPROFILE.
    [string]$SourceDir,
    [string]$Cloudflared,
    # Used by the elevated copy only: where it writes what it did.
    [string]$LogFile,
    [switch]$Elevated
)

$ErrorActionPreference = 'Stop'

if ($PSVersionTable.PSEdition -eq 'Core' -and -not $IsWindows) {
    Write-Host 'The tunnel service is a Windows service. Elsewhere, run scripts/tunnel.sh under your init system.'
    exit 2
}

$ServiceName = 'Cloudflared'
$SystemDir = Join-Path $env:SystemRoot 'System32\config\systemprofile\.cloudflared'
$SystemConfig = Join-Path $SystemDir 'config.yml'
$UuidPattern = '[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}'

function Say([string]$Text) {
    Write-Host $Text
    if ($LogFile) { Add-Content -LiteralPath $LogFile -Value $Text -Encoding UTF8 }
}

function Test-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    return ([Security.Principal.WindowsPrincipal]$id).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
}

# cloudflared logs to stderr. Under Windows PowerShell 5.1 with
# ErrorActionPreference Stop, `2>&1` turns its first stderr line into a
# terminating error, so the preference is relaxed for native calls only.
function Invoke-Native([string]$Exe, [string[]]$ArgList) {
    $ErrorActionPreference = 'Continue'
    $out = & $Exe @ArgList 2>&1
    foreach ($line in $out) { Say ('  ' + $line) }
    return $LASTEXITCODE
}

function Find-Cloudflared {
    $found = Get-Command cloudflared.exe -ErrorAction SilentlyContinue
    if ($found) { return $found.Source }
    foreach ($candidate in @(
        "${env:ProgramFiles(x86)}\cloudflared\cloudflared.exe",
        "$env:ProgramFiles\cloudflared\cloudflared.exe",
        'C:\Cloudflared\bin\cloudflared.exe'
    )) {
        if ($candidate -and (Test-Path -LiteralPath $candidate)) { return $candidate }
    }
    return $null
}

# The tunnel's UUID, read from the config tunnel.ps1 wrote, so nothing here
# hard-codes which tunnel this machine serves.
function Get-TunnelUuid([string]$ConfigPath) {
    foreach ($line in (Get-Content -LiteralPath $ConfigPath)) {
        $m = [regex]::Match($line, "^\s*tunnel:\s*'?($UuidPattern)'?\s*$")
        if ($m.Success) { return $m.Groups[1].Value }
    }
    return $null
}

function Show-Status {
    $svc = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
    if ($svc) {
        Say ("Cloudflared service: {0} (starts {1})" -f $svc.Status, $svc.StartType)
    } else {
        Say 'Cloudflared service: not installed'
    }
}

if ($Action -eq 'Status') {
    Show-Status
    exit 0
}

if (-not $SourceDir) { $SourceDir = Join-Path $env:USERPROFILE '.cloudflared' }
if (-not $Cloudflared) { $Cloudflared = Find-Cloudflared }

# -- checks that need no administrator --
# Done before the prompt, so nobody approves a UAC prompt for a job that was
# always going to fail.
$TunnelConfig = Join-Path $SourceDir "$Tunnel.yml"
if ($Action -eq 'Install') {
    if (-not $Cloudflared) {
        Say 'cloudflared was not found. Install it, open a new window, and try again:'
        Say '  winget install --id Cloudflare.cloudflared'
        exit 2
    }
    if (-not (Test-Path -LiteralPath $TunnelConfig)) {
        Say "There is no $Tunnel.yml in $SourceDir."
        Say 'Run Publish - Cloudflare (scripts\tunnel.ps1) once on this machine first;'
        Say 'it writes that file. Then Install again.'
        exit 2
    }
    $uuid = Get-TunnelUuid $TunnelConfig
    if (-not $uuid) {
        Say "$TunnelConfig has no tunnel: line with a tunnel id. Run scripts\tunnel.ps1 once to rewrite it."
        exit 2
    }
    $credentials = Join-Path $SourceDir "$uuid.json"
    if (-not (Test-Path -LiteralPath $credentials)) {
        Say "The credentials for tunnel $uuid are not in $SourceDir, so this machine"
        Say 'cannot serve it. That file only exists on the machine that created the tunnel.'
        exit 2
    }
}
if ($Action -in @('Start', 'Stop') -and -not (Get-Service -Name $ServiceName -ErrorAction SilentlyContinue)) {
    Say 'The Cloudflared service is not installed. Install it first.'
    exit 2
}
if ($Action -eq 'Uninstall' -and -not (Get-Service -Name $ServiceName -ErrorAction SilentlyContinue)) {
    Say 'The Cloudflared service is not installed; nothing to remove.'
    exit 0
}

# -- elevate for the one step that needs it --
if (-not (Test-Admin)) {
    if ($Elevated) {
        Say 'Still not running as an administrator after elevating. Nothing changed.'
        exit 1
    }
    $log = Join-Path ([IO.Path]::GetTempPath()) ('fbc-tunnel-service-{0}.log' -f [guid]::NewGuid().ToString('N'))
    $argLine = ('-NoProfile -ExecutionPolicy Bypass -File "{0}" -Action {1} -Port {2} -Tunnel "{3}" -SourceDir "{4}" -LogFile "{5}" -Elevated' -f
        $PSCommandPath, $Action, $Port, $Tunnel, $SourceDir, $log)
    if ($Cloudflared) { $argLine += (' -Cloudflared "{0}"' -f $Cloudflared) }
    Write-Host "$Action the Cloudflared service: asking Windows for administrator approval."
    Write-Host 'Look for the UAC prompt; it may be behind this window.'
    try {
        $proc = Start-Process -FilePath 'powershell.exe' -ArgumentList $argLine -Verb RunAs `
            -WindowStyle Hidden -Wait -PassThru
    } catch {
        Write-Host 'Not approved. The administrator prompt was declined or closed, so nothing changed.'
        exit 1223
    }
    if (Test-Path -LiteralPath $log) {
        Get-Content -LiteralPath $log | ForEach-Object { Write-Host $_ }
        Remove-Item -LiteralPath $log -Force -ErrorAction SilentlyContinue
    }
    exit $proc.ExitCode
}

# -- elevated from here on --
try {
    switch ($Action) {
        'Install' {
            $uuid = Get-TunnelUuid $TunnelConfig
            $credentials = Join-Path $SourceDir "$uuid.json"
            $systemCredentials = Join-Path $SystemDir "$uuid.json"

            New-Item -ItemType Directory -Force -Path $SystemDir | Out-Null
            Copy-Item -LiteralPath $credentials -Destination $systemCredentials -Force
            # The copied config points at the copied credentials, so the
            # service never depends on a user profile being readable, and at
            # -Port, so a changed port takes effect by installing again.
            $rewritten = foreach ($line in (Get-Content -LiteralPath $TunnelConfig)) {
                if ($line -match '^\s*credentials-file:') {
                    "credentials-file: '$systemCredentials'"
                } else {
                    [regex]::Replace($line, '127\.0\.0\.1:\d+', "127.0.0.1:$Port")
                }
            }
            Set-Content -LiteralPath $SystemConfig -Value $rewritten -Encoding ASCII
            Say "Wrote $SystemConfig (tunnel $uuid, 127.0.0.1:$Port)"

            if ((Invoke-Native $Cloudflared @('tunnel', '--config', $SystemConfig, 'ingress', 'validate')) -ne 0) {
                Say 'cloudflared rejected the copied config. The service was not changed.'
                exit 1
            }

            if (-not (Get-Service -Name $ServiceName -ErrorAction SilentlyContinue)) {
                Say 'Installing the Cloudflared service:'
                Invoke-Native $Cloudflared @('service', 'install') | Out-Null
                if (-not (Get-Service -Name $ServiceName -ErrorAction SilentlyContinue)) {
                    Say 'cloudflared did not create the service. Its output is above.'
                    exit 1
                }
            }
            # What Cloudflare's page has you set by hand in regedit.
            $image = '"{0}" --config="{1}" tunnel run' -f $Cloudflared, $SystemConfig
            Set-ItemProperty -Path "HKLM:\SYSTEM\CurrentControlSet\Services\$ServiceName" -Name ImagePath -Value $image
            Set-Service -Name $ServiceName -StartupType Automatic
            Restart-Service -Name $ServiceName -Force
            Start-Sleep -Seconds 3
            Show-Status
            Say 'fbc.omniflexfitness.com is now served by the service, with or without the console open.'
        }
        'Start' {
            Start-Service -Name $ServiceName
            Start-Sleep -Seconds 2
            Show-Status
        }
        'Stop' {
            Stop-Service -Name $ServiceName -Force
            Show-Status
            Say 'The hostname shows Cloudflare error 1033 until the tunnel runs again.'
        }
        'Uninstall' {
            $svc = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
            if ($svc -and $svc.Status -ne 'Stopped') { Stop-Service -Name $ServiceName -Force }
            if ($Cloudflared) {
                Invoke-Native $Cloudflared @('service', 'uninstall') | Out-Null
            } else {
                Invoke-Native 'sc.exe' @('delete', $ServiceName) | Out-Null
            }
            # The copied credentials are account credentials; they do not stay
            # behind in the system profile once nothing uses them.
            if (Test-Path -LiteralPath $SystemConfig) { Remove-Item -LiteralPath $SystemConfig -Force }
            Get-ChildItem -LiteralPath $SystemDir -Filter '*.json' -ErrorAction SilentlyContinue |
                Where-Object { $_.BaseName -match "^$UuidPattern$" } |
                Remove-Item -Force
            Show-Status
        }
    }
    exit 0
} catch {
    Say ('Failed: ' + $_.Exception.Message)
    exit 1
}
