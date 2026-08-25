<#
.SYNOPSIS
    A window with buttons: pull, rebuild, publish. Output lands in the window.

.DESCRIPTION
    The three commands that take this desktop deployment from whatever it is
    running now to the tip of main, live at fbc.omniflexfitness.com — as buttons,
    with the output of each streamed into the console pane below them. Nothing to
    copy, nothing to paste, and no second PowerShell window to arrange.

    HOW IT RUNS THINGS
    Each action starts powershell.exe with no window and its output redirected to
    a temporary file; a timer drains that file into the pane four times a second.
    Redirect-and-poll rather than the OutputDataReceived event on purpose: that
    event fires on a background thread, and touching a WinForms control from one
    throws unless every write is marshalled back. Polling a file keeps every line
    of this script on the UI thread, which is one whole class of bug that cannot
    happen.

    Two slots run independently, because the tunnel is long-lived and you need it
    up while a rebuild happens:
      - a task slot for pull and rebuild, which run to completion
      - a tunnel slot, which stays up until stopped
    Both write into the one pane, each fenced by a rule naming what started.

    Stopping the tunnel kills the whole process tree. tunnel.ps1 runs tunnel.sh
    under Git Bash, which runs cloudflared — killing only the top process would
    leave cloudflared serving with nothing owning it.

    NO ADMINISTRATOR RIGHTS ARE NEEDED. If Windows prompts for elevation,
    something else is going on. Docker Desktop does have to be running, which is
    a separate matter.

    The status strip polls /healthz and compares what the container reports with
    `git describe` on the working tree, so "did my rebuild take?" is answered on
    the window rather than by reading a curl response.

.PARAMETER Repo
    Repository root. Defaults to the parent of this script's own directory, which
    is correct whenever the script is left where it lives.

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
$INK1   = [System.Drawing.Color]::FromArgb(0x16, 0x19, 0x1C)
$INK3   = [System.Drawing.Color]::FromArgb(0x55, 0x5B, 0x61)
$INK4   = [System.Drawing.Color]::FromArgb(0x8B, 0x92, 0x99)
$INK5   = [System.Drawing.Color]::FromArgb(0xB9, 0xC0, 0xC7)
$PAPER1 = [System.Drawing.Color]::FromArgb(0xEE, 0xF1, 0xF5)
$WHITE  = [System.Drawing.Color]::FromArgb(0xFF, 0xFF, 0xFF)
$EDGE   = [System.Drawing.Color]::FromArgb(0xD3, 0xDA, 0xE2)
$BLUE   = [System.Drawing.Color]::FromArgb(0x1F, 0x4E, 0x9C)
$RED    = [System.Drawing.Color]::FromArgb(0xB3, 0x25, 0x1E)
$GREEN  = [System.Drawing.Color]::FromArgb(0x1E, 0x6B, 0x45)
$OCHRE  = [System.Drawing.Color]::FromArgb(0x8A, 0x5A, 0x12)

function New-UiFont([string]$Family, [single]$Size, [string]$Style) {
    # IBM Plex is the system's face and is not installed by default; Segoe UI
    # and Consolas are the closest things Windows always has.
    $fs = [System.Drawing.FontStyle]::$Style
    try { New-Object System.Drawing.Font($Family, $Size, $fs) }
    catch { New-Object System.Drawing.Font('Segoe UI', $Size, $fs) }
}
$FontTitle = New-UiFont 'Segoe UI' 15 'Bold'
$FontBody  = New-UiFont 'Segoe UI'  9 'Regular'
$FontBtn   = New-UiFont 'Segoe UI'  9 'Bold'
$FontMono  = New-UiFont 'Consolas'  9 'Regular'
$FontLabel = New-UiFont 'Consolas'  8 'Regular'

# ── state ─────────────────────────────────────────────────────────────────
# script: scoped throughout. A scriptblock handler that assigns to a bare name
# creates a local and the assignment is silently lost when it returns.
$script:taskProc     = $null
$script:taskLabel    = ''
$script:tunnelProc   = $null
$script:statusTick   = 0

