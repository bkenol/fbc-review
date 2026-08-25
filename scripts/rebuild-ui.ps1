<#
.SYNOPSIS
    A small window with buttons: pull, rebuild, publish.

.DESCRIPTION
    The three commands that take this desktop deployment from whatever it is
    running now to the tip of main, live at fbc.omniflexfitness.com — as buttons
    rather than three things to remember and type in the right order.

    It is a launcher, not a runner. Each button starts the existing script in its
    own PowerShell window, so the real output stays visible and Ctrl-C still
    works where it needs to: tunnel.ps1 runs in the foreground by design and has
    to keep its own window. Nothing here reimplements what share.ps1 or
    tunnel.ps1 do, so there is no second copy of the procedure to drift.

    WinForms rather than a local web server on purpose. A server on 127.0.0.1
    that runs commands is reachable by any page open in any browser on this
    machine, which is a real cross-site request forgery surface for something
    whose entire job is executing scripts. A window has no such surface.

    NO ADMINISTRATOR RIGHTS ARE NEEDED. If Windows prompts for elevation,
    something else is going on — nothing here requires it. Docker Desktop does
    have to be running, which is a separate matter.

    The status strip polls /healthz and compares the version the container
    reports against the commit in the working tree, so "did my rebuild take?"
    is answered on the window instead of by reading a curl response.

.PARAMETER Repo
    Repository root. Defaults to the parent of this script's own directory,
    which is correct whenever the script is left where it lives.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File C:\Antigravity\fbc-review\scripts\rebuild-ui.ps1
#>
[CmdletBinding()]
param(
    [string]$Repo,
    [int]$Port = 8060,
    [int]$MaxUploadMb = 95,
    [switch]$Persistent
)

$ErrorActionPreference = 'Stop'

if (-not $Repo) { $Repo = Split-Path -Parent $PSScriptRoot }
if (-not (Test-Path (Join-Path $Repo 'scripts\share.ps1'))) {
    throw "No scripts\share.ps1 under '$Repo'. Pass -Repo with the repository root."
}

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()

# ── Meridian, as far as WinForms will carry it ────────────────────────────
# The system's own ink ramp and accents, from web/src/styles/meridian/tokens.
$INK1   = [System.Drawing.Color]::FromArgb(0x16, 0x19, 0x1C)
$INK3   = [System.Drawing.Color]::FromArgb(0x55, 0x5B, 0x61)
$INK4   = [System.Drawing.Color]::FromArgb(0x8B, 0x92, 0x99)
$PAPER1 = [System.Drawing.Color]::FromArgb(0xEE, 0xF1, 0xF5)
$WHITE  = [System.Drawing.Color]::FromArgb(0xFF, 0xFF, 0xFF)
$EDGE   = [System.Drawing.Color]::FromArgb(0xD3, 0xDA, 0xE2)
$BLUE   = [System.Drawing.Color]::FromArgb(0x1F, 0x4E, 0x9C)
$RED    = [System.Drawing.Color]::FromArgb(0xB3, 0x25, 0x1E)
$GREEN  = [System.Drawing.Color]::FromArgb(0x1E, 0x6B, 0x45)
$OCHRE  = [System.Drawing.Color]::FromArgb(0x8A, 0x5A, 0x12)

function New-UiFont([string]$Family, [single]$Size, [string]$Style) {
    # IBM Plex is the system's face and is not installed by default; Segoe UI
    # and Consolas are the closest things Windows always has. Falls back again
    # if even the named family is missing, so this can never throw at startup.
    $fs = [System.Drawing.FontStyle]::$Style
    try { New-Object System.Drawing.Font($Family, $Size, $fs) }
    catch { New-Object System.Drawing.Font('Segoe UI', $Size, $fs) }
}
$FontTitle = New-UiFont 'Segoe UI' 15 'Bold'
$FontBody  = New-UiFont 'Segoe UI'  9 'Regular'
$FontBtn   = New-UiFont 'Segoe UI' 10 'Bold'
$FontMono  = New-UiFont 'Consolas'  9 'Regular'
$FontLabel = New-UiFont 'Consolas'  8 'Regular'

# ── window ────────────────────────────────────────────────────────────────
# ClientSize, not Size: Size includes the border and title bar, and laying out
# absolute positions against it puts the last control under the bottom edge.
# Fixed, because an absolute layout has nothing to reflow.
$form = New-Object System.Windows.Forms.Form
$form.Text = 'Meridian - Rebuild Console'
$form.ClientSize = New-Object System.Drawing.Size(544, 480)
$form.FormBorderStyle = 'FixedSingle'
$form.MaximizeBox = $false
$form.StartPosition = 'CenterScreen'
$form.BackColor = $PAPER1
$form.Font = $FontBody

