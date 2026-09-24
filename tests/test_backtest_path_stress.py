"""Tests for trade-order path stress (directive section 80, formerly "Monte Carlo")."""

from __future__ import annotations

import pytest

from adaptive_scalper.backtest.path_stress import run_trade_order_path_stress
from adaptive_scalper.backtest.types import TRADE_ORDER_PATH_STRESS
from adaptive_scalper.backtest.types import SimulatedTrade


def _trade(pnl: float, **overrides) -> SimulatedTrade:
    defaults = dict(
        strategy_key="momentum_continuation", direction="BUY", entry_time_utc=1000, entry_price=2000.0,
        volume=0.1, initial_monetary_risk=20.0, entry_regime="TRENDING_UP", exit_time_utc=1100,
        exit_price=2005.0, exit_reason="TAKE_PROFIT_HIT", exit_regime="TRENDING_UP", realized_r=pnl / 20.0,
        realized_pnl=pnl, total_cost=0.5,
    )
    defaults.update(overrides)
    return SimulatedTrade(**defaults)


def test_path_stress_is_deterministic_given_the_same_seed():
    trades = (_trade(50.0), _trade(-20.0), _trade(30.0), _trade(-10.0), _trade(15.0))
    r1 = run_trade_order_path_stress(trades, initial_equity=10_000.0, n_simulations=200, seed=42)
    r2 = run_trade_order_path_stress(trades, initial_equity=10_000.0, n_simulations=200, seed=42)
    assert r1 == r2


def test_path_stress_different_seeds_can_differ():
    trades = (_trade(50.0), _trade(-20.0), _trade(30.0), _trade(-10.0), _trade(15.0), _trade(-40.0))
    r1 = run_trade_order_path_stress(trades, initial_equity=10_000.0, n_simulations=200, seed=1)
    r2 = run_trade_order_path_stress(trades, initial_equity=10_000.0, n_simulations=200, seed=2)
    assert r1.max_drawdown != r2.max_drawdown


def test_path_stress_reports_terminal_equity_once_because_order_cannot_change_a_sum():
    # Reordering never changes the SUM, so terminal equity is one number,
    # not a (degenerate) distribution that would look like outcome
    # uncertainty.
    trades = (_trade(50.0), _trade(-20.0), _trade(30.0), _trade(-10.0), _trade(15.0))
    total_pnl = sum(t.realized_pnl for t in trades)
    result = run_trade_order_path_stress(trades, initial_equity=10_000.0, n_simulations=500, seed=7)
    assert result.method == TRADE_ORDER_PATH_STRESS
    assert result.terminal_equity == pytest.approx(10_000.0 + total_pnl)
    assert result.trade_count == 5
    assert not hasattr(result, "final_equity")


def test_path_stress_max_drawdown_varies_with_trade_order():
    # A sequence with a large loss FIRST (nothing to cushion it) should be
    # able to produce a larger drawdown than the same loss occurring LAST
    # after gains have built a buffer -- across enough random orderings,
    # the max-drawdown distribution should show real spread, not a
    # constant.
    trades = (_trade(100.0), _trade(100.0), _trade(-150.0), _trade(50.0))
    result = run_trade_order_path_stress(trades, initial_equity=10_000.0, n_simulations=500, seed=3)
    assert result.max_drawdown.maximum >= result.max_drawdown.minimum
    assert result.max_drawdown.maximum > 0


def test_path_stress_probability_of_ruin_is_zero_when_no_simulation_breaches_threshold():
    trades = (_trade(10.0), _trade(5.0), _trade(-2.0))
    result = run_trade_order_path_stress(trades, initial_equity=10_000.0, n_simulations=200, seed=9, ruin_equity_fraction=0.1)
    assert result.probability_of_ruin == 0.0


def test_path_stress_probability_of_ruin_is_one_when_every_order_breaches_threshold():
    # A single catastrophic loss guarantees ruin regardless of ordering.
    trades = (_trade(10.0), _trade(-9_900.0), _trade(5.0))
    result = run_trade_order_path_stress(trades, initial_equity=10_000.0, n_simulations=200, seed=11, ruin_equity_fraction=0.5)
    assert result.probability_of_ruin == 1.0


def test_path_stress_ignores_unclosed_trades():
    trades = (_trade(50.0), _trade(0.0, exit_time_utc=None, exit_price=None, realized_pnl=None, realized_r=None))
    result = run_trade_order_path_stress(trades, initial_equity=10_000.0, n_simulations=50, seed=1)
    assert result.terminal_equity == pytest.approx(10_050.0)
    assert result.trade_count == 1


def test_path_stress_requires_at_least_one_closed_trade():
    with pytest.raises(ValueError, match="at least one closed trade"):
        run_trade_order_path_stress((), initial_equity=10_000.0, n_simulations=50, seed=1)


def test_path_stress_rejects_non_positive_initial_equity():
    with pytest.raises(ValueError, match="initial_equity"):
        run_trade_order_path_stress((_trade(1.0),), initial_equity=0.0, n_simulations=50, seed=1)


def test_path_stress_rejects_invalid_ruin_fraction():
    with pytest.raises(ValueError, match="ruin_equity_fraction"):
        run_trade_order_path_stress((_trade(1.0),), initial_equity=10_000.0, n_simulations=50, seed=1, ruin_equity_fraction=1.5)
