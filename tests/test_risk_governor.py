"""Tests for the risk governor (directive sections 32-33): safe volume
calculation (round down, reject below minimum, no martingale by
construction) and the hard portfolio risk ceilings.
"""

from __future__ import annotations

import inspect
import math

import pytest

from adaptive_scalper.config.loader import RiskConfig
from adaptive_scalper.gateway.types import SymbolSpec, SymbolTradeMode
from adaptive_scalper.risk.governor import (
    ALLOW,
    BLOCK_RISK,
    RiskGateInput,
    RiskLimits,
    calculate_safe_volume,
    effective_daily_loss_pct,
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
        current_total_open_risk=0.0, current_total_pending_risk=0.0,
        current_positions_count=0, current_positions_for_symbol=0,
        daily_realized_pnl=0.0, peak_equity=10000,
    )
    decision, reason = evaluate_risk_gate(inp, _limits())
    assert decision == ALLOW


def test_risk_gate_blocks_at_max_open_positions():
    inp = RiskGateInput(
        proposed_symbol="XAUUSD", proposed_monetary_risk=25.0, equity=10000,
        current_total_open_risk=0.0, current_total_pending_risk=0.0,
        current_positions_count=2, current_positions_for_symbol=0,
        daily_realized_pnl=0.0, peak_equity=10000,
    )
    decision, reason = evaluate_risk_gate(inp, _limits(max_open_positions=2))
    assert decision == BLOCK_RISK
    assert "max_open_positions" in reason


def test_risk_gate_blocks_at_max_positions_per_symbol():
    inp = RiskGateInput(
        proposed_symbol="XAUUSD", proposed_monetary_risk=25.0, equity=10000,
        current_total_open_risk=0.0, current_total_pending_risk=0.0,
        current_positions_count=0, current_positions_for_symbol=1,
        daily_realized_pnl=0.0, peak_equity=10000,
    )
    decision, reason = evaluate_risk_gate(inp, _limits(max_positions_per_symbol=1))
    assert decision == BLOCK_RISK
    assert "max_positions_per_symbol" in reason


def test_risk_gate_blocks_when_total_open_risk_would_be_exceeded():
    inp = RiskGateInput(
        proposed_symbol="XAUUSD", proposed_monetary_risk=20.0, equity=10000,
        current_total_open_risk=60.0, current_total_pending_risk=0.0,
        current_positions_count=0, current_positions_for_symbol=0,
        daily_realized_pnl=0.0, peak_equity=10000,
    )
    # max_total_open_risk_pct=0.75% of 10000 = $75; 60+0+20=80 > 75 -> block.
    # proposed_monetary_risk=20 stays within the independent per-trade
    # ceiling ($25 = 0.25% of 10000) so THIS check, not that one, fires.
    decision, reason = evaluate_risk_gate(inp, _limits(max_total_open_risk_pct=0.75))
    assert decision == BLOCK_RISK
    assert "max_total_open_risk_pct" in reason


def test_risk_gate_blocks_at_daily_loss_limit():
    inp = RiskGateInput(
        proposed_symbol="XAUUSD", proposed_monetary_risk=10.0, equity=9800,
        current_total_open_risk=0.0, current_total_pending_risk=0.0,
        current_positions_count=0, current_positions_for_symbol=0,
        daily_realized_pnl=-200.0, peak_equity=10000,
    )
    # daily loss 200/9800 ~= 2.04% >= max_daily_loss_pct=2.0
    decision, reason = evaluate_risk_gate(inp, _limits(max_daily_loss_pct=2.0))
    assert decision == BLOCK_RISK
    assert "max_daily_loss_pct" in reason


def test_risk_gate_allows_when_daily_pnl_is_positive():
    inp = RiskGateInput(
        proposed_symbol="XAUUSD", proposed_monetary_risk=10.0, equity=10500,
        current_total_open_risk=0.0, current_total_pending_risk=0.0,
        current_positions_count=0, current_positions_for_symbol=0,
        daily_realized_pnl=500.0, peak_equity=10500,
    )
    decision, _ = evaluate_risk_gate(inp, _limits())
    assert decision == ALLOW