function Add-Label($Text, $X, $Y, $W, $H, $Font, $Color) {
    $l = New-Object System.Windows.Forms.Label
    $l.Text = $Text
    $l.Location = New-Object System.Drawing.Point($X, $Y)
    $l.Size = New-Object System.Drawing.Size($W, $H)
    $l.Font = $Font
    $l.ForeColor = $Color
    $l.BackColor = [System.Drawing.Color]::Transparent
    $form.Controls.Add($l)
    $l
}

Add-Label 'MERIDIAN  ·  REBUILD CONSOLE' 24 18 400 18 $FontLabel $INK3 | Out-Null
Add-Label 'Pull, rebuild, publish.'      24 38 400 30 $FontTitle $INK1 | Out-Null
Add-Label $Repo                          24 72 496 18 $FontLabel $INK4 | Out-Null

# ── actions ───────────────────────────────────────────────────────────────
function Add-Action($Caption, $Blurb, $Y, $Accent) {
    $panel = New-Object System.Windows.Forms.Panel
    $panel.Location = New-Object System.Drawing.Point(24, $Y)
    $panel.Size = New-Object System.Drawing.Size(496, 56)
    $panel.BackColor = $WHITE
    $form.Controls.Add($panel)

    $b = New-Object System.Windows.Forms.Button
    $b.Text = $Caption
    $b.Location = New-Object System.Drawing.Point(10, 11)
    $b.Size = New-Object System.Drawing.Size(150, 34)
    $b.FlatStyle = 'Flat'
    $b.FlatAppearance.BorderSize = 1
    $b.FlatAppearance.BorderColor = $Accent
    $b.BackColor = $Accent
    $b.ForeColor = $WHITE
    $b.Font = $FontBtn
    $b.Cursor = [System.Windows.Forms.Cursors]::Hand
    $panel.Controls.Add($b)

    $t = New-Object System.Windows.Forms.Label
    $t.Text = $Blurb
    $t.Location = New-Object System.Drawing.Point(172, 12)
    $t.Size = New-Object System.Drawing.Size(314, 34)
    $t.Font = $FontBody
    $t.ForeColor = $INK3
    $panel.Controls.Add($t)

    $b
}

$btnAll    = Add-Action 'Pull + Rebuild' "The usual path. Fetches the latest code,`r`nthen rebuilds and restarts the container." 104 $RED
$btnPull   = Add-Action 'Pull only'      "Fetch the latest code.`r`nNothing restarts."                                          168 $BLUE
$btnBuild  = Add-Action 'Rebuild only'   "Client, image and container.`r`nTakes a few minutes."                                 232 $BLUE
$btnTunnel = Add-Action 'Publish'        "Only if the tunnel window is closed.`r`nIt survives a container restart."             296 $BLUE

# ── status ────────────────────────────────────────────────────────────────
$sep = New-Object System.Windows.Forms.Label
$sep.Location = New-Object System.Drawing.Point(24, 368)
$sep.Size = New-Object System.Drawing.Size(496, 1)
$sep.BackColor = $EDGE
$form.Controls.Add($sep)

$headLabel = Add-Label 'Working tree - checking...' 24 380 410 16 $FontLabel $INK3
$liveLabel = Add-Label 'Container - checking...'    24 400 410 16 $FontLabel $INK3
$verdict   = Add-Label ''                           24 422 496 20 $FontMono  $INK4

$btnRefresh = New-Object System.Windows.Forms.Button
$btnRefresh.Text = 'Refresh'
$btnRefresh.Location = New-Object System.Drawing.Point(444, 378)
$btnRefresh.Size = New-Object System.Drawing.Size(76, 26)
$btnRefresh.FlatStyle = 'Flat'
$btnRefresh.FlatAppearance.BorderColor = $EDGE
$btnRefresh.BackColor = $WHITE
$btnRefresh.ForeColor = $INK3
$btnRefresh.Cursor = [System.Windows.Forms.Cursors]::Hand
$form.Controls.Add($btnRefresh)

$linkSite = New-Object System.Windows.Forms.LinkLabel
$linkSite.Text = 'Open fbc.omniflexfitness.com'
$linkSite.Location = New-Object System.Drawing.Point(24, 450)
$linkSite.Size = New-Object System.Drawing.Size(220, 18)
$linkSite.LinkColor = $BLUE
$linkSite.ActiveLinkColor = $RED
$linkSite.Font = $FontBody
$form.Controls.Add($linkSite)

$linkLocal = New-Object System.Windows.Forms.LinkLabel
$linkLocal.Text = "Open 127.0.0.1:$Port"
$linkLocal.Location = New-Object System.Drawing.Point(256, 450)
$linkLocal.Size = New-Object System.Drawing.Size(180, 18)
$linkLocal.LinkColor = $BLUE
$linkLocal.ActiveLinkColor = $RED
$linkLocal.Font = $FontBody
$form.Controls.Add($linkLocal)

# ── behaviour ─────────────────────────────────────────────────────────────

