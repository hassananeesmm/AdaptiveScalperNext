"""Tests for portfolio exposure tracking (directive section 35)."""

from __future__ import annotations

import pytest

from adaptive_scalper.portfolio.correlation import CorrelationResult
from adaptive_scalper.portfolio.exposure import (
    ALLOW,
    BLOCK_PORTFOLIO_RISK,
    PortfolioRiskLimits,
    PositionExposure,
    compute_exposure,
    correlated_cluster_exposure,
    evaluate_portfolio_risk_gate,
    portfolio_risk_limits_from_risk_limits,
)


def test_position_exposure_validates_symbol():
    with pytest.raises(ValueError):
        PositionExposure(canonical_symbol="EURUSD", direction="BUY", monetary_risk=10.0)


def test_position_exposure_validates_direction():
    with pytest.raises(ValueError):
        PositionExposure(canonical_symbol="XAUUSD", direction="HOLD", monetary_risk=10.0)


def test_position_exposure_validates_non_negative_risk():
    with pytest.raises(ValueError):
        PositionExposure(canonical_symbol="XAUUSD", direction="BUY", monetary_risk=-1.0)


def test_total_open_risk_sums_across_positions():
    positions = [
        PositionExposure("XAUUSD", "BUY", 10.0),
        PositionExposure("GBPJPY", "SELL", 20.0),
    ]
    exposure = compute_exposure(positions)
    assert exposure.total_open_risk == pytest.approx(30.0)


def test_pending_risk_tracked_separately_from_open():
    open_positions = [PositionExposure("XAUUSD", "BUY", 10.0)]
    pending = [PositionExposure("BTCUSD", "BUY", 5.0)]
    exposure = compute_exposure(open_positions, pending)
    assert exposure.total_open_risk == pytest.approx(10.0)
    assert exposure.total_pending_risk == pytest.approx(5.0)


def test_symbol_exposure_aggregates_multiple_positions_same_symbol():
    positions = [
        PositionExposure("XAUUSD", "BUY", 10.0),
        PositionExposure("XAUUSD", "BUY", 15.0),
    ]
    exposure = compute_exposure(positions)
    assert exposure.symbol_exposure["XAUUSD"] == pytest.approx(25.0)
    assert exposure.positions_per_symbol["XAUUSD"] == 2


def test_currency_direction_exposure_for_a_buy():
    # BUY XAUUSD -> long XAU, short USD
    exposure = compute_exposure([PositionExposure("XAUUSD", "BUY", 10.0)])
    assert exposure.currency_direction_exposure["XAU"] == pytest.approx(10.0)
    assert exposure.currency_direction_exposure["USD"] == pytest.approx(-10.0)


def test_currency_direction_exposure_for_a_sell():
    exposure = compute_exposure([PositionExposure("XAUUSD", "SELL", 10.0)])
    assert exposure.currency_direction_exposure["XAU"] == pytest.approx(-10.0)
    assert exposure.currency_direction_exposure["USD"] == pytest.approx(10.0)


def test_currency_direction_exposure_nets_across_symbols():
    # BUY XAUUSD (short USD 10) + BUY BTCUSD (short USD 5) -> net USD = -15
    exposure = compute_exposure([
        PositionExposure("XAUUSD", "BUY", 10.0),
        PositionExposure("BTCUSD", "BUY", 5.0),
    ])
    assert exposure.currency_direction_exposure["USD"] == pytest.approx(-15.0)


def test_usd_related_exposure_includes_xauusd_and_btcusd_not_gbpjpy():
    exposure = compute_exposure([
        PositionExposure("XAUUSD", "BUY", 10.0),
        PositionExposure("BTCUSD", "BUY", 5.0),
        PositionExposure("GBPJPY", "BUY", 100.0),
    ])
    assert exposure.usd_related_exposure == pytest.approx(15.0)


# --------------------------------------------------------------------------
# External review finding #10/#11: a RESTING/PLACED order is still
# potential broker exposure and must participate in every heat dimension,
# not just the total-risk sum.
# --------------------------------------------------------------------------

def test_pending_position_contributes_to_symbol_exposure():
    exposure = compute_exposure(
        open_positions=[PositionExposure("XAUUSD", "BUY", 10.0)],
        pending_positions=[PositionExposure("XAUUSD", "BUY", 15.0)],
    )
    assert exposure.symbol_exposure["XAUUSD"] == pytest.approx(25.0)
    assert exposure.positions_per_symbol["XAUUSD"] == 2


