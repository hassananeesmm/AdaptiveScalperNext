"""Tests for the .claude/hooks/guardrails.py PreToolUse safety gate.

Covers both the pure policy functions (imported directly) and the full
stdin-JSON -> stdout-JSON hook protocol (subprocess), so a regression in
either the policy logic or the plumbing around it is caught.

Note: this file lives under tests/, which guardrails.py's own
CONTENT_SCAN_EXEMPT_MARKERS excludes from content-based regex scanning —
without that exemption, the fixture strings below (e.g. "LIVE_TRADING =
True") would cause guardrails.py to block edits to this very file.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
HOOKS_DIR = PROJECT_ROOT / ".claude" / "hooks"
GUARDRAILS = HOOKS_DIR / "guardrails.py"

sys.path.insert(0, str(HOOKS_DIR))
import guardrails  # noqa: E402


# --------------------------------------------------------------------------
# Pure policy function tests
# --------------------------------------------------------------------------

def test_targets_sibling_project_true():
    assert guardrails.targets_sibling_project(r"C:\AdaptiveScalper\config.py")
    assert guardrails.targets_sibling_project(r"C:/AdaptiveScalper/config.py")


def test_targets_sibling_project_false_for_next():
    assert not guardrails.targets_sibling_project(r"C:\AdaptiveScalperNext\config.py")
    assert not guardrails.targets_sibling_project(r"C:\AdaptiveScalperNextOther\x.py")


def test_real_money_patterns():
    assert guardrails.contains_any(guardrails.REAL_MONEY_PATTERNS, "LIVE_TRADING = True")
    assert guardrails.contains_any(guardrails.REAL_MONEY_PATTERNS, "real_money = True")
    assert guardrails.contains_any(guardrails.REAL_MONEY_PATTERNS, 'trading_mode = "LIVE"')
    assert not guardrails.contains_any(guardrails.REAL_MONEY_PATTERNS, "MODE = 'DEMO'")


def test_secret_patterns():
    assert guardrails.contains_any(guardrails.SECRET_PATTERNS, 'MT5_PASSWORD = "hunter2xyz"')
    assert guardrails.contains_any(guardrails.SECRET_PATTERNS, 'api_key: "sk-abcdefghijklmnop"')
    assert not guardrails.contains_any(guardrails.SECRET_PATTERNS, "password = load_from_env()")


def test_full_test_suite_delete_detected():
    assert guardrails.contains_any(guardrails.FULL_TEST_SUITE_DELETE_PATTERNS, "rm -rf tests")
    assert guardrails.contains_any(guardrails.FULL_TEST_SUITE_DELETE_PATTERNS,
                                    "Remove-Item tests -Recurse -Force")


def test_individual_test_delete_not_full_suite():
    assert not guardrails.contains_any(guardrails.FULL_TEST_SUITE_DELETE_PATTERNS,
                                        "rm tests/test_environment.py")
    assert guardrails.contains_any(guardrails.INDIVIDUAL_TEST_DELETE_PATTERNS,
                                    "rm tests/test_environment.py")


def test_force_push_detected():
    assert guardrails.FORCE_PUSH_RE.search("git push --force origin main")
    assert guardrails.FORCE_PUSH_RE.search("git push -f")
    assert not guardrails.FORCE_PUSH_RE.search("git push --force-with-lease origin main")


def test_git_reset_hard_detected():
    assert guardrails.GIT_RESET_HARD_RE.search("git reset --hard HEAD~1")
    assert not guardrails.GIT_RESET_HARD_RE.search("git reset HEAD~1")


def test_git_clean_f_detected():
    assert guardrails.GIT_CLEAN_F_RE.search("git clean -fd")
    assert not guardrails.GIT_CLEAN_F_RE.search("git status")


def test_bypass_permissions_detected():
    assert guardrails.contains_any(guardrails.BYPASS_PATTERNS,
                                    "claude --dangerously-skip-permissions")
    assert guardrails.contains_any(guardrails.BYPASS_PATTERNS, '"defaultMode": "bypassPermissions"')


def test_outside_project_root():
    root = r"C:\AdaptiveScalperNext"
    assert guardrails.is_outside_project_root(r"C:\Windows\System32\config.py", root)
    assert not guardrails.is_outside_project_root(r"C:\AdaptiveScalperNext\src\x.py", root)
    assert not guardrails.is_outside_project_root(r"C:\AdaptiveScalperNext", root)
    assert guardrails.is_outside_project_root(r"C:\AdaptiveScalperNextOther\x.py", root)
    assert not guardrails.is_outside_project_root(r"relative\x.py", root)


def test_touches_safety_boundary():
    assert guardrails.touches_safety_boundary("src/risk/risk_governor.py")
    assert guardrails.touches_safety_boundary("src/execution/kill_switch.py")
    assert not guardrails.touches_safety_boundary("src/dashboard/panel.py")


def test_content_scan_exempt_paths():
    assert guardrails.is_content_scan_exempt(r"C:\AdaptiveScalperNext\tests\test_foo.py")
    assert guardrails.is_content_scan_exempt(r"C:\AdaptiveScalperNext\.claude\hooks\guardrails.py")
    assert not guardrails.is_content_scan_exempt(r"C:\AdaptiveScalperNext\adaptive_scalper\config\loader.py")


# --------------------------------------------------------------------------
# End-to-end hook protocol tests (subprocess, real stdin/stdout contract)
# --------------------------------------------------------------------------

def run_hook(payload: dict) -> dict:
    # The e2e payloads use Windows paths under C:\AdaptiveScalperNext, so
    # the hook must evaluate them against that root on ANY host (on Linux
    # it would otherwise derive a /home/... root from its own location).
    env = {**os.environ, "CLAUDE_PROJECT_DIR": r"C:\AdaptiveScalperNext"}
    proc = subprocess.run(
        [sys.executable, str(GUARDRAILS)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_e2e_blocks_write_to_sibling_project():
    result = run_hook({
        "hook_event_name": "PreToolUse",
        "tool_name": "Write",
        "cwd": r"C:\AdaptiveScalperNext",
        "tool_input": {"file_path": r"C:\AdaptiveScalper\config.py", "content": "x = 1"},
    })
    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_e2e_allows_write_inside_project():
    result = run_hook({
        "hook_event_name": "PreToolUse",
        "tool_name": "Write",
        "cwd": r"C:\AdaptiveScalperNext",
        "tool_input": {"file_path": r"C:\AdaptiveScalperNext\src\config.py", "content": "x = 1"},
    })
    assert result["hookSpecificOutput"]["permissionDecision"] == "allow"


def test_e2e_blocks_real_money_enablement():
    result = run_hook({
        "hook_event_name": "PreToolUse",
        "tool_name": "Edit",
        "cwd": r"C:\AdaptiveScalperNext",
        "tool_input": {
            "file_path": r"C:\AdaptiveScalperNext\src\config.py",
            "old_string": "x = 1",
            "new_string": "LIVE_TRADING = True",
        },
    })
    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_e2e_allows_real_money_string_inside_tests_directory():
    # tests/ is content-scan exempt: a test asserting the regex works must
    # not get itself blocked for containing the literal trigger string.
    result = run_hook({
        "hook_event_name": "PreToolUse",
        "tool_name": "Write",
        "cwd": r"C:\AdaptiveScalperNext",
        "tool_input": {
            "file_path": r"C:\AdaptiveScalperNext\tests\test_something.py",
            "content": 'assert contains_any(REAL_MONEY_PATTERNS, "LIVE_TRADING = True")',
        },
    })
    assert result["hookSpecificOutput"]["permissionDecision"] == "allow"


def test_e2e_blocks_hardcoded_secret_in_edit_content():
    result = run_hook({
        "hook_event_name": "PreToolUse",
        "tool_name": "Write",
        "cwd": r"C:\AdaptiveScalperNext",
        "tool_input": {
            "file_path": r"C:\AdaptiveScalperNext\src\broker_config.py",
            "content": 'MT5_PASSWORD = "hunter2-real-password"',
        },
    })
    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_e2e_blocks_full_test_suite_delete():
    result = run_hook({
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "cwd": r"C:\AdaptiveScalperNext",
        "tool_input": {"command": "rm -rf tests"},
    })
    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_e2e_warns_on_force_push():
    result = run_hook({
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "cwd": r"C:\AdaptiveScalperNext",
        "tool_input": {"command": "git push --force origin main"},
    })
    assert result["hookSpecificOutput"]["permissionDecision"] == "ask"


def test_e2e_allows_benign_bash():
    result = run_hook({
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "cwd": r"C:\AdaptiveScalperNext",
        "tool_input": {"command": "pytest -q"},
    })
    assert result["hookSpecificOutput"]["permissionDecision"] == "allow"


def test_e2e_blocks_bypass_permissions_command():
    result = run_hook({
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "cwd": r"C:\AdaptiveScalperNext",
        "tool_input": {"command": "claude --dangerously-skip-permissions"},
    })
    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_e2e_malformed_json_fails_open():
    proc = subprocess.run(
        [sys.executable, str(GUARDRAILS)],
        input="not json",
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0
    result = json.loads(proc.stdout)
    assert result["hookSpecificOutput"]["permissionDecision"] == "allow"
