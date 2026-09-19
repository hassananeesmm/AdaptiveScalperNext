#!/usr/bin/env bash
# Resolve a working Python 3 interpreter and exec guardrails.py with it.
# Prefers the project's own venv (canonical interpreter per CLAUDE.md) so
# behavior is identical to `pytest`; falls back to any python3 on PATH so
# the hook still fails safe (allow) on a machine with no venv yet, rather
# than silently no-op'ing because bash itself couldn't find an interpreter.
set -u

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$DIR/../.." && pwd)"

candidates=(
    "$PROJECT_ROOT/.venv/Scripts/python.exe"
    "$PROJECT_ROOT/.venv/bin/python"
)

for c in "${candidates[@]}"; do
    if [ -x "$c" ]; then
        exec "$c" "$DIR/guardrails.py"
    fi
done

for cmd in python3 python; do
    if command -v "$cmd" >/dev/null 2>&1; then
        exec "$cmd" "$DIR/guardrails.py"
    fi
done

if command -v py >/dev/null 2>&1; then
    exec py -3 "$DIR/guardrails.py"
fi

# No interpreter at all — fail open rather than silently blocking every
# tool call. scripts/setup_claude_hooks.ps1 flags this state.
echo '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"allow","permissionDecisionReason":"guardrails.py: no python interpreter found, failing open"}}'