$stamp = [System.IO.Path]::GetRandomFileName().Substring(0, 8)
$script:taskOut   = Join-Path $env:TEMP "fbc-task-$stamp.log"
$script:tunnelOut = Join-Path $env:TEMP "fbc-tunnel-$stamp.log"
$script:taskOffset   = [int64]0
$script:tunnelOffset = [int64]0

# ── window ────────────────────────────────────────────────────────────────
$form = New-Object System.Windows.Forms.Form
$form.Text = 'Meridian - Rebuild Console'
$form.ClientSize = New-Object System.Drawing.Size(780, 640)
$form.MinimumSize = New-Object System.Drawing.Size(700, 560)
$form.StartPosition = 'CenterScreen'
$form.BackColor = $PAPER1
$form.Font = $FontBody

function Add-Text($Text, $X, $Y, $W, $H, $Font, $Color) {
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

Add-Text 'MERIDIAN  ·  REBUILD CONSOLE' 20 16 400 16 $FontLabel $INK3 | Out-Null
Add-Text 'Pull, rebuild, publish.'      20 34 400 28 $FontTitle $INK1 | Out-Null
Add-Text $Repo                          20 64 500 16 $FontLabel $INK4 | Out-Null

function Add-Btn($Text, $X, $Y, $W, $Accent, $Filled) {
    $b = New-Object System.Windows.Forms.Button
    $b.Text = $Text
    $b.Location = New-Object System.Drawing.Point($X, $Y)
    $b.Size = New-Object System.Drawing.Size($W, 32)
    $b.FlatStyle = 'Flat'
    $b.FlatAppearance.BorderSize = 1
    $b.FlatAppearance.BorderColor = $Accent
    $b.Font = $FontBtn
    $b.Cursor = [System.Windows.Forms.Cursors]::Hand
    if ($Filled) { $b.BackColor = $Accent; $b.ForeColor = $WHITE }
    else         { $b.BackColor = $WHITE;  $b.ForeColor = $Accent }
    $form.Controls.Add($b)
    $b
}

$btnAll    = Add-Btn 'Pull + Rebuild' 20  92 132 $RED  $true
$btnPull   = Add-Btn 'Pull only'      160 92  86 $BLUE $false
$btnBuild  = Add-Btn 'Rebuild only'   254 92 100 $BLUE $false
$btnTunnel = Add-Btn 'Publish tunnel' 362 92 120 $BLUE $false
$btnCancel = Add-Btn 'Cancel'         680 92  80 $INK4 $false

# ── status strip ──────────────────────────────────────────────────────────
$sep = New-Object System.Windows.Forms.Label
$sep.Location = New-Object System.Drawing.Point(20, 138)
$sep.Size = New-Object System.Drawing.Size(740, 1)
$sep.BackColor = $EDGE
$form.Controls.Add($sep)

$lblTree    = Add-Text 'Working tree - checking...' 20 148 480 15 $FontLabel $INK3
$lblLive    = Add-Text 'Container - checking...'    20 166 480 15 $FontLabel $INK3
$lblTunnel  = Add-Text 'Tunnel - not started'       20 184 480 15 $FontLabel $INK4
$lblVerdict = Add-Text ''                           20 210 740 18 $FontMono  $INK4

$btnRefresh = Add-Btn 'Refresh' 680 148 80 $EDGE $false
$btnRefresh.ForeColor = $INK3
$btnRefresh.Size = New-Object System.Drawing.Size(80, 26)

$btnClear = Add-Btn 'Clear log' 680 180 80 $EDGE $false
$btnClear.ForeColor = $INK3
$btnClear.Size = New-Object System.Drawing.Size(80, 26)

# ── console pane ──────────────────────────────────────────────────────────
$console = New-Object System.Windows.Forms.TextBox
$console.Location = New-Object System.Drawing.Point(20, 234)
$console.Size = New-Object System.Drawing.Size(740, 362)
$console.Multiline = $true
$console.ReadOnly = $true
$console.ScrollBars = 'Both'
$console.WordWrap = $false
$console.BackColor = $INK1
$console.ForeColor = $INK5
$console.Font = $FontMono
$console.Anchor = 'Top,Left,Bottom,Right'
$console.BorderStyle = 'FixedSingle'
$form.Controls.Add($console)

$linkSite = New-Object System.Windows.Forms.LinkLabel
$linkSite.Text = 'Open fbc.omniflexfitness.com'
$linkSite.Location = New-Object System.Drawing.Point(20, 608)
$linkSite.Size = New-Object System.Drawing.Size(210, 16)
$linkSite.LinkColor = $BLUE
$linkSite.ActiveLinkColor = $RED
$linkSite.Anchor = 'Bottom,Left'
$form.Controls.Add($linkSite)

$linkLocal = New-Object System.Windows.Forms.LinkLabel
$linkLocal.Text = "Open 127.0.0.1:$Port"
$linkLocal.Location = New-Object System.Drawing.Point(240, 608)
$linkLocal.Size = New-Object System.Drawing.Size(170, 16)
$linkLocal.LinkColor = $BLUE
$linkLocal.ActiveLinkColor = $RED
$linkLocal.Anchor = 'Bottom,Left'
$form.Controls.Add($linkLocal)

# ── console plumbing ──────────────────────────────────────────────────────
function Write-Console([string]$Text) {
    if (-not $Text) { return }
    $console.AppendText(($Text -replace "`r`n", "`n") -replace "`n", "`r`n")
}

function Write-Rule([string]$Caption) {
    $bar = '-' * [Math]::Max(4, 68 - $Caption.Length)
    Write-Console "`r`n-- $Caption $bar`r`n"
}

# Opened with FileShare.ReadWrite because the child still has the file open;
# anything less throws "in use by another process" on the first poll.
function Read-Tail([string]$Path, [ref]$Offset) {
    if (-not (Test-Path -LiteralPath $Path)) { return '' }
    try {
        $fs = [System.IO.File]::Open(
            $Path,
            [System.IO.FileMode]::Open,
            [System.IO.FileAccess]::Read,
            [System.IO.FileShare]::ReadWrite)
    } catch { return '' }
    try {
        if ($fs.Length -le $Offset.Value) { return '' }
        [void]$fs.Seek($Offset.Value, [System.IO.SeekOrigin]::Begin)
        $count = [int]($fs.Length - $Offset.Value)
        $buf = New-Object byte[] $count
        $read = $fs.Read($buf, 0, $count)
        $Offset.Value = $fs.Position
        return [System.Text.Encoding]::UTF8.GetString($buf, 0, $read)
    } catch { return '' } finally { $fs.Dispose() }
}

# The child is told to emit UTF-8 so the decode above is not a guess.
function Start-Hidden([string]$Command, [string]$OutFile) {
    $prelude = '$OutputEncoding = [Console]::OutputEncoding = [System.Text.Encoding]::UTF8; '
    Start-Process -FilePath 'powershell.exe' -WorkingDirectory $Repo -PassThru -NoNewWindow `
        -RedirectStandardOutput $OutFile `
        -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command', ($prelude + $Command))
}

# taskkill /T, because tunnel.ps1 runs tunnel.sh under Git Bash which runs
# cloudflared: stopping only the top process orphans the one actually serving.
function Stop-Tree($Proc) {
    if (-not $Proc) { return }
    try { & taskkill.exe /PID $Proc.Id /T /F 2>&1 | Out-Null } catch { }
}

# ── commands ──────────────────────────────────────────────────────────────
$pullCommand = "git -C '$Repo' pull"

function Get-ShareCommand {
    $cmd = "& powershell -NoProfile -ExecutionPolicy Bypass -File '$(Join-Path $Repo 'scripts\share.ps1')'"
    if ($Port -ne 8060)      { $cmd += " -Port $Port" }
    if ($MaxUploadMb -ne 95) { $cmd += " -MaxUploadMb $MaxUploadMb" }
    if ($Persistent)         { $cmd += ' -Persistent' }
    $cmd
}

function Set-Busy([bool]$Busy) {
    $btnAll.Enabled = -not $Busy
    $btnPull.Enabled = -not $Busy
    $btnBuild.Enabled = -not $Busy
    $btnCancel.Enabled = $Busy
}

function Start-Task([string]$Label, [string]$Command) {
    if ($script:taskProc -and -not $script:taskProc.HasExited) { return }
    Remove-Item -LiteralPath $script:taskOut -ErrorAction SilentlyContinue
    $script:taskOffset = [int64]0
    $script:taskLabel = $Label
    Write-Rule $Label
    # 2>&1 inside the child, so stderr lands in the same stream in the right
    # order instead of a second file that would interleave wrongly.
    $script:taskProc = Start-Hidden ('& { ' + $Command + ' } 2>&1') $script:taskOut
    Set-Busy $true
}

$btnPull.Add_Click({ Start-Task 'pull' $pullCommand })
$btnBuild.Add_Click({ Start-Task 'rebuild' (Get-ShareCommand) })
$btnAll.Add_Click({
    # One child, both steps in order, so the rebuild starts only if the pull
    # succeeded rather than racing it.
    Start-Task 'pull + rebuild' "$pullCommand; if (`$LASTEXITCODE -eq 0) { $(Get-ShareCommand) } else { Write-Host 'Pull failed - not rebuilding.' }"
})

$btnCancel.Add_Click({
    if ($script:taskProc -and -not $script:taskProc.HasExited) {
        Stop-Tree $script:taskProc
        Write-Console "`r`n[cancelled]`r`n"
    }
})

$btnTunnel.Add_Click({
    if ($script:tunnelProc -and -not $script:tunnelProc.HasExited) {
        Stop-Tree $script:tunnelProc
        $script:tunnelProc = $null
        Write-Console "`r`n[tunnel stopped]`r`n"
        $btnTunnel.Text = 'Publish tunnel'
        return
    }
    Remove-Item -LiteralPath $script:tunnelOut -ErrorAction SilentlyContinue
    $script:tunnelOffset = [int64]0
    Write-Rule 'tunnel'
    $cmd = "& { & powershell -NoProfile -ExecutionPolicy Bypass -File '$(Join-Path $Repo 'scripts\tunnel.ps1')' } 2>&1"
    $script:tunnelProc = Start-Hidden $cmd $script:tunnelOut
    $btnTunnel.Text = 'Stop tunnel'
})

$btnClear.Add_Click({ $console.Clear() })
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

# Connect first: on localhost a closed port refuses instantly, so the common
# case during a rebuild costs nothing. Without this the HTTP timeout would
# stall the UI thread on every poll while the container is down.
function Test-PortOpen([int]$P) {
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $async = $client.BeginConnect('127.0.0.1', $P, $null, $null)
        if (-not $async.AsyncWaitHandle.WaitOne(200)) { return $false }
        $client.EndConnect($async)
        return $true
    } catch { return $false } finally { $client.Close() }
}