def test_risk_gate_blocks_at_max_drawdown():
    inp = RiskGateInput(
        proposed_symbol="XAUUSD", proposed_monetary_risk=10.0, equity=9400,
        current_total_open_risk=0.0, current_total_pending_risk=0.0,
        current_positions_count=0, current_positions_for_symbol=0,
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
        current_total_open_risk=0.0, current_total_pending_risk=0.0,
        current_positions_count=2, current_positions_for_symbol=0,
        daily_realized_pnl=-300.0, peak_equity=10000,
    )
    decision, reason = evaluate_risk_gate(inp, _limits(max_open_positions=2, max_daily_loss_pct=2.0))
    assert decision == BLOCK_RISK
    assert "max_open_positions" in reason


# --------------------------------------------------------------------------
# External review fix #1: independent per-trade risk ceiling — the gate
# must not simply trust that proposed_monetary_risk was correctly derived
# from calculate_safe_volume().
# --------------------------------------------------------------------------

def test_risk_gate_blocks_a_tampered_oversized_proposal_even_with_room_elsewhere():
    # Everything else about the portfolio is pristine (no open positions,
    # no daily loss, no drawdown) — only the proposal itself is too large
    # relative to equity, as if calculate_safe_volume() was bypassed or
    # its result was tampered with downstream.
    inp = RiskGateInput(
        proposed_symbol="XAUUSD", proposed_monetary_risk=500.0, equity=10000,  # 5% of equity, way over 0.25%
        current_total_open_risk=0.0, current_total_pending_risk=0.0,
        current_positions_count=0, current_positions_for_symbol=0,
        daily_realized_pnl=0.0, peak_equity=10000,
    )
    decision, reason = evaluate_risk_gate(inp, _limits(risk_per_trade_pct=0.25))
    assert decision == BLOCK_RISK
    assert "per-trade ceiling" in reason


def test_risk_gate_allows_proposal_exactly_at_per_trade_ceiling():
    inp = RiskGateInput(
        proposed_symbol="XAUUSD", proposed_monetary_risk=25.0, equity=10000,  # exactly 0.25%
        current_total_open_risk=0.0, current_total_pending_risk=0.0,
        current_positions_count=0, current_positions_for_symbol=0,
        daily_realized_pnl=0.0, peak_equity=10000,
    )
    decision, _ = evaluate_risk_gate(inp, _limits(risk_per_trade_pct=0.25))
    assert decision == ALLOW


def test_risk_gate_blocks_non_positive_proposed_risk():
    for bad_risk in (0.0, -10.0):
        inp = RiskGateInput(
            proposed_symbol="XAUUSD", proposed_monetary_risk=bad_risk, equity=10000,
            current_total_open_risk=0.0, current_total_pending_risk=0.0,
            current_positions_count=0, current_positions_for_symbol=0,
            daily_realized_pnl=0.0, peak_equity=10000,
        )
        decision, reason = evaluate_risk_gate(inp, _limits())
        assert decision == BLOCK_RISK
        assert "proposed_monetary_risk" in reason


def test_risk_gate_blocks_non_finite_proposed_risk():
    inp = RiskGateInput(
        proposed_symbol="XAUUSD", proposed_monetary_risk=float("inf"), equity=10000,
        current_total_open_risk=0.0, current_total_pending_risk=0.0,
        current_positions_count=0, current_positions_for_symbol=0,
        daily_realized_pnl=0.0, peak_equity=10000,
    )
    decision, reason = evaluate_risk_gate(inp, _limits())
    assert decision == BLOCK_RISK


def test_risk_gate_blocks_non_positive_equity():
    inp = RiskGateInput(
        proposed_symbol="XAUUSD", proposed_monetary_risk=10.0, equity=0.0,
        current_total_open_risk=0.0, current_total_pending_risk=0.0,
        current_positions_count=0, current_positions_for_symbol=0,
        daily_realized_pnl=0.0, peak_equity=10000,
    )
    decision, reason = evaluate_risk_gate(inp, _limits())
    assert decision == BLOCK_RISK
    assert "equity" in reason


