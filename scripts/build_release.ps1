<#
.SYNOPSIS
    Builds a release zip of the committed source tree (git archive), with a
    SHA-256 checksum file and a manifest.

.DESCRIPTION
    Only tracked files are packaged, so .venv, data\, logs\, databases,
    model artifacts and any local secrets can never enter a release. Refuses
    to build from a dirty working tree or when the test suite fails.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\build_release.ps1 -Version 0.1.0
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Version,
    [switch]$SkipTests
)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$Py = Join-Path $Root ".venv\Scripts\python.exe"

if (git status --porcelain) { throw "Working tree is not clean; commit or stash first." }
if (-not $SkipTests) {
    & $Py -m pytest -q
    if ($LASTEXITCODE -ne 0) { throw "Tests failed; no release built." }
}
& $Py -m adaptive_scalper.cli okf validate | Out-Null
if ($LASTEXITCODE -ne 0) { throw "OKF bundle invalid; no release built." }

$Commit = (git rev-parse HEAD).Trim()
$Dist = Join-Path $Root "dist"
New-Item -ItemType Directory -Force -Path $Dist | Out-Null
$Name = "AdaptiveScalperNext-$Version"
$Zip = Join-Path $Dist "$Name.zip"
if (Test-Path $Zip) { throw "$Zip already exists; choose a new version." }

git archive --format=zip --prefix="$Name/" -o $Zip HEAD
if ($LASTEXITCODE -ne 0) { throw "git archive failed" }

$Forbidden = @("*.sqlite3", "*.db", "*.key", "*.pem", ".env", "secrets*", "credentials*", "*.joblib")
Add-Type -AssemblyName System.IO.Compression.FileSystem
$Entries = [System.IO.Compression.ZipFile]::OpenRead($Zip).Entries | ForEach-Object { $_.FullName }
foreach ($pattern in $Forbidden) {
    $hits = $Entries | Where-Object { ($_ -split "/")[-1] -like $pattern }
    if ($hits) { Remove-Item $Zip; throw "Release would contain forbidden files: $hits" }
}

$Hash = (Get-FileHash -Algorithm SHA256 $Zip).Hash.ToLower()
"$Hash  $Name.zip" | Out-File -Encoding ascii (Join-Path $Dist "$Name.zip.sha256")
@{
    name = $Name; version = $Version; commit = $Commit; built_utc = (Get-Date).ToUniversalTime().ToString("o")
    sha256 = $Hash; files = $Entries.Count; real_money_execution = "DISABLED"
} | ConvertTo-Json | Out-File -Encoding utf8 (Join-Path $Dist "$Name.manifest.json")

Write-Host "Built $Zip ($($Entries.Count) entries) sha256=$Hash commit=$Commit"
Write-Host "Next: scripts\release_smoke_test.ps1 -Zip `"$Zip`""
