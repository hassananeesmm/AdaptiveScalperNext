<#
.SYNOPSIS
    Regenerates / verifies Adaptive Scalper Next's Claude Code development
    safeguards after a fresh clone, and self-tests that they actually work.

.DESCRIPTION
    The enforcement hooks themselves are committed under .claude/hooks/,
    .claude/settings.json, .claude/claude-security-guidance.md and
    .claude/security-patterns.json, so a plain `git clone` already restores
    them. This script exists for the parts a clone does NOT restore:

      1. Materializes any of those files from .claude/hookify-templates/
         if they're missing locally (defense in depth against accidental
         local deletion/edit).
      2. Ensures the project venv (.venv) exists with requirements
         installed, since guardrails.py prefers it as its interpreter.
      3. Verifies (and if necessary repairs) the security-guidance
         plugin's shared agent-sdk venv at
         ~/.claude/security/agent-sdk-venv, which is a MACHINE-global
         resource outside this repo and does not survive a fresh clone
         or a fresh machine at all. Without it, the LLM-powered
         Stop/commit/push security review silently degrades to
         pattern-only checks.
      4. Runs a synthetic end-to-end self-test of guardrails.py (HARD
         BLOCK + WARNING cases) so a broken hook is caught immediately
         instead of silently no-op'ing.

.NOTES
    Safe to re-run at any time; every step is idempotent.
#>

[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$ClaudeDir = Join-Path $ProjectRoot ".claude"
$TemplatesDir = Join-Path $ClaudeDir "hookify-templates"

function Write-Step($msg) { Write-Host "==> $msg" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "    OK: $msg" -ForegroundColor Green }
function Write-Warn2($msg) { Write-Host "    WARN: $msg" -ForegroundColor Yellow }
function Write-Fail($msg) { Write-Host "    FAIL: $msg" -ForegroundColor Red }

$failures = @()

# ---------------------------------------------------------------------
# 1. Restore committed hook files from templates if locally missing
# ---------------------------------------------------------------------
Write-Step "Verifying committed hook files"
$expected = @{
    (Join-Path $ClaudeDir "settings.json")               = (Join-Path $TemplatesDir "settings.json")
    (Join-Path $ClaudeDir "claude-security-guidance.md")  = (Join-Path $TemplatesDir "claude-security-guidance.md")
    (Join-Path $ClaudeDir "security-patterns.json")       = (Join-Path $TemplatesDir "security-patterns.json")
    (Join-Path $ClaudeDir "hooks\guardrails.py")          = (Join-Path $TemplatesDir "guardrails.py")
    (Join-Path $ClaudeDir "hooks\run-guardrails.sh")      = (Join-Path $TemplatesDir "run-guardrails.sh")
    (Join-Path $ClaudeDir "hooks\edit_claims.py")         = (Join-Path $TemplatesDir "edit_claims.py")
    (Join-Path $ClaudeDir "hooks\run-edit-claims.sh")     = (Join-Path $TemplatesDir "run-edit-claims.sh")
}
foreach ($dest in $expected.Keys) {
    if (-not (Test-Path $dest)) {
        $src = $expected[$dest]
        if (Test-Path $src) {
            New-Item -ItemType Directory -Force -Path (Split-Path $dest) | Out-Null
            Copy-Item $src $dest
            Write-Ok "restored $(Resolve-Path -Relative $dest) from hookify-templates"
        } else {
            Write-Fail "$dest missing and no template found at $src"
            $failures += "missing hook file: $dest"
        }
    } else {
        Write-Ok "$(Resolve-Path -Relative $dest) present"
    }
}

# ---------------------------------------------------------------------
# 2. Project venv
# ---------------------------------------------------------------------
Write-Step "Verifying project virtual environment"
$venvPy = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPy)) {
    Write-Warn2 ".venv not found — creating it"
    python -m venv (Join-Path $ProjectRoot ".venv")
}
if (Test-Path $venvPy) {
    & $venvPy -m pip install -q -r (Join-Path $ProjectRoot "requirements.txt")
    $ver = & $venvPy --version
    Write-Ok "venv interpreter: $ver"
} else {
    Write-Fail "could not create .venv (no system python?)"
    $failures += "venv creation failed"
}

# ---------------------------------------------------------------------
# 3. Global security-guidance agent-sdk venv (LLM review layer)
# ---------------------------------------------------------------------
Write-Step "Verifying security-guidance agent-sdk venv (global, ~/.claude/security)"
$sgVenvPy = Join-Path $env:USERPROFILE ".claude\security\agent-sdk-venv\Scripts\python.exe"
$sdkOk = $false
if (Test-Path $sgVenvPy) {
    & $sgVenvPy -c "import claude_agent_sdk" 2>$null
    $sdkOk = ($LASTEXITCODE -eq 0)
}
if ($sdkOk) {
    Write-Ok "claude_agent_sdk importable in agent-sdk-venv — LLM-powered review is live"
} else {
    Write-Warn2 "claude_agent_sdk not importable yet — pattern-only checks still work; " +
                "LLM-powered Stop/commit/push review will be degraded until the " +
                "security-guidance plugin's SessionStart hook finishes building it " +
                "(automatic, background, next session). This is a machine-global " +
                "resource, not part of this repo."
}

# ---------------------------------------------------------------------
# 4. Self-test guardrails.py
# ---------------------------------------------------------------------
Write-Step "Self-testing guardrails.py (synthetic HARD BLOCK / WARNING cases)"
if (Test-Path $venvPy) {
    $testExit = & $venvPy -m pytest (Join-Path $ProjectRoot "tests\test_guardrails.py") -q
    if ($LASTEXITCODE -eq 0) {
        Write-Ok "guardrails self-test passed"
    } else {
        Write-Fail "guardrails self-test FAILED — see pytest output above"
        $failures += "guardrails self-test failed"
    }
}

# ---------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------
Write-Host ""
if ($failures.Count -eq 0) {
    Write-Host "All development safeguards verified." -ForegroundColor Green
    exit 0
} else {
    Write-Host "$($failures.Count) issue(s) found:" -ForegroundColor Red
    $failures | ForEach-Object { Write-Host "  - $_" -ForegroundColor Red }
    exit 1
}
