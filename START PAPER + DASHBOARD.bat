@echo off
setlocal
cd /d "%~dp0"
title Adaptive Scalper Next - PAPER + DASHBOARD
set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" (
  echo The project environment is missing. Run SETUP.bat first.
  pause
  exit /b 1
)
echo ============================================================
echo  PAPER + DASHBOARD
echo  PAPER MODE: real MT5 market data, SIMULATED fills
echo  No order is ever sent. REAL-MONEY EXECUTION: DISABLED
echo ============================================================
echo Checking configuration, database, MT5 DEMO account and broker clock ...
"%PY%" -m adaptive_scalper.cli doctor
if errorlevel 1 (
  echo.
  echo Prerequisites failed: see PROBLEM lines above. PAPER was NOT started.
  pause
  exit /b 1
)
"%PY%" -m adaptive_scalper.cli kill-switch status
echo Opening the observer-only dashboard in its own window: http://127.0.0.1:8765/
start "Adaptive Scalper Next - Dashboard" cmd /c "START DASHBOARD.bat"
echo Starting PAPER in this window. Stop with Ctrl+C (stopping never closes anything).
"%PY%" -m adaptive_scalper.cli paper
echo Runtime exited with code %errorlevel%. The dashboard window keeps running until you close it.
pause
