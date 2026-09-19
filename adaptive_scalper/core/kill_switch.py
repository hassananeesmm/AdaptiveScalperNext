"""Persistent kill switch.

Per MASTER_BUILD_DIRECTIVE.md section 37: state must survive restart, and
must never automatically clear. Per external architecture review, this
module additionally guarantees:

1. FAIL CLOSED ON UNKNOWN STATE. A missing, unreadable, or corrupted
   app_state row does NOT mean "disengaged". `get_state()` returns an
   explicit `KillSwitchStatus.UNINITIALIZED` (no row yet) or `.INVALID`
   (row present but unparseable/malformed) status, and
   `blocks_new_entries` is True for every status except the explicit
   `DISENGAGED` — there is no code path where "we don't know" reads as
   "safe to trade".
2. ATOMIC WRITE + AUDIT. Every state transition writes `app_state` and
   appends to `configuration_audit` inside one real transaction
   (BEGIN/COMMIT, ROLLBACK on any failure) — never as two independent
   autocommit statements. There is no way to observe a state change
   without its audit row, or vice versa.
3. TYPED OPERATOR CAPABILITY. `clear()` and `bootstrap()` require an
   `OperatorAuthority` instance (see `operator_authority.py`), not a bare
   role string. See that module's docstring for exactly what this does
   and does not enforce.

Any component may call `engage()` — that only ever makes the system
safer (risk governor, reconciliation, the engine itself all need to be
able to trip it directly). Only `bootstrap()` (first-time setup) and
`clear()` (recovery) require `OperatorAuthority`.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum

from adaptive_scalper.core.operator_authority import OperatorAuthority

STATE_KEY = "kill_switch"


class KillSwitchStatus(str, Enum):
    UNINITIALIZED = "UNINITIALIZED"  # no app_state row yet — never trade
    INVALID = "INVALID"              # row present but unparseable/malformed — never trade
    ENGAGED = "ENGAGED"
    DISENGAGED = "DISENGAGED"        # the ONLY status that permits new exposure


@dataclass(frozen=True)
class KillSwitchState:
    status: KillSwitchStatus
    reason: str | None
    changed_at: str | None
    changed_by: str | None

    @property
    def blocks_new_entries(self) -> bool:
        """Fail closed: everything except an explicit DISENGAGED blocks
        new exposure, including states we've never seen before."""
        return self.status != KillSwitchStatus.DISENGAGED

    def as_dict(self) -> dict:
        return {
            "status": self.status.value,
            "reason": self.reason,
            "changed_at": self.changed_at,
            "changed_by": self.changed_by,
        }


_UNINITIALIZED = KillSwitchState(
    status=KillSwitchStatus.UNINITIALIZED, reason=None, changed_at=None, changed_by=None,
)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def get_state(conn: sqlite3.Connection) -> KillSwitchState:
    row = conn.execute(
        "SELECT value FROM app_state WHERE key = ?", (STATE_KEY,)
    ).fetchone()
    if row is None:
        return _UNINITIALIZED

    try:
        data = json.loads(row["value"])
        status = KillSwitchStatus(data["status"])
        if status not in (KillSwitchStatus.ENGAGED, KillSwitchStatus.DISENGAGED):
            # A stored row should only ever record ENGAGED/DISENGAGED —
            # UNINITIALIZED/INVALID are synthesized, never persisted as
            # such. A row claiming otherwise is itself malformed.
            raise ValueError(f"persisted status {status!r} is not a valid stored state")
        return KillSwitchState(
            status=status,
            reason=data.get("reason"),
            changed_at=data.get("changed_at"),
            changed_by=data.get("changed_by"),
        )
    except (KeyError, ValueError, TypeError, json.JSONDecodeError):
        return KillSwitchState(
            status=KillSwitchStatus.INVALID, reason="unparseable app_state row",
            changed_at=None, changed_by=None,
        )


def _write(
    conn: sqlite3.Connection,
    old: KillSwitchState,
    new: KillSwitchState,
    actor: str,
    reason: str,
) -> None:
    """Write the new state AND its audit row atomically. Either both
    commit or neither does."""
    try:
        conn.execute("BEGIN")
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
        conn.execute("COMMIT")
    except sqlite3.Error:
        conn.execute("ROLLBACK")
        raise


def history(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Full, time-ordered audit trail of every state transition."""
    return conn.execute(
        "SELECT * FROM configuration_audit WHERE key = ? ORDER BY id ASC",
        (STATE_KEY,),
    ).fetchall()


def engage(conn: sqlite3.Connection, reason: str, actor: str) -> KillSwitchState:
    """Engage the kill switch. No role restriction — any safety component
    (risk governor, reconciliation, the engine itself) may call this
    directly, since it only ever makes the system safer.

    Idempotent in effect: engaging an already-ENGAGED switch still writes
    a fresh audit row (newest reason/actor/timestamp), but the resulting
    status is ENGAGED either way.
    """
    if not reason.strip():
        raise ValueError("engage() requires a non-empty reason")
    if not actor.strip():
        raise ValueError("engage() requires a non-empty actor")
    old = get_state(conn)
    new = KillSwitchState(
        status=KillSwitchStatus.ENGAGED, reason=reason, changed_at=_now(), changed_by=actor,
    )
    _write(conn, old, new, actor=actor, reason=reason)
    return new


def bootstrap(
    conn: sqlite3.Connection, authority: OperatorAuthority, reason: str = "initial bootstrap",
) -> KillSwitchState:
    """One-time, operator-authorized transition out of UNINITIALIZED/
    INVALID into DISENGAGED — the only way a fresh (or corrupted) system
    ever becomes tradeable. Idempotent: a no-op (returns the current
    state, writes nothing) if the switch is already ENGAGED or DISENGAGED,
    so it is safe to call unconditionally during every startup sequence.
    """
    if not isinstance(authority, OperatorAuthority):
        raise PermissionError("bootstrap() requires an OperatorAuthority instance")
    old = get_state(conn)
    if old.status in (KillSwitchStatus.ENGAGED, KillSwitchStatus.DISENGAGED):
        return old  # already initialized; not this function's job to change it
    if not reason.strip():
        raise ValueError("bootstrap() requires a non-empty reason")
    new = KillSwitchState(
        status=KillSwitchStatus.DISENGAGED, reason=reason, changed_at=_now(),
        changed_by=authority.operator_id,
    )
    _write(conn, old, new, actor=authority.operator_id, reason=reason)
    return new


def clear(conn: sqlite3.Connection, reason: str, authority: OperatorAuthority) -> KillSwitchState:
    """Clear (disengage) the kill switch. Requires an OperatorAuthority
    instance — see operator_authority.py for exactly what this does and
    does not enforce — and a non-empty reason."""
    if not isinstance(authority, OperatorAuthority):
        raise PermissionError(
            "clear() requires an OperatorAuthority instance, not a bare role string"
        )
    if not reason.strip():
        raise ValueError("clear() requires a non-empty reason")
    old = get_state(conn)
    new = KillSwitchState(
        status=KillSwitchStatus.DISENGAGED, reason=reason, changed_at=_now(),
        changed_by=authority.operator_id,
    )
    _write(conn, old, new, actor=authority.operator_id, reason=reason)
    return new
