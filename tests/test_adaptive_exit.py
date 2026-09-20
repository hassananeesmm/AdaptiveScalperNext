"""Tests for position_management.adaptive_exit (directive sections 17-23)."""

from __future__ import annotations

import pytest

from adaptive_scalper.position_management.adaptive_exit import (
    FULL_CLOSE,
    HOLD,
    MOVE_PROTECTIVE_STOP,
    AdaptiveExitParams,
    compute_current_r,
    evaluate_adaptive_exit,
    resolve_new_stop_price,
)


def _decide(**overrides):
    defaults = dict(
        current_r=0.1, peak_r=0.1, holding_seconds=10, thesis_valid=True, regime_reversed=False,
    )
    defaults.update(overrides)
    return evaluate_adaptive_exit(**defaults)


# --------------------------------------------------------------------------
# compute_current_r
# --------------------------------------------------------------------------

def test_compute_current_r_basic():
    assert compute_current_r(20.0, 10.0) == 0.5


def test_compute_current_r_negative_pnl():
    assert compute_current_r(20.0, -10.0) == -0.5


def test_compute_current_r_none_for_zero_risk():
    assert compute_current_r(0.0, 10.0) is None


def test_compute_current_r_none_for_negative_risk():
    assert compute_current_r(-5.0, 10.0) is None


# --------------------------------------------------------------------------
# Priority order: max holding time first
# --------------------------------------------------------------------------

def test_max_holding_time_triggers_full_close():
    decision = _decide(holding_seconds=600)
    assert decision.action == FULL_CLOSE
    assert "max holding" in decision.reason


def test_max_holding_time_below_threshold_does_not_trigger():
    decision = _decide(holding_seconds=599, current_r=0.1, peak_r=0.1)
    assert decision.action == HOLD


def test_max_holding_disabled_never_triggers():
    params = AdaptiveExitParams(max_holding_enabled=False)
    decision = _decide(holding_seconds=999999, params=params)
    assert decision.action != FULL_CLOSE or "max holding" not in decision.reason


def test_max_holding_time_overrides_profitable_position():
    # Even a nicely profitable position must still close on max holding.
    decision = _decide(holding_seconds=600, current_r=0.9, peak_r=0.9)
    assert decision.action == FULL_CLOSE
    assert "max holding" in decision.reason


# --------------------------------------------------------------------------
# Thesis invalidation
# --------------------------------------------------------------------------

def test_thesis_invalidated_triggers_full_close():
    decision = _decide(thesis_valid=False)
    assert decision.action == FULL_CLOSE
    assert "thesis" in decision.reason


def test_thesis_invalidated_disabled_does_not_trigger():
    params = AdaptiveExitParams(setup_deterioration_exit=False)
    decision = _decide(thesis_valid=False, params=params)
    assert decision.action == HOLD


# --------------------------------------------------------------------------
# Regime reversal
# --------------------------------------------------------------------------

def test_regime_reversed_triggers_full_close():
    decision = _decide(regime_reversed=True)
    assert decision.action == FULL_CLOSE
    assert "regime" in decision.reason


def test_regime_reversed_disabled_does_not_trigger():
    params = AdaptiveExitParams(regime_reversal_exit=False)
    decision = _decide(regime_reversed=True, params=params)
    assert decision.action == HOLD


def test_thesis_checked_before_regime():
    decision = _decide(thesis_valid=False, regime_reversed=True)
    assert "thesis" in decision.reason


# --------------------------------------------------------------------------
# Early take profit
# --------------------------------------------------------------------------

def test_early_take_profit_triggers_full_close():
    decision = _decide(current_r=1.00, peak_r=1.00)
    assert decision.action == FULL_CLOSE
    assert "early profit" in decision.reason


def test_just_below_early_take_profit_does_not_trigger():
    decision = _decide(current_r=0.99, peak_r=0.99)
    assert decision.action != FULL_CLOSE


# --------------------------------------------------------------------------
# Profit giveback protection (directive's own worked example)
# --------------------------------------------------------------------------

def test_directive_worked_example_giveback():
    # peak_r=0.85, current_r=0.65 -> giveback=0.20, peak >= 0.60 trigger.
    decision = _decide(current_r=0.65, peak_r=0.85)
    assert decision.action == FULL_CLOSE
    assert "giveback" in decision.reason


def test_giveback_below_trigger_peak_does_not_fire():
    # peak never reached the 0.60 trigger, so giveback doesn't apply
    # even though the retracement itself would exceed 0.20 in isolation.
    decision = _decide(current_r=0.10, peak_r=0.35)
    assert decision.action != FULL_CLOSE


def test_giveback_below_max_retracement_does_not_fire():
    decision = _decide(current_r=0.70, peak_r=0.85)  # giveback=0.15 < 0.20
    assert decision.action != FULL_CLOSE


def test_giveback_exactly_at_thresholds_fires():
    decision = _decide(current_r=0.40, peak_r=0.60)  # peak==trigger, giveback==max_retracement
    assert decision.action == FULL_CLOSE


# --------------------------------------------------------------------------
# Breakeven advancement
# --------------------------------------------------------------------------

def test_breakeven_trigger_moves_stop():
    decision = _decide(current_r=0.40, peak_r=0.40)
    assert decision.action == MOVE_PROTECTIVE_STOP
    assert decision.new_stop_r == 0.05


def test_breakeven_below_trigger_holds():
    decision = _decide(current_r=0.39, peak_r=0.39)
    assert decision.action == HOLD


def test_breakeven_disabled_does_not_trigger():
    params = AdaptiveExitParams(breakeven_enabled=False)
    decision = _decide(current_r=0.40, peak_r=0.40, params=params)
    assert decision.action == HOLD


# --------------------------------------------------------------------------
# HOLD as the deliberate default
# --------------------------------------------------------------------------

def test_neutral_position_holds():
    decision = _decide(current_r=0.05, peak_r=0.05)
    assert decision.action == HOLD


def test_losing_position_within_risk_holds():
    decision = _decide(current_r=-0.5, peak_r=0.0)
    assert decision.action == HOLD


# --------------------------------------------------------------------------
# resolve_new_stop_price: never move a protective stop backward
# --------------------------------------------------------------------------

def test_buy_stop_moves_up_only():
    assert resolve_new_stop_price(1990.0, 1995.0, "BUY") == 1995.0


def test_buy_stop_never_moves_down():
    assert resolve_new_stop_price(1995.0, 1990.0, "BUY") == 1995.0


def test_sell_stop_moves_down_only():
    assert resolve_new_stop_price(2010.0, 2005.0, "SELL") == 2005.0


def test_sell_stop_never_moves_up():
    assert resolve_new_stop_price(2005.0, 2010.0, "SELL") == 2005.0


def test_resolve_new_stop_price_invalid_direction_raises():
    with pytest.raises(ValueError):
        resolve_new_stop_price(1990.0, 1995.0, "HOLD")
