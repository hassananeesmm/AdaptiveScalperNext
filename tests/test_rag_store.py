"""Tests for RAG memory storage (rag/store.py)."""

from __future__ import annotations

import pytest

from adaptive_scalper.persistence import connect, migrate
from adaptive_scalper.rag.store import (
    MEMORY_TYPES,
    UnknownMemoryTypeError,
    get_all_memories,
    get_memory_count,
    store_memory,
)


@pytest.fixture()
def db(tmp_path):
    conn = connect(tmp_path / "test.sqlite3")
    migrate(conn)
    yield conn
    conn.close()


def test_store_memory_basic(db):
    mem = store_memory(db, "TRADE_SETUP", "BUY XAUUSD momentum breakout", {"confidence": 0.7}, now_utc=1000)
    assert mem.id is not None
    assert mem.memory_type == "TRADE_SETUP"
    assert mem.content_text == "BUY XAUUSD momentum breakout"
    assert mem.metadata == {"confidence": 0.7}
    assert mem.created_at_utc == 1000


def test_all_eight_memory_types_accepted(db):
    for i, memory_type in enumerate(MEMORY_TYPES):
        mem = store_memory(db, memory_type, f"content {i}", {}, now_utc=1000 + i)
        assert mem.memory_type == memory_type
    assert len(MEMORY_TYPES) == 8


def test_unknown_memory_type_rejected(db):
    with pytest.raises(UnknownMemoryTypeError):
        store_memory(db, "NOT_A_REAL_TYPE", "content", {})


def test_get_all_memories_ordering(db):
    store_memory(db, "TRADE_SETUP", "first", {}, now_utc=1000)
    store_memory(db, "TRADE_SETUP", "second", {}, now_utc=2000)
    memories = get_all_memories(db)
    assert [m.content_text for m in memories] == ["second", "first"]  # newest first


def test_get_all_memories_filters_by_type(db):
    store_memory(db, "TRADE_SETUP", "setup", {}, now_utc=1000)
    store_memory(db, "REJECTION", "rejection", {}, now_utc=1001)
    memories = get_all_memories(db, memory_type="REJECTION")
    assert len(memories) == 1
    assert memories[0].content_text == "rejection"


def test_get_all_memories_filters_by_symbol(db):
    store_memory(db, "TRADE_SETUP", "xau", {}, canonical_symbol="XAUUSD", now_utc=1000)
    store_memory(db, "TRADE_SETUP", "gbp", {}, canonical_symbol="GBPJPY", now_utc=1001)
    memories = get_all_memories(db, canonical_symbol="XAUUSD")
    assert len(memories) == 1
    assert memories[0].content_text == "xau"


def test_get_memory_count(db):
    assert get_memory_count(db) == 0
    store_memory(db, "SYSTEM_EVENT", "startup", {})
    assert get_memory_count(db) == 1


def test_optional_fields_default_to_none(db):
    mem = store_memory(db, "SYSTEM_EVENT", "startup", {})
    assert mem.canonical_symbol is None
    assert mem.strategy_key is None
    assert mem.chain_key is None


def test_memory_persists_across_connections(tmp_path):
    path = tmp_path / "test.sqlite3"
    conn1 = connect(path)
    migrate(conn1)
    store_memory(conn1, "TRADE_RESULT", "closed +0.5R", {"r_multiple": 0.5}, now_utc=1000)
    conn1.close()

    conn2 = connect(path)
    memories = get_all_memories(conn2)
    assert len(memories) == 1
    assert memories[0].metadata == {"r_multiple": 0.5}
    conn2.close()
