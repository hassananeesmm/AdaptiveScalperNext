"""A failed or stale reconciliation never leaves a CLEAN verdict admitting
new DEMO entries (found while integrating PR #4's Mt5QueryError).

Before: `DemoRuntime.position_cycle` let a `run_reconciliation` exception
escape before persisting anything, so the `reconciliation` state kept its
last CLEAN value indefinitely, and `global_entry_block` never checked that
value's age.
"""

from __future__ import annotations

from adaptive_scalper.core.kill_switch import bootstrap as bootstrap_kill_switch
from adaptive_scalper.core.operator_authority import OperatorAuthority
from adaptive_scalper.gateway.mt5_gateway import Mt5QueryError
from adaptive_scalper.runtime.demo import (
    BLOCK_RECONCILIATION,
    RECONCILIATION_BROKER_TRUTH_UNAVAILABLE,
    RECONCILIATION_ERROR,
)
from adaptive_scalper.runtime.state import get_state, put_state
from chaos_harness import raise_
from runtime_helpers import STEP, T0, FakeClock, build_engine, step

START_AT = T0 + 60 * STEP + 10


def _started(tmp_path):
    clock = FakeClock(START_AT)
    engine, conn, gateway = build_engine(tmp_path, mode="DEMO", clock=clock)
    bootstrap_kill_switch(conn, OperatorAuthority("test-operator"), reason="test setup")
    engine.startup()
    return clock, engine, conn, gateway


def _reconciliation_blocks(conn):
    return [r["reason"] for r in conn.execute(
        "SELECT reason FROM entry_decisions WHERE stage = 'GLOBAL' AND decision = ?", (BLOCK_RECONCILIATION,),
    )]


def test_broker_query_failure_persists_error_and_blocks_new_entries(tmp_path):
    clock, engine, conn, gateway = _started(tmp_path)
    assert get_state(conn, "reconciliation")["status"] == "CLEAN"

    gateway.on("orders_get", raise_(Mt5QueryError("orders_get returned None (MT5 query error -10004)")))
    step(engine, clock, seconds=STEP * 3, tick=4)

    recon = get_state(conn, "reconciliation")
    # 0.2.6: an unreadable broker is typed BROKER_TRUTH_UNAVAILABLE (not the
    # generic ERROR), and the last proven verdict is kept, labelled as such.
    assert recon["status"] == RECONCILIATION_BROKER_TRUTH_UNAVAILABLE != RECONCILIATION_ERROR
    assert "Mt5QueryError" in recon["error"]
    assert recon["last_known"]["status"] == "CLEAN"
    assert get_state(conn, "broker_truth")["status"] == "UNAVAILABLE"
    assert get_state(conn, "engine")["state"] == "DEGRADED"
    assert _reconciliation_blocks(conn), "entries must be blocked by the failed reconciliation"
    assert gateway.calls["order_send"] == 0
    failed = "SELECT COUNT(*) FROM runtime_events WHERE event = 'RECONCILIATION_FAILED' AND cleared_at_utc IS NULL"
    assert conn.execute(failed).fetchone()[0] == 1

    # Broker truth readable again: the next pass is CLEAN and the block lifts.
    gateway._plan.pop("orders_get")
    step(engine, clock, seconds=8, tick=4)
    assert get_state(conn, "reconciliation")["status"] == "CLEAN"
    assert conn.execute(failed).fetchone()[0] == 0  # the visible failure clears once truth is readable


def test_stale_clean_reconciliation_blocks_new_entries(tmp_path):
    clock, engine, conn, gateway = _started(tmp_path)
    for task in engine.scheduler.tasks:
        if task.name == "position_cycle":
            task.run = lambda: None  # reconciliation silently stops refreshing
    put_state(conn, "reconciliation", {"status": "CLEAN", "at": int(clock.now) - 120}, now_utc=int(clock.now))

    step(engine, clock, seconds=STEP * 3, tick=4)

    reasons = _reconciliation_blocks(conn)
    assert reasons and all("stale" in r for r in reasons)
    assert gateway.calls["order_send"] == 0


def test_fresh_clean_reconciliation_does_not_block(tmp_path):
    clock, engine, conn, gateway = _started(tmp_path)
    step(engine, clock, seconds=STEP * 4, tick=4)
    assert _reconciliation_blocks(conn) == []
