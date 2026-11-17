@echo off
rem One-click dev launcher: stop any running dev session, then start from source.
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
  echo .venv is missing. Create it first:
  echo   py -3.12 -m venv .venv ^&^& .venv\Scripts\pip install -r requirements.txt
  pause
  exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -File "build\stop-dev.ps1" >nul 2>&1
powershell -NoProfile -ExecutionPolicy Bypass -File "build\run-dev.ps1"
