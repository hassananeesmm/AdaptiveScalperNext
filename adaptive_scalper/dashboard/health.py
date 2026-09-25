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

import contextlib
import sqlite3
import threading
import time
from dataclasses import dataclass
from enum import Enum

from adaptive_scalper.core.kill_switch import get_state
from adaptive_scalper.gateway.protocol import Gateway
from adaptive_scalper.persistence.database import connect_readonly, integrity_check

INTEGRITY_PENDING = "PENDING"


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


class IntegrityMonitor:
    """Full `PRAGMA integrity_check`, off the request path, at most every
    `ttl_seconds` per database (the dashboard process).

    On the laptop's 218 MB database the check takes ~6 s; run on every panel
    refresh it made each 2-s dashboard update take 6 s. Each check uses its
    own read-only connection on a background thread; callers get the last
    result and when it was taken, or PENDING before the first one finishes
    (never reported as "ok"). The CLI `health`/`doctor` keep a synchronous check."""

    def __init__(self, ttl_seconds: float = 600.0) -> None:
        self.ttl_seconds = ttl_seconds
        self._lock = threading.Lock()
        self._results: dict[str, tuple[str, float]] = {}
        self._running: dict[str, threading.Thread] = {}

    def _check(self, db_path: str) -> None:
        try:
            with contextlib.closing(connect_readonly(db_path)) as conn:
                result = integrity_check(conn)
        except sqlite3.Error as exc:
            result = f"integrity check could not run: {type(exc).__name__}: {exc}"
        with self._lock:
            self._results[db_path] = (result, time.time())
            self._running.pop(db_path, None)

    def status(self, db_path: str, wait_seconds: float = 0.5) -> tuple[str, float | None]:
        """(result or PENDING, epoch seconds of that result or None)."""
        db_path = str(db_path)
        with self._lock:
            cached = self._results.get(db_path)
            thread = self._running.get(db_path)
            if thread is None and (cached is None or time.time() - cached[1] >= self.ttl_seconds):
                thread = threading.Thread(target=self._check, args=(db_path,), name="integrity-check", daemon=True)
                self._running[db_path] = thread
                thread.start()
        if cached is None and thread is not None:
            thread.join(timeout=wait_seconds)
            with self._lock:
                cached = self._results.get(db_path)
        return cached if cached is not None else (INTEGRITY_PENDING, None)


DASHBOARD_INTEGRITY = IntegrityMonitor()


def compute_health(
    conn: sqlite3.Connection, gateway: Gateway | None = None, *, integrity: str | None = None,
) -> HealthReport:
    """Compose current health from whatever subsystems exist today.

    Precedence (highest wins): CRITICAL (database integrity failure) >
    TRADING_BLOCKED (kill switch blocks new entries — this includes
    UNINITIALIZED/INVALID, not just ENGAGED, since the kill switch fails
    closed) > NEW_ENTRIES_BLOCKED (gateway supplied but not connected) >
    HEALTHY.
    """
    reasons: list[str] = []

    if integrity is None:  # `integrity`: a recent IntegrityMonitor result, instead of checking now
        integrity = integrity_check(conn)
    if integrity == INTEGRITY_PENDING:
        reasons.append("database integrity check pending (first check still running)")
    elif integrity != "ok":
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

    if integrity not in ("ok", INTEGRITY_PENDING):
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
