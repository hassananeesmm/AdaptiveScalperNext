"""Account-bound, append-only drawdown baseline (ASN-034; migration 0033).

The drawdown limit (`risk.max_drawdown_pct`) compares broker equity with the
PEAK equity of the SAME broker account (login + server). Every change of a
peak is one row in `peak_equity_history`; nothing updates or deletes a row
(SQLite triggers), so the full history of the baseline is auditable.

Before 0033 the peak was one unbound `runtime_state["peak_equity"]` value,
written only when the global entry block happened to reach the drawdown
step (never while the session window, news, the kill switch or the daily
loss lock blocked first), so equity highs made at those times were lost and
the drawdown was understated. `observe_equity` is now called on every
completed DEMO position cycle as well as at both permission rounds.

Fail-closed rules:
- the first observation on a database with NO baseline initializes it (a
  fresh install); the pre-0033 unbound value, if present, is adopted ONCE
  for the first account observed (never below current equity);
- an account with no baseline on a database that already has one for a
  DIFFERENT account is an account change: `observe_equity` blocks and never
  invents a baseline -- only `operator_reset` can record one;
- a baseline is only ever LOWERED (or bound to a new account) by
  `core.peak_equity_reset.operator_reset`: an OperatorAuthority, the kill
  switch ENGAGED, a flat book, a written reason, an evidence file (SHA-256
  recorded) and an explicit acknowledgement for a lower value. This module
  (risk/) never imports operator authority; there is no automatic reset.
"""

from __future__ import annotations

import math
import sqlite3
import time
from dataclasses import dataclass

from adaptive_scalper.runtime.state import get_state, put_state, record_event

LEGACY_STATE_KEY = "peak_equity"   # pre-0033 unbound value; still mirrored for observers (dashboard)

EVENT_INITIALIZED = "INITIALIZED"
EVENT_LEGACY_ADOPTED = "LEGACY_ADOPTED"
EVENT_RAISED = "RAISED"
EVENT_OPERATOR_RESET = "OPERATOR_RESET"

RUNTIME_ACTOR = "demo_runtime"


@dataclass(frozen=True)
class PeakEquityStatus:
    peak_equity: float | None          # None when blocked
    blocked_reason: str | None = None
    appended_event: str | None = None  # the row this observation appended, if any

    @property
    def blocked(self) -> bool:
        return self.blocked_reason is not None


def mask_login(login) -> str:
    """Account logins stay out of logs, events and documents (last 3 digits only)."""
    text = str(login)
    return "***" + text[-3:] if len(text) > 3 else "***"


def drawdown_pct(peak_equity: float, equity: float) -> float:
    return max(0.0, (peak_equity - equity) / peak_equity * 100.0)


def current_peak(conn: sqlite3.Connection, login: int, server: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM peak_equity_history WHERE account_login = ? AND account_server = ? ORDER BY id DESC LIMIT 1",
        (int(login), server),
    ).fetchone()


def history(conn: sqlite3.Connection, login: int | None = None, server: str | None = None) -> list[sqlite3.Row]:
    if login is None:
        return conn.execute("SELECT * FROM peak_equity_history ORDER BY id").fetchall()
    return conn.execute(
        "SELECT * FROM peak_equity_history WHERE account_login = ? AND account_server = ? ORDER BY id",
        (int(login), server),
    ).fetchall()


def append_row(conn: sqlite3.Connection, *, login: int, server: str, event_type: str, peak: float,
            previous: float | None, observed: float | None, actor: str, reason: str, now: int,
            evidence_path: str | None = None, evidence_sha256: str | None = None) -> None:
    conn.execute(
        "INSERT INTO peak_equity_history (account_login, account_server, event_type, peak_equity, "
        "previous_peak_equity, observed_equity, actor, reason, evidence_path, evidence_sha256, recorded_at_utc) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (int(login), server, event_type, float(peak), previous, observed, actor, reason, evidence_path,
         evidence_sha256, now),
    )


def _valid_amount(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0


def observe_equity(conn: sqlite3.Connection, *, login, server, equity, now_utc: int | None = None,
                   actor: str = RUNTIME_ACTOR) -> PeakEquityStatus:
    """Record `equity` of account (login, server) and return its drawdown
    baseline. Raises the peak (one RAISED row) on a new high; never lowers
    it. Blocked (no write) on an unusable account identity or equity, or on
    an account with no baseline while another account has one."""
    now = now_utc if now_utc is not None else int(time.time())
    if not isinstance(login, int) or isinstance(login, bool) or login <= 0 or not isinstance(server, str) \
            or not server.strip():
        return PeakEquityStatus(None, f"broker account identity unusable (login={login!r}, server={server!r})")
    if not _valid_amount(equity):
        return PeakEquityStatus(None, f"broker equity unusable ({equity!r})")
    row = current_peak(conn, login, server)
    if row is not None:
        peak = float(row["peak_equity"])
        if equity > peak:
            append_row(conn, login=login, server=server, event_type=EVENT_RAISED, peak=equity, previous=peak,
                    observed=equity, actor=actor, reason="broker equity above the recorded peak", now=now)
            put_state(conn, LEGACY_STATE_KEY, equity, now_utc=now)
            return PeakEquityStatus(equity, None, EVENT_RAISED)
        put_state(conn, LEGACY_STATE_KEY, peak, now_utc=now)
        return PeakEquityStatus(peak, None, None)

    other = conn.execute("SELECT account_login, account_server FROM peak_equity_history ORDER BY id DESC LIMIT 1") \
        .fetchone()
    if other is not None:
        reason = (f"no drawdown baseline for account {mask_login(login)} on {server!r} (the last baseline belongs to "
                  f"{mask_login(other['account_login'])} on {other['account_server']!r}): an account change never "
                  f"creates a baseline automatically -- an operator must record one with `peak-equity reset`")
        record_event(conn, "BLOCKED", "risk", "PEAK_EQUITY_ACCOUNT_UNBOUND", reason,
                     dedup_key=f"peak_equity_unbound:{login}:{server}", now_utc=now)
        return PeakEquityStatus(None, reason)

    legacy = get_state(conn, LEGACY_STATE_KEY)
    if _valid_amount(legacy):
        peak = max(float(legacy), float(equity))
        append_row(conn, login=login, server=server, event_type=EVENT_LEGACY_ADOPTED, peak=peak, previous=float(legacy),
                observed=equity, actor=actor, now=now,
                reason="pre-0033 unbound runtime_state peak_equity adopted for the first account observed after "
                       "migration 0033 (never below current equity)")
        event = EVENT_LEGACY_ADOPTED
    else:
        peak = float(equity)
        append_row(conn, login=login, server=server, event_type=EVENT_INITIALIZED, peak=peak, previous=None,
                observed=equity, actor=actor, now=now, reason="first equity observation on a database with no baseline")
        event = EVENT_INITIALIZED
    put_state(conn, LEGACY_STATE_KEY, peak, now_utc=now)
    record_event(conn, "INFO", "risk", "PEAK_EQUITY_BASELINE",
                 f"{event}: drawdown baseline {peak:.2f} bound to account {mask_login(login)} on {server!r}",
                 now_utc=now)
    return PeakEquityStatus(peak, None, event)