def test_pending_only_position_still_appears_in_symbol_exposure():
    exposure = compute_exposure(open_positions=[], pending_positions=[PositionExposure("BTCUSD", "BUY", 5.0)])
    assert exposure.symbol_exposure["BTCUSD"] == pytest.approx(5.0)


def test_pending_position_contributes_to_currency_direction_exposure():
    exposure = compute_exposure(
        open_positions=[], pending_positions=[PositionExposure("XAUUSD", "BUY", 10.0)],
    )
    assert exposure.currency_direction_exposure["XAU"] == pytest.approx(10.0)
    assert exposure.currency_direction_exposure["USD"] == pytest.approx(-10.0)


def test_pending_position_contributes_to_usd_related_exposure():
    exposure = compute_exposure(open_positions=[], pending_positions=[PositionExposure("BTCUSD", "BUY", 5.0)])
    assert exposure.usd_related_exposure == pytest.approx(5.0)


def test_cluster_exposure_includes_a_pending_only_symbol():
    exposure = compute_exposure(
        open_positions=[PositionExposure("XAUUSD", "BUY", 10.0)],
        pending_positions=[PositionExposure("BTCUSD", "BUY", 20.0)],
    )
    matrix = {("BTCUSD", "XAUUSD"): CorrelationResult(0.8, 50), ("XAUUSD", "BTCUSD"): CorrelationResult(0.8, 50)}
    clusters = correlated_cluster_exposure(exposure, matrix, high_correlation_threshold=0.7)
    assert clusters[frozenset({"XAUUSD", "BTCUSD"})] == pytest.approx(30.0)


def test_portfolio_risk_gate_blocks_on_per_symbol_ceiling_from_pending_alone():
    decision, reason = evaluate_portfolio_risk_gate(
        proposed_symbol="XAUUSD", proposed_direction="BUY", proposed_monetary_risk=20.0, equity=10000,
        open_positions=[], pending_positions=[PositionExposure("XAUUSD", "BUY", 40.0)],
        correlation_matrix={}, limits=_limits(max_total_open_risk_pct=5.0, max_symbol_risk_pct=0.5),
    )
    assert decision == BLOCK_PORTFOLIO_RISK
    assert "per-symbol risk" in reason


def test_portfolio_risk_gate_blocks_on_correlated_cluster_from_pending_alone():
    decision, reason = evaluate_portfolio_risk_gate(
        proposed_symbol="BTCUSD", proposed_direction="BUY", proposed_monetary_risk=20.0, equity=10000,
        open_positions=[], pending_positions=[PositionExposure("XAUUSD", "BUY", 40.0)],
        correlation_matrix={
            ("BTCUSD", "XAUUSD"): CorrelationResult(0.9, 100), ("XAUUSD", "BTCUSD"): CorrelationResult(0.9, 100),
        },
        limits=_limits(max_total_open_risk_pct=5.0, max_currency_direction_risk_pct=5.0, max_correlated_cluster_risk_pct=0.5),
    )
    assert decision == BLOCK_PORTFOLIO_RISK
    assert "correlated-cluster risk" in reason


def test_empty_portfolio_has_zero_everything():
    exposure = compute_exposure([])
    assert exposure.total_open_risk == 0.0
    assert exposure.symbol_exposure == {}
    assert exposure.currency_direction_exposure == {}


# --------------------------------------------------------------------------
# correlated_cluster_exposure
# --------------------------------------------------------------------------

def test_cluster_exposure_combines_highly_correlated_open_symbols():
    exposure = compute_exposure([
        PositionExposure("XAUUSD", "BUY", 10.0),
        PositionExposure("BTCUSD", "BUY", 20.0),
    ])
    matrix = {("BTCUSD", "XAUUSD"): CorrelationResult(0.8, 50), ("XAUUSD", "BTCUSD"): CorrelationResult(0.8, 50)}
    clusters = correlated_cluster_exposure(exposure, matrix, high_correlation_threshold=0.7)
    assert clusters[frozenset({"XAUUSD", "BTCUSD"})] == pytest.approx(30.0)


def test_cluster_exposure_excludes_na_correlation():
    exposure = compute_exposure([
        PositionExposure("XAUUSD", "BUY", 10.0),
        PositionExposure("BTCUSD", "BUY", 20.0),
    ])
    matrix = {("BTCUSD", "XAUUSD"): CorrelationResult(None, 3)}
    clusters = correlated_cluster_exposure(exposure, matrix)
    assert clusters == {}


