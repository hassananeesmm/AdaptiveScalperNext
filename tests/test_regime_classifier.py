"""Tests for deterministic regime classification + hysteresis
(directive section 13)."""

from __future__ import annotations

import pytest

from adaptive_scalper.features.bar_features import FeatureSnapshot
from adaptive_scalper.regimes.classifier import (
    BREAKOUT,
    COMPRESSION,
    ERRATIC,
    RANGE,
    REGIME_STATES,
    TRENDING_DOWN,
    TRENDING_UP,
    UNKNOWN,
    VOLATILITY_EXPANSION,
    RegimeTracker,
    classify_regime,
)


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


# --------------------------------------------------------------------------
# classify_regime
# --------------------------------------------------------------------------

def test_missing_efficiency_ratio_is_unknown():
    result = classify_regime(_snap(efficiency_ratio=None))
    assert result.regime == UNKNOWN
    assert result.confidence == 0.0


def test_missing_expansion_ratio_is_unknown():
    result = classify_regime(_snap(range_expansion_ratio=None))
    assert result.regime == UNKNOWN


def test_wide_bar_with_decisive_direction_is_breakout():
    result = classify_regime(_snap(
        range_expansion_ratio=2.5, efficiency_ratio=0.8, directional_persistence=0.9, return_1=3.0,
    ))
    assert result.regime == BREAKOUT
    assert 0.0 < result.confidence <= 1.0


def test_wide_bar_choppy_is_erratic():
    result = classify_regime(_snap(
        range_expansion_ratio=2.5, efficiency_ratio=0.1, directional_persistence=0.3,
    ))
    assert result.regime == ERRATIC


def test_wide_bar_no_clear_direction_is_volatility_expansion():
    result = classify_regime(_snap(
        range_expansion_ratio=2.5, efficiency_ratio=0.4, directional_persistence=0.3,
    ))
    assert result.regime == VOLATILITY_EXPANSION


def test_narrow_bar_is_compression():
    result = classify_regime(_snap(range_expansion_ratio=0.3, efficiency_ratio=0.4))
    assert result.regime == COMPRESSION


def test_high_efficiency_persistent_up_is_trending_up():
    result = classify_regime(_snap(
        range_expansion_ratio=1.0, efficiency_ratio=0.7, directional_persistence=0.8, return_1=2.0,
    ))
    assert result.regime == TRENDING_UP


def test_high_efficiency_persistent_down_is_trending_down():
    result = classify_regime(_snap(
        range_expansion_ratio=1.0, efficiency_ratio=0.7, directional_persistence=0.8, return_1=-2.0,
    ))
    assert result.regime == TRENDING_DOWN


def test_low_efficiency_is_range():
    result = classify_regime(_snap(range_expansion_ratio=1.0, efficiency_ratio=0.1, directional_persistence=0.2))
    assert result.regime == RANGE


def test_ambiguous_middle_ground_defaults_to_range():
    # efficiency between range and trend thresholds, weak persistence
    result = classify_regime(_snap(range_expansion_ratio=1.0, efficiency_ratio=0.4, directional_persistence=0.3))
    assert result.regime == RANGE


def test_trending_requires_nonzero_return_even_with_high_efficiency():
    result = classify_regime(_snap(
        range_expansion_ratio=1.0, efficiency_ratio=0.7, directional_persistence=0.8, return_1=None,
    ))
    assert result.regime != TRENDING_UP
    assert result.regime != TRENDING_DOWN


@pytest.mark.parametrize("overrides", [
    {"range_expansion_ratio": 2.5, "efficiency_ratio": 0.8, "directional_persistence": 0.9, "return_1": 1.0},
    {"range_expansion_ratio": 0.3, "efficiency_ratio": 0.4},
    {"range_expansion_ratio": 1.0, "efficiency_ratio": 0.1, "directional_persistence": 0.2},
    {"range_expansion_ratio": 1.0, "efficiency_ratio": 0.7, "directional_persistence": 0.8, "return_1": 2.0},
])
def test_confidence_always_in_bounds(overrides):
    result = classify_regime(_snap(**overrides))
    assert 0.0 <= result.confidence <= 1.0


def test_every_returned_regime_is_a_known_state():
    for overrides in [
        {"efficiency_ratio": None},
        {"range_expansion_ratio": 2.5, "efficiency_ratio": 0.8, "directional_persistence": 0.9, "return_1": 1.0},
        {"range_expansion_ratio": 0.3},
        {"range_expansion_ratio": 1.0, "efficiency_ratio": 0.1, "directional_persistence": 0.2},
    ]:
        result = classify_regime(_snap(**overrides))
        assert result.regime in REGIME_STATES


# --------------------------------------------------------------------------
# RegimeTracker hysteresis
# --------------------------------------------------------------------------

def test_tracker_starts_at_initial_regime():
    tracker = RegimeTracker(min_confirmations=2)
    assert tracker.confirmed_regime == UNKNOWN


def test_tracker_does_not_flip_on_a_single_noisy_observation():
    tracker = RegimeTracker(min_confirmations=2, initial_regime=RANGE)
    result = tracker.update(classify_regime(_snap(range_expansion_ratio=2.5, efficiency_ratio=0.1, directional_persistence=0.3)))
    assert result == RANGE  # still RANGE — only one ERRATIC observation so far


def test_tracker_flips_after_min_confirmations():
    tracker = RegimeTracker(min_confirmations=2, initial_regime=RANGE)
    erratic = classify_regime(_snap(range_expansion_ratio=2.5, efficiency_ratio=0.1, directional_persistence=0.3))
    tracker.update(erratic)
    result = tracker.update(erratic)
    assert result == ERRATIC


def test_tracker_resets_candidate_count_when_candidate_changes():
    tracker = RegimeTracker(min_confirmations=3, initial_regime=RANGE)
    erratic = classify_regime(_snap(range_expansion_ratio=2.5, efficiency_ratio=0.1, directional_persistence=0.3))
    compression = classify_regime(_snap(range_expansion_ratio=0.3, efficiency_ratio=0.4))
    tracker.update(erratic)
    tracker.update(erratic)
    # A different candidate interrupts the streak — must reset to 1, not carry over.
    result = tracker.update(compression)
    assert result == RANGE  # still confirmed RANGE, compression streak only at 1
    result2 = tracker.update(compression)
    assert result2 == RANGE  # streak at 2, still short of min_confirmations=3
    result3 = tracker.update(compression)
    assert result3 == COMPRESSION  # streak at 3 -> flips


def test_tracker_resets_when_a_raw_observation_matches_confirmed_again():
    tracker = RegimeTracker(min_confirmations=3, initial_regime=RANGE)
    erratic = classify_regime(_snap(range_expansion_ratio=2.5, efficiency_ratio=0.1, directional_persistence=0.3))
    range_obs = classify_regime(_snap(range_expansion_ratio=1.0, efficiency_ratio=0.1, directional_persistence=0.2))
    tracker.update(erratic)
    tracker.update(erratic)
    tracker.update(range_obs)  # back to RANGE — resets the ERRATIC candidate streak
    tracker.update(erratic)
    result = tracker.update(erratic)
    # Only 2 consecutive ERRATIC since the reset — must not have flipped yet.
    assert result == RANGE


def test_tracker_rejects_non_positive_min_confirmations():
    with pytest.raises(ValueError):
        RegimeTracker(min_confirmations=0)
