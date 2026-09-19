"""Tests for portfolio exposure tracking (directive section 35)."""

from __future__ import annotations

import pytest

from adaptive_scalper.portfolio.correlation import CorrelationResult
from adaptive_scalper.portfolio.exposure import (
    PositionExposure,
    compute_exposure,
    correlated_cluster_exposure,
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
