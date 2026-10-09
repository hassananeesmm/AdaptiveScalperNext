"""Journal -> RAG ingestion (completion directive Phase 4).

Unit level: every source maps to the right typed memory with the right
origin, ingestion is idempotent and watermark-driven, a rejected proposal
is never a TRADE_RESULT, and memory text carries no account identifiers.
Runtime level: the trading cycles no longer write RAG themselves; the
scheduled `rag_ingest` task does, and a broken RAG store only degrades
advisory health.
"""

from __future__ import annotations

import json

import pytest

from adaptive_scalper.core.kill_switch import bootstrap as bootstrap_kill_switch
from adaptive_scalper.core.operator_authority import OperatorAuthority
from adaptive_scalper.execution.reconciliation import record_incident
from adaptive_scalper.journal.events import append_event
from adaptive_scalper.paper.state import get_or_create_session
from adaptive_scalper.persistence.database import connect, migrate
from adaptive_scalper.rag.ingestion import ingest
from adaptive_scalper.rag.service import RagService
from adaptive_scalper.rag.store import store_memory
from adaptive_scalper.research.ledger import record_trial
from adaptive_scalper.runtime.state import record_event, record_position_entry_context
from runtime_helpers import STEP, T0, FakeClock, build_engine, step
from edge_fixtures import FixtureValidatedProvider

NOW = 1_800_000_000


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "rag.db")
    migrate(c)
    return c


def _memories(conn, **where):
    sql = "SELECT * FROM rag_memories"
    if where:
        sql += " WHERE " + " AND ".join(f"{k} = ?" for k in where)
    return conn.execute(sql + " ORDER BY id", tuple(where.values())).fetchall()


def test_each_journal_event_kind_maps_to_its_memory_type_and_origin(conn):
    append_event(conn, "c1", "ENTRY_BLOCKED", NOW, "XAUUSD",
                 {"decision": "BLOCK_RISK", "reason": "total risk", "direction": "BUY"}, strategy_key="trend_pullback")
    append_event(conn, "c2", "ENTRY_BLOCKED", NOW, "XAUUSD",
                 {"decision": "BLOCK_REENTRY_CHURN", "reason": "too soon"}, strategy_key="trend_pullback")
    append_event(conn, "c3", "PROPOSAL_REJECTED", NOW, "GBPJPY", {"reason": "lower edge"}, strategy_key="momentum")
    append_event(conn, "c4", "POSITION_OPENED", NOW, "XAUUSD",
                 {"volume": 0.1, "price": 2400.5, "initial_monetary_risk": 25.0}, broker_position_id="777",
                 strategy_key="trend_pullback")
    append_event(conn, "c4", "POSITION_REVIEWED", NOW + 5, "XAUUSD",
                 {"selected_action": "HOLD", "current_r": 0.2, "peak_r": 0.3}, broker_position_id="777")
    append_event(conn, "c4", "POSITION_REVIEWED", NOW + 10, "XAUUSD",
                 {"selected_action": "FULL_CLOSE", "current_r": 0.9, "peak_r": 1.4, "entry_regime": "TREND_UP",
                  "current_regime": "RANGE", "reason": "regime change"}, broker_position_id="777")
    append_event(conn, "c4", "STOP_ADVANCED", NOW + 7, "XAUUSD",
                 {"new_stop_price": 2401.0, "current_r": 1.1, "peak_r": 1.1, "reason": "breakeven"})
    append_event(conn, "c4", "POSITION_CLOSED", NOW + 11, "XAUUSD",
                 {"reason": "closed", "total_profit": 22.0, "total_commission": -0.7, "total_swap": 0.0},
                 broker_position_id="777")
    append_event(conn, "c5", "ORDER_UNKNOWN", NOW, "BTCUSD", {"detail": "order_send raised"})
    append_event(conn, "c6", "REENTRY_REJECTED", NOW, "XAUUSD", {"reason": "cooldown"})
    append_event(conn, "c7", "SIGNAL_CREATED", NOW, "XAUUSD", {"direction": "BUY"})  # not a memory

    report = ingest(conn, now_utc=NOW + 60)
    got = [(r["memory_type"], r["origin"], r["source_key"]) for r in _memories(conn)]
    types = [g[0] for g in got]
    assert types == ["REJECTION", "REENTRY_DECISION", "REJECTION", "TRADE_SETUP", "EXIT_DECISION", "EXIT_DECISION",
                     "TRADE_RESULT", "EXECUTION_INCIDENT", "REENTRY_DECISION"]
    assert report.scanned["journal"] == 11
    assert all(key.startswith("journal:") for _, _, key in got)
    by_type = {t: o for t, o, _ in got}
    assert by_type["REJECTION"] == "DECISION" and by_type["TRADE_SETUP"] == "BROKER_DEMO_CONFIRMED"
    assert by_type["TRADE_RESULT"] == "BROKER_DEMO_CONFIRMED" and by_type["EXECUTION_INCIDENT"] == "EXECUTION"


