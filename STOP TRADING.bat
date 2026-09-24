@echo off
setlocal
cd /d "%~dp0"
title Adaptive Scalper Next - STOP TRADING
set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" (
  echo The project environment is missing. Run SETUP.bat first.
  pause
  exit /b 1
)
echo ============================================================
echo  STOP TRADING: engages the kill switch.
echo  New entries are blocked immediately.
echo  Open positions are NOT closed: they keep their protective stops
echo  and the runtime keeps managing them.
echo ============================================================
"%PY%" -m adaptive_scalper.cli kill-switch engage --reason "STOP TRADING launcher" --actor "operator:stop-trading-launcher"
"%PY%" -m adaptive_scalper.cli kill-switch status
echo Resuming requires an explicit operator command: kill-switch clear --operator-id ... --reason ...
pause
