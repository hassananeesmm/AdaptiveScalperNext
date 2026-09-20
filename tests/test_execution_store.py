"""Tests for idempotent order persistence (directive section 29)."""

from __future__ import annotations

import pytest

from adaptive_scalper.execution.state_machine import InvalidTransitionError, OrderState
from adaptive_scalper.execution.store import (
    create_order,
    get_order_by_client_request_id,
    get_order_state_history,
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


def test_create_order_idempotent_even_with_different_params(db):
    # Same client_request_id must return the EXISTING order regardless
    # of what the (presumably retried) caller passes this time.
    order1 = create_order(db, "req-1", "XAUUSD", "XAUUSDm", "BUY", 0.05)
    order2 = create_order(db, "req-1", "GBPJPY", "GBPJPYm", "SELL", 99.0)
    assert order1.id == order2.id
    assert order2.canonical_symbol == "XAUUSD"  # original, not the second call's params


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
