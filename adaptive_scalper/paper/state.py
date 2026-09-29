"""PAPER session persistence (migrations `0018_paper`, `0019`, `0020`).

A "session" is one (canonical_symbol, resolution) PAPER run, identified
by a caller-chosen `session_key` (default `f"PAPER:{symbol}:{resolution}"`)
AND bound to the configuration fingerprint it was created under. Everything
here is plain SQLite CRUD -- the actual decision logic lives in
`backtest.engine.run_backtest()`, reused unchanged by `paper/engine.py`.
"""

from __future__ import annotations

import json
import math
import sqlite3
import time
from dataclasses import asdict, dataclass

from adaptive_scalper.backtest.persistence import TRADE_PROVENANCE_COLUMNS, trade_provenance_values
from adaptive_scalper.backtest.types import (
    OpenPositionState,
    PendingEntryState,
    RegimeTrackerState,
    RiskState,
    SimulatedTrade,
)


class PaperStateError(ValueError):
    """Durable PAPER state is malformed or internally inconsistent.

    Fail closed: a corrupt deferred entry is never silently dropped or
    re-decided, because that would change causal behaviour after a restart
    (ported from PR #3, fix/paper-pending-entry-persistence-20260928)."""


_PENDING_NUMERIC_FIELDS = ("stop_distance", "target_distance", "raw_confidence")
_PENDING_OPTIONAL_NUMERIC_FIELDS = ("estimated_cost_price", "expected_net_edge_price")
_PENDING_OPTIONAL_STRING_FIELDS = ("canonical_symbol", "entry_method", "fingerprint")
_PENDING_OPTIONAL_POSITIVE_INT_FIELDS = ("strategy_version", "expected_duration_seconds")

# Serialization version of `pending_entry_json`. Rows written before the
# version existed carry no `state_version` key and are decoded as version 1
# (their field set is identical); any OTHER explicit value -- including a
# JSON boolean, which Python would otherwise accept as int 1 -- fails closed.
PENDING_ENTRY_STATE_VERSION = 1
_VERSION_KEY = "state_version"


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_finite_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _dump_pending_entry(pending: PendingEntryState | None) -> str | None:
    if pending is None:
        return None
    return json.dumps({_VERSION_KEY: PENDING_ENTRY_STATE_VERSION, **asdict(pending)}, allow_nan=False)


def _validate_pending_entry(pending: PendingEntryState) -> None:
    if pending.direction not in ("BUY", "SELL"):
        raise ValueError(f"direction must be BUY or SELL, got {pending.direction!r}")
    if not isinstance(pending.strategy_key, str) or not pending.strategy_key:
        raise TypeError("strategy_key must be a non-empty string")
    if not isinstance(pending.regime, str) or not pending.regime:
        raise TypeError("regime must be a non-empty string")
    for name in _PENDING_NUMERIC_FIELDS:
        if not _is_finite_number(getattr(pending, name)):
            raise TypeError(f"{name} must be a finite number")
    if pending.stop_distance <= 0 or pending.target_distance <= 0:
        raise ValueError("stop_distance and target_distance must be positive")
    if not _is_int(pending.signal_time_utc):
        raise TypeError("signal_time_utc must be an integer")
    for name in _PENDING_OPTIONAL_NUMERIC_FIELDS:
        value = getattr(pending, name)
        if value is not None and not _is_finite_number(value):
            raise TypeError(f"{name} must be a finite number or null")
    for name in _PENDING_OPTIONAL_STRING_FIELDS:
        value = getattr(pending, name)
        if value is not None and (not isinstance(value, str) or not value):
            raise TypeError(f"{name} must be a non-empty string or null")
    for name in _PENDING_OPTIONAL_POSITIVE_INT_FIELDS:
        value = getattr(pending, name)
        if value is not None and (not _is_int(value) or value <= 0):
            raise TypeError(f"{name} must be a positive integer or null")
    features = pending.entry_features
    if features is not None:
        if not isinstance(features, dict):
            raise TypeError("entry_features must be an object or null")
        for key, value in features.items():
            if not isinstance(key, str):
                raise TypeError("entry_features names must be strings")
            if value is not None and not _is_finite_number(value):
                raise TypeError(f"entry feature {key!r} must be a finite number or null")


