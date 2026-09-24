@echo off
setlocal
cd /d "%~dp0"
title Adaptive Scalper Next - DEMO
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
echo The runtime refuses to start unless the logged-in MT5 account is a DEMO account.
echo Stop with Ctrl+C. To block new entries at any time, run STOP TRADING.bat.
echo.
"%PY%" -m adaptive_scalper.cli demo
echo Runtime exited with code %errorlevel%.
pause
