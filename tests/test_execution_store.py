"""Tests for idempotent order persistence (directive section 29)."""

from __future__ import annotations

import pytest

from adaptive_scalper.execution.state_machine import InvalidTransitionError, OrderState
from adaptive_scalper.execution.store import (
    IdempotencyConflictError,
    create_order,
    get_order_by_client_request_id,
    get_order_state_history,
    record_order_risk_accounting,
    transition_order_state,
)
from adaptive_scalper.persistence import connect, migrate


@pytest.fixture()
def db(tmp_path):
    conn = connect(tmp_path / "test.sqlite3")
    migrate(conn)
    yield conn
    conn.close()


def test_create_order_starts_at_proposed(db):
    order = create_order(db, "req-1", "XAUUSD", "XAUUSDm", "BUY", 0.05, now_utc=1000)
    assert order.state == OrderState.PROPOSED
    assert order.client_request_id == "req-1"


def test_create_order_is_idempotent(db):
    order1 = create_order(db, "req-1", "XAUUSD", "XAUUSDm", "BUY", 0.05, now_utc=1000)
    order2 = create_order(db, "req-1", "XAUUSD", "XAUUSDm", "BUY", 0.05, now_utc=2000)
    assert order1.id == order2.id
    assert order2.created_at_utc == 1000  # unchanged — second call was a no-op, not an update
    count = db.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
    assert count == 1


def test_create_order_idempotent_on_exact_retry(db):
    # An EXACT retry (same client_request_id, same immutable fields) is
    # the only safe case — returns the existing row unchanged, never a
    # second order.
    order1 = create_order(db, "req-1", "XAUUSD", "XAUUSDm", "BUY", 0.05, stop_loss=1990.0, take_profit=2020.0)
    order2 = create_order(db, "req-1", "XAUUSD", "XAUUSDm", "BUY", 0.05, stop_loss=1990.0, take_profit=2020.0)
    assert order1.id == order2.id
    count = db.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
    assert count == 1


# execution-safety review finding #6: a client_request_id collision with
# DIFFERENT immutable fields must raise loudly, not silently return the
# stale row — that would hide a real caller bug. No second order may be
# created in any of these cases either.

def test_idempotency_conflict_changed_symbol_raises(db):
    create_order(db, "req-1", "XAUUSD", "XAUUSDm", "BUY", 0.05)
    with pytest.raises(IdempotencyConflictError):
        create_order(db, "req-1", "GBPJPY", "GBPJPYm", "BUY", 0.05)
    assert db.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 1


def test_idempotency_conflict_changed_direction_raises(db):
    create_order(db, "req-1", "XAUUSD", "XAUUSDm", "BUY", 0.05)
    with pytest.raises(IdempotencyConflictError):
        create_order(db, "req-1", "XAUUSD", "XAUUSDm", "SELL", 0.05)
    assert db.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 1


def test_idempotency_conflict_changed_volume_raises(db):
    create_order(db, "req-1", "XAUUSD", "XAUUSDm", "BUY", 0.05)
    with pytest.raises(IdempotencyConflictError):
        create_order(db, "req-1", "XAUUSD", "XAUUSDm", "BUY", 0.10)
    assert db.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 1


def test_idempotency_conflict_changed_stop_loss_raises(db):
    create_order(db, "req-1", "XAUUSD", "XAUUSDm", "BUY", 0.05, stop_loss=1990.0)
    with pytest.raises(IdempotencyConflictError):
        create_order(db, "req-1", "XAUUSD", "XAUUSDm", "BUY", 0.05, stop_loss=1980.0)
    assert db.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 1


def test_idempotency_conflict_changed_take_profit_raises(db):
    create_order(db, "req-1", "XAUUSD", "XAUUSDm", "BUY", 0.05, take_profit=2020.0)
    with pytest.raises(IdempotencyConflictError):
        create_order(db, "req-1", "XAUUSD", "XAUUSDm", "BUY", 0.05, take_profit=2030.0)
    assert db.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 1


