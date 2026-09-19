"""Tests for per-symbol cost estimation (directive section 34)."""

from __future__ import annotations

import pytest

from adaptive_scalper.costs.model import estimate_cost, price_equivalent_of_monetary_cost


def test_price_equivalent_of_monetary_cost_basic_conversion():
    # $7 commission per lot, tick_size=0.01, tick_value=$1 per tick per lot
    # -> price distance needed to earn back $7 = 7 * 0.01 / 1 = 0.07
    result = price_equivalent_of_monetary_cost(7.0, tick_size=0.01, tick_value=1.0)
    assert result == pytest.approx(0.07)


def test_price_equivalent_rejects_non_positive_tick_value():
    with pytest.raises(ValueError):
        price_equivalent_of_monetary_cost(7.0, tick_size=0.01, tick_value=0.0)


def test_price_equivalent_rejects_non_positive_tick_size():
    with pytest.raises(ValueError):
        price_equivalent_of_monetary_cost(7.0, tick_size=0.0, tick_value=1.0)


def test_price_equivalent_rejects_negative_monetary_cost():
    with pytest.raises(ValueError):
        price_equivalent_of_monetary_cost(-1.0, tick_size=0.01, tick_value=1.0)


def test_estimate_cost_sums_components_plus_margin():
    cost = estimate_cost(
        spread_price=1.0, commission_price_equivalent=0.5, expected_slippage_price=0.2,
        swap_price_equivalent=0.1, uncertainty_margin_pct=0.1,
    )
    subtotal = 1.0 + 0.5 + 0.2 + 0.1
    assert cost.uncertainty_margin == pytest.approx(subtotal * 0.1)
    assert cost.total_cost == pytest.approx(subtotal * 1.1)


def test_estimate_cost_defaults_to_spread_only():
    cost = estimate_cost(spread_price=1.0)
    assert cost.commission_cost == 0.0
    assert cost.slippage_cost == 0.0
    assert cost.swap_cost == 0.0
    assert cost.total_cost == pytest.approx(1.1)  # 1.0 + 10% default margin


def test_estimate_cost_zero_margin_means_total_equals_subtotal():
    cost = estimate_cost(spread_price=1.0, commission_price_equivalent=0.5, uncertainty_margin_pct=0.0)
    assert cost.total_cost == pytest.approx(1.5)
    assert cost.uncertainty_margin == 0.0


@pytest.mark.parametrize("field,value", [
    ("spread_price", -1.0),
    ("commission_price_equivalent", -1.0),
    ("expected_slippage_price", -1.0),
    ("swap_price_equivalent", -1.0),
])
def test_estimate_cost_rejects_negative_components(field, value):
    kwargs = {"spread_price": 1.0}
    kwargs[field] = value
    with pytest.raises(ValueError):
        estimate_cost(**kwargs)


def test_estimate_cost_rejects_negative_uncertainty_margin_pct():
    with pytest.raises(ValueError):
        estimate_cost(spread_price=1.0, uncertainty_margin_pct=-0.1)


def test_margin_never_reduces_effective_cost():
    zero_margin = estimate_cost(spread_price=1.0, uncertainty_margin_pct=0.0)
    with_margin = estimate_cost(spread_price=1.0, uncertainty_margin_pct=0.2)
    assert with_margin.total_cost > zero_margin.total_cost
