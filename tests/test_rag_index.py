"""Tests for the TF-IDF retrieval index (rag/index.py)."""

from __future__ import annotations

import pytest

from adaptive_scalper.persistence import connect, migrate
from adaptive_scalper.rag.index import RagIndex
from adaptive_scalper.rag.store import store_memory


@pytest.fixture()
def db(tmp_path):
    conn = connect(tmp_path / "test.sqlite3")
    migrate(conn)
    yield conn
    conn.close()


def test_empty_index_is_not_built(db):
    index = RagIndex()
    index.rebuild(db)
    assert index.is_built is False
    assert index.size == 0


def test_query_on_unbuilt_index_returns_empty(db):
    index = RagIndex()
    assert index.query("anything") == []


def test_rebuild_and_query_returns_relevant_match(db):
    store_memory(db, "TRADE_SETUP", "BUY XAUUSD momentum breakout above resistance", {}, now_utc=1000)
    store_memory(db, "TRADE_SETUP", "SELL GBPJPY range reversion at support", {}, now_utc=1001)
    store_memory(db, "TRADE_SETUP", "BUY BTCUSD volatility expansion breakout", {}, now_utc=1002)

    index = RagIndex()
    index.rebuild(db)
    assert index.is_built is True
    assert index.size == 3

    matches = index.query("XAUUSD momentum breakout resistance", top_k=2)
    assert len(matches) >= 1
    assert matches[0].memory.content_text == "BUY XAUUSD momentum breakout above resistance"
    assert matches[0].score > 0.0


def test_query_respects_top_k(db):
    for i in range(5):
        store_memory(db, "TRADE_SETUP", f"BUY XAUUSD momentum setup number {i}", {}, now_utc=1000 + i)
    index = RagIndex()
    index.rebuild(db)
    matches = index.query("XAUUSD momentum setup", top_k=2)
    assert len(matches) <= 2


def test_query_filters_zero_score_matches(db):
    store_memory(db, "TRADE_SETUP", "completely unrelated content about weather", {}, now_utc=1000)
    index = RagIndex()
    index.rebuild(db)
    matches = index.query("XAUUSD momentum breakout gold trading strategy signal")
    assert all(m.score > 0.0 for m in matches)


def test_rebuild_reflects_new_memories(db):
    index = RagIndex()
    index.rebuild(db)
    assert index.size == 0

    store_memory(db, "TRADE_SETUP", "BUY XAUUSD breakout", {}, now_utc=1000)
    index.rebuild(db)  # explicit rebuild required — no implicit auto-refresh
    assert index.size == 1


def test_rebuild_filters_by_memory_type(db):
    store_memory(db, "TRADE_SETUP", "setup content", {}, now_utc=1000)
    store_memory(db, "REJECTION", "rejection content", {}, now_utc=1001)
    index = RagIndex()
    index.rebuild(db, memory_type="REJECTION")
    assert index.size == 1
