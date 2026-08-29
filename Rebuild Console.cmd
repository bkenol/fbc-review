@echo off
rem  Meridian Rebuild Console — double-click this, or a shortcut to it.
rem
rem  Opens the console in your browser: Pull, Rebuild and Publish as buttons,
rem  with their output streaming into the page.
rem
rem  Run it once with an argument to put a shortcut on the Desktop:
rem      "Rebuild Console.cmd" shortcut
rem
rem  No administrator rights are needed. Docker Desktop must be running before
rem  a rebuild will work, which is a separate matter.

setlocal
set "REPO=%~dp0"
if "%REPO:~-1%"=="\" set "REPO=%REPO:~0,-1%"

rem Prefer the project virtualenv, then the py launcher, then bare python.
set "PY="
if exist "%REPO%\.venv\Scripts\pythonw.exe" set "PY=%REPO%\.venv\Scripts\pythonw.exe"
if not defined PY where pyw >nul 2>&1 && set "PY=pyw"
if not defined PY where pythonw >nul 2>&1 && set "PY=pythonw"
if not defined PY where python >nul 2>&1 && set "PY=python"

if not defined PY (
  echo Python was not found on PATH and there is no .venv in %REPO%.
  echo Install Python 3, or run scripts\setup.ps1 to create the virtualenv.
  pause
  exit /b 1
)

if /i "%~1"=="shortcut" goto :shortcut

rem pythonw has no console, so the console window is the browser page and
rem nothing else. start /b keeps this cmd window from lingering either.
start "" /b "%PY%" "%REPO%\scripts\rebuild_console.py"
exit /b 0

:shortcut
rem WScript.Shell is the only thing on a stock Windows that writes a .lnk.
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$s = (New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path ([Environment]::GetFolderPath('Desktop')) 'Rebuild Console.lnk'));" ^
  "$s.TargetPath = '%REPO%\Rebuild Console.cmd';" ^
  "$s.WorkingDirectory = '%REPO%';" ^
  "$s.Description = 'Meridian Rebuild Console';" ^
  "$s.Save();" ^
  "Write-Host 'Shortcut placed on the Desktop.'"
pause
exit /b 0
