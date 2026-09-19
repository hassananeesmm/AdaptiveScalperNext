"""Tests for the immutable decision journal (directive sections 54-58):
append, deterministic ordering, linkage fields, immutability (both the
API surface and the database-level trigger), restart persistence,
invalid event type handling, and transaction behavior.
"""

from __future__ import annotations

import sqlite3

import pytest

from adaptive_scalper.journal.events import (
    EVENT_TYPES,
    UnknownEventTypeError,
    append_event,
    get_or_create_chain,
)
from adaptive_scalper.journal.queries import get_chain_events, get_events_by_type, get_events_for_broker_order
from adaptive_scalper.persistence import connect, migrate


@pytest.fixture()
def db(tmp_path):
    conn = connect(tmp_path / "test.sqlite3")
    migrate(conn)
    yield conn
    conn.close()


# --------------------------------------------------------------------------
# Append + ordering
# --------------------------------------------------------------------------

def test_append_event_returns_a_new_row_id_each_time(db):
    id1 = append_event(db, "chain-1", "SIGNAL_CREATED", 1000, "XAUUSD", {"a": 1})
    id2 = append_event(db, "chain-1", "PROPOSAL_CREATED", 1001, "XAUUSD", {"b": 2})
    assert id1 != id2


def test_events_are_returned_in_deterministic_chain_order(db):
    append_event(db, "chain-1", "SIGNAL_CREATED", 1000, "XAUUSD", {})
    append_event(db, "chain-1", "PROPOSAL_CREATED", 1001, "XAUUSD", {})
    append_event(db, "chain-1", "ENTRY_ALLOWED", 1002, "XAUUSD", {})

    events = get_chain_events(db, "chain-1")
    assert [e.event_type for e in events] == ["SIGNAL_CREATED", "PROPOSAL_CREATED", "ENTRY_ALLOWED"]
    assert [e.sequence_in_chain for e in events] == [1, 2, 3]


def test_unknown_chain_key_returns_empty_list_not_error(db):
    assert get_chain_events(db, "no-such-chain") == []


def test_two_different_chains_have_independent_sequences(db):
    append_event(db, "chain-a", "SIGNAL_CREATED", 1000, "XAUUSD", {})
    append_event(db, "chain-b", "SIGNAL_CREATED", 1000, "GBPJPY", {})
    append_event(db, "chain-a", "PROPOSAL_CREATED", 1001, "XAUUSD", {})

    a_events = get_chain_events(db, "chain-a")
    b_events = get_chain_events(db, "chain-b")
    assert [e.sequence_in_chain for e in a_events] == [1, 2]
    assert [e.sequence_in_chain for e in b_events] == [1]


# --------------------------------------------------------------------------
# Linkage fields + payload
# --------------------------------------------------------------------------

def test_linkage_fields_and_payload_round_trip(db):
    append_event(
        db, "chain-1", "ORDER_FILLED", 1000, "XAUUSD",
        {"fill_price": 2001.5, "slippage": 0.2},
        broker_symbol="XAUUSDm", strategy_key="momentum_continuation",
        client_request_id="req-abc", broker_order_id="ord-123",
        broker_position_id="pos-456", broker_deal_id="deal-789",
    )
    event = get_chain_events(db, "chain-1")[0]
    assert event.broker_symbol == "XAUUSDm"
    assert event.strategy_key == "momentum_continuation"
    assert event.client_request_id == "req-abc"
    assert event.broker_order_id == "ord-123"
    assert event.broker_position_id == "pos-456"
    assert event.broker_deal_id == "deal-789"
    assert event.payload == {"fill_price": 2001.5, "slippage": 0.2}


def test_get_events_for_broker_order_finds_all_referencing_events(db):
    append_event(db, "chain-1", "ORDER_SUBMITTED", 1000, "XAUUSD", {}, broker_order_id="ord-1")
    append_event(db, "chain-1", "ORDER_FILLED", 1001, "XAUUSD", {}, broker_order_id="ord-1")
    append_event(db, "chain-2", "ORDER_SUBMITTED", 1000, "GBPJPY", {}, broker_order_id="ord-2")

    events = get_events_for_broker_order(db, "ord-1")
    assert [e.event_type for e in events] == ["ORDER_SUBMITTED", "ORDER_FILLED"]


def test_get_events_by_type_filters_and_orders_newest_first(db):
    append_event(db, "chain-1", "ENTRY_BLOCKED", 1000, "XAUUSD", {"reason": "BLOCK_NEWS"})
    append_event(db, "chain-2", "ENTRY_BLOCKED", 2000, "GBPJPY", {"reason": "BLOCK_COST"})
    append_event(db, "chain-1", "ENTRY_ALLOWED", 3000, "XAUUSD", {})

    blocked = get_events_by_type(db, "ENTRY_BLOCKED")
    assert [e.event_timestamp_utc for e in blocked] == [2000, 1000]


