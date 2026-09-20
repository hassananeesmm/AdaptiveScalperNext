"""Tests for per-symbol cost estimation (directive section 34)."""

from __future__ import annotations

import pytest

from adaptive_scalper.costs.model import estimate_cost, estimate_cost_from_evidence, price_equivalent_of_monetary_cost


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


def test_estimate_cost_has_no_implicit_zero_defaults():
    # External review: every component must be a REQUIRED keyword arg —
    # a caller that forgets one must get a loud TypeError, never a
    # silent "this cost is exactly zero."
    with pytest.raises(TypeError):
        estimate_cost(spread_price=1.0)  # missing commission/slippage/swap


def test_estimate_cost_accepts_an_explicit_known_zero():
    # An explicit 0.0 remains correct when the caller genuinely knows a
    # component is zero (e.g. a documented commission-free account).
    cost = estimate_cost(
        spread_price=1.0, commission_price_equivalent=0.0, expected_slippage_price=0.0,
        swap_price_equivalent=0.0, uncertainty_margin_pct=0.1,
    )
    assert cost.total_cost == pytest.approx(1.1)


def test_estimate_cost_zero_margin_means_total_equals_subtotal():
    cost = estimate_cost(
        spread_price=1.0, commission_price_equivalent=0.5, expected_slippage_price=0.0,
        swap_price_equivalent=0.0, uncertainty_margin_pct=0.0,
    )
    assert cost.total_cost == pytest.approx(1.5)
    assert cost.uncertainty_margin == 0.0


@pytest.mark.parametrize("field,value", [
    ("spread_price", -1.0),
    ("commission_price_equivalent", -1.0),
    ("expected_slippage_price", -1.0),
    ("swap_price_equivalent", -1.0),
])
def test_estimate_cost_rejects_negative_components(field, value):
    kwargs = dict(spread_price=1.0, commission_price_equivalent=0.0, expected_slippage_price=0.0, swap_price_equivalent=0.0)
    kwargs[field] = value
    with pytest.raises(ValueError):
        estimate_cost(**kwargs)


def test_estimate_cost_rejects_negative_uncertainty_margin_pct():
    with pytest.raises(ValueError):
        estimate_cost(
            spread_price=1.0, commission_price_equivalent=0.0, expected_slippage_price=0.0,
            swap_price_equivalent=0.0, uncertainty_margin_pct=-0.1,
        )


def test_margin_never_reduces_effective_cost():
    common = dict(spread_price=1.0, commission_price_equivalent=0.0, expected_slippage_price=0.0, swap_price_equivalent=0.0)
    zero_margin = estimate_cost(**common, uncertainty_margin_pct=0.0)
    with_margin = estimate_cost(**common, uncertainty_margin_pct=0.2)
    assert with_margin.total_cost > zero_margin.total_cost


# --------------------------------------------------------------------------
# estimate_cost_from_evidence — external review fix #3: unknown cost must
# never silently become a known zero.
# --------------------------------------------------------------------------

def test_evidence_returns_estimate_when_everything_is_known():
    result = estimate_cost_from_evidence(
        spread_price=1.0, commission_price_equivalent=0.5,
        expected_slippage_price=0.2, swap_price_equivalent=0.0,
    )
    assert result is not None
    assert result.total_cost > 0


@pytest.mark.parametrize("missing_field", [
    "spread_price", "commission_price_equivalent", "expected_slippage_price", "swap_price_equivalent",
])
def test_evidence_returns_none_when_any_single_component_is_unknown(missing_field):
    kwargs = dict(spread_price=1.0, commission_price_equivalent=0.5, expected_slippage_price=0.2, swap_price_equivalent=0.0)
    kwargs[missing_field] = None
    result = estimate_cost_from_evidence(**kwargs)
    assert result is None


def test_evidence_returns_none_when_everything_is_unknown():
    result = estimate_cost_from_evidence(
        spread_price=None, commission_price_equivalent=None,
        expected_slippage_price=None, swap_price_equivalent=None,
    )
    assert result is None


def test_evidence_zero_swap_must_be_explicit_not_omitted():
    # A genuinely-known-zero swap must be passed as 0.0, not left as
    # None (which would correctly block) and not silently assumed.
    known_zero = estimate_cost_from_evidence(
        spread_price=1.0, commission_price_equivalent=0.0,
        expected_slippage_price=0.0, swap_price_equivalent=0.0,
    )
    unknown = estimate_cost_from_evidence(
        spread_price=1.0, commission_price_equivalent=0.0,
        expected_slippage_price=0.0, swap_price_equivalent=None,
    )
    assert known_zero is not None
    assert unknown is None