function Get-ContainerVersion {
    if (-not (Test-PortOpen $Port)) { return $null }
    try { (Invoke-RestMethod "http://127.0.0.1:$Port/healthz" -TimeoutSec 2).version }
    catch { $null }
}

function Update-Status {
    $tree = Get-TreeState
    $live = Get-ContainerVersion

    if ($tree) {
        $lblTree.Text = "Working tree - $tree"
        $lblTree.ForeColor = $INK3
    } else {
        $lblTree.Text = 'Working tree - git unavailable'
        $lblTree.ForeColor = $OCHRE
    }

    if ($script:tunnelProc -and -not $script:tunnelProc.HasExited) {
        $lblTunnel.Text = "Tunnel - running (pid $($script:tunnelProc.Id))"
        $lblTunnel.ForeColor = $GREEN
    } else {
        $lblTunnel.Text = 'Tunnel - not running'
        $lblTunnel.ForeColor = $INK4
    }

    if (-not $live) {
        $lblLive.Text = "Container - nothing on 127.0.0.1:$Port"
        $lblLive.ForeColor = $INK4
        $lblVerdict.Text = 'Not running. Press Pull + Rebuild.'
        $lblVerdict.ForeColor = $INK4
        return
    }

    $lblLive.Text = "Container - $live"
    $lblLive.ForeColor = $INK3

    # Compare only the metadata the local stamp carries. Deliberately not
    # rebuilt from a hardcoded "1.0.0-alpha": the base moves when VERSION is
    # promoted to beta, and this comparison must not care.
    if ($live -notmatch '\+local\.(.+)$') {
        $lblVerdict.Text = 'No local stamp - cannot tell which commit is running.'
        $lblVerdict.ForeColor = $OCHRE
        return
    }

    $liveTree = $Matches[1]
    $treeNorm = if ($tree) { $tree -replace '-', '.' } else { $null }

    if ($treeNorm -and $liveTree -eq $treeNorm) {
        if ($liveTree -match '\.dirty$') {
            $lblVerdict.Text = 'LIVE - matches the tree, which has uncommitted changes.'
            $lblVerdict.ForeColor = $OCHRE
        } else {
            $lblVerdict.Text = 'LIVE - this is the commit in your working tree.'
            $lblVerdict.ForeColor = $GREEN
        }
    } else {
        $lblVerdict.Text = 'STALE - container is on another commit. Rebuild.'
        $lblVerdict.ForeColor = $RED
    }
}