def test_cluster_exposure_excludes_below_threshold_correlation():
    exposure = compute_exposure([
        PositionExposure("XAUUSD", "BUY", 10.0),
        PositionExposure("BTCUSD", "BUY", 20.0),
    ])
    matrix = {("BTCUSD", "XAUUSD"): CorrelationResult(0.3, 50)}
    clusters = correlated_cluster_exposure(exposure, matrix, high_correlation_threshold=0.7)
    assert clusters == {}


def test_cluster_exposure_empty_when_only_one_symbol_open():
    exposure = compute_exposure([PositionExposure("XAUUSD", "BUY", 10.0)])
    clusters = correlated_cluster_exposure(exposure, {})
    assert clusters == {}


# --------------------------------------------------------------------------
# evaluate_portfolio_risk_gate (execution-safety review round 2 finding #6)
# --------------------------------------------------------------------------

def _limits(**overrides) -> PortfolioRiskLimits:
    defaults = dict(
        max_total_open_risk_pct=1.0, max_symbol_risk_pct=1.0,
        max_currency_direction_risk_pct=1.0, max_correlated_cluster_risk_pct=1.0,
    )
    defaults.update(overrides)
    return PortfolioRiskLimits(**defaults)


def test_allows_within_every_limit():
    decision, _ = evaluate_portfolio_risk_gate(
        proposed_symbol="XAUUSD", proposed_direction="BUY", proposed_monetary_risk=10.0, equity=10000,
        open_positions=[], pending_positions=[], correlation_matrix={}, limits=_limits(),
    )
    assert decision == ALLOW


def test_blocks_on_invalid_equity():
    decision, _ = evaluate_portfolio_risk_gate(
        proposed_symbol="XAUUSD", proposed_direction="BUY", proposed_monetary_risk=10.0, equity=-1,
        open_positions=[], pending_positions=[], correlation_matrix={}, limits=_limits(),
    )
    assert decision == BLOCK_PORTFOLIO_RISK


def test_blocks_on_invalid_proposed_risk():
    decision, _ = evaluate_portfolio_risk_gate(
        proposed_symbol="XAUUSD", proposed_direction="BUY", proposed_monetary_risk=0.0, equity=10000,
        open_positions=[], pending_positions=[], correlation_matrix={}, limits=_limits(),
    )
    assert decision == BLOCK_PORTFOLIO_RISK


def test_blocks_on_total_ceiling():
    # limit = 1% of 10000 = 100; open=90, proposed=20 -> total=110 > 100
    decision, reason = evaluate_portfolio_risk_gate(
        proposed_symbol="XAUUSD", proposed_direction="BUY", proposed_monetary_risk=20.0, equity=10000,
        open_positions=[PositionExposure("GBPJPY", "BUY", 90.0)], pending_positions=[],
        correlation_matrix={}, limits=_limits(max_total_open_risk_pct=1.0),
    )
    assert decision == BLOCK_PORTFOLIO_RISK
    assert "total portfolio risk" in reason


def test_blocks_on_per_symbol_ceiling():
    # symbol ceiling tighter than total: 0.5% of 10000 = 50; existing
    # XAUUSD=40 + proposed XAUUSD=20 = 60 > 50, even though total ceiling
    # (say 5%) is nowhere near breached.
    decision, reason = evaluate_portfolio_risk_gate(
        proposed_symbol="XAUUSD", proposed_direction="BUY", proposed_monetary_risk=20.0, equity=10000,
        open_positions=[PositionExposure("XAUUSD", "BUY", 40.0)], pending_positions=[],
        correlation_matrix={}, limits=_limits(max_total_open_risk_pct=5.0, max_symbol_risk_pct=0.5),
    )
    assert decision == BLOCK_PORTFOLIO_RISK
    assert "per-symbol risk" in reason


def test_blocks_on_currency_direction_ceiling():
    # Both XAUUSD (long) and BTCUSD (long) are net-long USD (short USD
    # actually, since buying XAU/BTC means SELLING USD) -- net USD
    # exposure from two same-direction USD-quoted longs compounds.
    decision, reason = evaluate_portfolio_risk_gate(
        proposed_symbol="BTCUSD", proposed_direction="BUY", proposed_monetary_risk=20.0, equity=10000,
        open_positions=[PositionExposure("XAUUSD", "BUY", 40.0)], pending_positions=[],
        correlation_matrix={}, limits=_limits(max_total_open_risk_pct=5.0, max_currency_direction_risk_pct=0.5),
    )
    assert decision == BLOCK_PORTFOLIO_RISK
    assert "USD exposure" in reason


