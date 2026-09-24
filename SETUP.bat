@echo off
setlocal
cd /d "%~dp0"
title Adaptive Scalper Next - Setup
echo ============================================================
echo  Adaptive Scalper Next - setup (PAPER + MT5 DEMO only)
echo  REAL-MONEY EXECUTION: DISABLED
echo ============================================================
if not exist ".venv\Scripts\python.exe" (
  echo Creating .venv with Python 3.13 ...
  py -3.13 -m venv .venv
  if errorlevel 1 (
    echo Python 3.13 was not found. Install it from python.org, then run SETUP.bat again.
    pause
    exit /b 1
  )
)
set "PY=%~dp0.venv\Scripts\python.exe"
"%PY%" -m pip install --upgrade pip
"%PY%" -m pip install -r requirements.txt
if errorlevel 1 (
  echo Dependency installation failed.
  pause
  exit /b 1
)
echo.
echo Checking configuration, database and MT5 terminal ...
"%PY%" -m adaptive_scalper.cli doctor
echo.
echo Setup never bootstraps or clears the kill switch. When you are ready, run as the operator:
echo   .venv\Scripts\python.exe -m adaptive_scalper.cli kill-switch bootstrap --operator-id YOUR_NAME --reason "first start"
echo Then start with START PAPER.bat. See LOCAL_MT5_HANDOFF.md.
pause
