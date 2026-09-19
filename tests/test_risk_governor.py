"""Tests for the risk governor (directive sections 32-33): safe volume
calculation (round down, reject below minimum, no martingale by
construction) and the hard portfolio risk ceilings.
"""

from __future__ import annotations

import inspect

import pytest

from adaptive_scalper.config.loader import RiskConfig
from adaptive_scalper.gateway.types import SymbolSpec, SymbolTradeMode
from adaptive_scalper.risk.governor import (
    ALLOW,
    BLOCK_RISK,
    RiskGateInput,
    RiskLimits,
    calculate_safe_volume,
    evaluate_risk_gate,
    risk_limits_from_config,
)


def _symbol_spec(**overrides) -> SymbolSpec:
    defaults = dict(
        name="XAUUSD", description="Gold vs US Dollar", currency_base="XAU", currency_profit="USD",
        currency_margin="XAU", digits=2, point=0.01, trade_contract_size=100.0,
        volume_min=0.01, volume_max=100.0, volume_step=0.01, trade_tick_size=0.01,
        trade_tick_value=1.0, spread=10, visible=True, trade_mode=SymbolTradeMode.FULL,
    )
    defaults.update(overrides)
    return SymbolSpec(**defaults)


def _limits(**overrides) -> RiskLimits:
    defaults = dict(
        risk_per_trade_pct=0.25, max_total_open_risk_pct=0.75, max_daily_loss_pct=2.0,
        max_drawdown_pct=5.0, max_open_positions=2, max_positions_per_symbol=1,
    )
    defaults.update(overrides)
    return RiskLimits(**defaults)


# --------------------------------------------------------------------------
# No martingale BY CONSTRUCTION — the API itself has no such input
# --------------------------------------------------------------------------

def test_calculate_safe_volume_has_no_martingale_style_parameter():
    params = set(inspect.signature(calculate_safe_volume).parameters)
    forbidden_substrings = ("previous", "multiplier", "streak", "martingale", "last_volume", "double")
    for param in params:
        for forbidden in forbidden_substrings:
            assert forbidden not in param.lower(), (
                f"calculate_safe_volume has a parameter {param!r} suggesting it could scale "
                f"sizing off a prior trade — this must never be possible"
            )


# --------------------------------------------------------------------------
# calculate_safe_volume
# --------------------------------------------------------------------------

def test_safe_volume_basic_calculation():
    # equity=10000, risk 0.25% = $25 risk. stop=1.0 price unit.
    # risk_per_lot = (1.0 / 0.01) * 1.0 = 100 (i.e. $100 risk per lot at 1.0 stop distance).
    # raw_volume = 25 / 100 = 0.25 lots.
    spec = _symbol_spec()
    result = calculate_safe_volume(equity=10000, risk_per_trade_pct=0.25, stop_distance_price=1.0, symbol_spec=spec)
    assert result.approved is True
    assert result.volume == pytest.approx(0.25)
    assert result.monetary_risk == pytest.approx(25.0)


def test_safe_volume_rounds_down_to_volume_step():
    spec = _symbol_spec(volume_step=0.1)
    # raw_volume = 25/100 = 0.25 -> rounds down to 0.2 (nearest 0.1 step below)
    result = calculate_safe_volume(equity=10000, risk_per_trade_pct=0.25, stop_distance_price=1.0, symbol_spec=spec)
    assert result.volume == pytest.approx(0.2)
    assert result.monetary_risk == pytest.approx(20.0)  # actual risk taken, not the requested $25


def test_safe_volume_rejects_when_below_broker_minimum():
    spec = _symbol_spec(volume_min=1.0)  # requires at least 1 lot
    # raw_volume = 0.25, far below volume_min=1.0
    result = calculate_safe_volume(equity=10000, risk_per_trade_pct=0.25, stop_distance_price=1.0, symbol_spec=spec)
    assert result.approved is False
    assert result.volume is None
    assert "below broker minimum" in result.reason


