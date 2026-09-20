"""Tests for durable continuous position-management state
(position_management/state_store.py)."""

from __future__ import annotations

import pytest

from adaptive_scalper.persistence import connect, migrate
from adaptive_scalper.position_management.state_store import (
    get_or_create_state,
    get_state,
    record_exit_decision,
    record_exit_fill,
    record_exit_request,
    record_review,
)


@pytest.fixture()
def db(tmp_path):
    conn = connect(tmp_path / "test.sqlite3")
    migrate(conn)
    yield conn
    conn.close()


def _insert_position(conn, **overrides) -> int:
    row = dict(
        broker_position_id="pos-1", canonical_symbol="XAUUSD", direction="BUY", volume=0.05,
        entry_price=2000.0, initial_monetary_risk=20.0, strategy_key="momentum_continuation",
        status="OPEN", opened_at_utc=1000,
    )
    row.update(overrides)
    cursor = conn.execute(
        """
        INSERT INTO positions
            (broker_position_id, canonical_symbol, direction, volume, entry_price,
             initial_monetary_risk, strategy_key, status, opened_at_utc)
        VALUES (:broker_position_id, :canonical_symbol, :direction, :volume, :entry_price,
                :initial_monetary_risk, :strategy_key, :status, :opened_at_utc)
        """,
        row,
    )
    conn.commit()
    return cursor.lastrowid


def test_get_or_create_state_starts_at_zero_peak(db):
    position_id = _insert_position(db)
    state = get_or_create_state(db, position_id, initial_monetary_risk=20.0, entry_regime="TRENDING_UP", now_utc=1000)
    assert state.peak_r == 0.0
    assert state.initial_monetary_risk == 20.0
    assert state.entry_regime == "TRENDING_UP"
    assert state.latest_regime == "TRENDING_UP"


def test_get_or_create_state_is_idempotent(db):
    position_id = _insert_position(db)
    first = get_or_create_state(db, position_id, initial_monetary_risk=20.0, entry_regime="TRENDING_UP", now_utc=1000)
    second = get_or_create_state(db, position_id, initial_monetary_risk=999.0, entry_regime="RANGE", now_utc=2000)
    assert second.id == first.id
    assert second.initial_monetary_risk == 20.0  # never redefined
    assert second.entry_regime == "TRENDING_UP"   # never redefined


def test_get_state_returns_none_for_unknown_position(db):
    assert get_state(db, 9999) is None


def test_record_review_updates_current_r_and_regime(db):
    position_id = _insert_position(db)
    get_or_create_state(db, position_id, initial_monetary_risk=20.0, entry_regime="TRENDING_UP", now_utc=1000)
    state = record_review(db, position_id, current_r=0.3, latest_regime="TRENDING_UP", now_utc=1010)
    assert state.current_r == 0.3
    assert state.peak_r == 0.3
    assert state.last_review_at_utc == 1010


def test_peak_r_is_monotonic_never_decreases(db):
    position_id = _insert_position(db)
    get_or_create_state(db, position_id, initial_monetary_risk=20.0, entry_regime="TRENDING_UP", now_utc=1000)
    record_review(db, position_id, current_r=0.6, latest_regime="TRENDING_UP", now_utc=1010)
    state = record_review(db, position_id, current_r=0.2, latest_regime="TRENDING_UP", now_utc=1020)
    assert state.current_r == 0.2
    assert state.peak_r == 0.6  # never dropped despite current_r falling


def test_peak_r_persists_across_a_fresh_connection(tmp_path):
    path = tmp_path / "test.sqlite3"
    conn1 = connect(path)
    migrate(conn1)
    position_id = _insert_position(conn1)
    get_or_create_state(conn1, position_id, initial_monetary_risk=20.0, entry_regime="TRENDING_UP", now_utc=1000)
    record_review(conn1, position_id, current_r=0.7, latest_regime="TRENDING_UP", now_utc=1010)
    conn1.close()

    conn2 = connect(path)
    reloaded = get_state(conn2, position_id)
    assert reloaded.peak_r == 0.7
    conn2.close()


def test_record_review_without_prior_create_raises(db):
    position_id = _insert_position(db)
    with pytest.raises(ValueError):
        record_review(db, position_id, current_r=0.1, latest_regime="TRENDING_UP")


def test_record_exit_decision_computes_giveback(db):
    position_id = _insert_position(db)
    get_or_create_state(db, position_id, initial_monetary_risk=20.0, entry_regime="TRENDING_UP", now_utc=1000)
    record_review(db, position_id, current_r=0.85, latest_regime="TRENDING_UP", now_utc=1010)
    state = record_exit_decision(db, position_id, decision_r=0.65, decision_at_utc=1020, now_utc=1020)
    assert state.decision_r == 0.65
    assert state.giveback_decision == pytest.approx(0.20)  # peak(0.85) - decision(0.65)


def test_record_exit_request_sets_expected_slippage(db):
    position_id = _insert_position(db)
    get_or_create_state(db, position_id, initial_monetary_risk=20.0, entry_regime="TRENDING_UP", now_utc=1000)
    state = record_exit_request(db, position_id, request_at_utc=1030, expected_slippage=0.02, now_utc=1030)
    assert state.request_at_utc == 1030
    assert state.expected_slippage == 0.02


def test_record_exit_fill_computes_giveback_and_realized_slippage(db):
    position_id = _insert_position(db)
    get_or_create_state(db, position_id, initial_monetary_risk=20.0, entry_regime="TRENDING_UP", now_utc=1000)
    record_review(db, position_id, current_r=0.85, latest_regime="TRENDING_UP", now_utc=1010)
    state = record_exit_fill(
        db, position_id, fill_r=0.60, broker_response_at_utc=1035, realized_slippage=0.03, now_utc=1035,
    )
    assert state.fill_r == 0.60
    assert state.giveback_fill == pytest.approx(0.25)  # peak(0.85) - fill(0.60)
    assert state.realized_slippage == 0.03
    assert state.broker_response_at_utc == 1035


def test_full_lifecycle_review_decision_request_fill(db):
    position_id = _insert_position(db)
    get_or_create_state(db, position_id, initial_monetary_risk=20.0, entry_regime="TRENDING_UP", now_utc=1000)
    record_review(db, position_id, current_r=0.85, latest_regime="TRENDING_UP", now_utc=1010)
    record_exit_decision(db, position_id, decision_r=0.65, decision_at_utc=1020, threshold_cross_at_utc=1015, now_utc=1020)
    record_exit_request(db, position_id, request_at_utc=1021, expected_slippage=0.02, now_utc=1021)
    final = record_exit_fill(db, position_id, fill_r=0.62, broker_response_at_utc=1022, realized_slippage=0.03, now_utc=1022)

    assert final.threshold_cross_at_utc == 1015
    assert final.decision_r == 0.65
    assert final.request_at_utc == 1021
    assert final.fill_r == 0.62
    assert final.giveback_decision == pytest.approx(0.20)
    assert final.giveback_fill == pytest.approx(0.23)
    assert final.expected_slippage == 0.02
    assert final.realized_slippage == 0.03