def test_a_rejected_proposal_is_never_recorded_as_a_trade_result(conn):
    for i in range(5):
        append_event(conn, f"c{i}", "PROPOSAL_REJECTED", NOW, "XAUUSD", {"reason": "lower edge"})
        append_event(conn, f"d{i}", "ENTRY_BLOCKED", NOW, "XAUUSD", {"decision": "BLOCK_NEWS", "reason": "NFP"})
    ingest(conn, now_utc=NOW)
    assert not _memories(conn, memory_type="TRADE_RESULT")
    assert len(_memories(conn, memory_type="REJECTION")) == 10


def test_trade_setup_is_enriched_with_the_recorded_entry_context(conn):
    record_position_entry_context(
        conn, broker_position_id="42", canonical_symbol="XAUUSD", strategy_key="trend_pullback", strategy_version=3,
        direction="SELL", entry_regime="TREND_DOWN", raw_confidence=0.61, stop_distance_price=2.0,
        target_distance_price=4.0, signal_bar_time_utc=NOW - 300, chain_key="c", now_utc=NOW,
    )
    append_event(conn, "c", "POSITION_OPENED", NOW, "XAUUSD", {"volume": 0.05, "price": 2390.0}, broker_position_id="42")
    ingest(conn, now_utc=NOW)
    (row,) = _memories(conn, memory_type="TRADE_SETUP")
    meta = json.loads(row["metadata_json"])
    assert meta["entry_regime"] == "TREND_DOWN" and meta["strategy_version"] == 3
    assert "trend_pullback SELL regime TREND_DOWN" in row["content_text"]


def test_paper_incidents_research_and_runtime_errors_are_ingested(conn):
    get_or_create_session(conn, "PAPER:XAUUSD:M5:v1", "XAUUSD", "M5", initial_equity=10_000.0, now_utc=NOW)
    conn.execute(
        "INSERT INTO paper_trades (session_key, canonical_symbol, strategy_key, direction, entry_time_utc, entry_price, "
        "volume, initial_monetary_risk, entry_regime, exit_time_utc, exit_price, exit_reason, exit_regime, realized_r, "
        "realized_pnl, total_cost, entry_features_json, origin, recorded_at_utc, cost_provenance) "
        "VALUES ('PAPER:XAUUSD:M5:v1', 'XAUUSD', 'trend_pullback', 'BUY', ?, 2400, 0.1, 25, 'TREND_UP', ?, 2402, "
        "'TARGET', 'TREND_UP', 0.8, 20, 1.5, '{}', 'PAPER_LIVE_DATA', ?, 'UNVERIFIED_ASSUMPTION')",
        (NOW, NOW + 600, NOW + 600),
    )
    conn.commit()
    record_incident(conn, "UNKNOWN_OUTCOME", "order_send raised TimeoutError", now_utc=NOW)
    record_trial(conn, trial_id="t-ok", family="f", kind="BACKTEST", strategy_versions={"trend_pullback": 1},
                 params={}, status="COMPLETED", sharpe=0.4, n_observations=120, now_utc=NOW)
    record_trial(conn, trial_id="t-fail", family="f", kind="BACKTEST", strategy_versions={"trend_pullback": 1},
                 params={}, status="FAILED", now_utc=NOW)
    record_event(conn, "INFO", "engine", "ENGINE_STARTED", "ok", now_utc=NOW)
    record_event(conn, "CRITICAL", "paper", "PAPER_SESSION_CONFIG_MISMATCH", "drift", canonical_symbol="XAUUSD",
                 now_utc=NOW)

    ingest(conn, now_utc=NOW + 700)
    (paper,) = _memories(conn, origin="PAPER_LIVE_DATA")
    assert paper["memory_type"] == "TRADE_RESULT" and paper["source_key"].startswith("paper_trade:")
    assert json.loads(paper["metadata_json"])["cost_provenance"] == "UNVERIFIED_ASSUMPTION"
    assert [r["source_key"] for r in _memories(conn, memory_type="STRATEGY_CONTEXT")] == ["trial:t-ok"]
    assert len(_memories(conn, memory_type="EXECUTION_INCIDENT")) == 1
    (system,) = _memories(conn, memory_type="SYSTEM_EVENT")  # INFO is not a memory
    assert "PAPER_SESSION_CONFIG_MISMATCH" in system["content_text"]
    # PAPER and DEMO evidence are never pooled under one origin
    assert not _memories(conn, origin="BROKER_DEMO_CONFIRMED")


def test_ingestion_is_idempotent_and_only_reads_new_rows(conn):
    append_event(conn, "c", "ENTRY_BLOCKED", NOW, "XAUUSD", {"decision": "BLOCK_NEWS", "reason": "CPI"})
    first = ingest(conn, now_utc=NOW)
    second = ingest(conn, now_utc=NOW + 1)
    assert first.inserted == {"REJECTION": 1}
    assert second.inserted == {} and second.scanned["journal"] == 0
    append_event(conn, "d", "ENTRY_BLOCKED", NOW + 2, "GBPJPY", {"decision": "BLOCK_NEWS", "reason": "BoE"})
    third = ingest(conn, now_utc=NOW + 3)
    assert third.scanned["journal"] == 1 and len(_memories(conn)) == 2