def test_safe_volume_never_rounds_up_to_reach_minimum():
    # Confirms the rejection path is taken rather than any silent bump to volume_min.
    spec = _symbol_spec(volume_min=1.0, volume_step=0.01)
    result = calculate_safe_volume(equity=100, risk_per_trade_pct=0.25, stop_distance_price=1.0, symbol_spec=spec)
    assert result.approved is False


def test_safe_volume_caps_at_volume_max():
    spec = _symbol_spec(volume_max=0.1)
    result = calculate_safe_volume(equity=1_000_000, risk_per_trade_pct=1.0, stop_distance_price=0.5, symbol_spec=spec)
    assert result.approved is True
    assert result.volume == pytest.approx(0.1)


@pytest.mark.parametrize("equity", [0, -100])
def test_safe_volume_rejects_non_positive_equity(equity):
    result = calculate_safe_volume(equity=equity, risk_per_trade_pct=0.25, stop_distance_price=1.0, symbol_spec=_symbol_spec())
    assert result.approved is False


@pytest.mark.parametrize("stop", [0, -1.0])
def test_safe_volume_rejects_non_positive_stop_distance(stop):
    result = calculate_safe_volume(equity=10000, risk_per_trade_pct=0.25, stop_distance_price=stop, symbol_spec=_symbol_spec())
    assert result.approved is False


def test_safe_volume_rejects_invalid_contract_spec():
    spec = _symbol_spec(trade_tick_value=0.0)
    result = calculate_safe_volume(equity=10000, risk_per_trade_pct=0.25, stop_distance_price=1.0, symbol_spec=spec)
    assert result.approved is False


def test_safe_volume_scales_with_stop_distance():
    spec = _symbol_spec()
    tight = calculate_safe_volume(equity=10000, risk_per_trade_pct=0.25, stop_distance_price=0.5, symbol_spec=spec)
    wide = calculate_safe_volume(equity=10000, risk_per_trade_pct=0.25, stop_distance_price=2.0, symbol_spec=spec)
    # Same dollar risk budget, wider stop -> smaller volume. The two
    # actual monetary risks won't match exactly (volume_step rounding
    # discretizes each independently), but both must stay close to the
    # $25 budget (0.25% of $10000).
    assert tight.volume > wide.volume
    assert tight.monetary_risk == pytest.approx(25.0, abs=1.0)
    assert wide.monetary_risk == pytest.approx(25.0, abs=1.0)


# --------------------------------------------------------------------------
# evaluate_risk_gate
# --------------------------------------------------------------------------

def test_risk_gate_allows_within_all_limits():
    inp = RiskGateInput(
        proposed_symbol="XAUUSD", proposed_monetary_risk=25.0, equity=10000,
        current_total_open_risk=0.0, current_positions_count=0, current_positions_for_symbol=0,
        daily_realized_pnl=0.0, peak_equity=10000,
    )
    decision, reason = evaluate_risk_gate(inp, _limits())
    assert decision == ALLOW


def test_risk_gate_blocks_at_max_open_positions():
    inp = RiskGateInput(
        proposed_symbol="XAUUSD", proposed_monetary_risk=25.0, equity=10000,
        current_total_open_risk=0.0, current_positions_count=2, current_positions_for_symbol=0,
        daily_realized_pnl=0.0, peak_equity=10000,
    )
    decision, reason = evaluate_risk_gate(inp, _limits(max_open_positions=2))
    assert decision == BLOCK_RISK
    assert "max_open_positions" in reason


def test_risk_gate_blocks_at_max_positions_per_symbol():
    inp = RiskGateInput(
        proposed_symbol="XAUUSD", proposed_monetary_risk=25.0, equity=10000,
        current_total_open_risk=0.0, current_positions_count=0, current_positions_for_symbol=1,
        daily_realized_pnl=0.0, peak_equity=10000,
    )
    decision, reason = evaluate_risk_gate(inp, _limits(max_positions_per_symbol=1))
    assert decision == BLOCK_RISK
    assert "max_positions_per_symbol" in reason


