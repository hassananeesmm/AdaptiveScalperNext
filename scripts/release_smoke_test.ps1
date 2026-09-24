<#
.SYNOPSIS
    Installs a release zip into a fresh temporary folder and smoke-tests it
    WITHOUT touching the broker or the real database.

.DESCRIPTION
    Verifies the checksum, extracts, creates a fresh venv, installs the pinned
    requirements, then runs: doctor (against a throwaway database), strategies,
    okf validate, a kill-switch status read, and the fake/cloud test suite.
    Never bootstraps/clears the kill switch against a real database and never
    starts PAPER or DEMO.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\release_smoke_test.ps1 -Zip dist\AdaptiveScalperNext-0.1.0.zip
#>
[CmdletBinding()]
param([Parameter(Mandatory = $true)][string]$Zip)
$ErrorActionPreference = "Stop"
$Zip = (Resolve-Path $Zip).Path
$Expected = ((Get-Content "$Zip.sha256") -split "\s+")[0]
$Actual = (Get-FileHash -Algorithm SHA256 $Zip).Hash.ToLower()
if ($Expected -ne $Actual) { throw "Checksum mismatch: expected $Expected, got $Actual" }

$Work = Join-Path $env:TEMP ("asn_smoke_" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $Work | Out-Null
Expand-Archive -Path $Zip -DestinationPath $Work
$App = Get-ChildItem $Work -Directory | Select-Object -First 1
Set-Location $App.FullName

py -3.13 -m venv .venv
$Py = Join-Path $App.FullName ".venv\Scripts\python.exe"
& $Py -m pip install -q -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

$Db = Join-Path $Work "smoke.sqlite3"
$Cfg = Join-Path $Work "smoke.toml"
@"
mode = "PAPER"
[market]
symbols = ["XAUUSD", "GBPJPY", "BTCUSD"]
[database]
path = "$($Db -replace '\\', '/')"
"@ | Out-File -Encoding ascii $Cfg

& $Py -m adaptive_scalper.cli --config $Cfg doctor
& $Py -m adaptive_scalper.cli --config $Cfg strategies; if ($LASTEXITCODE -ne 0) { throw "strategies failed" }
& $Py -m adaptive_scalper.cli --config $Cfg okf validate; if ($LASTEXITCODE -ne 0) { throw "okf validate failed" }
& $Py -m adaptive_scalper.cli --config $Cfg kill-switch status; if ($LASTEXITCODE -ne 0) { throw "kill-switch status failed" }
& $Py -m pytest -q; if ($LASTEXITCODE -ne 0) { throw "test suite failed in the installed release" }

Write-Host "Release smoke test PASSED in $($App.FullName)" -ForegroundColor Green
Write-Host "REAL-MONEY EXECUTION REMAINS DISABLED"
