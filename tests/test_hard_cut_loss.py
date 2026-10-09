"""Hard cut-loss backstop (config `[hard_stop]`, 2026-10-09).

Shipped: a BTCUSD position whose close-side price (bid for a BUY, ask for a
SELL) has moved MORE than 0.5 % against its broker fill price is closed at
market through the safe close service, independent of its broker SL.
"""

from __future__ import annotations

import math
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from adaptive_scalper.config.loader import HardStopConfig, load_config
from adaptive_scalper.core.kill_switch import bootstrap as bootstrap_kill_switch
from adaptive_scalper.core.operator_authority import OperatorAuthority
from adaptive_scalper.execution.close import CloseOutcome
from adaptive_scalper.gateway.mt5_gateway import Mt5QueryError
from adaptive_scalper.gateway.types import PositionSnapshot, Tick
from adaptive_scalper.risk.hard_stop import adverse_move_pct, breaches_hard_stop
from adaptive_scalper.runtime import demo as demo_module
from runtime_helpers import STEP, T0, FakeClock, build_engine

REPO_ROOT = Path(__file__).resolve().parent.parent
START_AT = T0 + 60 * STEP + 10
FILL = 60_000.0


# --------------------------------------------------------------------------
# the pure rule
# --------------------------------------------------------------------------

def test_buy_is_measured_on_the_bid_and_sell_on_the_ask():
    assert adverse_move_pct("BUY", FILL, bid=59_700.0, ask=59_710.0) == pytest.approx(0.5)
    assert adverse_move_pct("SELL", FILL, bid=60_290.0, ask=60_300.0) == pytest.approx(0.5)


def test_a_position_in_profit_is_negative():
    assert adverse_move_pct("BUY", FILL, bid=60_300.0, ask=60_310.0) < 0
    assert adverse_move_pct("SELL", FILL, bid=59_690.0, ask=59_700.0) < 0


@pytest.mark.parametrize("args", [("HOLD", FILL, 1.0, 1.0), ("BUY", 0.0, 1.0, 1.0), ("BUY", FILL, math.nan, 1.0),
                                  ("SELL", FILL, 1.0, -1.0)])
def test_uncomputable_is_none(args):
    assert adverse_move_pct(*args) is None


def test_breach_is_strictly_beyond_the_limit():
    assert breaches_hard_stop(0.5001, 0.5) is True
    assert breaches_hard_stop(0.5, 0.5) is False
    assert breaches_hard_stop(None, 0.5) is False
    assert breaches_hard_stop(9.0, None) is False


# --------------------------------------------------------------------------
# config
# --------------------------------------------------------------------------

def test_code_default_has_no_backstop():
    assert HardStopConfig().max_adverse_move_pct == {}


def test_shipped_config_cuts_btcusd_beyond_half_a_percent():
    assert load_config(REPO_ROOT / "config" / "default.toml").hard_stop.max_adverse_move_pct == {"BTCUSD": 0.5}


@pytest.mark.parametrize("value", [{"EURUSD": 0.5}, {"BTCUSD": 0.0}, {"BTCUSD": -0.5}, {"BTCUSD": math.inf}])
def test_invalid_settings_are_rejected(value):
    with pytest.raises(ValidationError):
        HardStopConfig(max_adverse_move_pct=value)


# --------------------------------------------------------------------------
# DEMO runtime
# --------------------------------------------------------------------------

def _started(tmp_path, monkeypatch):
    clock = FakeClock(START_AT)
    engine, conn, gateway = build_engine(tmp_path, mode="DEMO", clock=clock)
    bootstrap_kill_switch(conn, OperatorAuthority("test-operator"), reason="test setup")
    engine.startup()
    demo = engine.demo
    demo.config = demo.config.model_copy(update={"hard_stop": HardStopConfig(max_adverse_move_pct={"BTCUSD": 0.5})})
    closes = []

    def fake_close(gw, **kwargs):
        closes.append(kwargs)
        return CloseOutcome("FULLY_CLOSED", "closed")

    monkeypatch.setattr(demo_module, "close_position_safely", fake_close)
    return clock, demo, conn, gateway, closes


def _quote(monkeypatch, gateway, clock, *, bid, ask, age=0):
    tick = Tick(time=int(clock.now) - age, bid=bid, ask=ask, last=bid, volume=1.0)
    monkeypatch.setattr(gateway, "symbol_info_tick", lambda name: tick)


def _position(direction="BUY", symbol="BTCUSD"):
    local = SimpleNamespace(id=5, broker_position_id="77", direction=direction, canonical_symbol=symbol)
    live = PositionSnapshot(broker_position_id="77", symbol=symbol, direction=direction, volume=0.01,
                            price_open=FILL, stop_loss=0.0, take_profit=0.0, profit=0.0, magic=1, comment="ASN")
    return local, live


def _events(conn, event):
    return [r["detail"] for r in conn.execute("SELECT detail FROM runtime_events WHERE event = ?", (event,))]


