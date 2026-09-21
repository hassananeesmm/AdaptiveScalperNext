"""Tests for adaptive_scalper.simulation.fill_model."""

from __future__ import annotations

import pytest

from adaptive_scalper.gateway.types import Bar
from adaptive_scalper.simulation.fill_model import (
    FillAssumptions,
    money_from_price_distance,
    round_trip_commission_price,
    simulate_fill,
)


def _bar(**overrides) -> Bar:
    defaults = dict(time=1000, open=2000.0, high=2001.0, low=1999.0, close=2000.5, tick_volume=10, spread=20, real_volume=0)
    defaults.update(overrides)
    return Bar(**defaults)


def test_fill_assumptions_rejects_negative_fields():
    with pytest.raises(ValueError):
        FillAssumptions(slippage_price=-0.01, commission_monetary_per_lot=1.0)
    with pytest.raises(ValueError):
        FillAssumptions(slippage_price=0.01, commission_monetary_per_lot=-1.0)


def test_buy_fill_pays_half_spread_and_slippage_above_open():
    bar = _bar(open=2000.0, spread=20)  # 20 points
    assumptions = FillAssumptions(slippage_price=0.02, commission_monetary_per_lot=7.0)
    fill = simulate_fill(bar, "BUY", point_size=0.01, assumptions=assumptions)
    # spread_price = 20 * 0.01 = 0.20; half = 0.10
    assert fill.spread_cost_price == pytest.approx(0.20)
    assert fill.slippage_cost_price == pytest.approx(0.02)
    assert fill.price == pytest.approx(2000.0 + 0.10 + 0.02)


def test_sell_fill_pays_half_spread_and_slippage_below_open():
    bar = _bar(open=2000.0, spread=20)
    assumptions = FillAssumptions(slippage_price=0.02, commission_monetary_per_lot=7.0)
    fill = simulate_fill(bar, "SELL", point_size=0.01, assumptions=assumptions)
    assert fill.price == pytest.approx(2000.0 - 0.10 - 0.02)


def test_buy_fills_worse_than_sell_for_the_same_bar():
    bar = _bar(open=2000.0, spread=20)
    assumptions = FillAssumptions(slippage_price=0.0, commission_monetary_per_lot=0.0)
    buy = simulate_fill(bar, "BUY", point_size=0.01, assumptions=assumptions)
    sell = simulate_fill(bar, "SELL", point_size=0.01, assumptions=assumptions)
    assert buy.price > sell.price  # the spread always costs the trader, both directions


def test_negative_spread_never_produces_a_negative_cost():
    bar = _bar(spread=-5)  # defensive: a malformed/negative spread field
    assumptions = FillAssumptions(slippage_price=0.0, commission_monetary_per_lot=0.0)
    fill = simulate_fill(bar, "BUY", point_size=0.01, assumptions=assumptions)
    assert fill.spread_cost_price == 0.0


def test_invalid_direction_raises():
    with pytest.raises(ValueError):
        simulate_fill(_bar(), "HOLD", point_size=0.01, assumptions=FillAssumptions(slippage_price=0.0, commission_monetary_per_lot=0.0))


def test_round_trip_commission_price_zero_when_commission_zero():
    assumptions = FillAssumptions(slippage_price=0.0, commission_monetary_per_lot=0.0)
    assert round_trip_commission_price(assumptions, tick_size=0.01, tick_value=1.0) == 0.0


def test_round_trip_commission_price_matches_conversion_formula():
    assumptions = FillAssumptions(slippage_price=0.0, commission_monetary_per_lot=7.0)
    # price_equivalent = monetary_cost * tick_size / tick_value = 7.0 * 0.01 / 1.0 = 0.07
    assert round_trip_commission_price(assumptions, tick_size=0.01, tick_value=1.0) == pytest.approx(0.07)


def test_money_from_price_distance_matches_safe_volume_formula():
    # Same formula risk.governor.calculate_safe_volume() uses inverted:
    # risk_per_lot = (stop_distance / tick_size) * tick_value
    money = money_from_price_distance(4.0, 0.05, tick_size=0.01, tick_value=1.0)
    assert money == pytest.approx(4.0 * 0.05 * (1.0 / 0.01))


def test_money_from_price_distance_rejects_invalid_contract_spec():
    with pytest.raises(ValueError):
        money_from_price_distance(1.0, 0.1, tick_size=0.0, tick_value=1.0)
    with pytest.raises(ValueError):
        money_from_price_distance(1.0, 0.1, tick_size=0.01, tick_value=0.0)
