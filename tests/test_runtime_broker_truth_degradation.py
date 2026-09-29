"""Broker truth unavailable -> explicit degraded runtime (0.2.6; master
prompt sections 14-15).

Before: a broker query failing AFTER a CLEAN reconciliation (UNKNOWN
resolution, the position reviews) only recorded TASK_FAILED; the engine
stayed RUNNING and the fresh CLEAN verdict kept admitting new entries.
"""

from __future__ import annotations

from adaptive_scalper.core.kill_switch import bootstrap as bootstrap_kill_switch
from adaptive_scalper.core.operator_authority import OperatorAuthority
from adaptive_scalper.gateway.mt5_gateway import Mt5QueryError
from adaptive_scalper.runtime import demo as demo_module
from adaptive_scalper.runtime.demo import BLOCK_RECONCILIATION, BLOCK_UNKNOWN_ORDER
from adaptive_scalper.runtime.state import get_state
from runtime_helpers import STEP, T0, FakeClock, LiveMarketGateway, build_engine, default_market, step

START_AT = T0 + 60 * STEP + 10


def _started(tmp_path, gateway=None, clock=None):
    clock = clock or FakeClock(START_AT)
    engine, conn, gateway = build_engine(tmp_path, mode="DEMO", clock=clock, gateway=gateway)
    bootstrap_kill_switch(conn, OperatorAuthority("test-operator"), reason="test setup")
    engine.startup()
    return clock, engine, conn, gateway


def _blocks(conn, decision):
    return [r["reason"] for r in conn.execute(
        "SELECT reason FROM entry_decisions WHERE stage = 'GLOBAL' AND decision = ?", (decision,))]


def _open_event(conn, event):
    return conn.execute("SELECT COUNT(*) FROM runtime_events WHERE event = ? AND cleared_at_utc IS NULL",
                        (event,)).fetchone()[0]


def test_query_failure_after_a_clean_reconciliation_degrades_and_blocks(tmp_path, monkeypatch):
    clock, engine, conn, gateway = _started(tmp_path)
    assert get_state(conn, "broker_truth")["status"] == "AVAILABLE"
    assert get_state(conn, "engine")["state"] == "RUNNING"

    def unavailable(*args, **kwargs):
        raise Mt5QueryError("history_orders_get returned None (MT5 query error -10004)")
    monkeypatch.setattr(demo_module, "apply_unknown_resolutions", unavailable)
    step(engine, clock, seconds=STEP * 3, tick=4)

    truth = get_state(conn, "broker_truth")
    assert truth["status"] == "UNAVAILABLE" and "Mt5QueryError" in truth["error"]
    recon = get_state(conn, "reconciliation")
    assert recon["status"] == "BROKER_TRUTH_UNAVAILABLE"
    assert recon["last_known"]["status"] == "CLEAN"  # kept, labelled -- never presented as current
    engine_state = get_state(conn, "engine")
    assert engine_state["state"] == "DEGRADED"
    assert "task:position_cycle" in engine_state["safety_critical_degraded"]
    assert "broker_truth" in engine_state["safety_critical_degraded"]
    assert _blocks(conn, BLOCK_RECONCILIATION)
    assert gateway.calls["order_send"] == 0
    assert _open_event(conn, "BROKER_TRUTH_UNAVAILABLE") == 1

    # Time alone never clears it.
    step(engine, clock, seconds=120, tick=4)
    assert get_state(conn, "broker_truth")["status"] == "UNAVAILABLE"
    assert gateway.calls["order_send"] == 0

    # Only a later fully successful cycle does.
    monkeypatch.undo()
    step(engine, clock, seconds=8, tick=1)
    assert get_state(conn, "broker_truth")["status"] == "AVAILABLE"
    assert get_state(conn, "reconciliation")["status"] == "CLEAN"
    assert _open_event(conn, "BROKER_TRUTH_UNAVAILABLE") == 0
    assert conn.execute("SELECT COUNT(*) FROM runtime_events WHERE event = 'BROKER_TRUTH_RESTORED'").fetchone()[0] == 1
    assert get_state(conn, "engine")["state"] == "RUNNING"
    assert "task:position_cycle" not in engine.component_health


