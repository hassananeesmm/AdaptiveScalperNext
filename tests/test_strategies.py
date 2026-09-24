"""Per-strategy behavior tests: fires under the right conditions, stays
FLAT (returns None) otherwise, and every produced StrategySignal is
internally valid (direction/confidence/distances)."""

from __future__ import annotations

import pytest

from adaptive_scalper.features.bar_features import FeatureSnapshot
from adaptive_scalper.regimes.classifier import (
    BREAKOUT,
    COMPRESSION,
    ERRATIC,
    RANGE,
    TRENDING_UP,
    UNKNOWN,
    VOLATILITY_EXPANSION,
    RegimeClassification,
)
from adaptive_scalper.strategies.base import StrategySignal
from adaptive_scalper.strategies.microstructure_acceleration import MicrostructureAccelerationStrategy
from adaptive_scalper.strategies.momentum_continuation import MomentumContinuationStrategy
from adaptive_scalper.strategies.pullback_continuation import PullbackContinuationStrategy
from adaptive_scalper.strategies.range_breakout import RangeBreakoutStrategy
from adaptive_scalper.strategies.statistical_reversion import StatisticalReversionStrategy
from adaptive_scalper.strategies.volatility_expansion import VolatilityExpansionStrategy


def _snap(**overrides) -> FeatureSnapshot:
    defaults = dict(
        canonical_symbol="XAUUSD", resolution="M1", feature_schema_version=1,
        data_timestamp=1_700_000_000, feature_timestamp=1_700_000_000, bar_count_used=25, lookback=20,
        close=2000.0, return_1=1.0, log_return_1=0.0005, realized_volatility=0.001, atr=1.0,
        normalized_range=0.0005, momentum=5.0, velocity=0.5, acceleration=0.1,
        efficiency_ratio=0.6, directional_persistence=0.7, range_expansion_ratio=1.0,
        body_ratio=0.5, upper_wick_ratio=0.25, lower_wick_ratio=0.25,
        recent_high=2005.0, recent_low=1995.0, spread_current=10.0, spread_percentile=0.5,
        movement_to_cost=2.0, session="LONDON", hour_of_day_utc=9, weekday_utc=2,
    )
    defaults.update(overrides)
    return FeatureSnapshot(**defaults)


def _regime(regime: str, confidence: float = 0.8) -> RegimeClassification:
    return RegimeClassification(regime=regime, confidence=confidence, version=1, reason="test")


def _assert_valid_signal(signal: StrategySignal) -> None:
    assert signal is not None
    assert signal.direction in ("BUY", "SELL")
    assert 0.0 <= signal.raw_confidence <= 1.0
    assert signal.stop_distance > 0
    assert signal.target_distance > 0


# --------------------------------------------------------------------------
# StrategySignal validation
# --------------------------------------------------------------------------

def _signal_kwargs(**overrides):
    defaults = dict(
        strategy_key="momentum_continuation", strategy_version=1, canonical_symbol="XAUUSD",
        direction="BUY", raw_confidence=0.5, stop_distance=1.0, target_distance=2.0,
        expected_duration_seconds=300, entry_method="MARKET", regime="TRENDING_UP",
        rationale="test", feature_schema_version=1, data_timestamp=1000,
    )
    defaults.update(overrides)
    return defaults


def test_strategy_signal_rejects_invalid_direction():
    with pytest.raises(ValueError):
        StrategySignal(**_signal_kwargs(direction="HOLD"))


def test_strategy_signal_rejects_out_of_range_confidence():
    with pytest.raises(ValueError):
        StrategySignal(**_signal_kwargs(raw_confidence=1.5))


def test_strategy_signal_rejects_non_positive_stop_distance():
    with pytest.raises(ValueError):
        StrategySignal(**_signal_kwargs(stop_distance=0))


def test_strategy_signal_rejects_non_positive_target_distance():
    with pytest.raises(ValueError):
        StrategySignal(**_signal_kwargs(target_distance=-1))


def test_strategy_signal_has_no_money_or_volume_field():
    field_names = {f.name for f in __import__("dataclasses").fields(StrategySignal)}
    assert "volume" not in field_names
    assert "lot_size" not in field_names
    assert "monetary_risk" not in field_names
    assert "money_risk" not in field_names


# --------------------------------------------------------------------------
# momentum_continuation
# --------------------------------------------------------------------------

def test_momentum_continuation_fires_on_trend():
    strat = MomentumContinuationStrategy()
    signal = strat.evaluate(_snap(), _regime(TRENDING_UP, confidence=0.9))
    _assert_valid_signal(signal)
    assert signal.direction == "BUY"
    assert signal.strategy_key == "momentum_continuation"


def test_momentum_continuation_flat_outside_trend():
    strat = MomentumContinuationStrategy()
    assert strat.evaluate(_snap(), _regime(RANGE)) is None


def test_momentum_continuation_flat_without_atr():
    strat = MomentumContinuationStrategy()
    assert strat.evaluate(_snap(atr=None), _regime(TRENDING_UP)) is None


def test_momentum_continuation_flat_when_confidence_too_low():
    strat = MomentumContinuationStrategy(min_confidence=0.9)
    assert strat.evaluate(_snap(efficiency_ratio=0.3), _regime(TRENDING_UP, confidence=0.5)) is None


# --------------------------------------------------------------------------
# pullback_continuation
# --------------------------------------------------------------------------

def test_pullback_continuation_fires_on_pullback_within_uptrend():
    strat = PullbackContinuationStrategy()
    # trend up, but latest bar dipped (return_1 < 0), longer momentum still positive
    signal = strat.evaluate(_snap(return_1=-0.5, momentum=5.0), _regime(TRENDING_UP, confidence=0.9))
    _assert_valid_signal(signal)
    assert signal.direction == "BUY"


