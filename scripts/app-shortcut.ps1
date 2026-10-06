<#
.SYNOPSIS
    Put a Rebuild Console icon on the Desktop and in the Start menu, so the
    console opens like a program.

.DESCRIPTION
    Writes "Rebuild Console.lnk" to the Desktop and the Start menu. Opening it
    starts the console and shows it as its own Chrome (or Edge) window: no tab
    strip, no address bar, its own taskbar button and the console's own icon.
    For a control panel rather than a document that is the right frame, and it
    stops the console getting lost among thirty tabs. Pin it to the taskbar
    from the Start menu entry.

    Opening it again while the console is running brings back that console
    rather than starting a second one, and closing the window leaves the
    console running in the background with whatever it was doing. "Shut down
    this console", at the foot of the page, ends it.

    The shortcut runs the console as you, never as an administrator, and does
    not ask to. The buttons that need an administrator (the tunnel service)
    ask Windows for approval themselves, each time, for that one step.

    Two differences from the shortcut `"Rebuild Console.cmd" shortcut` writes:

    * The target is pythonw.exe directly, not the .cmd. Windows opens a console
      window for a .cmd however briefly, and a black window flashing on every
      launch is what stops people using a shortcut at all. The old script worked
      around that with WindowStyle 7 (minimised), which still puts a button on
      the taskbar. pythonw has no console, so there is nothing to hide.
    * The console is told to open in app mode rather than in the default browser.

    Harmless to re-run: the shortcuts are overwritten. Needs no administrator
    rights - it only writes to your own Desktop and Start menu folders.

.PARAMETER Name
    Shortcut file name, without ".lnk". Defaults to "Rebuild Console".

.PARAMETER NoStartMenu
    Write the Desktop shortcut only.

.PARAMETER Remove
    Delete the shortcuts instead of writing them.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\app-shortcut.ps1

.EXAMPLE
    .\"Rebuild Console.cmd" app-shortcut
#>
[CmdletBinding()]
param(
    [string]$Name = 'Rebuild Console',
    [switch]$NoStartMenu,
    [switch]$Remove
)

$ErrorActionPreference = 'Stop'

$repo = Split-Path -Parent $PSScriptRoot
$folders = @([Environment]::GetFolderPath('Desktop'))
if (-not $NoStartMenu) { $folders += [Environment]::GetFolderPath('Programs') }
# GetFolderPath returns '' for a folder this account does not have.
$links = @($folders | Where-Object { $_ } | ForEach-Object { Join-Path $_ "$Name.lnk" })
if (-not $links.Count) {
    Write-Host 'Could not find a Desktop or Start menu folder for this account.' -ForegroundColor Red
    exit 1
}

if ($Remove) {
    foreach ($link in $links) {
        if (Test-Path $link) {
            Remove-Item $link -Force
            Write-Host "Removed $link"
        } else {
            Write-Host "Nothing to remove; $link does not exist."
        }
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

# The browser the console opens in app mode. Kept in step with
# _APP_BROWSER_PATHS in rebuild_console.py: if none of these is installed the
# console falls back to the default browser at launch.
$browser = @(
    "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe",
    "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
    "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
    "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe",
    "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe",
    "$env:LOCALAPPDATA\BraveSoftware\Brave-Browser\Application\brave.exe"
) | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1

# The console's own icon, which it also serves as the page's favicon so the app
# window's taskbar button matches. The browser's icon if the file is missing.
$icon = Join-Path $repo 'scripts\console-assets\rebuild-console.ico'
if (Test-Path $icon) { $iconLocation = "$icon,0" }
elseif ($browser) { $iconLocation = "$browser,0" }
else { $iconLocation = $null }

$shell = New-Object -ComObject WScript.Shell
foreach ($link in $links) {
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $link) | Out-Null
    $s = $shell.CreateShortcut($link)
    $s.TargetPath = $python
    $s.Arguments = '"{0}" --browser app' -f $script
    $s.WorkingDirectory = $repo
    $s.Description = 'Meridian Rebuild Console - pull, rebuild, publish'
    $s.WindowStyle = 1
    if ($iconLocation) { $s.IconLocation = $iconLocation }
    $s.Save()
    Write-Host "Shortcut written: $link" -ForegroundColor Green
}

Write-Host ''
if ($browser) {
    Write-Host "Opens in an app window of: $browser"
} else {
    Write-Host 'No Chrome, Edge or Brave found. The console will open in your' -ForegroundColor Yellow
    Write-Host 'default browser instead; install one of those for app mode.' -ForegroundColor Yellow
}
Write-Host 'Pin it: Start menu, right-click Rebuild Console, Pin to taskbar.'
Write-Host 'It runs as you. The tunnel-service buttons ask Windows for approval when pressed.'
Write-Host ''