$btnRefresh.Add_Click({ Update-Status })

# ── the pump ──────────────────────────────────────────────────────────────
# One timer drains both logs four times a second and refreshes the status strip
# every fifteenth tick, so a rebuild is watched live without fifteen timers.
$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = 250
$timer.Add_Tick({
    Write-Console (Read-Tail $script:taskOut ([ref]$script:taskOffset))
    Write-Console (Read-Tail $script:tunnelOut ([ref]$script:tunnelOffset))

    if ($script:taskProc -and $script:taskProc.HasExited) {
        # One last drain: the child can write between the read above and exit.
        Write-Console (Read-Tail $script:taskOut ([ref]$script:taskOffset))
        $code = $script:taskProc.ExitCode
        Write-Console "`r`n[$($script:taskLabel) finished, exit $code]`r`n"
        $script:taskProc = $null
        Set-Busy $false
        Update-Status
    }

    if ($script:tunnelProc -and $script:tunnelProc.HasExited) {
        Write-Console (Read-Tail $script:tunnelOut ([ref]$script:tunnelOffset))
        Write-Console "`r`n[tunnel exited]`r`n"
        $script:tunnelProc = $null
        $btnTunnel.Text = 'Publish tunnel'
    }

    $script:statusTick++
    if ($script:statusTick % 24 -eq 0) { Update-Status }
})
$timer.Start()

$form.Add_Shown({
    Set-Busy $false
    Write-Console "Ready. $Repo`r`n"
    Update-Status
})

$form.Add_FormClosing({
    param($eventSource, $eventArgs)
    if ($script:tunnelProc -and -not $script:tunnelProc.HasExited) {
        $answer = [System.Windows.Forms.MessageBox]::Show(
            'The tunnel is still running. Stop it and close?',
            'Rebuild Console',
            [System.Windows.Forms.MessageBoxButtons]::YesNo,
            [System.Windows.Forms.MessageBoxIcon]::Question)
        if ($answer -eq [System.Windows.Forms.DialogResult]::No) {
            $eventArgs.Cancel = $true
            return
        }
    }
    Stop-Tree $script:tunnelProc
    Stop-Tree $script:taskProc
})

$form.Add_FormClosed({
    $timer.Stop()
    $timer.Dispose()
    Remove-Item -LiteralPath $script:taskOut, $script:tunnelOut -ErrorAction SilentlyContinue
})

[void]$form.ShowDialog()