def _load_pending_entry(raw: str | None) -> PendingEntryState | None:
    if not raw:
        return None
    try:
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise TypeError("pending entry payload must be a JSON object")
        version = payload.pop(_VERSION_KEY, PENDING_ENTRY_STATE_VERSION)
        if not _is_int(version) or version != PENDING_ENTRY_STATE_VERSION:
            raise ValueError(f"unsupported pending entry state version {version!r}")
        pending = PendingEntryState(**payload)
        _validate_pending_entry(pending)
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise PaperStateError(f"invalid pending PAPER entry state: {exc}") from exc
    return pending


def _check_pending_consistency(
    pending: PendingEntryState | None, open_position: OpenPositionState | None, *,
    cursor: int | None, canonical_symbol: str | None,
) -> None:
    if pending is None:
        return
    if open_position is not None:
        raise PaperStateError("a PAPER session cannot hold both an open position and a pending entry")
    if cursor is None or pending.signal_time_utc != cursor:
        raise PaperStateError(
            f"stale pending PAPER entry: signal bar {pending.signal_time_utc} != session cursor {cursor}"
        )
    if pending.canonical_symbol is not None and canonical_symbol is not None             and pending.canonical_symbol != canonical_symbol:
        raise PaperStateError(
            f"pending PAPER entry is for {pending.canonical_symbol!r}, session is {canonical_symbol!r}"
        )


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
    risk_state: RiskState | None = None
    config_fingerprint: str | None = None
    config_json: str | None = None


def _row_to_session(row: sqlite3.Row) -> PaperSessionState:
    def load(column: str, cls):
        return cls(**json.loads(row[column])) if row[column] else None

    regime_tracker_state = (
        RegimeTrackerState(row["regime_confirmed"], row["regime_candidate"], row["regime_candidate_count"])
        if row["regime_confirmed"] is not None else None
    )
    open_position = load("open_position_json", OpenPositionState)
    pending_entry = _load_pending_entry(row["pending_entry_json"])
    _check_pending_consistency(
        pending_entry, open_position, cursor=row["last_processed_bar_time_utc"],
        canonical_symbol=row["canonical_symbol"],
    )
    return PaperSessionState(
        session_key=row["session_key"], canonical_symbol=row["canonical_symbol"], resolution=row["resolution"],
        equity=row["equity"], last_processed_bar_time_utc=row["last_processed_bar_time_utc"],
        open_position=open_position, regime_tracker_state=regime_tracker_state,
        pending_entry=pending_entry, risk_state=load("risk_state_json", RiskState),
        config_fingerprint=row["config_fingerprint"], config_json=row["config_json"],
    )


def get_session(conn: sqlite3.Connection, session_key: str) -> PaperSessionState | None:
    row = conn.execute("SELECT * FROM paper_session_state WHERE session_key = ?", (session_key,)).fetchone()
    return _row_to_session(row) if row is not None else None


def list_sessions(conn: sqlite3.Connection) -> list[PaperSessionState]:
    return [_row_to_session(r) for r in conn.execute("SELECT * FROM paper_session_state ORDER BY session_key")]


def get_or_create_session(
    conn: sqlite3.Connection, session_key: str, canonical_symbol: str, resolution: str, *,
    initial_equity: float, config_fingerprint: str | None = None, config_json: str | None = None,
    now_utc: int | None = None,
) -> PaperSessionState:
    existing = get_session(conn, session_key)
    if existing is not None:
        return existing
    now = now_utc if now_utc is not None else int(time.time())
    conn.execute(
        "INSERT INTO paper_session_state "
        "(session_key, canonical_symbol, resolution, equity, last_processed_bar_time_utc, "
        "open_position_json, regime_confirmed, regime_candidate, regime_candidate_count, "
        "config_fingerprint, config_json, created_at_utc, updated_at_utc) "
        "VALUES (?, ?, ?, ?, NULL, NULL, NULL, NULL, 0, ?, ?, ?, ?)",
        (session_key, canonical_symbol, resolution, initial_equity, config_fingerprint, config_json, now, now),
    )
    conn.commit()
    return get_session(conn, session_key)


