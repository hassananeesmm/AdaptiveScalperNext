"""Tests for PAPER session persistence (migration 0018_paper)."""

from __future__ import annotations

import json

import pytest

from adaptive_scalper.backtest.types import OpenPositionState, PendingEntryState, SimulatedTrade
from adaptive_scalper.paper.state import (
    get_or_create_session,
    get_paper_trades,
    PaperStateError,
    get_session,
    record_paper_trades,
    save_session_state,
)
from adaptive_scalper.persistence import connect, migrate
from adaptive_scalper.strategies.base import StrategySignal


@pytest.fixture()
def db(tmp_path):
    conn = connect(tmp_path / "test.sqlite3")
    migrate(conn)
    yield conn
    conn.close()


def _open_position(**overrides) -> OpenPositionState:
    defaults = dict(
        strategy_key="momentum_continuation", direction="BUY", entry_time_utc=1000, entry_price=2000.0,
        volume=0.1, initial_monetary_risk=20.0, entry_regime="TRENDING_UP", stop_price=1995.0,
        target_price=2010.0, initial_stop_distance_price=5.0, total_cost=0.5,
        entry_features={"return_1": 0.5}, entry_raw_confidence=0.8,
    )
    defaults.update(overrides)
    return OpenPositionState(**defaults)


def _pending_entry(
    *, signal_bar_time_utc: int = 5000, canonical_symbol: str = "XAUUSD",
    expires_at_utc: int = 5600,
) -> PendingEntryState:
    signal = StrategySignal(
        strategy_key="momentum_continuation", strategy_version=1,
        canonical_symbol=canonical_symbol, direction="BUY", raw_confidence=0.8,
        stop_distance=5.0, target_distance=10.0, expected_duration_seconds=600,
        entry_method="MARKET", regime="TRENDING_UP", rationale="test",
        feature_schema_version=1, data_timestamp=signal_bar_time_utc,
    )
    return PendingEntryState(
        signal=signal, entry_features={"return_1": 0.5},
        signal_bar_time_utc=signal_bar_time_utc, expires_at_utc=expires_at_utc,
    )


def _closed_trade(**overrides) -> SimulatedTrade:
    defaults = dict(
        strategy_key="momentum_continuation", direction="BUY", entry_time_utc=1000, entry_price=2000.0,
        volume=0.1, initial_monetary_risk=20.0, entry_regime="TRENDING_UP", exit_time_utc=1100,
        exit_price=2010.0, exit_reason="TAKE_PROFIT_HIT", exit_regime="TRENDING_UP", realized_r=1.5,
        realized_pnl=30.0, total_cost=0.5, entry_features={"return_1": 0.5}, entry_raw_confidence=0.8,
    )
    defaults.update(overrides)
    return SimulatedTrade(**defaults)


def test_get_or_create_session_creates_once_and_returns_the_same_row_after(db):
    created = get_or_create_session(db, "PAPER:XAUUSD:M5", "XAUUSD", "M5", initial_equity=10_000.0, now_utc=1000)
    assert created.equity == 10_000.0
    assert created.last_processed_bar_time_utc is None
    assert created.open_position is None

    fetched_again = get_or_create_session(db, "PAPER:XAUUSD:M5", "XAUUSD", "M5", initial_equity=99_999.0, now_utc=2000)
    # The SECOND call must NOT reset equity to a different initial_equity --
    # it returns the EXISTING session, never silently recreates it.
    assert fetched_again.equity == 10_000.0


def test_save_session_state_persists_open_position_round_trip(db):
    get_or_create_session(db, "PAPER:XAUUSD:M5", "XAUUSD", "M5", initial_equity=10_000.0, now_utc=1000)
    position = _open_position()
    save_session_state(
        db, "PAPER:XAUUSD:M5", equity=10_050.0, last_processed_bar_time_utc=5000,
        open_position=position, now_utc=2000,
    )
    session = get_session(db, "PAPER:XAUUSD:M5")
    assert session.equity == 10_050.0
    assert session.last_processed_bar_time_utc == 5000
    assert session.open_position == position


def test_save_session_state_can_clear_the_open_position(db):
    get_or_create_session(db, "PAPER:XAUUSD:M5", "XAUUSD", "M5", initial_equity=10_000.0, now_utc=1000)
    save_session_state(
        db, "PAPER:XAUUSD:M5", equity=10_050.0, last_processed_bar_time_utc=5000,
        open_position=_open_position(), now_utc=2000,
    )
    save_session_state(
        db, "PAPER:XAUUSD:M5", equity=10_080.0, last_processed_bar_time_utc=6000,
        open_position=None, now_utc=3000,
    )
    session = get_session(db, "PAPER:XAUUSD:M5")
    assert session.open_position is None