def test_idempotency_conflict_changed_broker_symbol_raises(db):
    create_order(db, "req-1", "XAUUSD", "XAUUSDm", "BUY", 0.05)
    with pytest.raises(IdempotencyConflictError):
        create_order(db, "req-1", "XAUUSD", "XAUUSD.raw", "BUY", 0.05)
    assert db.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 1


def test_idempotency_conflict_changed_chain_key_raises(db):
    create_order(db, "req-1", "XAUUSD", "XAUUSDm", "BUY", 0.05, chain_key="chain-a")
    with pytest.raises(IdempotencyConflictError):
        create_order(db, "req-1", "XAUUSD", "XAUUSDm", "BUY", 0.05, chain_key="chain-b")
    assert db.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 1


def test_get_order_by_client_request_id_returns_none_for_unknown(db):
    assert get_order_by_client_request_id(db, "no-such-id") is None


def test_transition_order_state_moves_through_lifecycle(db):
    order = create_order(db, "req-1", "XAUUSD", "XAUUSDm", "BUY", 0.05, now_utc=1000)
    order = transition_order_state(db, order.id, OrderState.SUBMITTED, now_utc=1001)
    assert order.state == OrderState.SUBMITTED
    order = transition_order_state(db, order.id, OrderState.ACCEPTED, broker_order_id="12345", now_utc=1002)
    assert order.state == OrderState.ACCEPTED
    assert order.broker_order_id == "12345"
    order = transition_order_state(db, order.id, OrderState.FILLED, broker_position_id="999", now_utc=1003)
    assert order.state == OrderState.FILLED
    assert order.broker_position_id == "999"


def test_transition_order_state_rejects_illegal_jump(db):
    order = create_order(db, "req-1", "XAUUSD", "XAUUSDm", "BUY", 0.05)
    with pytest.raises(InvalidTransitionError):
        transition_order_state(db, order.id, OrderState.FILLED)  # PROPOSED -> FILLED is illegal


def test_transition_order_state_unknown_order_id_raises(db):
    with pytest.raises(ValueError):
        transition_order_state(db, 9999, OrderState.SUBMITTED)


def test_broker_fields_are_preserved_across_transitions_that_dont_set_them(db):
    order = create_order(db, "req-1", "XAUUSD", "XAUUSDm", "BUY", 0.05)
    order = transition_order_state(db, order.id, OrderState.SUBMITTED)
    order = transition_order_state(db, order.id, OrderState.ACCEPTED, broker_order_id="ord-1")
    order = transition_order_state(db, order.id, OrderState.FILLED)  # doesn't pass broker_order_id again
    assert order.broker_order_id == "ord-1"  # preserved via COALESCE, not wiped


def test_get_order_state_history_records_full_lifecycle(db):
    order = create_order(db, "req-1", "XAUUSD", "XAUUSDm", "BUY", 0.05, now_utc=1000)
    order = transition_order_state(db, order.id, OrderState.SUBMITTED, now_utc=1001)
    order = transition_order_state(db, order.id, OrderState.ACCEPTED, now_utc=1002)
    order = transition_order_state(db, order.id, OrderState.FILLED, now_utc=1003)

    history = get_order_state_history(db, order.id)
    assert history == [
        (None, "PROPOSED", 1000),
        ("PROPOSED", "SUBMITTED", 1001),
        ("SUBMITTED", "ACCEPTED", 1002),
        ("ACCEPTED", "FILLED", 1003),
    ]


def test_order_persists_across_a_fresh_connection(tmp_path):
    path = tmp_path / "test.sqlite3"
    conn1 = connect(path)
    migrate(conn1)
    order = create_order(conn1, "req-1", "XAUUSD", "XAUUSDm", "BUY", 0.05, now_utc=1000)
    transition_order_state(conn1, order.id, OrderState.SUBMITTED, now_utc=1001)
    conn1.close()

    conn2 = connect(path)
    reloaded = get_order_by_client_request_id(conn2, "req-1")
    assert reloaded.state == OrderState.SUBMITTED
    conn2.close()


