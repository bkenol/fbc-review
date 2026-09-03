<#
.SYNOPSIS
    Put a Rebuild Console shortcut on the Desktop that opens in a Chrome app window.

.DESCRIPTION
    Writes "Rebuild Console.lnk" to the Desktop. Double-clicking it starts the
    console server and opens the page as its own Chrome (or Edge) window: no tab
    strip, no address bar, its own taskbar button and its own icon. For a control
    panel rather than a document that is the right frame, and it stops the
    console getting lost among thirty tabs.

    Two differences from the shortcut `"Rebuild Console.cmd" shortcut` writes:

    * The target is pythonw.exe directly, not the .cmd. Windows opens a console
      window for a .cmd however briefly, and a black window flashing on every
      launch is what stops people using a shortcut at all. The old script worked
      around that with WindowStyle 7 (minimised), which still puts a button on
      the taskbar. pythonw has no console, so there is nothing to hide.
    * The console is told to open in app mode rather than in the default browser.

    Harmless to re-run: the shortcut is overwritten. Needs no administrator
    rights - it only writes one file to your own Desktop.

.PARAMETER Name
    Shortcut file name, without ".lnk". Defaults to "Rebuild Console".

.PARAMETER Remove
    Delete the shortcut instead of writing it.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\app-shortcut.ps1

.EXAMPLE
    .\"Rebuild Console.cmd" app-shortcut
#>
[CmdletBinding()]
param(
    [string]$Name = 'Rebuild Console',
    [switch]$Remove
)

$ErrorActionPreference = 'Stop'

$repo = Split-Path -Parent $PSScriptRoot
$desktop = [Environment]::GetFolderPath('Desktop')
$link = Join-Path $desktop "$Name.lnk"

if ($Remove) {
    if (Test-Path $link) {
        Remove-Item $link -Force
        Write-Host "Removed $link"
    } else {
        Write-Host "Nothing to remove; $link does not exist."
    }
    exit 0
}

# pythonw over python: no console window, so the only thing that appears is the
# browser. The virtualenv is preferred because that is where the project's own
# Python lives; the console itself is standard library only and runs under any
# Python 3, which is why a bare pythonw on PATH is an acceptable fallback.
$python = Join-Path $repo '.venv\Scripts\pythonw.exe'
if (-not (Test-Path $python)) {
    $found = Get-Command pythonw.exe -ErrorAction SilentlyContinue
    if ($found) { $python = $found.Source }
}
if (-not (Test-Path $python)) {
    Write-Host ''
    Write-Host 'Python was not found on PATH, and there is no .venv in:' -ForegroundColor Red
    Write-Host "  $repo"
    Write-Host ''
    Write-Host 'Install Python 3, or run scripts\setup.ps1 to create the virtualenv.'
    exit 1
}

$script = Join-Path $repo 'scripts\rebuild_console.py'
if (-not (Test-Path $script)) {
    Write-Host "scripts\rebuild_console.py is missing from $repo." -ForegroundColor Red
    exit 1
}

# Borrow the browser's own icon, so the shortcut looks like what it opens. Kept
# in step with _APP_BROWSER_PATHS in rebuild_console.py: if none of these is
# installed the console falls back to the default browser at launch, and the
# shortcut just keeps Python's icon.
$browser = @(
    "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe",
    "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
    "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
    "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe",
    "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe",
    "$env:LOCALAPPDATA\BraveSoftware\Brave-Browser\Application\brave.exe"
) | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1

$shell = New-Object -ComObject WScript.Shell
$s = $shell.CreateShortcut($link)
$s.TargetPath = $python
$s.Arguments = '"{0}" --browser app' -f $script
$s.WorkingDirectory = $repo
$s.Description = 'Meridian Rebuild Console - pull, rebuild, publish'
$s.WindowStyle = 1
if ($browser) { $s.IconLocation = "$browser,0" }
$s.Save()

Write-Host ''
Write-Host "Shortcut written: $link" -ForegroundColor Green
if ($browser) {
    Write-Host "Opens in an app window of: $browser"
} else {
    Write-Host 'No Chrome, Edge or Brave found. The console will open in your' -ForegroundColor Yellow
    Write-Host 'default browser instead; install one of those for app mode.' -ForegroundColor Yellow
}
Write-Host ''