def bind_session_config(
    conn: sqlite3.Connection, session_key: str, *, config_fingerprint: str, config_json: str,
) -> None:
    """Record the configuration of a session that has not processed any
    bar yet (e.g. created before migration 0020). Never used to overwrite
    the fingerprint of a session with history -- `paper.engine` refuses
    those instead."""
    conn.execute(
        "UPDATE paper_session_state SET config_fingerprint = ?, config_json = ? "
        "WHERE session_key = ? AND last_processed_bar_time_utc IS NULL",
        (config_fingerprint, config_json, session_key),
    )
    conn.commit()


def save_session_state(
    conn: sqlite3.Connection, session_key: str, *,
    equity: float, last_processed_bar_time_utc: int, open_position: OpenPositionState | None,
    regime_tracker_state: RegimeTrackerState | None = None,
    pending_entry: PendingEntryState | None = None,
    risk_state: RiskState | None = None,
    now_utc: int | None = None,
) -> None:
    now = now_utc if now_utc is not None else int(time.time())
    if pending_entry is not None:
        try:
            _validate_pending_entry(pending_entry)
        except (TypeError, ValueError) as exc:
            raise PaperStateError(f"refusing to persist an invalid pending PAPER entry: {exc}") from exc
        row = conn.execute(
            "SELECT canonical_symbol FROM paper_session_state WHERE session_key = ?", (session_key,)
        ).fetchone()
        if row is None:
            raise PaperStateError(f"unknown PAPER session {session_key!r}")
        _check_pending_consistency(
            pending_entry, open_position, cursor=last_processed_bar_time_utc,
            canonical_symbol=row["canonical_symbol"],
        )

    def dump(value) -> str | None:
        return json.dumps(asdict(value)) if value is not None else None

    regime_confirmed = regime_tracker_state.confirmed if regime_tracker_state is not None else None
    regime_candidate = regime_tracker_state.candidate if regime_tracker_state is not None else None
    regime_candidate_count = regime_tracker_state.candidate_count if regime_tracker_state is not None else 0
    conn.execute(
        "UPDATE paper_session_state SET equity = ?, last_processed_bar_time_utc = ?, "
        "open_position_json = ?, regime_confirmed = ?, regime_candidate = ?, regime_candidate_count = ?, "
        "pending_entry_json = ?, risk_state_json = ?, updated_at_utc = ? WHERE session_key = ?",
        (
            equity, last_processed_bar_time_utc, dump(open_position), regime_confirmed, regime_candidate,
            regime_candidate_count, _dump_pending_entry(pending_entry), dump(risk_state), now, session_key,
        ),
    )


_BASE_COLUMNS = (
    "session_key", "canonical_symbol", "strategy_key", "direction", "entry_time_utc", "entry_price",
    "volume", "initial_monetary_risk", "entry_regime", "exit_time_utc", "exit_price", "exit_reason",
    "exit_regime", "realized_r", "realized_pnl", "total_cost", "entry_features_json",
    "entry_raw_confidence", "recorded_at_utc",
)
_INSERT_SQL = (
    f"INSERT OR IGNORE INTO paper_trades ({', '.join(_BASE_COLUMNS + TRADE_PROVENANCE_COLUMNS)}) "
    f"VALUES ({', '.join('?' * (len(_BASE_COLUMNS) + len(TRADE_PROVENANCE_COLUMNS)))})"
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
        cursor = conn.execute(_INSERT_SQL, (
            session_key, canonical_symbol, trade.strategy_key, trade.direction, trade.entry_time_utc,
            trade.entry_price, trade.volume, trade.initial_monetary_risk, trade.entry_regime,
            trade.exit_time_utc, trade.exit_price, trade.exit_reason, trade.exit_regime,
            trade.realized_r, trade.realized_pnl, trade.total_cost,
            json.dumps(trade.entry_features) if trade.entry_features is not None else None,
            trade.entry_raw_confidence, now,
        ) + trade_provenance_values(trade))
        if cursor.rowcount > 0:
            inserted += 1
    return inserted


def get_paper_trades(conn: sqlite3.Connection, session_key: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM paper_trades WHERE session_key = ? ORDER BY entry_time_utc", (session_key,)
    ).fetchall()
