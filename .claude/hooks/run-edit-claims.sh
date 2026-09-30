#!/usr/bin/env bash
# Resolve a Python 3 interpreter and exec edit_claims.py with it (same
# resolution order as run-guardrails.sh). No interpreter: print nothing and
# exit 0 -- this guard fails open and never overrides another hook.
set -u

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$DIR/../.." && pwd)"

for c in "$PROJECT_ROOT/.venv/Scripts/python.exe" "$PROJECT_ROOT/.venv/bin/python"; do
    if [ -x "$c" ]; then
        exec "$c" "$DIR/edit_claims.py"
    fi
done

for cmd in python3 python; do
    if command -v "$cmd" >/dev/null 2>&1; then
        exec "$cmd" "$DIR/edit_claims.py"
    fi
done

if command -v py >/dev/null 2>&1; then
    exec py -3 "$DIR/edit_claims.py"
fi
exit 0
