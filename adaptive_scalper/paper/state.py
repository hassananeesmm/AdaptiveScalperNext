"""PAPER session persistence (migrations `0018_paper` + `0019_paper_pending_entry`).

A "session" is one (canonical_symbol, resolution) PAPER run, identified
by a caller-chosen `session_key` (default `f"PAPER:{symbol}:{resolution}"`).
Everything here is plain SQLite CRUD -- the actual decision logic lives
in `backtest.engine.run_backtest()`, reused unchanged by `paper/engine.py`.
"""

from __future__ import annotations

import json
import math
import sqlite3
import time
from dataclasses import asdict, dataclass

from adaptive_scalper.backtest.types import (
    OpenPositionState,
    PendingEntryState,
    RegimeTrackerState,
    SimulatedTrade,
)
from adaptive_scalper.strategies.base import StrategySignal

PENDING_ENTRY_STATE_VERSION = 1


class PaperStateError(ValueError):
    """Raised when durable PAPER state is malformed or internally stale.

    Fail closed: a corrupt deferred entry must never be silently dropped
    and replaced by a fresh decision, because that would change causal
    behavior after restart.
    """


@dataclass(frozen=True)
class PaperSessionState:
    session_key: str
    canonical_symbol: str
    resolution: str
    equity: float
    last_processed_bar_time_utc: int | None
    open_position: OpenPositionState | None
    pending_entry: PendingEntryState | None
    regime_tracker_state: RegimeTrackerState | None


