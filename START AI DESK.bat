@echo off
setlocal
cd /d "%~dp0"
title Adaptive Scalper Next - AI Trading Desk (advisory only)
set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" (
  echo The project environment is missing. Run SETUP.bat first.
  pause
  exit /b 1
)
echo AI Trading Desk research service: ADVISORY ONLY, no execution authority.
echo It reads the trading database read-only and writes only data\ai_desk.sqlite3.
echo It does not start trading, does not touch the kill switch and makes no paid model calls unless configured.
echo It runs only when config\ai_desk.toml sets enabled = true (see docs\AI_DESK.md).
"%PY%" -m adaptive_scalper.cli ai-desk doctor
if errorlevel 1 (
  echo The AI desk self-check failed; the research service was NOT started. Trading is unaffected.
  pause
  exit /b 1
)
"%PY%" -m adaptive_scalper.cli ai-desk serve
pause