def test_risk_gate_blocks_when_total_open_risk_would_be_exceeded():
    inp = RiskGateInput(
        proposed_symbol="XAUUSD", proposed_monetary_risk=50.0, equity=10000,
        current_total_open_risk=50.0, current_positions_count=0, current_positions_for_symbol=0,
        daily_realized_pnl=0.0, peak_equity=10000,
    )
    # max_total_open_risk_pct=0.75% of 10000 = $75; 50+50=100 > 75 -> block
    decision, reason = evaluate_risk_gate(inp, _limits(max_total_open_risk_pct=0.75))
    assert decision == BLOCK_RISK
    assert "max_total_open_risk_pct" in reason


def test_risk_gate_blocks_at_daily_loss_limit():
    inp = RiskGateInput(
        proposed_symbol="XAUUSD", proposed_monetary_risk=10.0, equity=9800,
        current_total_open_risk=0.0, current_positions_count=0, current_positions_for_symbol=0,
        daily_realized_pnl=-200.0, peak_equity=10000,
    )
    # daily loss 200/9800 ~= 2.04% >= max_daily_loss_pct=2.0
    decision, reason = evaluate_risk_gate(inp, _limits(max_daily_loss_pct=2.0))
    assert decision == BLOCK_RISK
    assert "max_daily_loss_pct" in reason


def test_risk_gate_allows_when_daily_pnl_is_positive():
    inp = RiskGateInput(
        proposed_symbol="XAUUSD", proposed_monetary_risk=10.0, equity=10500,
        current_total_open_risk=0.0, current_positions_count=0, current_positions_for_symbol=0,
        daily_realized_pnl=500.0, peak_equity=10500,
    )
    decision, _ = evaluate_risk_gate(inp, _limits())
    assert decision == ALLOW


def test_risk_gate_blocks_at_max_drawdown():
    inp = RiskGateInput(
        proposed_symbol="XAUUSD", proposed_monetary_risk=10.0, equity=9400,
        current_total_open_risk=0.0, current_positions_count=0, current_positions_for_symbol=0,
        daily_realized_pnl=0.0, peak_equity=10000,
    )
    # drawdown = (10000-9400)/10000 = 6% >= max_drawdown_pct=5.0
    decision, reason = evaluate_risk_gate(inp, _limits(max_drawdown_pct=5.0))
    assert decision == BLOCK_RISK
    assert "max_drawdown_pct" in reason


def test_risk_gate_checks_open_positions_before_other_limits():
    # Both max_open_positions AND daily loss would independently block —
    # the FIRST check (max_open_positions) must be the reported reason.
    inp = RiskGateInput(
        proposed_symbol="XAUUSD", proposed_monetary_risk=10.0, equity=9700,
        current_total_open_risk=0.0, current_positions_count=2, current_positions_for_symbol=0,
        daily_realized_pnl=-300.0, peak_equity=10000,
    )
    decision, reason = evaluate_risk_gate(inp, _limits(max_open_positions=2, max_daily_loss_pct=2.0))
    assert decision == BLOCK_RISK
    assert "max_open_positions" in reason


# --------------------------------------------------------------------------
# risk_limits_from_config
# --------------------------------------------------------------------------

def test_risk_limits_from_config_copies_every_field():
    cfg = RiskConfig(
        risk_per_trade_pct=0.5, max_total_open_risk_pct=1.5, max_daily_loss_pct=3.0,
        max_drawdown_pct=6.0, max_open_positions=3, max_positions_per_symbol=2,
    )
    limits = risk_limits_from_config(cfg)
    assert limits.risk_per_trade_pct == 0.5
    assert limits.max_total_open_risk_pct == 1.5
    assert limits.max_daily_loss_pct == 3.0
    assert limits.max_drawdown_pct == 6.0
    assert limits.max_open_positions == 3
    assert limits.max_positions_per_symbol == 2


def test_risk_limits_from_default_config_matches_directive_defaults():
    limits = risk_limits_from_config(RiskConfig())
    assert limits.risk_per_trade_pct == 0.25
    assert limits.max_total_open_risk_pct == 0.75
    assert limits.max_daily_loss_pct == 2.00
    assert limits.max_drawdown_pct == 5.00
    assert limits.max_open_positions == 2
    assert limits.max_positions_per_symbol == 1
