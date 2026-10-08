"""DEMO daily loss circuit breaker (2026-10-06): realized + FLOATING loss
reaching `max_daily_loss_pct` engages the kill switch (only the operator
clears it) and closes every tracked open position through the safe close
service."""

from __future__ import annotations

from types import SimpleNamespace

from adaptive_scalper.core.kill_switch import KillSwitchStatus
from adaptive_scalper.core.kill_switch import bootstrap as bootstrap_kill_switch
from adaptive_scalper.core.kill_switch import get_state as get_kill_switch_state
from adaptive_scalper.core.kill_switch import history as kill_switch_history
from adaptive_scalper.core.operator_authority import OperatorAuthority
from adaptive_scalper.execution.close import CloseOutcome
from adaptive_scalper.gateway.types import PendingOrderSnapshot, PositionSnapshot
from adaptive_scalper.runtime import demo as demo_module
from chaos_harness import demo_account
from runtime_helpers import STEP, T0, FakeClock, build_engine, step

START_AT = T0 + 60 * STEP + 10


def _started(tmp_path):
    clock = FakeClock(START_AT)
    engine, conn, gateway = build_engine(tmp_path, mode="DEMO", clock=clock)
    bootstrap_kill_switch(conn, OperatorAuthority("test-operator"), reason="test setup")
    engine.startup()
    return clock, engine, conn, gateway


def _events(conn, event):
    return [r["detail"] for r in conn.execute("SELECT detail FROM runtime_events WHERE event = ?", (event,))]


def test_floating_loss_alone_trips_the_breaker_and_disarms(tmp_path):
    clock, engine, conn, gateway = _started(tmp_path)
    gateway.set_account(demo_account(balance=10000.0, equity=9750.0))  # floating -250 = 2.5 %
    step(engine, clock, seconds=3)

    kill = get_kill_switch_state(conn)
    assert kill.status == KillSwitchStatus.ENGAGED
    assert kill.changed_by == "risk_governor:daily_loss_circuit_breaker"
    assert "floating -250.00" in kill.reason
    assert _events(conn, "DAILY_LOSS_CIRCUIT_BREAKER")
    assert gateway.calls["order_send"] == 0

    # Idempotent: further cycles do not re-engage (one audit row from the breaker).
    step(engine, clock, seconds=5)
    breaker_rows = [r for r in kill_switch_history(conn) if r["actor"] == "risk_governor:daily_loss_circuit_breaker"]
    assert len(breaker_rows) == 1


def test_loss_below_the_limit_does_not_trip(tmp_path):
    clock, engine, conn, gateway = _started(tmp_path)
    gateway.set_account(demo_account(balance=10000.0, equity=9850.0))  # 1.5 %
    step(engine, clock, seconds=3)
    assert get_kill_switch_state(conn).status == KillSwitchStatus.DISENGAGED
    assert not _events(conn, "DAILY_LOSS_CIRCUIT_BREAKER")


def test_floating_gain_does_not_trip(tmp_path):
    clock, engine, conn, gateway = _started(tmp_path)
    gateway.set_account(demo_account(balance=10000.0, equity=10400.0))
    step(engine, clock, seconds=3)
    assert get_kill_switch_state(conn).status == KillSwitchStatus.DISENGAGED


def test_breach_closes_every_tracked_open_position_through_the_safe_close_service(tmp_path, monkeypatch):
    clock, engine, conn, gateway = _started(tmp_path)
    gateway.set_account(demo_account(balance=10000.0, equity=9700.0))
    local = SimpleNamespace(id=5, broker_position_id="77", direction="BUY", canonical_symbol="XAUUSD")
    live = PositionSnapshot(broker_position_id="77", symbol="XAUUSD", direction="BUY", volume=0.05,
                            price_open=2000.0, stop_loss=1990.0, take_profit=2020.0, profit=-300.0,
                            magic=engine.config.runtime.magic, comment="ASN")
    closes = []

    def fake_close(gw, **kwargs):
        closes.append(kwargs)
        return CloseOutcome("FULLY_CLOSED", "closed")
    monkeypatch.setattr(demo_module, "get_open_positions", lambda conn: [local])
    monkeypatch.setattr(demo_module, "close_position_safely", fake_close)

    assert engine.demo._daily_loss_circuit_breaker(int(clock.now), {"77": live}) is True
    assert len(closes) == 1
    call = closes[0]
    assert (call["broker_position_id"], call["expected_direction"], call["expected_volume"], call["broker_symbol"]) \
        == ("77", "BUY", 0.05, "XAUUSD")
    assert call["conn"] is conn and call["reconciliation_chain_key"] == "daily-loss-breaker:5"
    assert any("FULLY_CLOSED" in d for d in _events(conn, "DAILY_LOSS_FLATTEN"))
    assert get_kill_switch_state(conn).status == KillSwitchStatus.ENGAGED


def test_breach_surfaces_own_working_orders_for_the_operator(tmp_path):
    clock, engine, conn, gateway = _started(tmp_path)
    gateway.set_account(demo_account(balance=10000.0, equity=9700.0))
    gateway.inject_pending_order(PendingOrderSnapshot(
        broker_order_id="901", symbol="XAUUSD", direction="BUY", volume=0.05, price=1995.0,
        magic=engine.config.runtime.magic, comment="ASN"))
    assert engine.demo._daily_loss_circuit_breaker(int(clock.now), {}) is True
    assert any("901" in d for d in _events(conn, "DAILY_LOSS_WORKING_ORDERS"))
