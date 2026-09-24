@echo off
setlocal
cd /d "%~dp0"
title Adaptive Scalper Next - PAPER
set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" (
  echo The project environment is missing. Run SETUP.bat first.
  pause
  exit /b 1
)
echo ============================================================
echo  PAPER MODE: real MT5 market data, SIMULATED fills
echo  No order is ever sent. REAL-MONEY EXECUTION: DISABLED
echo  Stop with Ctrl+C (stopping never closes anything).
echo ============================================================
"%PY%" -m adaptive_scalper.cli paper
echo Runtime exited with code %errorlevel%.
pause