def test_record_paper_trades_persists_closed_trades_with_paper_live_data_origin(db):
    get_or_create_session(db, "PAPER:XAUUSD:M5", "XAUUSD", "M5", initial_equity=10_000.0, now_utc=1000)
    inserted = record_paper_trades(db, "PAPER:XAUUSD:M5", "XAUUSD", (_closed_trade(),), now_utc=2000)
    assert inserted == 1
    rows = get_paper_trades(db, "PAPER:XAUUSD:M5")
    assert len(rows) == 1
    assert rows[0]["origin"] == "PAPER_LIVE_DATA"
    assert rows[0]["realized_pnl"] == 30.0


def test_record_paper_trades_skips_unclosed_trades(db):
    get_or_create_session(db, "PAPER:XAUUSD:M5", "XAUUSD", "M5", initial_equity=10_000.0, now_utc=1000)
    unclosed = _closed_trade(exit_time_utc=None, exit_price=None, realized_pnl=None, realized_r=None)
    inserted = record_paper_trades(db, "PAPER:XAUUSD:M5", "XAUUSD", (unclosed,), now_utc=2000)
    assert inserted == 0
    assert get_paper_trades(db, "PAPER:XAUUSD:M5") == []


def test_record_paper_trades_is_idempotent_on_repeated_identical_trades(db):
    get_or_create_session(db, "PAPER:XAUUSD:M5", "XAUUSD", "M5", initial_equity=10_000.0, now_utc=1000)
    trade = _closed_trade()
    first = record_paper_trades(db, "PAPER:XAUUSD:M5", "XAUUSD", (trade,), now_utc=2000)
    second = record_paper_trades(db, "PAPER:XAUUSD:M5", "XAUUSD", (trade,), now_utc=3000)
    assert first == 1
    assert second == 0
    assert len(get_paper_trades(db, "PAPER:XAUUSD:M5")) == 1


def test_record_paper_trades_allows_distinct_trades_with_different_entry_times(db):
    get_or_create_session(db, "PAPER:XAUUSD:M5", "XAUUSD", "M5", initial_equity=10_000.0, now_utc=1000)
    trade_a = _closed_trade(entry_time_utc=1000)
    trade_b = _closed_trade(entry_time_utc=2000)
    inserted = record_paper_trades(db, "PAPER:XAUUSD:M5", "XAUUSD", (trade_a, trade_b), now_utc=3000)
    assert inserted == 2


def test_save_session_state_persists_pending_entry_round_trip(db):
    get_or_create_session(db, "PAPER:XAUUSD:M5", "XAUUSD", "M5", initial_equity=10_000.0, now_utc=1000)
    pending = _pending_entry()
    save_session_state(
        db, "PAPER:XAUUSD:M5", equity=10_000.0, last_processed_bar_time_utc=5000,
        open_position=None, pending_entry=pending, now_utc=2000,
    )
    session = get_session(db, "PAPER:XAUUSD:M5")
    assert session.pending_entry == pending
    assert session.open_position is None


def test_save_session_state_rejects_open_position_and_pending_entry_together(db):
    get_or_create_session(db, "PAPER:XAUUSD:M5", "XAUUSD", "M5", initial_equity=10_000.0, now_utc=1000)
    with pytest.raises(PaperStateError, match="both an open position and a pending entry"):
        save_session_state(
            db, "PAPER:XAUUSD:M5", equity=10_000.0, last_processed_bar_time_utc=5000,
            open_position=_open_position(), pending_entry=_pending_entry(), now_utc=2000,
        )


def test_save_session_state_rejects_stale_pending_entry_cursor_mismatch(db):
    get_or_create_session(db, "PAPER:XAUUSD:M5", "XAUUSD", "M5", initial_equity=10_000.0, now_utc=1000)
    with pytest.raises(PaperStateError, match="stale pending entry"):
        save_session_state(
            db, "PAPER:XAUUSD:M5", equity=10_000.0, last_processed_bar_time_utc=5300,
            open_position=None, pending_entry=_pending_entry(signal_bar_time_utc=5000), now_utc=2000,
        )


def test_save_session_state_rejects_pending_entry_for_another_symbol(db):
    get_or_create_session(db, "PAPER:XAUUSD:M5", "XAUUSD", "M5", initial_equity=10_000.0, now_utc=1000)
    with pytest.raises(PaperStateError, match="canonical symbol"):
        save_session_state(
            db, "PAPER:XAUUSD:M5", equity=10_000.0, last_processed_bar_time_utc=5000,
            open_position=None, pending_entry=_pending_entry(canonical_symbol="GBPJPY"), now_utc=2000,
        )