def test_get_events_by_type_respects_since_utc(db):
    append_event(db, "chain-1", "ENTRY_BLOCKED", 1000, "XAUUSD", {})
    append_event(db, "chain-2", "ENTRY_BLOCKED", 5000, "GBPJPY", {})

    recent = get_events_by_type(db, "ENTRY_BLOCKED", since_utc=3000)
    assert len(recent) == 1
    assert recent[0].event_timestamp_utc == 5000


# --------------------------------------------------------------------------
# Invalid event type
# --------------------------------------------------------------------------

def test_unknown_event_type_raises_before_touching_the_database(db):
    with pytest.raises(UnknownEventTypeError):
        append_event(db, "chain-1", "NOT_A_REAL_EVENT", 1000, "XAUUSD", {})
    assert get_chain_events(db, "chain-1") == []  # nothing partially written


def test_every_directive_named_event_type_is_accepted(db):
    for i, event_type in enumerate(sorted(EVENT_TYPES)):
        append_event(db, f"chain-{i}", event_type, 1000, "XAUUSD", {})  # must not raise


# --------------------------------------------------------------------------
# get_or_create_chain
# --------------------------------------------------------------------------

def test_get_or_create_chain_is_idempotent(db):
    id1 = get_or_create_chain(db, "chain-1", "XAUUSD")
    id2 = get_or_create_chain(db, "chain-1", "XAUUSD")
    assert id1 == id2


def test_get_or_create_chain_rejects_symbol_mismatch_for_existing_chain(db):
    get_or_create_chain(db, "chain-1", "XAUUSD")
    with pytest.raises(ValueError):
        get_or_create_chain(db, "chain-1", "GBPJPY")


# --------------------------------------------------------------------------
# Immutability: no update/delete function exists, AND the DB blocks it too
# --------------------------------------------------------------------------

def test_journal_events_module_exposes_no_update_or_delete_function():
    import adaptive_scalper.journal.events as events_module
    public_names = [n for n in dir(events_module) if not n.startswith("_")]
    assert not any("update" in n.lower() for n in public_names)
    assert not any("delete" in n.lower() for n in public_names)


def test_direct_sql_update_is_blocked_by_the_database_trigger(db):
    event_id = append_event(db, "chain-1", "SIGNAL_CREATED", 1000, "XAUUSD", {"confidence": 0.5})
    with pytest.raises(sqlite3.Error, match="append-only"):
        db.execute("UPDATE journal_events SET payload_json = '{}' WHERE id = ?", (event_id,))


def test_direct_sql_delete_is_blocked_by_the_database_trigger(db):
    event_id = append_event(db, "chain-1", "SIGNAL_CREATED", 1000, "XAUUSD", {})
    with pytest.raises(sqlite3.Error, match="append-only"):
        db.execute("DELETE FROM journal_events WHERE id = ?", (event_id,))


def test_a_later_outcome_is_a_new_event_not_a_mutation_of_the_earlier_one(db):
    """Directive section 54: do not rewrite what the bot knew at an
    earlier timestamp; append subsequent outcomes instead."""
    append_event(db, "chain-1", "SIGNAL_CREATED", 1000, "XAUUSD", {"raw_confidence": 0.72})
    append_event(db, "chain-1", "POSITION_CLOSED", 2000, "XAUUSD", {"result": "loss", "r_multiple": -1.0})

    events = get_chain_events(db, "chain-1")
    assert len(events) == 2
    # The original SIGNAL_CREATED payload is exactly what was known at
    # the time — unaffected by the later loss.
    assert events[0].payload == {"raw_confidence": 0.72}
    assert events[1].payload == {"result": "loss", "r_multiple": -1.0}


# --------------------------------------------------------------------------
# Restart persistence
# --------------------------------------------------------------------------

def test_journal_persists_across_a_fresh_connection(tmp_path):
    path = tmp_path / "test.sqlite3"
    conn1 = connect(path)
    migrate(conn1)
    append_event(conn1, "chain-1", "SIGNAL_CREATED", 1000, "XAUUSD", {"x": 1})
    conn1.close()

    conn2 = connect(path)
    events = get_chain_events(conn2, "chain-1")
    assert len(events) == 1
    assert events[0].payload == {"x": 1}
    conn2.close()


# --------------------------------------------------------------------------
# Transaction behavior
# --------------------------------------------------------------------------

def test_append_event_is_atomic_chain_creation_plus_insert(db):
    # A brand-new chain_key: get_or_create_chain's INSERT and the
    # journal_events INSERT must both land or neither must.
    append_event(db, "brand-new-chain", "SIGNAL_CREATED", 1000, "XAUUSD", {})
    chain_row = db.execute("SELECT id FROM decision_chains WHERE chain_key = 'brand-new-chain'").fetchone()
    assert chain_row is not None
    event_row = db.execute(
        "SELECT * FROM journal_events WHERE chain_id = ?", (chain_row["id"],)
    ).fetchone()
    assert event_row is not None
