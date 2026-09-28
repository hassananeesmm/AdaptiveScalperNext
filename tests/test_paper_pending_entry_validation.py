"""Durable pending PAPER entries fail closed when malformed or inconsistent.

Ported from PR #3 (fix/paper-pending-entry-persistence-20260928): the
current lineage already persisted pending entries (migration 0019, commit
856aa04) but decoded them without typed validation and did not check them
against the session cursor/symbol or reject open-position + pending pairs.
"""

from __future__ import annotations

import json
from dataclasses import asdict

import pytest

from adaptive_scalper.backtest.types import OpenPositionState, PendingEntryState
from adaptive_scalper.paper import engine as paper_engine
from adaptive_scalper.paper.state import (
    PaperStateError,
    get_or_create_session,
    get_paper_trades,
    get_session,
    save_session_state,
)
from adaptive_scalper.persistence import connect, migrate
from tests.test_paper_engine import CANONICAL_SYMBOL, RESOLUTION, _config, _seed, _symbol_spec, _trending_bars

KEY = "PAPER:XAUUSD:M5"
CURSOR = 1_000_200


@pytest.fixture()
def db(tmp_path):
    conn = connect(tmp_path / "test.sqlite3")
    migrate(conn)
    yield conn
    conn.close()


@pytest.fixture()
def session_db(db):
    get_or_create_session(db, KEY, "XAUUSD", "M5", initial_equity=10_000.0, now_utc=1000)
    return db


def _pending(**overrides) -> PendingEntryState:
    defaults = dict(
        strategy_key="momentum_continuation", direction="BUY", stop_distance=5.0, target_distance=10.0,
        regime="TRENDING_UP", raw_confidence=0.7, signal_time_utc=CURSOR, entry_features={"return_1": 0.5},
        strategy_version=1, canonical_symbol="XAUUSD",
    )
    defaults.update(overrides)
    return PendingEntryState(**defaults)


def _open_position() -> OpenPositionState:
    return OpenPositionState(
        strategy_key="momentum_continuation", direction="BUY", entry_time_utc=1000, entry_price=2000.0,
        volume=0.1, initial_monetary_risk=20.0, entry_regime="TRENDING_UP", stop_price=1995.0,
        target_price=2010.0, initial_stop_distance_price=5.0, total_cost=0.5,
        entry_features={"return_1": 0.5}, entry_raw_confidence=0.8,
    )


def _write_raw(db, pending_json: str | None, *, cursor: int | None = CURSOR, open_position: str | None = None):
    db.execute(
        "UPDATE paper_session_state SET pending_entry_json = ?, last_processed_bar_time_utc = ?, "
        "open_position_json = ? WHERE session_key = ?",
        (pending_json, cursor, open_position, KEY),
    )
    db.commit()


def test_valid_pending_entry_round_trips(session_db):
    save_session_state(session_db, KEY, equity=10_000.0, last_processed_bar_time_utc=CURSOR, open_position=None,
                       pending_entry=_pending(), now_utc=2000)
    session_db.commit()
    assert get_session(session_db, KEY).pending_entry == _pending()


@pytest.mark.parametrize("raw", [
    "{not json",
    "[]",
    json.dumps({"strategy_key": "x"}),  # missing required fields
    json.dumps({**asdict(_pending()), "unexpected": 1}),
    json.dumps({**asdict(_pending()), "direction": "SIDEWAYS"}),
    json.dumps({**asdict(_pending()), "stop_distance": "5"}),
    json.dumps({**asdict(_pending()), "stop_distance": 0}),
    json.dumps({**asdict(_pending()), "raw_confidence": True}),
    json.dumps({**asdict(_pending()), "signal_time_utc": 1.5}),
    json.dumps({**asdict(_pending()), "strategy_key": ""}),
])
def test_malformed_pending_entry_fails_closed_with_a_typed_error(session_db, raw):
    _write_raw(session_db, raw)
    with pytest.raises(PaperStateError):
        get_session(session_db, KEY)


def test_non_finite_pending_number_fails_closed(session_db):
    raw = json.dumps(asdict(_pending())).replace('"target_distance": 10.0', '"target_distance": NaN')
    assert "NaN" in raw
    _write_raw(session_db, raw)
    with pytest.raises(PaperStateError, match="finite"):
        get_session(session_db, KEY)


def test_stale_pending_entry_not_at_the_session_cursor_fails_closed(session_db):
    _write_raw(session_db, json.dumps(asdict(_pending(signal_time_utc=CURSOR - 300))))
    with pytest.raises(PaperStateError, match="stale"):
        get_session(session_db, KEY)


def test_pending_entry_with_no_cursor_fails_closed(session_db):
    _write_raw(session_db, json.dumps(asdict(_pending())), cursor=None)
    with pytest.raises(PaperStateError, match="stale"):
        get_session(session_db, KEY)


def test_pending_entry_for_another_symbol_fails_closed(session_db):
    _write_raw(session_db, json.dumps(asdict(_pending(canonical_symbol="BTCUSD"))))
    with pytest.raises(PaperStateError, match="BTCUSD"):
        get_session(session_db, KEY)


def test_open_position_and_pending_entry_together_fail_closed_on_load(session_db):
    _write_raw(session_db, json.dumps(asdict(_pending())), open_position=json.dumps(asdict(_open_position())))
    with pytest.raises(PaperStateError, match="both"):
        get_session(session_db, KEY)


@pytest.mark.parametrize("pending, open_position, match", [
    (_pending(signal_time_utc=CURSOR - 300), None, "stale"),
    (_pending(canonical_symbol="BTCUSD"), None, "BTCUSD"),
    (_pending(), _open_position(), "both"),
])
def test_save_refuses_inconsistent_pending_entry(session_db, pending, open_position, match):
    with pytest.raises(PaperStateError, match=match):
        save_session_state(session_db, KEY, equity=10_000.0, last_processed_bar_time_utc=CURSOR,
                           open_position=open_position, pending_entry=pending, now_utc=2000)
    assert get_session(session_db, KEY).last_processed_bar_time_utc is None  # nothing written


def test_paper_cycle_rolls_back_on_typed_state_error(db, monkeypatch):
    """A PaperStateError while persisting a cycle rolls back the WHOLE
    cycle transaction -- trades recorded earlier in it and the cursor."""
    bars = _trending_bars(400)
    config = _config()
    _seed(db, bars, config)
    key = f"PAPER:{CANONICAL_SYMBOL}:{RESOLUTION}"
    before = get_session(db, key)
    trades_before = len(get_paper_trades(db, key))

    real_record = paper_engine.record_paper_trades
    record_calls = []

    def recording(conn, *args, **kwargs):
        record_calls.append(1)
        conn.execute("UPDATE paper_session_state SET equity = -1 WHERE session_key = ?", (key,))
        return real_record(conn, *args, **kwargs)

    def failing_save(*args, **kwargs):
        raise PaperStateError("injected typed validation failure")

    monkeypatch.setattr(paper_engine, "record_paper_trades", recording)
    monkeypatch.setattr(paper_engine, "save_session_state", failing_save)
    with pytest.raises(PaperStateError, match="injected"):
        paper_engine.run_paper_cycle(db, bars, CANONICAL_SYMBOL, RESOLUTION, _symbol_spec(), config=config,
                                     now_utc=2_000_001_000)

    assert record_calls, "the cycle must have reached the persistence transaction"
    assert not db.in_transaction
    after = get_session(db, key)
    assert after.equity == before.equity
    assert after.last_processed_bar_time_utc == before.last_processed_bar_time_utc
    assert len(get_paper_trades(db, key)) == trades_before