def _serialize_pending_entry(state: PendingEntryState | None) -> str | None:
    if state is None:
        return None
    return json.dumps(
        {
            "version": PENDING_ENTRY_STATE_VERSION,
            "signal": asdict(state.signal),
            "entry_features": state.entry_features,
            "signal_bar_time_utc": state.signal_bar_time_utc,
            "expires_at_utc": state.expires_at_utc,
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _require_json_int(payload: dict, key: str) -> int:
    value = payload[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{key} must be an integer")
    return value


def _validate_entry_features(features) -> dict[str, float | None] | None:
    if features is None:
        return None
    if not isinstance(features, dict):
        raise TypeError("pending entry entry_features must be an object or null")
    validated: dict[str, float | None] = {}
    for key, value in features.items():
        if not isinstance(key, str):
            raise TypeError("pending entry feature names must be strings")
        if value is None:
            validated[key] = None
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError(f"pending entry feature {key!r} must be numeric or null")
        numeric = float(value)
        if not math.isfinite(numeric):
            raise ValueError(f"pending entry feature {key!r} must be finite")
        validated[key] = numeric
    return validated


def _validate_signal(signal: StrategySignal) -> None:
    if not isinstance(signal.strategy_key, str) or not signal.strategy_key:
        raise TypeError("pending entry strategy_key must be a non-empty string")
    if isinstance(signal.strategy_version, bool) or not isinstance(signal.strategy_version, int) or signal.strategy_version <= 0:
        raise TypeError("pending entry strategy_version must be a positive integer")
    if not isinstance(signal.canonical_symbol, str) or not signal.canonical_symbol:
        raise TypeError("pending entry canonical_symbol must be a non-empty string")
    for name in ("raw_confidence", "stop_distance", "target_distance"):
        value = getattr(signal, name)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
            raise TypeError(f"pending entry signal {name} must be a finite number")
    if isinstance(signal.expected_duration_seconds, bool) or not isinstance(signal.expected_duration_seconds, int):
        raise TypeError("pending entry expected_duration_seconds must be an integer")
    if signal.expected_duration_seconds <= 0:
        raise ValueError("pending entry expected_duration_seconds must be positive")
    for name in ("entry_method", "regime", "rationale"):
        if not isinstance(getattr(signal, name), str):
            raise TypeError(f"pending entry signal {name} must be a string")
    if isinstance(signal.feature_schema_version, bool) or not isinstance(signal.feature_schema_version, int):
        raise TypeError("pending entry feature_schema_version must be an integer")
    if signal.feature_schema_version <= 0:
        raise ValueError("pending entry feature_schema_version must be positive")
    if isinstance(signal.data_timestamp, bool) or not isinstance(signal.data_timestamp, int):
        raise TypeError("pending entry signal data_timestamp must be an integer")
    if signal.data_timestamp <= 0:
        raise ValueError("pending entry signal data_timestamp must be positive")


def _deserialize_pending_entry(raw: str | None) -> PendingEntryState | None:
    if raw is None:
        return None
    try:
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise TypeError("pending entry payload must be an object")
        if payload.get("version") != PENDING_ENTRY_STATE_VERSION:
            raise ValueError(
                f"unsupported pending entry state version {payload.get('version')!r}"
            )
        signal_payload = payload["signal"]
        if not isinstance(signal_payload, dict):
            raise TypeError("pending entry signal must be an object")
        signal = StrategySignal(**signal_payload)
        _validate_signal(signal)
        features = _validate_entry_features(payload.get("entry_features"))
        return PendingEntryState(
            signal=signal,
            entry_features=features,
            signal_bar_time_utc=_require_json_int(payload, "signal_bar_time_utc"),
            expires_at_utc=_require_json_int(payload, "expires_at_utc"),
        )
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise PaperStateError(f"invalid pending PAPER entry state: {exc}") from exc


def _row_to_session(row: sqlite3.Row) -> PaperSessionState:
    open_position = (
        OpenPositionState(**json.loads(row["open_position_json"])) if row["open_position_json"] else None
    )
    pending_entry = _deserialize_pending_entry(row["pending_entry_json"])
    regime_tracker_state = (
        RegimeTrackerState(row["regime_confirmed"], row["regime_candidate"], row["regime_candidate_count"])
        if row["regime_confirmed"] is not None else None
    )
    if open_position is not None and pending_entry is not None:
        raise PaperStateError("PAPER session cannot contain both an open position and a pending entry")
    if pending_entry is not None:
        cursor = row["last_processed_bar_time_utc"]
        if cursor is None or pending_entry.signal_bar_time_utc != cursor:
            raise PaperStateError(
                "stale pending PAPER entry: signal bar must equal the session cursor "
                f"({pending_entry.signal_bar_time_utc} != {cursor})"
            )
        if pending_entry.signal.canonical_symbol != row["canonical_symbol"]:
            raise PaperStateError(
                "pending PAPER entry symbol does not match its session "
                f"({pending_entry.signal.canonical_symbol!r} != {row['canonical_symbol']!r})"
            )
    return PaperSessionState(
        session_key=row["session_key"], canonical_symbol=row["canonical_symbol"], resolution=row["resolution"],
        equity=row["equity"], last_processed_bar_time_utc=row["last_processed_bar_time_utc"],
        open_position=open_position, pending_entry=pending_entry, regime_tracker_state=regime_tracker_state,
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
        if existing.canonical_symbol != canonical_symbol or existing.resolution != resolution:
            raise PaperStateError(
                "PAPER session identity mismatch: existing session "
                f"{session_key!r} is {existing.canonical_symbol}/{existing.resolution}, "
                f"requested {canonical_symbol}/{resolution}"
            )
        return existing
    now = now_utc if now_utc is not None else int(time.time())
    conn.execute(
        "INSERT INTO paper_session_state "
        "(session_key, canonical_symbol, resolution, equity, last_processed_bar_time_utc, "
        "open_position_json, pending_entry_json, regime_confirmed, regime_candidate, regime_candidate_count, "
        "created_at_utc, updated_at_utc) "
        "VALUES (?, ?, ?, ?, NULL, NULL, NULL, NULL, NULL, 0, ?, ?)",
        (session_key, canonical_symbol, resolution, initial_equity, now, now),
    )
    conn.commit()
    return PaperSessionState(
        session_key=session_key, canonical_symbol=canonical_symbol, resolution=resolution,
        equity=initial_equity, last_processed_bar_time_utc=None, open_position=None,
        pending_entry=None, regime_tracker_state=None,
    )


def save_session_state(
    conn: sqlite3.Connection, session_key: str, *,
    equity: float, last_processed_bar_time_utc: int, open_position: OpenPositionState | None,
    pending_entry: PendingEntryState | None = None,
    regime_tracker_state: RegimeTrackerState | None = None,
    now_utc: int | None = None,
) -> None:
    now = now_utc if now_utc is not None else int(time.time())
    if open_position is not None and pending_entry is not None:
        raise PaperStateError("cannot persist both an open position and a pending entry")
    if pending_entry is not None:
        if pending_entry.signal_bar_time_utc != last_processed_bar_time_utc:
            raise PaperStateError(
                "cannot persist stale pending entry: signal bar must equal last_processed_bar_time_utc"
            )
        row = conn.execute(
            "SELECT canonical_symbol FROM paper_session_state WHERE session_key = ?", (session_key,)
        ).fetchone()
        if row is None:
            raise PaperStateError(f"unknown PAPER session {session_key!r}")
        if pending_entry.signal.canonical_symbol != row["canonical_symbol"]:
            raise PaperStateError("pending entry canonical symbol does not match PAPER session")
    open_position_json = json.dumps(asdict(open_position)) if open_position is not None else None
    pending_entry_json = _serialize_pending_entry(pending_entry)
    regime_confirmed = regime_tracker_state.confirmed if regime_tracker_state is not None else None
    regime_candidate = regime_tracker_state.candidate if regime_tracker_state is not None else None
    regime_candidate_count = regime_tracker_state.candidate_count if regime_tracker_state is not None else 0
    conn.execute(
        "UPDATE paper_session_state SET equity = ?, last_processed_bar_time_utc = ?, "
        "open_position_json = ?, pending_entry_json = ?, regime_confirmed = ?, regime_candidate = ?, "
        "regime_candidate_count = ?, updated_at_utc = ? WHERE session_key = ?",
        (
            equity, last_processed_bar_time_utc, open_position_json, pending_entry_json, regime_confirmed,
            regime_candidate, regime_candidate_count, now, session_key,
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
