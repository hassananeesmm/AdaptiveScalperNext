@echo off
setlocal
cd /d "%~dp0"
title Adaptive Scalper Next - Backtest
set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" (
  echo The project environment is missing. Run SETUP.bat first.
  pause
  exit /b 1
)
echo Backtest over stored history (run history bootstrap first). The range is recorded as
echo VALIDATION data and can never be used as untouched out-of-sample data afterwards.
set /p SYMBOL="Symbol (XAUUSD, GBPJPY or BTCUSD): "
set /p START="Start date (YYYY-MM-DD, UTC): "
set /p END="End date (YYYY-MM-DD, UTC): "
"%PY%" -m adaptive_scalper.cli backtest --symbol "%SYMBOL%" --start "%START%" --end "%END%"
pause
