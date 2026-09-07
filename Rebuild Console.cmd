@echo off
rem  Meridian Rebuild Console — double-click this, or a shortcut to it.
rem
rem  Opens the console in your browser: Pull, Rebuild and Publish as buttons,
rem  with their output streaming into the page, plus Config and Doctor for
rem  what this machine is actually set up to do - including whether mail and
rem  the comment assist have their keys in secrets\local.env, and whether the
rem  running container has picked them up.
rem
rem      "Rebuild Console.cmd"                open the console
rem      "Rebuild Console.cmd" app            open it as its own Chrome window,
rem                                           with no tab strip or address bar
rem      "Rebuild Console.cmd" app-shortcut   put a Desktop shortcut that does
rem                                           that - the one to use
rem      "Rebuild Console.cmd" shortcut       the older Desktop shortcut, which
rem                                           opens in the default browser
rem      "Rebuild Console.cmd" debug          open it with a visible console, so
rem                                           a startup error is readable
rem
rem  From Git Bash, use scripts/rebuild-console.sh instead — a POSIX path with
rem  no spaces in it, which avoids the backslash-escaping this file's name runs
rem  into there.
rem
rem  No administrator rights are needed. Docker Desktop must be running before a
rem  rebuild will work, which is a separate matter.

setlocal
set "REPO=%~dp0"
if "%REPO:~-1%"=="\" set "REPO=%REPO:~0,-1%"

rem Which interpreter. Each candidate is tested in its own block: written as
rem `if not defined PY where x && set PY=x` on one line, cmd binds the && to the
rem whole line rather than to the if, so the set runs even when the if is
rem skipped — which silently overwrote the virtualenv with whatever came last
rem and made the not-found message below unreachable.
set "PY="
set "PYC="
if exist "%REPO%\.venv\Scripts\pythonw.exe" (
  set "PY=%REPO%\.venv\Scripts\pythonw.exe"
  set "PYC=%REPO%\.venv\Scripts\python.exe"
)
if not defined PY (
  where pythonw.exe >nul 2>&1
  if not errorlevel 1 (
    set "PY=pythonw.exe"
    set "PYC=python.exe"
  )
)
if not defined PY (
  where python.exe >nul 2>&1
  if not errorlevel 1 (
    set "PY=python.exe"
    set "PYC=python.exe"
  )
)

if not defined PY (
  echo.
  echo Python was not found on PATH, and there is no .venv in:
  echo   %REPO%
  echo.
  echo Install Python 3, or run scripts\setup.ps1 to create the virtualenv.
  echo.
  pause
  exit /b 1
)

if /i "%~1"=="app" goto :app
if /i "%~1"=="app-shortcut" goto :appshortcut
if /i "%~1"=="shortcut" goto :shortcut
if /i "%~1"=="debug" goto :debug

rem pythonw has no console, so the only window that appears is the browser.
start "" /b "%PY%" "%REPO%\scripts\rebuild_console.py"
exit /b 0

:app
rem As its own Chrome window rather than a tab. Falls back to the default
rem browser when no Chromium-family browser is installed.
start "" /b "%PY%" "%REPO%\scripts\rebuild_console.py" --browser app
exit /b 0

:appshortcut
rem The shortcut worth having: targets pythonw directly, so no console window
rem flashes, and opens the page in an app window carrying Chrome's own icon.
powershell -NoProfile -ExecutionPolicy Bypass -File "%REPO%\scripts\app-shortcut.ps1"
pause
exit /b 0

:debug
rem Same thing with a console attached, so a traceback is visible.
"%PYC%" "%REPO%\scripts\rebuild_console.py"
echo.
pause
exit /b 0

:shortcut
rem WScript.Shell is the only thing on a stock Windows that writes a .lnk.
rem
rem WindowStyle 7 is minimised. The target is this .cmd, so Windows opens a
rem console for it however briefly - it only starts pythonw and exits - and a
rem black window flashing on every launch is what stops people using a
rem shortcut.
rem
rem Every comment stays above the command: a caret continues the line, so a
rem `rem` between two continued lines is not a batch comment at all - it is
rem passed to powershell as an argument, and the shortcut silently stops
rem being written.
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$s = (New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path ([Environment]::GetFolderPath('Desktop')) 'Rebuild Console.lnk'));" ^
  "$s.TargetPath = '%REPO%\Rebuild Console.cmd';" ^
  "$s.WorkingDirectory = '%REPO%';" ^
  "$s.Description = 'Meridian Rebuild Console';" ^
  "$s.WindowStyle = 7;" ^
  "$s.Save();" ^
  "Write-Host 'Shortcut placed on the Desktop.'"
pause
exit /b 0