# --------------------------------------------------------------------------
# External review fix #2: pending risk must count toward the total-risk
# ceiling alongside open risk — it must not "disappear" until filled.
# --------------------------------------------------------------------------

def test_risk_gate_blocks_on_pending_risk_alone():
    inp = RiskGateInput(
        proposed_symbol="XAUUSD", proposed_monetary_risk=10.0, equity=10000,
        current_total_open_risk=0.0, current_total_pending_risk=70.0,  # pending alone near the cap
        current_positions_count=0, current_positions_for_symbol=0,
        daily_realized_pnl=0.0, peak_equity=10000,
    )
    # max_total_open_risk_pct=0.75% of 10000 = $75; 0+70+10=80 > 75 -> block
    decision, reason = evaluate_risk_gate(inp, _limits(max_total_open_risk_pct=0.75))
    assert decision == BLOCK_RISK
    assert "pending" in reason


def test_risk_gate_allows_when_open_plus_pending_plus_proposal_stays_under_limit():
    inp = RiskGateInput(
        proposed_symbol="XAUUSD", proposed_monetary_risk=10.0, equity=10000,
        current_total_open_risk=30.0, current_total_pending_risk=30.0,
        current_positions_count=0, current_positions_for_symbol=0,
        daily_realized_pnl=0.0, peak_equity=10000,
    )
    # 30+30+10=70 <= 75 -> allow
    decision, _ = evaluate_risk_gate(inp, _limits(max_total_open_risk_pct=0.75))
    assert decision == ALLOW


def test_risk_gate_blocks_when_open_plus_pending_plus_proposal_crosses_limit():
    inp = RiskGateInput(
        proposed_symbol="XAUUSD", proposed_monetary_risk=20.0, equity=10000,
        current_total_open_risk=30.0, current_total_pending_risk=30.0,
        current_positions_count=0, current_positions_for_symbol=0,
        daily_realized_pnl=0.0, peak_equity=10000,
    )
    # 30+30+20=80 > 75 -> block
    decision, reason = evaluate_risk_gate(inp, _limits(max_total_open_risk_pct=0.75))
    assert decision == BLOCK_RISK
    assert "max_total_open_risk_pct" in reason


# --------------------------------------------------------------------------
# risk_limits_from_config
# --------------------------------------------------------------------------

def test_risk_limits_from_config_copies_every_field():
    # Non-default values, all within the hard ceilings (which config may lower,
    # never raise); max_positions_per_symbol's ceiling is 1, its only legal value.
    cfg = RiskConfig(
        risk_per_trade_pct=0.2, max_total_open_risk_pct=0.6, max_daily_loss_pct=1.5,
        max_drawdown_pct=4.0, max_open_positions=1, max_positions_per_symbol=1,
    )
    limits = risk_limits_from_config(cfg)
    assert limits.risk_per_trade_pct == 0.2
    assert limits.max_total_open_risk_pct == 0.6
    assert limits.max_daily_loss_pct == 1.5
    assert limits.max_drawdown_pct == 4.0
    assert limits.max_open_positions == 1
    assert limits.max_positions_per_symbol == 1


def test_risk_limits_from_default_config_matches_directive_defaults():
    limits = risk_limits_from_config(RiskConfig())
    assert limits.risk_per_trade_pct == 0.25
    assert limits.max_total_open_risk_pct == 0.75
    assert limits.max_daily_loss_pct == 2.00
    assert limits.max_drawdown_pct == 5.00
    assert limits.max_open_positions == 2
    assert limits.max_positions_per_symbol == 1


# --------------------------------------------------------------------------
# Daily loss = realized + floating LOSS (2026-10-06 tightening)
# --------------------------------------------------------------------------

