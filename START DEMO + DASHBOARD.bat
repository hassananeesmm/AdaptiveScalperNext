@echo off
setlocal
cd /d "%~dp0"
title Adaptive Scalper Next - VERIFIED DEMO + DASHBOARD
set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" (
  echo The project environment is missing. Run SETUP.bat first.
  pause
  exit /b 1
)
echo ============================================================
echo  REAL-MONEY EXECUTION: DISABLED
echo  DEMO ACCOUNT REQUIRED
echo  NEW ENTRIES REQUIRE KILL SWITCH DISENGAGED
echo ============================================================
echo Checking configuration, database, MT5 DEMO account and broker clock ...
"%PY%" -m adaptive_scalper.cli doctor
if errorlevel 1 (
  echo.
  echo Prerequisites failed: see PROBLEM lines above. DEMO was NOT started.
  pause
  exit /b 1
)
echo Broker reconciliation (read-only; never sends an order):
"%PY%" -m adaptive_scalper.cli reconcile
"%PY%" -m adaptive_scalper.cli kill-switch status
echo The kill switch is never changed by this launcher. If it is not DISENGAGED, the runtime
echo manages existing positions but blocks every new entry.
echo Opening the observer-only dashboard in its own window: http://127.0.0.1:8765/
start "Adaptive Scalper Next - Dashboard" cmd /c "START DASHBOARD.bat"
echo Starting DEMO in this window. Stop with Ctrl+C. To block new entries at any time, run STOP TRADING.bat.
"%PY%" -m adaptive_scalper.cli demo
echo Runtime exited with code %errorlevel%. The dashboard window keeps running until you close it.
pause