def test_pullback_continuation_flat_when_no_pullback():
    strat = PullbackContinuationStrategy()
    # trend up and latest bar ALSO up -> not a pullback, momentum_continuation's territory
    assert strat.evaluate(_snap(return_1=0.5, momentum=5.0), _regime(TRENDING_UP)) is None


def test_pullback_continuation_flat_outside_trend():
    strat = PullbackContinuationStrategy()
    assert strat.evaluate(_snap(return_1=-0.5, momentum=5.0), _regime(RANGE)) is None


# --------------------------------------------------------------------------
# range_breakout
# --------------------------------------------------------------------------

def test_range_breakout_fires_on_breakout_regime():
    strat = RangeBreakoutStrategy()
    signal = strat.evaluate(_snap(return_1=2.0), _regime(BREAKOUT, confidence=0.85))
    _assert_valid_signal(signal)
    assert signal.direction == "BUY"


def test_range_breakout_direction_follows_return_sign():
    strat = RangeBreakoutStrategy()
    signal = strat.evaluate(_snap(return_1=-2.0), _regime(BREAKOUT, confidence=0.85))
    assert signal.direction == "SELL"


def test_range_breakout_flat_outside_breakout_regime():
    strat = RangeBreakoutStrategy()
    assert strat.evaluate(_snap(), _regime(VOLATILITY_EXPANSION)) is None


# --------------------------------------------------------------------------
# statistical_reversion
# --------------------------------------------------------------------------

def test_statistical_reversion_fires_near_range_high():
    strat = StatisticalReversionStrategy()
    snap = _snap(close=2004.5, recent_high=2005.0, recent_low=1995.0)  # within 5% of high, span=10
    signal = strat.evaluate(snap, _regime(RANGE, confidence=0.7))
    _assert_valid_signal(signal)
    assert signal.direction == "SELL"


def test_statistical_reversion_fires_near_range_low():
    strat = StatisticalReversionStrategy()
    snap = _snap(close=1995.5, recent_high=2005.0, recent_low=1995.0)
    signal = strat.evaluate(snap, _regime(RANGE, confidence=0.7))
    assert signal.direction == "BUY"


def test_statistical_reversion_flat_in_middle_of_range():
    strat = StatisticalReversionStrategy()
    snap = _snap(close=2000.0, recent_high=2005.0, recent_low=1995.0)  # dead center
    assert strat.evaluate(snap, _regime(RANGE)) is None


def test_statistical_reversion_flat_outside_range_and_compression():
    strat = StatisticalReversionStrategy()
    snap = _snap(close=2004.5, recent_high=2005.0, recent_low=1995.0)
    assert strat.evaluate(snap, _regime(TRENDING_UP)) is None


# --------------------------------------------------------------------------
# volatility_expansion
# --------------------------------------------------------------------------

def test_volatility_expansion_fires_on_lower_wick_rejection():
    strat = VolatilityExpansionStrategy()
    signal = strat.evaluate(
        _snap(upper_wick_ratio=0.1, lower_wick_ratio=0.5), _regime(VOLATILITY_EXPANSION, confidence=0.8)
    )
    _assert_valid_signal(signal)
    assert signal.direction == "BUY"


def test_volatility_expansion_fires_on_upper_wick_rejection():
    strat = VolatilityExpansionStrategy()
    signal = strat.evaluate(
        _snap(upper_wick_ratio=0.5, lower_wick_ratio=0.1), _regime(VOLATILITY_EXPANSION, confidence=0.8)
    )
    assert signal.direction == "SELL"


def test_volatility_expansion_flat_on_symmetric_wicks():
    strat = VolatilityExpansionStrategy()
    signal = strat.evaluate(
        _snap(upper_wick_ratio=0.3, lower_wick_ratio=0.3), _regime(VOLATILITY_EXPANSION)
    )
    assert signal is None


def test_volatility_expansion_flat_outside_its_regime():
    strat = VolatilityExpansionStrategy()
    assert strat.evaluate(_snap(upper_wick_ratio=0.1, lower_wick_ratio=0.5), _regime(RANGE)) is None


# --------------------------------------------------------------------------
# microstructure_acceleration
# --------------------------------------------------------------------------

def test_microstructure_acceleration_fires_on_aligned_velocity_and_acceleration():
    strat = MicrostructureAccelerationStrategy()
    signal = strat.evaluate(_snap(velocity=0.5, acceleration=0.3, atr=1.0), _regime(TRENDING_UP))
    _assert_valid_signal(signal)
    assert signal.direction == "BUY"


def test_microstructure_acceleration_flat_when_signs_disagree():
    strat = MicrostructureAccelerationStrategy()
    assert strat.evaluate(_snap(velocity=0.5, acceleration=-0.3, atr=1.0), _regime(TRENDING_UP)) is None


def test_microstructure_acceleration_flat_when_acceleration_too_small():
    strat = MicrostructureAccelerationStrategy(min_acceleration_to_atr_ratio=0.5)
    assert strat.evaluate(_snap(velocity=0.5, acceleration=0.01, atr=1.0), _regime(TRENDING_UP)) is None


@pytest.mark.parametrize("excluded_regime", [COMPRESSION, ERRATIC, UNKNOWN])
def test_microstructure_acceleration_flat_in_excluded_regimes(excluded_regime):
    strat = MicrostructureAccelerationStrategy()
    assert strat.evaluate(_snap(velocity=0.5, acceleration=0.3, atr=1.0), _regime(excluded_regime)) is None
