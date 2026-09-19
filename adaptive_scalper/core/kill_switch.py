"""Persistent kill switch.

Per MASTER_BUILD_DIRECTIVE.md section 37: state must survive restart, and
must never automatically clear. ENGAGED means no new exposure; existing
safe position management and reconciliation may continue (see
``adaptive_scalper.core.permission`` for the kill-switch slice of the
final-permission-gate policy — this module only tracks the flag and its
audit trail).

Any component may call engage() (that only ever makes the system safer —
risk governors, reconciliation, the engine itself all need to be able to
trip it). Only an explicit operator action may call clear(): clear()
requires actor_role="operator" and raises PermissionError for every other
role, so ML/RAG/strategy/model code cannot clear it through this API no
matter what actor name it passes.

Every engage()/clear() call appends one row to configuration_audit
(old state -> new state, actor, reason), so the full transition history is
reviewable and each call's effect is deterministic no matter how many
times it is repeated.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

STATE_KEY = "kill_switch"

# The only actor_role permitted to clear() the kill switch. There is
# deliberately no way to widen this set from config or at the call site.
AUTHORIZED_CLEAR_ROLES: frozenset[str] = frozenset({"operator"})


@dataclass(frozen=True)
class KillSwitchState:
    engaged: bool
    reason: str | None
    changed_at: str | None
    changed_by: str | None

    @property
    def blocks_new_entries(self) -> bool:
        return self.engaged

    def as_dict(self) -> dict:
        return {
            "engaged": self.engaged,
            "reason": self.reason,
            "changed_at": self.changed_at,
            "changed_by": self.changed_by,
        }


_DEFAULT = KillSwitchState(engaged=False, reason=None, changed_at=None, changed_by=None)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def get_state(conn: sqlite3.Connection) -> KillSwitchState:
    row = conn.execute(
        "SELECT value FROM app_state WHERE key = ?", (STATE_KEY,)
    ).fetchone()
    if row is None:
        return _DEFAULT
    data = json.loads(row["value"])
    return KillSwitchState(
        engaged=data["engaged"],
        reason=data.get("reason"),
        changed_at=data.get("changed_at"),
        changed_by=data.get("changed_by"),
    )


def _write(
    conn: sqlite3.Connection,
    old: KillSwitchState,
    new: KillSwitchState,
    actor: str,
    reason: str,
) -> None:
    conn.execute(
        """
        INSERT INTO app_state (key, value, updated_at) VALUES (?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET
            value = excluded.value,
            updated_at = excluded.updated_at
        """,
        (STATE_KEY, json.dumps(new.as_dict()), new.changed_at),
    )
    conn.execute(
        """
        INSERT INTO configuration_audit
            (changed_at, actor, key, old_value, new_value, reason)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            new.changed_at,
            actor,
            STATE_KEY,
            json.dumps(old.as_dict()),
            json.dumps(new.as_dict()),
            reason,
        ),
    )


def history(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Full, time-ordered audit trail of every engage()/clear() transition."""
    return conn.execute(
        "SELECT * FROM configuration_audit WHERE key = ? ORDER BY id ASC",
        (STATE_KEY,),
    ).fetchall()


def engage(conn: sqlite3.Connection, reason: str, actor: str) -> KillSwitchState:
    """Engage the kill switch.

    Idempotent: engaging an already-engaged switch still records the
    newest reason/actor/timestamp and appends a fresh audit row, but the
    resulting state (engaged=True) is the same regardless of how many
    times this is called.
    """
    if not reason.strip():
        raise ValueError("engage() requires a non-empty reason")
    if not actor.strip():
        raise ValueError("engage() requires a non-empty actor")
    old = get_state(conn)
    new = KillSwitchState(engaged=True, reason=reason, changed_at=_now(), changed_by=actor)
    _write(conn, old, new, actor=actor, reason=reason)
    return new


def clear(conn: sqlite3.Connection, reason: str, actor: str, actor_role: str) -> KillSwitchState:
    """Clear the kill switch.

    Requires a non-empty reason, a non-empty actor, AND actor_role
    exactly "operator". Any other role — including anything an ML model,
    RAG component, strategy, or the selector might plausibly pass — is
    rejected with PermissionError before any state is touched.
    """
    if actor_role not in AUTHORIZED_CLEAR_ROLES:
        raise PermissionError(
            f"actor_role={actor_role!r} is not authorized to clear the kill "
            f"switch; only {sorted(AUTHORIZED_CLEAR_ROLES)} may do so"
        )
    if not reason.strip():
        raise ValueError("clear() requires a non-empty reason")
    if not actor.strip():
        raise ValueError("clear() requires a non-empty actor")
    old = get_state(conn)
    new = KillSwitchState(engaged=False, reason=reason, changed_at=_now(), changed_by=actor)
    _write(conn, old, new, actor=actor, reason=reason)
    return new
