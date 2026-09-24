"""PAPER session persistence (migration `0018_paper`).

A "session" is one (canonical_symbol, resolution) PAPER run, identified
by a caller-chosen `session_key` (default `f"PAPER:{symbol}:{resolution}"`).
Everything here is plain SQLite CRUD -- the actual decision logic lives
in `backtest.engine.run_backtest()`, reused unchanged by `paper/engine.py`.
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import asdict, dataclass

from adaptive_scalper.backtest.types import OpenPositionState, PendingEntryState, RegimeTrackerState, SimulatedTrade


@dataclass(frozen=True)
class PaperSessionState:
    session_key: str
    canonical_symbol: str
    resolution: str
    equity: float
    last_processed_bar_time_utc: int | None
    open_position: OpenPositionState | None
    regime_tracker_state: RegimeTrackerState | None
    pending_entry: PendingEntryState | None = None


def _row_to_session(row: sqlite3.Row) -> PaperSessionState:
    open_position = (
        OpenPositionState(**json.loads(row["open_position_json"])) if row["open_position_json"] else None
    )
    pending_entry = (
        PendingEntryState(**json.loads(row["pending_entry_json"])) if row["pending_entry_json"] else None
    )
    regime_tracker_state = (
        RegimeTrackerState(row["regime_confirmed"], row["regime_candidate"], row["regime_candidate_count"])
        if row["regime_confirmed"] is not None else None
    )
    return PaperSessionState(
        session_key=row["session_key"], canonical_symbol=row["canonical_symbol"], resolution=row["resolution"],
        equity=row["equity"], last_processed_bar_time_utc=row["last_processed_bar_time_utc"],
        open_position=open_position, regime_tracker_state=regime_tracker_state, pending_entry=pending_entry,
    )


def get_session(conn: sqlite3.Connection, session_key: str) -> PaperSessionState | None:
    row = conn.execute("SELECT * FROM paper_session_state WHERE session_key = ?", (session_key,)).fetchone()
    return _row_to_session(row) if row is not None else None


def get_or_create_session(
    conn: sqlite3.Connection, session_key: str, canonical_symbol: str, resolution: str, *,
    initial_equity: float, now_utc: int | None = None,
) -> PaperSessionState:
    existing = get_session(conn, session_key)
    if existing is not None:
        return existing
    now = now_utc if now_utc is not None else int(time.time())
    conn.execute(
        "INSERT INTO paper_session_state "
        "(session_key, canonical_symbol, resolution, equity, last_processed_bar_time_utc, "
        "open_position_json, regime_confirmed, regime_candidate, regime_candidate_count, "
        "created_at_utc, updated_at_utc) "
        "VALUES (?, ?, ?, ?, NULL, NULL, NULL, NULL, 0, ?, ?)",
        (session_key, canonical_symbol, resolution, initial_equity, now, now),
    )
    conn.commit()
    return PaperSessionState(
        session_key=session_key, canonical_symbol=canonical_symbol, resolution=resolution,
        equity=initial_equity, last_processed_bar_time_utc=None, open_position=None,
        regime_tracker_state=None,
    )


def save_session_state(
    conn: sqlite3.Connection, session_key: str, *,
    equity: float, last_processed_bar_time_utc: int, open_position: OpenPositionState | None,
    regime_tracker_state: RegimeTrackerState | None = None,
    pending_entry: PendingEntryState | None = None,
    now_utc: int | None = None,
) -> None:
    now = now_utc if now_utc is not None else int(time.time())
    open_position_json = json.dumps(asdict(open_position)) if open_position is not None else None
    pending_entry_json = json.dumps(asdict(pending_entry)) if pending_entry is not None else None
    regime_confirmed = regime_tracker_state.confirmed if regime_tracker_state is not None else None
    regime_candidate = regime_tracker_state.candidate if regime_tracker_state is not None else None
    regime_candidate_count = regime_tracker_state.candidate_count if regime_tracker_state is not None else 0
    conn.execute(
        "UPDATE paper_session_state SET equity = ?, last_processed_bar_time_utc = ?, "
        "open_position_json = ?, regime_confirmed = ?, regime_candidate = ?, regime_candidate_count = ?, "
        "pending_entry_json = ?, updated_at_utc = ? WHERE session_key = ?",
        (
            equity, last_processed_bar_time_utc, open_position_json, regime_confirmed, regime_candidate,
            regime_candidate_count, pending_entry_json, now, session_key,
        ),
    )


def record_paper_trades(
    conn: sqlite3.Connection, session_key: str, canonical_symbol: str, trades: tuple[SimulatedTrade, ...],
    *, now_utc: int | None = None,
) -> int:
    """Idempotent: a trade sharing `(session_key, entry_time_utc,
    direction)` with an already-recorded row is silently skipped (a
    retried cycle re-deriving the SAME deterministic trades is a safe
    no-op, never a duplicate row). Returns the number ACTUALLY inserted."""
    now = now_utc if now_utc is not None else int(time.time())
    inserted = 0
    for trade in trades:
        if not trade.is_closed:
            continue
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO paper_trades
                (session_key, canonical_symbol, strategy_key, direction, entry_time_utc, entry_price,
                 volume, initial_monetary_risk, entry_regime, exit_time_utc, exit_price, exit_reason,
                 exit_regime, realized_r, realized_pnl, total_cost, entry_features_json,
                 entry_raw_confidence, recorded_at_utc)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_key, canonical_symbol, trade.strategy_key, trade.direction, trade.entry_time_utc,
                trade.entry_price, trade.volume, trade.initial_monetary_risk, trade.entry_regime,
                trade.exit_time_utc, trade.exit_price, trade.exit_reason, trade.exit_regime,
                trade.realized_r, trade.realized_pnl, trade.total_cost,
                json.dumps(trade.entry_features) if trade.entry_features is not None else None,
                trade.entry_raw_confidence, now,
            ),
        )
        if cursor.rowcount > 0:
            inserted += 1
    return inserted


def get_paper_trades(conn: sqlite3.Connection, session_key: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM paper_trades WHERE session_key = ? ORDER BY entry_time_utc", (session_key,)
    ).fetchall()