def test_a_lost_watermark_never_duplicates_memories(conn):
    append_event(conn, "c", "ENTRY_BLOCKED", NOW, "XAUUSD", {"decision": "BLOCK_NEWS", "reason": "CPI"})
    ingest(conn, now_utc=NOW)
    conn.execute("DELETE FROM rag_ingestion_state")
    conn.commit()
    ingest(conn, now_utc=NOW + 1)
    assert len(_memories(conn)) == 1


def test_store_memory_with_the_same_source_key_returns_the_existing_row(conn):
    a = store_memory(conn, "SYSTEM_EVENT", "x", {}, source_key="k:1", origin="SYSTEM", now_utc=NOW)
    b = store_memory(conn, "SYSTEM_EVENT", "different text", {}, source_key="k:1", origin="SYSTEM", now_utc=NOW)
    assert a.id == b.id and b.content_text == "x"
    # memories without a source key (manual CLI notes) are never deduplicated
    store_memory(conn, "SYSTEM_EVENT", "n", {}, now_utc=NOW)
    store_memory(conn, "SYSTEM_EVENT", "n", {}, now_utc=NOW)
    assert len(_memories(conn)) == 3


def test_memory_text_carries_no_account_identifiers(conn):
    append_event(conn, "c", "POSITION_CLOSED", NOW, "XAUUSD",
                 {"reason": "closed", "total_profit": 1.0, "login": 12345678, "server": "Broker-Demo"},
                 broker_position_id="9")
    ingest(conn, now_utc=NOW)
    (row,) = _memories(conn)
    assert "12345678" not in row["content_text"] and "Broker-Demo" not in row["content_text"]


def test_ingested_memories_are_retrievable_through_the_advisory_index(conn):
    append_event(conn, "c", "ENTRY_BLOCKED", NOW, "XAUUSD",
                 {"decision": "BLOCK_NEWS", "reason": "nonfarm payrolls window", "direction": "BUY"},
                 strategy_key="trend_pullback")
    ingest(conn, now_utc=NOW)
    rag = RagService()
    assert rag.rebuild_index(conn) == "OK"
    result = rag.query_similar("XAUUSD trend_pullback nonfarm payrolls", top_k=3)
    assert result.status == "OK" and result.matches


# ---------------------------------------------------------------------------
# runtime wiring
# ---------------------------------------------------------------------------

START_AT = T0 + 60 * STEP + 10


def test_paper_runtime_trades_reach_rag_through_the_ingestion_task_only(tmp_path):
    clock = FakeClock(START_AT)
    engine, conn, _ = build_engine(tmp_path, mode="PAPER", clock=clock, edge_evidence=FixtureValidatedProvider())
    bootstrap_kill_switch(conn, OperatorAuthority("test-operator"), reason="test setup")
    engine.startup()
    step(engine, clock, seconds=STEP * 25, tick=4)
    trades = conn.execute("SELECT COUNT(*) FROM paper_trades").fetchone()[0]
    assert trades > 0
    assert "rag_ingest" in {t.name for t in engine.scheduler.tasks}
    engine._ingest_rag()
    paper_memories = _memories(conn, origin="PAPER_LIVE_DATA", memory_type="TRADE_RESULT")
    assert len(paper_memories) == trades
    assert all(r["source_key"] for r in _memories(conn))  # nothing written without provenance


def test_demo_runtime_positions_become_setup_memories(tmp_path):
    clock = FakeClock(START_AT)
    engine, conn, _ = build_engine(tmp_path, mode="DEMO", clock=clock, edge_evidence=FixtureValidatedProvider())
    bootstrap_kill_switch(conn, OperatorAuthority("test-operator"), reason="test setup")
    engine.startup()
    step(engine, clock, seconds=STEP * 4, tick=4)
    engine._ingest_rag()
    setups = _memories(conn, memory_type="TRADE_SETUP")
    assert setups and all(r["origin"] == "BROKER_DEMO_CONFIRMED" for r in setups)
    assert "regime" in setups[0]["content_text"]


def test_an_ingestion_failure_degrades_advisory_health_only(tmp_path, monkeypatch):
    import adaptive_scalper.runtime.engine as engine_module

    clock = FakeClock(START_AT)
    engine, conn, _ = build_engine(tmp_path, mode="PAPER", clock=clock, edge_evidence=FixtureValidatedProvider())
    bootstrap_kill_switch(conn, OperatorAuthority("test-operator"), reason="test setup")

    def broken(*_a, **_k):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(engine_module, "ingest_rag_memories", broken)
    engine.startup()
    step(engine, clock, seconds=STEP * 3, tick=4)
    assert engine.component_health["rag_ingest"]["status"] == "DEGRADED"
    assert conn.execute("SELECT COUNT(*) FROM entry_decisions").fetchone()[0] > 0  # trading cycles kept running
