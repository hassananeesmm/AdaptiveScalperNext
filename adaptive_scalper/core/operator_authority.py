"""Operator-command capability boundary.

Per external architecture review: a bare `actor_role="operator"` string
argument is not a real access boundary — any caller (a strategy, an ML
model, RAG, the selector) can pass that exact string and nothing stops it.

`OperatorAuthority` replaces the string. It is a typed object that must be
explicitly constructed and passed to `kill_switch.clear()` /
`kill_switch.bootstrap()`. This is intentionally named and documented as a
CAPABILITY, not a password: Python has no language-level mechanism to stop
arbitrary code from doing `OperatorAuthority("whoever")` and calling
`clear()` with it — the real enforcement boundary is code review plus
which packages are ever allowed to import this module.

Intended construction site: `adaptive_scalper/cli/operator.py` operator commands
(`kill-switch bootstrap` / `kill-switch clear`). The dashboard is observer-only and has no
operator-action endpoint. `adaptive_scalper/strategies/`,
`adaptive_scalper/learning/`, `adaptive_scalper/rag/`, and
`adaptive_scalper/portfolio/`/`risk/` (the risk governor engages, it does
not clear) must never import this module — if a review ever finds such an
import, that is the finding, not a bypass to patch reactively.

Future hardening (once the CLI/dashboard operator-auth boundary exists):
require `operator_id` to match an authenticated session and/or require an
explicit confirmation token, checked inside `__init__` rather than trusted
from the caller. That upgrade is transparent to every existing caller of
`kill_switch.clear()`/`bootstrap()` since they only ever handle the
resulting object, never its internals.
"""

from __future__ import annotations

from datetime import datetime, timezone


class OperatorAuthority:
    """Proof that an explicit operator action authorized a kill-switch
    state change. See module docstring for what this does and does not
    guarantee."""

    __slots__ = ("operator_id", "granted_at")

    def __init__(self, operator_id: str) -> None:
        if not operator_id or not operator_id.strip():
            raise ValueError("OperatorAuthority requires a non-empty operator_id")
        self.operator_id = operator_id
        self.granted_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")

    def __repr__(self) -> str:  # no secrets to redact, but keep it tidy
        return f"OperatorAuthority(operator_id={self.operator_id!r}, granted_at={self.granted_at!r})"