def test_get_session_fails_closed_on_malformed_pending_entry_json(db):
    get_or_create_session(db, "PAPER:XAUUSD:M5", "XAUUSD", "M5", initial_equity=10_000.0, now_utc=1000)
    db.execute(
        "UPDATE paper_session_state SET last_processed_bar_time_utc = ?, pending_entry_json = ? "
        "WHERE session_key = ?",
        (5000, "{not-json", "PAPER:XAUUSD:M5"),
    )
    with pytest.raises(PaperStateError, match="invalid pending PAPER entry state"):
        get_session(db, "PAPER:XAUUSD:M5")


def test_get_session_fails_closed_on_persisted_stale_pending_entry(db):
    get_or_create_session(db, "PAPER:XAUUSD:M5", "XAUUSD", "M5", initial_equity=10_000.0, now_utc=1000)
    save_session_state(
        db, "PAPER:XAUUSD:M5", equity=10_000.0, last_processed_bar_time_utc=5000,
        open_position=None, pending_entry=_pending_entry(), now_utc=2000,
    )
    # Simulate a corrupted/crash-recovery state where the cursor advanced
    # without consuming the deferred entry. Reload must fail closed rather
    # than silently executing or dropping it.
    db.execute(
        "UPDATE paper_session_state SET last_processed_bar_time_utc = ? WHERE session_key = ?",
        (5300, "PAPER:XAUUSD:M5"),
    )
    with pytest.raises(PaperStateError, match="stale pending PAPER entry"):
        get_session(db, "PAPER:XAUUSD:M5")


def test_get_or_create_session_rejects_reusing_key_for_a_different_symbol(db):
    get_or_create_session(
        db, "PAPER:shared", "XAUUSD", "M5", initial_equity=10_000.0, now_utc=1000,
    )
    with pytest.raises(PaperStateError, match="identity mismatch"):
        get_or_create_session(
            db, "PAPER:shared", "GBPJPY", "M5", initial_equity=10_000.0, now_utc=2000,
        )


def test_get_or_create_session_rejects_reusing_key_for_a_different_resolution(db):
    get_or_create_session(
        db, "PAPER:shared", "XAUUSD", "M5", initial_equity=10_000.0, now_utc=1000,
    )
    with pytest.raises(PaperStateError, match="identity mismatch"):
        get_or_create_session(
            db, "PAPER:shared", "XAUUSD", "M1", initial_equity=10_000.0, now_utc=2000,
        )


def test_get_session_rejects_string_timestamp_in_pending_state(db):
    get_or_create_session(db, "PAPER:XAUUSD:M5", "XAUUSD", "M5", initial_equity=10_000.0, now_utc=1000)
    save_session_state(
        db, "PAPER:XAUUSD:M5", equity=10_000.0, last_processed_bar_time_utc=5000,
        open_position=None, pending_entry=_pending_entry(), now_utc=2000,
    )
    row = db.execute(
        "SELECT pending_entry_json FROM paper_session_state WHERE session_key = ?",
        ("PAPER:XAUUSD:M5",),
    ).fetchone()
    payload = json.loads(row["pending_entry_json"])
    payload["signal_bar_time_utc"] = "5000"
    db.execute(
        "UPDATE paper_session_state SET pending_entry_json = ? WHERE session_key = ?",
        (json.dumps(payload), "PAPER:XAUUSD:M5"),
    )
    with pytest.raises(PaperStateError, match="must be an integer"):
        get_session(db, "PAPER:XAUUSD:M5")


def test_get_session_rejects_non_finite_pending_feature(db):
    get_or_create_session(db, "PAPER:XAUUSD:M5", "XAUUSD", "M5", initial_equity=10_000.0, now_utc=1000)
    save_session_state(
        db, "PAPER:XAUUSD:M5", equity=10_000.0, last_processed_bar_time_utc=5000,
        open_position=None, pending_entry=_pending_entry(), now_utc=2000,
    )
    row = db.execute(
        "SELECT pending_entry_json FROM paper_session_state WHERE session_key = ?",
        ("PAPER:XAUUSD:M5",),
    ).fetchone()
    payload = json.loads(row["pending_entry_json"])
    payload["entry_features"]["return_1"] = float("nan")
    db.execute(
        "UPDATE paper_session_state SET pending_entry_json = ? WHERE session_key = ?",
        (json.dumps(payload), "PAPER:XAUUSD:M5"),
    )
    with pytest.raises(PaperStateError, match="must be finite"):
        get_session(db, "PAPER:XAUUSD:M5")