def _daily_input(*, equity, realized, floating, day_start=None, peak=10000.0) -> RiskGateInput:
    return RiskGateInput(
        proposed_symbol="XAUUSD", proposed_monetary_risk=10.0, equity=equity,
        current_total_open_risk=0.0, current_total_pending_risk=0.0,
        current_positions_count=0, current_positions_for_symbol=0,
        daily_realized_pnl=realized, peak_equity=peak,
        daily_floating_pnl=floating, day_start_equity=day_start,
    )


def test_effective_daily_loss_formula_is_percent_of_day_start_equity():
    # -100 * (-100 + min(0, -100)) / 10000 = 2.00 (percent, the max_daily_loss_pct unit)
    assert effective_daily_loss_pct(realized_pnl=-100.0, floating_pnl=-100.0,
                                    day_start_equity=10000.0, equity=10000.0) == pytest.approx(2.0)


def test_floating_gain_never_offsets_a_realized_loss():
    assert effective_daily_loss_pct(realized_pnl=-150.0, floating_pnl=+500.0,
                                    day_start_equity=10000.0, equity=10350.0) == pytest.approx(1.5)


def test_daily_profit_is_zero_loss_not_negative():
    assert effective_daily_loss_pct(realized_pnl=300.0, floating_pnl=0.0,
                                    day_start_equity=10000.0, equity=10300.0) == 0.0


def test_denominator_is_never_more_lenient_than_current_equity():
    # day start 10000, equity 9800: the smaller (9800) is used -> 200/9800 = 2.04 %
    pct = effective_daily_loss_pct(realized_pnl=-200.0, floating_pnl=0.0, day_start_equity=10000.0, equity=9800.0)
    assert pct == pytest.approx(200.0 / 9800.0 * 100.0)


@pytest.mark.parametrize("kwargs", [
    dict(realized_pnl=math.nan, floating_pnl=0.0, day_start_equity=10000.0, equity=10000.0),
    dict(realized_pnl=0.0, floating_pnl=math.inf, day_start_equity=10000.0, equity=10000.0),
    dict(realized_pnl=0.0, floating_pnl=0.0, day_start_equity=-1.0, equity=10000.0),
    dict(realized_pnl=0.0, floating_pnl=0.0, day_start_equity=math.nan, equity=10000.0),
])
def test_unknowable_daily_loss_is_treated_as_breached(kwargs):
    assert effective_daily_loss_pct(**kwargs) == math.inf


def test_risk_gate_blocks_on_floating_loss_alone():
    # realized 0, open positions -200 floating on a 10000 day start: 2.0 % -> breach
    decision, reason = evaluate_risk_gate(
        _daily_input(equity=9800.0, realized=0.0, floating=-200.0, day_start=10000.0), _limits())
    assert decision == BLOCK_RISK
    assert "max_daily_loss_pct" in reason and "floating loss -200.00" in reason


def test_risk_gate_blocks_on_realized_plus_floating_crossing_the_limit():
    # neither alone breaches (1.2 % and 0.9 %), together 2.1 %
    decision, reason = evaluate_risk_gate(
        _daily_input(equity=9790.0, realized=-120.0, floating=-90.0, day_start=10000.0), _limits())
    assert decision == BLOCK_RISK
    assert "max_daily_loss_pct" in reason


def test_risk_gate_allows_combined_loss_just_under_the_limit():
    decision, _ = evaluate_risk_gate(
        _daily_input(equity=9810.0, realized=-100.0, floating=-90.0, day_start=10000.0), _limits())
    assert decision == ALLOW  # 190/9810 = 1.94 %


def test_risk_gate_without_floating_fields_keeps_the_realized_only_behaviour():
    # Callers that do not supply floating/day-start (the backtest) are unchanged.
    inp = RiskGateInput(
        proposed_symbol="XAUUSD", proposed_monetary_risk=10.0, equity=9800,
        current_total_open_risk=0.0, current_total_pending_risk=0.0,
        current_positions_count=0, current_positions_for_symbol=0,
        daily_realized_pnl=-199.0, peak_equity=10000,
    )
    decision, _ = evaluate_risk_gate(inp, _limits())
    assert decision == BLOCK_RISK  # 199/9800 = 2.03 %, exactly as before
