"""System health computation.

Per MASTER_BUILD_DIRECTIVE.md section 107: HEALTHY / DEGRADED /
NEW_ENTRIES_BLOCKED / TRADING_BLOCKED / CRITICAL.

This is Phase 1's "basic dashboard health" (directive section 117) — it
composes only the subsystems that exist today (database integrity, the
kill switch, and the gateway's connection state if one is supplied).
News/RAG/model/learning health are added as those subsystems land, per
section 118's "no showpiece modules" — this module isn't pre-declaring
fields for things that don't exist yet.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from enum import Enum

from adaptive_scalper.core.kill_switch import KillSwitchStatus, get_state
from adaptive_scalper.gateway.protocol import Gateway
from adaptive_scalper.persistence.database import integrity_check


class HealthState(str, Enum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    NEW_ENTRIES_BLOCKED = "NEW_ENTRIES_BLOCKED"
    TRADING_BLOCKED = "TRADING_BLOCKED"
    CRITICAL = "CRITICAL"


@dataclass(frozen=True)
class HealthReport:
    state: HealthState
    reasons: tuple[str, ...]
    database_integrity: str
    kill_switch_status: str
    kill_switch_blocks_new_entries: bool
    kill_switch_reason: str | None
    mt5_connected: bool | None  # None = no gateway supplied, i.e. not checked


def compute_health(conn: sqlite3.Connection, gateway: Gateway | None = None) -> HealthReport:
    """Compose current health from whatever subsystems exist today.

    Precedence (highest wins): CRITICAL (database integrity failure) >
    TRADING_BLOCKED (kill switch blocks new entries — this includes
    UNINITIALIZED/INVALID, not just ENGAGED, since the kill switch fails
    closed) > NEW_ENTRIES_BLOCKED (gateway supplied but not connected) >
    HEALTHY.
    """
    reasons: list[str] = []

    integrity = integrity_check(conn)
    if integrity != "ok":
        reasons.append(f"database integrity check failed: {integrity}")

    ks = get_state(conn)
    if ks.blocks_new_entries:
        reasons.append(f"kill switch status={ks.status.value}: {ks.reason}")

    mt5_connected: bool | None = None
    if gateway is not None:
        try:
            terminal = gateway.terminal_info()
            mt5_connected = bool(terminal and terminal.connected)
        except Exception as exc:  # gateway calls must never crash the dashboard
            mt5_connected = False
            reasons.append(f"gateway.terminal_info() raised {type(exc).__name__}: {exc}")
        if mt5_connected is False:
            reasons.append("MT5 terminal not connected")

    if integrity != "ok":
        state = HealthState.CRITICAL
    elif ks.blocks_new_entries:
        state = HealthState.TRADING_BLOCKED
    elif gateway is not None and mt5_connected is False:
        state = HealthState.NEW_ENTRIES_BLOCKED
    else:
        state = HealthState.HEALTHY

    return HealthReport(
        state=state,
        reasons=tuple(reasons),
        database_integrity=integrity,
        kill_switch_status=ks.status.value,
        kill_switch_blocks_new_entries=ks.blocks_new_entries,
        kill_switch_reason=ks.reason,
        mt5_connected=mt5_connected,
    )