def test_buy_beyond_half_a_percent_is_closed_through_the_safe_close_service(tmp_path, monkeypatch):
    clock, demo, conn, gateway, closes = _started(tmp_path, monkeypatch)
    _quote(monkeypatch, gateway, clock, bid=59_699.0, ask=59_705.0)  # 0.5017 % against
    local, live = _position("BUY")

    assert demo._hard_cut_loss(local, live, int(clock.now)) is True
    assert len(closes) == 1
    call = closes[0]
    assert (call["broker_position_id"], call["expected_direction"], call["expected_volume"], call["broker_symbol"],
            call["comment"], call["reconciliation_chain_key"]) == \
        ("77", "BUY", 0.01, "BTCUSD", "ASN hard cut-loss", "hard-cut-loss:5")
    assert any("0.502% against" in d for d in _events(conn, "HARD_CUT_LOSS_TRIGGERED"))
    assert any("FULLY_CLOSED" in d for d in _events(conn, "HARD_CUT_LOSS_CLOSE"))


def test_sell_beyond_half_a_percent_is_closed(tmp_path, monkeypatch):
    clock, demo, conn, gateway, closes = _started(tmp_path, monkeypatch)
    _quote(monkeypatch, gateway, clock, bid=60_295.0, ask=60_301.0)
    local, live = _position("SELL")
    assert demo._hard_cut_loss(local, live, int(clock.now)) is True
    assert closes[0]["expected_direction"] == "SELL"


def test_exactly_half_a_percent_is_not_closed(tmp_path, monkeypatch):
    clock, demo, conn, gateway, closes = _started(tmp_path, monkeypatch)
    _quote(monkeypatch, gateway, clock, bid=59_700.0, ask=59_706.0)
    local, live = _position("BUY")
    assert demo._hard_cut_loss(local, live, int(clock.now)) is False
    assert closes == []


def test_unconfigured_symbol_is_never_cut(tmp_path, monkeypatch):
    clock, demo, conn, gateway, closes = _started(tmp_path, monkeypatch)
    _quote(monkeypatch, gateway, clock, bid=1.0, ask=1.0)  # absurd loss, but XAUUSD has no backstop
    local, live = _position("BUY", symbol="XAUUSD")
    assert demo._hard_cut_loss(local, live, int(clock.now)) is False
    assert closes == []


def test_stale_quote_is_journaled_never_read_as_no_loss(tmp_path, monkeypatch):
    clock, demo, conn, gateway, closes = _started(tmp_path, monkeypatch)
    _quote(monkeypatch, gateway, clock, bid=50_000.0, ask=50_010.0, age=3600)
    local, live = _position("BUY")
    assert demo._hard_cut_loss(local, live, int(clock.now)) is False
    assert closes == []
    assert _events(conn, "HARD_CUT_LOSS_UNEVALUATED")


def test_unreadable_broker_truth_after_the_close_degrades_the_cycle(tmp_path, monkeypatch):
    clock, demo, conn, gateway, closes = _started(tmp_path, monkeypatch)
    monkeypatch.setattr(demo_module, "close_position_safely",
                        lambda gw, **kw: CloseOutcome("SENT", "sent", broker_truth_error="positions_get None"))
    _quote(monkeypatch, gateway, clock, bid=59_000.0, ask=59_010.0)
    local, live = _position("BUY")
    with pytest.raises(Mt5QueryError):
        demo._hard_cut_loss(local, live, int(clock.now))


def test_position_cycle_cuts_before_and_instead_of_the_normal_review(tmp_path, monkeypatch):
    clock, demo, conn, gateway, closes = _started(tmp_path, monkeypatch)
    local, live = _position("BUY")
    reviewed = []
    clean = SimpleNamespace(status="CLEAN", unrepaired_position_ids=[], unrepaired_order_ids=[])
    monkeypatch.setattr(demo_module, "run_reconciliation", lambda *a, **k: clean)
    monkeypatch.setattr(demo_module, "apply_unknown_resolutions", lambda *a, **k: [])
    monkeypatch.setattr(demo_module, "resolve_unresolved_closes", lambda *a, **k: [])
    monkeypatch.setattr(demo_module, "get_open_positions", lambda conn: [local])
    monkeypatch.setattr(demo_module, "has_unresolved_close", lambda conn, pid: False)
    monkeypatch.setattr(gateway, "positions_get", lambda: [live])
    monkeypatch.setattr(demo, "_daily_loss_circuit_breaker", lambda now, positions: False)
    monkeypatch.setattr(demo, "_review", lambda local, live, now: reviewed.append(local.broker_position_id))

    _quote(monkeypatch, gateway, clock, bid=59_000.0, ask=59_010.0)  # 1.67 % against
    demo._position_cycle(int(clock.now))
    assert len(closes) == 1 and reviewed == []

    _quote(monkeypatch, gateway, clock, bid=59_900.0, ask=59_910.0)  # 0.17 % against: normal review
    demo._position_cycle(int(clock.now))
    assert len(closes) == 1 and reviewed == ["77"]
