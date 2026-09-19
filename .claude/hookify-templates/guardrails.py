#!/usr/bin/env python3
"""Deterministic PreToolUse safety gate for Adaptive Scalper Next.

Stdlib only (no third-party deps, no LLM SDK) so this cannot go dark the
way the security-guidance plugin's agent-sdk venv can. It reads the
standard Claude Code hook JSON on stdin and prints a PreToolUse decision
on stdout.

Policy source: CLAUDE.md ("Scope boundary", "Permanent project rules")
and the HOOK POLICY section of the project bootstrap instructions.

HARD BLOCK (permissionDecision=deny):
  - writes/deletes targeting the sibling C:\\AdaptiveScalper project
  - real-money trading enablement
  - committing clear credentials/secrets (git commit with a secret-shaped
    string in the staged diff)
  - deleting the complete test suite
  - clearly destructive filesystem operations outside the project root
  - bypassPermissions / unrestricted permission bypass

WARNING (permissionDecision=ask):
  - individual test removal / large test-file shrink
  - force push
  - git reset --hard
  - git clean -f
  - writes to unusual external paths (outside project root, not covered
    by a hard block above)
  - edits to the immutable trading-safety boundary files
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Pure policy functions — unit tested directly by tests/test_guardrails.py
# ---------------------------------------------------------------------------

SIBLING_PROJECT_NAME = "AdaptiveScalper"          # C:\AdaptiveScalper (old project)
THIS_PROJECT_NAME = "AdaptiveScalperNext"          # C:\AdaptiveScalperNext

# Files whose content encodes the non-negotiable trading-safety boundary.
# Editing them is not blocked outright (the directive's architecture lives
# here and must be implementable) but is flagged for explicit confirmation.
SAFETY_BOUNDARY_HINTS = (
    "risk_governor",
    "kill_switch",
    "final_permission",
    "demo_gate",
    "allowed_canonical_symbols",
    "retired_strategy",
)

REAL_MONEY_PATTERNS = [
    re.compile(r"\bLIVE_TRADING\s*=\s*True\b"),
    re.compile(r"\breal[_-]?money\s*=\s*True\b", re.IGNORECASE),
    re.compile(r"\benable[_-]?live[_-]?trading\b", re.IGNORECASE),
    re.compile(r"\ballow[_-]?real[_-]?money\b", re.IGNORECASE),
    re.compile(r"\btrading_mode\s*=\s*[\"']?LIVE[\"']?", re.IGNORECASE),
    re.compile(r"\bACCOUNT_TYPE\s*=\s*[\"']?REAL[\"']?", re.IGNORECASE),
    re.compile(r"\bdemo[_-]?only\s*=\s*False\b", re.IGNORECASE),
    re.compile(r"\bDEMO_REQUIRED\s*=\s*False\b"),
]

SECRET_PATTERNS = [
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\b(password|passwd|pwd|secret|api[_-]?key|access[_-]?token)\s*[:=]\s*[\"'][^\"'\n]{6,}[\"']", re.IGNORECASE),
    re.compile(r"\bMT5_(PASSWORD|LOGIN|SECRET)\s*[:=]\s*[\"'][^\"'\n]{2,}[\"']", re.IGNORECASE),
]

BYPASS_PATTERNS = [
    re.compile(r"--dangerously-skip-permissions"),
    re.compile(r"\bbypassPermissions\b"),
    re.compile(r"\bdefaultMode[\"']?\s*[:=]\s*[\"']?bypassPermissions", re.IGNORECASE),
]

FULL_TEST_SUITE_DELETE_PATTERNS = [
    re.compile(r"\brm\s+-[a-z]*r[a-z]*f[a-z]*\s+.*\btests\b", re.IGNORECASE),
    re.compile(r"\brm\s+-[a-z]*f[a-z]*r[a-z]*\s+.*\btests\b", re.IGNORECASE),
    re.compile(r"Remove-Item\s+.*\btests\b.*-Recurse", re.IGNORECASE),
    re.compile(r"\brd\s+/s\s+/q\s+.*\btests\b", re.IGNORECASE),
    re.compile(r"git\s+rm\s+-r\s+.*\btests\b", re.IGNORECASE),
]

INDIVIDUAL_TEST_DELETE_PATTERNS = [
    re.compile(r"\brm\s+.*tests[\\/][\w.\-]*test_[\w.\-]*\.py", re.IGNORECASE),
    re.compile(r"Remove-Item\s+.*tests[\\/][\w.\-]*test_[\w.\-]*\.py", re.IGNORECASE),
    re.compile(r"git\s+rm\s+.*tests[\\/][\w.\-]*test_[\w.\-]*\.py", re.IGNORECASE),
]

FORCE_PUSH_RE = re.compile(r"git\s+(-C\s+\S+\s+)?push\b.*(--force(?!-with-lease)|(?<!-with-lease)\s-f\b)", re.IGNORECASE)
GIT_RESET_HARD_RE = re.compile(r"git\s+(-C\s+\S+\s+)?reset\s+--hard\b", re.IGNORECASE)
GIT_CLEAN_F_RE = re.compile(r"git\s+(-C\s+\S+\s+)?clean\s+.*-[a-z]*f", re.IGNORECASE)
GIT_COMMIT_RE = re.compile(r"git\s+(-C\s+\S+\s+)?commit\b")

DESTRUCTIVE_VERB_RE = re.compile(
    r"\b(rm\s+-[a-z]*r[a-z]*f|rm\s+-[a-z]*f[a-z]*r|Remove-Item\b.*-Recurse.*-Force|"
    r"del\s+/f\s+/s\s+/q|rd\s+/s\s+/q|format\s+[a-z]:)",
    re.IGNORECASE,
)
# Absolute Windows path, drive-letter form.
ABS_WIN_PATH_RE = re.compile(r"[A-Za-z]:[\\/][^\s\"']+")


def norm(path: str) -> str:
    return path.replace("/", "\\").rstrip("\\").lower()


def targets_sibling_project(path: str) -> bool:
    """True iff `path` points inside C:\\AdaptiveScalper (not ...Next)."""
    p = norm(path)
    marker = f"\\{SIBLING_PROJECT_NAME.lower()}"
    idx = p.find(marker)
    if idx == -1:
        # also match a bare drive-root form: "c:\adaptivescalper"
        if p.startswith(f"c:\\{SIBLING_PROJECT_NAME.lower()}"):
            idx = 2  # position right after "c:"
        else:
            return False
    end = idx + len(marker)
    # Reject if what follows immediately continues the name, e.g. "...Next"
    if end < len(p) and (p[end].isalnum()):
        return False
    return True


def is_outside_project_root(path: str, project_root: str) -> bool:
    p = Path(path)
    try:
        if not p.is_absolute():
            return False
        p.resolve()
    except (OSError, ValueError):
        pass
    root_norm = norm(project_root)
    path_norm = norm(str(path))
    return not path_norm.startswith(root_norm)


def contains_any(patterns, text: str) -> bool:
    return any(p.search(text) for p in patterns)


def find_abs_paths_outside_root(cmd: str, project_root: str):
    return [
        m for m in ABS_WIN_PATH_RE.findall(cmd)
        if is_outside_project_root(m, project_root)
    ]


def touches_safety_boundary(path: str) -> bool:
    low = path.lower()
    return any(h in low for h in SAFETY_BOUNDARY_HINTS)


# Paths where safety-pattern strings legitimately appear as data, not live
# code: test fixtures asserting the regexes work, and the hook scripts
# themselves. Content-based regex checks (real-money, secrets, bypass) are
# skipped here; path-based checks (sibling project, outside-root,
# safety-boundary filename) still apply everywhere, including here.
CONTENT_SCAN_EXEMPT_MARKERS = ("/tests/", "/.claude/hooks/", "/.claude/hookify-templates/")


def is_content_scan_exempt(path: str) -> bool:
    norm_path = "/" + path.replace("\\", "/").strip("/") + "/"
    return any(m in norm_path for m in CONTENT_SCAN_EXEMPT_MARKERS)


# ---------------------------------------------------------------------------
# Hook glue
# ---------------------------------------------------------------------------

def _project_root() -> str:
    return os.environ.get("CLAUDE_PROJECT_DIR") or str(Path(__file__).resolve().parents[2])


def _decision(decision: str, reason: str) -> dict:
    out = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision,
        }
    }
    if reason:
        out["hookSpecificOutput"]["permissionDecisionReason"] = reason
    return out


def _staged_diff(cwd: str) -> str:
    # Exclude paths where secret/real-money-shaped strings legitimately
    # appear as data (test fixtures, the pattern/guidance definitions
    # themselves) — mirrors is_content_scan_exempt's markers so a commit
    # of, say, tests/test_guardrails.py isn't blocked for containing the
    # literal fixture string it asserts detection of.
    try:
        r = subprocess.run(
            ["git", "diff", "--cached", "--", ".",
             ":(exclude)tests", ":(exclude).claude/hooks", ":(exclude).claude/hookify-templates"],
            cwd=cwd, capture_output=True, timeout=10, text=True,
        )
        return r.stdout or ""
    except Exception:
        return ""


def evaluate(input_data: dict) -> dict:
    project_root = _project_root()
    tool_name = input_data.get("tool_name", "")
    tool_input = input_data.get("tool_input", {}) or {}
    cwd = input_data.get("cwd") or project_root

    if tool_name in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
        file_path = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
        content_parts = []
        for key in ("content", "new_string"):
            if isinstance(tool_input.get(key), str):
                content_parts.append(tool_input[key])
        for edit in tool_input.get("edits", []) or []:
            if isinstance(edit, dict) and isinstance(edit.get("new_string"), str):
                content_parts.append(edit["new_string"])
        content = "\n".join(content_parts)

        if file_path and targets_sibling_project(file_path):
            return _decision("deny", f"BLOCKED: write targets the sibling C:\\{SIBLING_PROJECT_NAME} "
                                      f"project, which CLAUDE.md forbids modifying. Path: {file_path}")

        if file_path and is_outside_project_root(file_path, project_root):
            return _decision("ask", f"WARNING: write targets a path outside the project root "
                                     f"({project_root}): {file_path}. Confirm this is intentional.")

        exempt = bool(file_path) and is_content_scan_exempt(file_path)

        if content and not exempt and contains_any(REAL_MONEY_PATTERNS, content):
            return _decision("deny", "BLOCKED: this edit appears to enable real-money / live "
                                      "trading, which is permanently prohibited (CLAUDE.md rule 4).")

        if content and not exempt and contains_any(SECRET_PATTERNS, content):
            return _decision("deny", "BLOCKED: this edit appears to add a hardcoded credential/"
                                      "secret (CLAUDE.md rule 9).")

        if file_path and touches_safety_boundary(file_path):
            return _decision("ask", f"WARNING: this file appears to implement an immutable "
                                     f"trading-safety boundary ({file_path}). Confirm the change "
                                     f"preserves the safety guarantee rather than weakening it.")

        if file_path and "/tests/" in file_path.replace("\\", "/").lower() or \
           (file_path and file_path.replace("\\", "/").lower().startswith("tests/")):
            # Heuristic: a Write that drastically shrinks an existing test file.
            if tool_name == "Write" and isinstance(tool_input.get("content"), str):
                try:
                    existing = Path(file_path).read_text(encoding="utf-8", errors="ignore")
                    if len(existing.splitlines()) >= 10 and \
                       len(tool_input["content"].splitlines()) < 0.3 * len(existing.splitlines()):
                        return _decision("ask", f"WARNING: this rewrite shrinks a test file by "
                                                 f"more than 70% ({file_path}). Confirm tests are "
                                                 f"not being deleted to hide failures.")
                except OSError:
                    pass

        return _decision("allow", "")

    if tool_name == "Bash":
        cmd = tool_input.get("command", "") or ""

        if contains_any(BYPASS_PATTERNS, cmd):
            return _decision("deny", "BLOCKED: command attempts to bypass Claude Code's "
                                      "permission system (bypassPermissions / "
                                      "--dangerously-skip-permissions).")

        for m in re.finditer(ABS_WIN_PATH_RE, cmd):
            if targets_sibling_project(m.group(0)) and DESTRUCTIVE_VERB_RE.search(cmd):
                return _decision("deny", f"BLOCKED: destructive command targets the sibling "
                                          f"C:\\{SIBLING_PROJECT_NAME} project: {cmd}")

        if contains_any(FULL_TEST_SUITE_DELETE_PATTERNS, cmd):
            return _decision("deny", "BLOCKED: command appears to delete the entire tests/ "
                                      "directory (CLAUDE.md rule 8: never delete failing tests "
                                      "to make the suite green).")

        if DESTRUCTIVE_VERB_RE.search(cmd):
            outside = find_abs_paths_outside_root(cmd, project_root)
            if outside:
                return _decision("deny", f"BLOCKED: destructive filesystem command targets a "
                                          f"path outside the project root: {outside}")

        if contains_any(INDIVIDUAL_TEST_DELETE_PATTERNS, cmd):
            return _decision("ask", f"WARNING: command deletes an individual test file. "
                                     f"Confirm this isn't removing a failing test to go green: {cmd}")

        if FORCE_PUSH_RE.search(cmd):
            return _decision("ask", f"WARNING: force push detected: {cmd}")

        if GIT_RESET_HARD_RE.search(cmd):
            return _decision("ask", f"WARNING: git reset --hard discards uncommitted work: {cmd}")

        if GIT_CLEAN_F_RE.search(cmd):
            return _decision("ask", f"WARNING: git clean -f permanently deletes untracked files: {cmd}")

        if contains_any(REAL_MONEY_PATTERNS, cmd):
            return _decision("deny", "BLOCKED: command appears to enable real-money / live "
                                      "trading, which is permanently prohibited.")

        if GIT_COMMIT_RE.search(cmd):
            diff = _staged_diff(cwd)
            if diff and contains_any(SECRET_PATTERNS, diff):
                return _decision("deny", "BLOCKED: the staged diff contains what looks like a "
                                          "hardcoded credential/secret. Remove it before "
                                          "committing (CLAUDE.md rule 9).")
            if diff and contains_any(REAL_MONEY_PATTERNS, diff):
                return _decision("deny", "BLOCKED: the staged diff appears to enable "
                                          "real-money / live trading.")

        outside = find_abs_paths_outside_root(cmd, project_root) if DESTRUCTIVE_VERB_RE.search(cmd) else []
        if outside:
            return _decision("ask", f"WARNING: command touches paths outside the project root: {outside}")

        return _decision("allow", "")

    return _decision("allow", "")


def main() -> None:
    try:
        raw = sys.stdin.read()
        input_data = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        print(json.dumps(_decision("allow", "")))
        return
    try:
        result = evaluate(input_data)
    except Exception as exc:  # never crash-block the session over a bug in this hook
        result = _decision("allow", f"guardrails.py internal error, failing open: {exc}")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
