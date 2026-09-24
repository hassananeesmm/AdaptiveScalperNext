@echo off
setlocal
cd /d "%~dp0"
title Adaptive Scalper Next - Dashboard
set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" (
  echo The project environment is missing. Run SETUP.bat first.
  pause
  exit /b 1
)
echo Observer-only dashboard: reads the database, never connects to MT5, cannot change anything.
start "" "http://127.0.0.1:8765/"
"%PY%" -m adaptive_scalper.cli dashboard --host 127.0.0.1 --port 8765
pause