def test_blocks_on_correlated_cluster_ceiling():
    # correlation_matrix convention (matches portfolio.correlation
    # .compute_correlation_matrix()): both key orderings populated.
    decision, reason = evaluate_portfolio_risk_gate(
        proposed_symbol="BTCUSD", proposed_direction="BUY", proposed_monetary_risk=20.0, equity=10000,
        open_positions=[PositionExposure("XAUUSD", "BUY", 40.0)], pending_positions=[],
        correlation_matrix={
            ("BTCUSD", "XAUUSD"): CorrelationResult(0.9, 100), ("XAUUSD", "BTCUSD"): CorrelationResult(0.9, 100),
        },
        limits=_limits(max_total_open_risk_pct=5.0, max_currency_direction_risk_pct=5.0, max_correlated_cluster_risk_pct=0.5),
    )
    assert decision == BLOCK_PORTFOLIO_RISK
    assert "correlated-cluster risk" in reason


def test_low_correlation_cluster_never_blocks():
    decision, _ = evaluate_portfolio_risk_gate(
        proposed_symbol="BTCUSD", proposed_direction="BUY", proposed_monetary_risk=20.0, equity=10000,
        open_positions=[PositionExposure("XAUUSD", "BUY", 40.0)], pending_positions=[],
        correlation_matrix={
            ("BTCUSD", "XAUUSD"): CorrelationResult(0.3, 100), ("XAUUSD", "BTCUSD"): CorrelationResult(0.3, 100),
        },
        limits=_limits(max_total_open_risk_pct=5.0, max_currency_direction_risk_pct=5.0, max_correlated_cluster_risk_pct=0.5),
    )
    assert decision == ALLOW


def test_na_correlation_never_counted_toward_cluster():
    # N/A correlation is never ASSUMED correlated -- the cluster check
    # simply doesn't apply (the separate pairwise correlation gate
    # upstream in final_permission is what fails closed on N/A).
    decision, _ = evaluate_portfolio_risk_gate(
        proposed_symbol="BTCUSD", proposed_direction="BUY", proposed_monetary_risk=20.0, equity=10000,
        open_positions=[PositionExposure("XAUUSD", "BUY", 40.0)], pending_positions=[],
        correlation_matrix={("BTCUSD", "XAUUSD"): CorrelationResult(None, 2)},
        limits=_limits(max_total_open_risk_pct=5.0, max_currency_direction_risk_pct=5.0, max_correlated_cluster_risk_pct=0.5),
    )
    assert decision == ALLOW


def test_pending_risk_counts_toward_total():
    decision, reason = evaluate_portfolio_risk_gate(
        proposed_symbol="XAUUSD", proposed_direction="BUY", proposed_monetary_risk=20.0, equity=10000,
        open_positions=[], pending_positions=[PositionExposure("GBPJPY", "BUY", 90.0)],
        correlation_matrix={}, limits=_limits(max_total_open_risk_pct=1.0),
    )
    assert decision == BLOCK_PORTFOLIO_RISK


def test_portfolio_risk_limits_from_risk_limits_bounds_every_sub_ceiling_to_the_total():
    limits = portfolio_risk_limits_from_risk_limits(0.75)
    assert limits.max_total_open_risk_pct == 0.75
    assert limits.max_symbol_risk_pct == 0.75
    assert limits.max_currency_direction_risk_pct == 0.75
    assert limits.max_correlated_cluster_risk_pct == 0.75


def test_checked_in_fixed_order_total_before_symbol():
    # Both total and symbol ceilings would be breached; total is checked
    # first, so its reason must be the one reported.
    decision, reason = evaluate_portfolio_risk_gate(
        proposed_symbol="XAUUSD", proposed_direction="BUY", proposed_monetary_risk=100.0, equity=10000,
        open_positions=[], pending_positions=[],
        correlation_matrix={}, limits=_limits(max_total_open_risk_pct=0.5, max_symbol_risk_pct=0.5),
    )
    assert decision == BLOCK_PORTFOLIO_RISK
    assert "total portfolio risk" in reason
