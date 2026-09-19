"""Tests for the expected-net-edge gate (directive section 34)."""

from __future__ import annotations

import pytest

from adaptive_scalper.costs.edge import (
    ALLOW,
    BLOCK_COST,
    BLOCK_EXPECTED_EDGE,
    evaluate_cost_gate,
    evaluate_expected_edge,
    expected_gross_edge_price,
)
from adaptive_scalper.costs.model import estimate_cost
from adaptive_scalper.strategies.base import StrategySignal


def _signal(**overrides) -> StrategySignal:
    defaults = dict(
        strategy_key="momentum_continuation", strategy_version=1, canonical_symbol="XAUUSD",
        direction="BUY", raw_confidence=0.6, stop_distance=1.0, target_distance=2.0,
        expected_duration_seconds=300, entry_method="MARKET", regime="TRENDING_UP",
        rationale="test", feature_schema_version=1, data_timestamp=1000,
    )
    defaults.update(overrides)
    return StrategySignal(**defaults)


def test_expected_gross_edge_matches_standard_ev_formula():
    signal = _signal(raw_confidence=0.6, stop_distance=1.0, target_distance=2.0)
    # EV = 0.6*2.0 - 0.4*1.0 = 1.2 - 0.4 = 0.8
    assert expected_gross_edge_price(signal) == pytest.approx(0.8)


def test_expected_gross_edge_negative_for_a_poor_setup():
    signal = _signal(raw_confidence=0.3, stop_distance=2.0, target_distance=1.0)
    # EV = 0.3*1.0 - 0.7*2.0 = 0.3 - 1.4 = -1.1
    assert expected_gross_edge_price(signal) == pytest.approx(-1.1)


def test_evaluate_expected_edge_sufficient_when_net_positive():
    signal = _signal(raw_confidence=0.6, stop_distance=1.0, target_distance=2.0)  # gross=0.8
    cost = estimate_cost(spread_price=0.1, uncertainty_margin_pct=0.0)  # total_cost=0.1
    result = evaluate_expected_edge(signal, cost)
    assert result.expected_net_edge == pytest.approx(0.7)
    assert result.sufficient is True


def test_evaluate_expected_edge_insufficient_when_cost_exceeds_gross():
    signal = _signal(raw_confidence=0.55, stop_distance=1.0, target_distance=1.2)  # gross=0.55*1.2-0.45*1=0.21
    cost = estimate_cost(spread_price=0.5, uncertainty_margin_pct=0.0)
    result = evaluate_expected_edge(signal, cost)
    assert result.expected_net_edge < 0
    assert result.sufficient is False


def test_evaluate_expected_edge_respects_min_net_edge_threshold():
    signal = _signal(raw_confidence=0.6, stop_distance=1.0, target_distance=2.0)  # gross=0.8
    cost = estimate_cost(spread_price=0.1, uncertainty_margin_pct=0.0)  # net=0.7
    assert evaluate_expected_edge(signal, cost, min_net_edge_price=0.5).sufficient is True
    assert evaluate_expected_edge(signal, cost, min_net_edge_price=0.75).sufficient is False


def test_evaluate_cost_gate_returns_block_cost_when_cost_is_none():
    signal = _signal()
    decision, evaluation = evaluate_cost_gate(signal, None)
    assert decision == BLOCK_COST
    assert evaluation is None


def test_evaluate_cost_gate_returns_allow_for_sufficient_edge():
    signal = _signal(raw_confidence=0.6, stop_distance=1.0, target_distance=2.0)
    cost = estimate_cost(spread_price=0.1, uncertainty_margin_pct=0.0)
    decision, evaluation = evaluate_cost_gate(signal, cost)
    assert decision == ALLOW
    assert evaluation.sufficient is True


def test_evaluate_cost_gate_returns_block_expected_edge_for_insufficient_edge():
    signal = _signal(raw_confidence=0.55, stop_distance=1.0, target_distance=1.2)
    cost = estimate_cost(spread_price=0.5, uncertainty_margin_pct=0.0)
    decision, evaluation = evaluate_cost_gate(signal, cost)
    assert decision == BLOCK_EXPECTED_EDGE
    assert evaluation.sufficient is False


def test_reason_string_is_specific_not_generic():
    signal = _signal(raw_confidence=0.6, stop_distance=1.0, target_distance=2.0)
    cost = estimate_cost(spread_price=0.1, uncertainty_margin_pct=0.0)
    result = evaluate_expected_edge(signal, cost)
    assert "gross_edge=" in result.reason
    assert "total_cost=" in result.reason
    assert "net_edge=" in result.reason