# Every action opens its own console and leaves it open, so a failure is still
# on screen afterwards rather than vanishing with the window.
function Start-Console([string]$Command) {
    Start-Process -FilePath 'powershell.exe' -WorkingDirectory $Repo -ArgumentList @(
        '-NoExit', '-ExecutionPolicy', 'Bypass', '-Command', $Command
    ) | Out-Null
}

# Quoted, because a repository path may contain spaces even though this one
# does not.
function Get-ShareCommand {
    $cmd = "& powershell -ExecutionPolicy Bypass -File '$(Join-Path $Repo 'scripts\share.ps1')'"
    if ($Port -ne 8060)      { $cmd += " -Port $Port" }
    if ($MaxUploadMb -ne 95) { $cmd += " -MaxUploadMb $MaxUploadMb" }
    if ($Persistent)         { $cmd += ' -Persistent' }
    $cmd
}

$pullCommand = "git -C '$Repo' pull"

$btnPull.Add_Click({
    Start-Console "$pullCommand; Write-Host ''; Write-Host 'Pull finished.' -ForegroundColor Green"
})
$btnBuild.Add_Click({ Start-Console (Get-ShareCommand) })
$btnTunnel.Add_Click({
    Start-Console "& powershell -ExecutionPolicy Bypass -File '$(Join-Path $Repo 'scripts\tunnel.ps1')'"
})
$btnAll.Add_Click({
    # One window, both steps in order, so the rebuild starts only if the pull
    # succeeded rather than racing it in a second console.
    Start-Console "$pullCommand; if (`$LASTEXITCODE -eq 0) { $(Get-ShareCommand) } else { Write-Host 'Pull failed - not rebuilding.' -ForegroundColor Red }"
})
$linkSite.Add_LinkClicked({ Start-Process 'https://fbc.omniflexfitness.com' })
$linkLocal.Add_LinkClicked({ Start-Process "http://127.0.0.1:$Port/" })

# ── status ────────────────────────────────────────────────────────────────
function Get-TreeState {
    # The same `git describe` webapp/version.py stamps with, so the two strings
    # are comparable without reimplementing the scheme here.
    try {
        $described = & git -C $Repo describe --always --dirty --abbrev=7 --match= 2>$null
        if ($LASTEXITCODE -ne 0 -or -not $described) { return $null }
        return ([string]$described).Trim()
    } catch { return $null }
}

function Get-ContainerVersion {
    # One second, because this runs on the UI thread: a longer timeout would
    # freeze the window for that long every time the container is mid-restart.
    try { (Invoke-RestMethod "http://127.0.0.1:$Port/healthz" -TimeoutSec 1).version }
    catch { $null }
}

function Update-Status {
    $tree = Get-TreeState
    $live = Get-ContainerVersion

    if ($tree) {
        $headLabel.Text = "Working tree - $tree"
        $headLabel.ForeColor = $INK3
    } else {
        $headLabel.Text = 'Working tree - git unavailable'
        $headLabel.ForeColor = $OCHRE
    }

    if (-not $live) {
        $liveLabel.Text = "Container - nothing on 127.0.0.1:$Port"
        $liveLabel.ForeColor = $INK4
        $verdict.Text = 'Not running. Press Pull + Rebuild.'
        $verdict.ForeColor = $INK4
        return
    }

    $liveLabel.Text = "Container - $live"
    $liveLabel.ForeColor = $INK3

    # Compare only the metadata the local stamp carries. Deliberately not
    # rebuilt from a hardcoded "1.0.0-alpha": the base moves when VERSION is
    # promoted to beta, and this comparison must not care.
    if ($live -notmatch '\+local\.(.+)$') {
        $verdict.Text = 'No local stamp - cannot tell which commit is running.'
        $verdict.ForeColor = $OCHRE
        return
    }

    $liveTree = $Matches[1]
    $treeNorm = if ($tree) { $tree -replace '-', '.' } else { $null }

    if ($treeNorm -and $liveTree -eq $treeNorm) {
        if ($liveTree -match '\.dirty$') {
            $verdict.Text = 'LIVE - matches the tree, which has uncommitted changes.'
            $verdict.ForeColor = $OCHRE
        } else {
            $verdict.Text = 'LIVE - this is the commit in your working tree.'
            $verdict.ForeColor = $GREEN
        }
    } else {
        $verdict.Text = 'STALE - container is on another commit. Rebuild.'
        $verdict.ForeColor = $RED
    }
}

$btnRefresh.Add_Click({ Update-Status })

# Polled rather than left to the Refresh button: a rebuild takes minutes, and
# the window should notice the new container coming up on its own.
$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = 6000
$timer.Add_Tick({ Update-Status })
$timer.Start()

$form.Add_Shown({ Update-Status })
$form.Add_FormClosed({ $timer.Stop(); $timer.Dispose() })

[void]$form.ShowDialog()
