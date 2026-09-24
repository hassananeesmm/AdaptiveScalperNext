<#
.SYNOPSIS
    Verifies Adaptive Scalper Next on the Windows laptop, including the parts
    the cloud cannot run (MetaTrader5 package, live DEMO terminal tests).

.DESCRIPTION
    Read-only against the broker: it never sends an order, never bootstraps or
    clears the kill switch, and refuses to continue if the terminal is logged
    into a non-DEMO account. Writes a timestamped report under logs\.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\windows_verify.ps1
#>
[CmdletBinding()]
param(
    [switch]$SkipLiveTests
)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$Py = Join-Path $Root ".venv\Scripts\python.exe"
New-Item -ItemType Directory -Force -Path (Join-Path $Root "logs") | Out-Null
$Report = Join-Path $Root ("logs\windows_verify_{0}.txt" -f (Get-Date -Format "yyyyMMdd_HHmmss"))
$Failures = @()

function Step([string]$Name, [scriptblock]$Body) {
    Write-Host "== $Name" -ForegroundColor Cyan
    "== $Name" | Out-File -Append -Encoding utf8 $Report
    try {
        $out = & $Body 2>&1 | Out-String
        $out | Out-File -Append -Encoding utf8 $Report
        Write-Host $out
        if ($LASTEXITCODE -ne 0 -and $null -ne $LASTEXITCODE) { throw "exit code $LASTEXITCODE" }
        "PASS" | Out-File -Append -Encoding utf8 $Report
    } catch {
        $script:Failures += "$Name ($_)"
        "FAIL: $_" | Out-File -Append -Encoding utf8 $Report
        Write-Host "FAIL: $_" -ForegroundColor Red
    }
}

if (-not (Test-Path $Py)) { throw "Missing .venv - run SETUP.bat first." }

Step "Python version (3.13 required)" { & $Py -c "import sys; assert sys.version_info[:2] == (3, 13), sys.version; print(sys.version)" }
Step "Pinned requirements installed" { & $Py -m pip check }
Step "MetaTrader5 package importable" { & $Py -c "import MetaTrader5 as m; print(m.__version__)" }
Step "Byte-compile" { & $Py -m compileall -q adaptive_scalper }
Step "Doctor (config, DB, kill switch, terminal, DEMO account)" { & $Py -m adaptive_scalper.cli doctor }
Step "Symbols resolve and specs captured" { & $Py -m adaptive_scalper.cli symbols }
Step "OKF knowledge bundle valid" { & $Py -m adaptive_scalper.cli okf validate }
Step "Full test suite (fake/cloud tests)" { & $Py -m pytest -q }
if (-not $SkipLiveTests) {
    Step "Live MT5 DEMO terminal tests" { & $Py -m pytest -q tests/test_mt5_gateway_live.py -rs }
}
Step "Kill switch state (read only)" { & $Py -m adaptive_scalper.cli kill-switch status }

"" | Out-File -Append -Encoding utf8 $Report
if ($Failures.Count -gt 0) {
    "RESULT: FAIL`n" + ($Failures -join "`n") | Out-File -Append -Encoding utf8 $Report
    Write-Host "RESULT: FAIL - see $Report" -ForegroundColor Red
    exit 1
}
"RESULT: PASS" | Out-File -Append -Encoding utf8 $Report
Write-Host "RESULT: PASS - report $Report" -ForegroundColor Green
Write-Host "REAL-MONEY EXECUTION REMAINS DISABLED"
