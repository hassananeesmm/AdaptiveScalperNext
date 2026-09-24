"""Runtime restart recovery (completion directive Phase 12 / acceptance
criterion "correct restart recovery"): a brand-new engine process over the
SAME database and broker continues exactly where the old one stopped --
no duplicate orders or trades, open positions still managed, PAPER
sessions resumed, never a kill-switch change."""

from __future__ import annotations

from adaptive_scalper.core.kill_switch import bootstrap as bootstrap_kill_switch
from adaptive_scalper.core.kill_switch import get_state as kill_switch_state
from adaptive_scalper.core.operator_authority import OperatorAuthority
from adaptive_scalper.paper.state import list_sessions
from runtime_helpers import STEP, T0, FakeClock, LiveMarketGateway, build_engine, default_market, step

START_AT = T0 + 60 * STEP + 10


def _count(conn, sql: str) -> int:
    return conn.execute(sql).fetchone()[0]


def test_a_restarted_demo_engine_keeps_managing_the_open_position_without_duplicates(tmp_path):
    clock = FakeClock(START_AT)
    gateway = LiveMarketGateway(clock, default_market())
    engine, conn, _ = build_engine(tmp_path, mode="DEMO", clock=clock, gateway=gateway)
    bootstrap_kill_switch(conn, OperatorAuthority("test-operator"), reason="test setup")
    engine.startup()
    step(engine, clock, seconds=STEP * 4, tick=4)
    open_before = _count(conn, "SELECT COUNT(*) FROM positions WHERE status = 'OPEN'")
    orders_before = _count(conn, "SELECT COUNT(*) FROM orders")
    sends_before = gateway.calls["order_send"]
    assert open_before >= 1
    engine.stop()

    # the process "dies" and a new one starts over the same DB and broker
    restarted, conn2, _ = build_engine(tmp_path, mode="DEMO", clock=clock, gateway=gateway)
    summary = restarted.startup()
    assert summary["recovery"]["reconciliation"] == "CLEAN"
    assert summary["recovery"]["quarantined_orders"] == []
    reviews_before = _count(conn2, "SELECT COUNT(*) FROM journal_events WHERE event_type = 'POSITION_REVIEWED'")
    step(restarted, clock, seconds=10, tick=1)
    assert _count(conn2, "SELECT COUNT(*) FROM journal_events WHERE event_type = 'POSITION_REVIEWED'") > reviews_before
    assert _count(conn2, "SELECT COUNT(*) FROM orders") == orders_before   # nothing re-submitted on restart
    assert gateway.calls["order_send"] == sends_before
    assert kill_switch_state(conn2).status.value == "DISENGAGED"          # untouched by restart


def test_a_crash_mid_submission_is_quarantined_before_anything_else_runs(tmp_path):
    clock = FakeClock(START_AT)
    gateway = LiveMarketGateway(clock, default_market())
    engine, conn, _ = build_engine(tmp_path, mode="DEMO", clock=clock, gateway=gateway)
    bootstrap_kill_switch(conn, OperatorAuthority("test-operator"), reason="test setup")
    engine.startup()
    conn.execute(
        "INSERT INTO orders (client_request_id, chain_key, canonical_symbol, broker_symbol, direction, "
        "requested_volume, state, created_at_utc, updated_at_utc) "
        "VALUES ('crash:1', 'crash-chain', 'XAUUSD', ?, 'BUY', 0.01, 'SUBMITTED', ?, ?)",
        (engine.symbols["XAUUSD"], START_AT, START_AT),
    )
    conn.commit()
    engine.stop()

    restarted, conn2, _ = build_engine(tmp_path, mode="DEMO", clock=clock, gateway=gateway)
    summary = restarted.startup()
    assert len(summary["recovery"]["quarantined_orders"]) == 1
    state = conn2.execute("SELECT state FROM orders WHERE client_request_id = 'crash:1'").fetchone()[0]
    assert state == "UNKNOWN"  # no positive broker proof either way -> stays dangerous
    step(restarted, clock, seconds=STEP * 3, tick=4)
    new_orders = _count(conn2, "SELECT COUNT(*) FROM orders WHERE client_request_id != 'crash:1'")
    assert new_orders == 0 and gateway.calls["order_send"] == 0      # no new exposure while UNKNOWN
    assert _count(conn2, "SELECT COUNT(*) FROM execution_incidents WHERE resolved_at_utc IS NULL") >= 1


def test_a_restarted_paper_engine_resumes_its_sessions(tmp_path):
    clock = FakeClock(START_AT)
    gateway = LiveMarketGateway(clock, default_market())
    engine, conn, _ = build_engine(tmp_path, mode="PAPER", clock=clock, gateway=gateway)
    bootstrap_kill_switch(conn, OperatorAuthority("test-operator"), reason="test setup")
    engine.startup()
    step(engine, clock, seconds=STEP * 12, tick=4)
    before = {s.session_key: (s.equity, s.last_processed_bar_time_utc) for s in list_sessions(conn)}
    trades_before = _count(conn, "SELECT COUNT(*) FROM paper_trades")
    engine.stop()

    restarted, conn2, _ = build_engine(tmp_path, mode="PAPER", clock=clock, gateway=gateway)
    restarted.startup()
    after_restart = {s.session_key: (s.equity, s.last_processed_bar_time_utc) for s in list_sessions(conn2)}
    assert after_restart == before                       # nothing replayed or reset by startup
    step(restarted, clock, seconds=STEP * 3, tick=4)
    for key, (equity, last_bar) in before.items():
        session = next(s for s in list_sessions(conn2) if s.session_key == key)
        assert session.last_processed_bar_time_utc == last_bar + 3 * STEP   # exactly the new bars
    assert _count(conn2, "SELECT COUNT(*) FROM paper_trades") >= trades_before
    dupes = _count(conn2, "SELECT COUNT(*) FROM (SELECT session_key, entry_time_utc, direction, COUNT(*) AS n "
                          "FROM paper_trades GROUP BY 1, 2, 3 HAVING n > 1)")
    assert dupes == 0