def test_a_non_query_cycle_failure_is_cycle_failed_and_still_blocks(tmp_path, monkeypatch):
    clock, engine, conn, gateway = _started(tmp_path)

    def bug(*args, **kwargs):
        raise ValueError("unexpected defect in UNKNOWN resolution")
    monkeypatch.setattr(demo_module, "apply_unknown_resolutions", bug)
    step(engine, clock, seconds=STEP * 3, tick=4)

    assert get_state(conn, "broker_truth")["status"] == "CYCLE_FAILED"
    assert get_state(conn, "reconciliation")["status"] == "CLEAN"  # reconciliation itself did succeed
    assert get_state(conn, "engine")["state"] == "DEGRADED"
    assert any("broker truth CYCLE_FAILED" in r for r in _blocks(conn, BLOCK_RECONCILIATION))
    assert gateway.calls["order_send"] == 0


def test_an_advisory_task_failure_degrades_only_itself(tmp_path):
    clock, engine, conn, gateway = _started(tmp_path)

    def explode():
        raise RuntimeError("index rebuild exploded")
    for task in engine.scheduler.tasks:
        if task.name == "rag_rebuild":
            task.run = explode
            task.next_due = 0
    step(engine, clock, seconds=4, tick=1)

    health = engine.component_health["task:rag_rebuild"]
    assert health["status"] == "DEGRADED" and health["criticality"] == "ADVISORY"
    engine_state = get_state(conn, "engine")
    assert engine_state["state"] == "DEGRADED"  # visible to the operator ...
    assert engine_state["safety_critical_degraded"] == []  # ... but not a safety degradation
    assert get_state(conn, "broker_truth")["status"] == "AVAILABLE"


def test_an_unresolved_close_blocks_entries_and_survives_a_restart(tmp_path):
    clock = FakeClock(START_AT)
    gateway = LiveMarketGateway(clock, default_market())
    _, engine, conn, _ = _started(tmp_path, gateway=gateway, clock=clock)
    conn.execute(
        "INSERT INTO close_requests (broker_position_id, broker_symbol, position_direction, requested_volume, "
        "magic, comment, requested_at_utc, send_outcome, status) "
        "VALUES ('424242', 'XAUUSD', 'BUY', 0.05, 1, 'ASN exit', ?, 'SENDING', 'UNRESOLVED')", (int(clock.now),))
    conn.commit()
    # A crash leftover (SENDING, no incident yet) blocks on its own ...
    assert engine.demo.global_entry_block(int(clock.now)) == (
        BLOCK_UNKNOWN_ORDER, "a close request's outcome is unresolved")
    step(engine, clock, seconds=STEP * 2, tick=4)
    # ... and once the resolver has looked, its UNKNOWN_OUTCOME incident blocks too.
    assert _blocks(conn, BLOCK_UNKNOWN_ORDER)
    assert _open_event(conn, "CLOSE_UNRESOLVED") == 1
    assert gateway.calls["order_send"] == 0
    row = conn.execute("SELECT status, attempt_count FROM close_requests").fetchone()
    assert row["status"] == "UNRESOLVED" and row["attempt_count"] > 0  # no local position, no guess
    engine.stop()
    conn.close()

    restarted, conn2, _ = build_engine(tmp_path, mode="DEMO", clock=clock, gateway=gateway)
    summary = restarted.startup()
    assert summary["recovery"]["close_resolutions"]
    assert summary["recovery"]["close_resolutions"][0].startswith("UNRESOLVED")
    step(restarted, clock, seconds=STEP * 2, tick=4)
    assert conn2.execute("SELECT status FROM close_requests").fetchone()[0] == "UNRESOLVED"
    assert gateway.calls["order_send"] == 0