# --------------------------------------------------------------------------
# record_order_risk_accounting (external review findings #5/#6, 2026-09-21):
# durable, typed pending/filled/remaining risk fields, reconstructable
# from SQLite alone after a restart.
# --------------------------------------------------------------------------

def test_record_order_risk_accounting_sets_requested_and_remaining(db):
    order = create_order(db, "req-1", "XAUUSD", "XAUUSDm", "BUY", 0.05, now_utc=1000)
    record_order_risk_accounting(
        db, order.id, requested_monetary_risk=20.0, remaining_volume=0.05,
        remaining_pending_monetary_risk=20.0, now_utc=1000,
    )
    row = db.execute("SELECT * FROM orders WHERE id = ?", (order.id,)).fetchone()
    assert row["requested_monetary_risk"] == pytest.approx(20.0)
    assert row["remaining_volume"] == pytest.approx(0.05)
    assert row["remaining_pending_monetary_risk"] == pytest.approx(20.0)
    assert row["filled_volume"] == 0.0  # column default, never NULL
    assert row["filled_initial_monetary_risk"] == 0.0


def test_record_order_risk_accounting_narrows_remaining_after_a_partial_fill(db):
    order = create_order(db, "req-1", "XAUUSD", "XAUUSDm", "BUY", 0.05, now_utc=1000)
    record_order_risk_accounting(
        db, order.id, requested_monetary_risk=20.0, remaining_volume=0.05,
        remaining_pending_monetary_risk=20.0, now_utc=1000,
    )
    # a partial fill of 0.02 of 0.05 at proportional risk 8.0
    record_order_risk_accounting(
        db, order.id, filled_volume=0.02, filled_initial_monetary_risk=8.0,
        remaining_volume=0.03, remaining_pending_monetary_risk=12.0, now_utc=1010,
    )
    row = db.execute("SELECT * FROM orders WHERE id = ?", (order.id,)).fetchone()
    assert row["requested_monetary_risk"] == pytest.approx(20.0)  # untouched by the second call
    assert row["filled_volume"] == pytest.approx(0.02)
    assert row["filled_initial_monetary_risk"] == pytest.approx(8.0)
    assert row["remaining_volume"] == pytest.approx(0.03)
    assert row["remaining_pending_monetary_risk"] == pytest.approx(12.0)


def test_record_order_risk_accounting_survives_a_fresh_connection(tmp_path):
    # Restart recovery: pending/filled risk must be reconstructable from
    # SQLite alone, no in-memory proposal object required.
    path = tmp_path / "test.sqlite3"
    conn1 = connect(path)
    migrate(conn1)
    order = create_order(conn1, "req-1", "XAUUSD", "XAUUSDm", "BUY", 0.05, now_utc=1000)
    record_order_risk_accounting(
        conn1, order.id, requested_monetary_risk=20.0, remaining_volume=0.05,
        remaining_pending_monetary_risk=20.0, now_utc=1000,
    )
    conn1.close()

    conn2 = connect(path)
    row = conn2.execute("SELECT * FROM orders WHERE id = ?", (order.id,)).fetchone()
    assert row["requested_monetary_risk"] == pytest.approx(20.0)
    assert row["remaining_pending_monetary_risk"] == pytest.approx(20.0)
    conn2.close()


def test_record_order_risk_accounting_with_no_fields_is_a_safe_no_op(db):
    order = create_order(db, "req-1", "XAUUSD", "XAUUSDm", "BUY", 0.05, now_utc=1000)
    record_order_risk_accounting(db, order.id, now_utc=1000)  # nothing supplied
    row = db.execute("SELECT * FROM orders WHERE id = ?", (order.id,)).fetchone()
    assert row["requested_monetary_risk"] is None
