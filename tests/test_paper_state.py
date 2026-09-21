"""Tests for PAPER session persistence (migration 0018_paper)."""

from __future__ import annotations

import pytest

from adaptive_scalper.backtest.types import OpenPositionState, SimulatedTrade
from adaptive_scalper.paper.state import (
    get_or_create_session,
    get_paper_trades,
    get_session,
    record_paper_trades,
    save_session_state,
)
from adaptive_scalper.persistence import connect, migrate


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
