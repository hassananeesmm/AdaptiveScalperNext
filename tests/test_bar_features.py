"""Tests for the causal bar feature engine (directive section 11).

Covers: input validation, no-lookahead contract, and hand-checkable
correctness for the core formulas (efficiency ratio, directional
persistence, body/wick ratios, range expansion, spread percentile,
movement-to-cost, session bucketing).
"""

from __future__ import annotations

import pytest

from adaptive_scalper.features.bar_features import (
    FeatureError,
    compute_bar_features,
    compute_multi_resolution_features,
)
from adaptive_scalper.gateway.types import Bar

BASE_TIME = 1_700_000_000  # arbitrary epoch second, a Tuesday
STEP = 60  # M1


def _bar(i: int, open_: float, high: float, low: float, close: float, spread: int = 10) -> Bar:
    return Bar(
        time=BASE_TIME + i * STEP, open=open_, high=high, low=low, close=close,
        tick_volume=10, spread=spread, real_volume=0,
    )


def _flat_series(n: int, start_price: float = 100.0, step: float = 1.0) -> list[Bar]:
    """n bars, strictly increasing close by `step` each bar, minimal wicks."""
    bars = []
    price = start_price
    for i in range(n):
        o = price
        c = price + step
        bars.append(_bar(i, o, max(o, c) + 0.1, min(o, c) - 0.1, c))
        price = c
    return bars


# --------------------------------------------------------------------------
# Input validation
# --------------------------------------------------------------------------

def test_empty_bars_raises():
    with pytest.raises(FeatureError):
        compute_bar_features("XAUUSD", "M1", [])


def test_out_of_order_bars_raises():
    bars = [_bar(1, 100, 101, 99, 100.5), _bar(0, 100, 101, 99, 100.5)]
    with pytest.raises(FeatureError):
        compute_bar_features("XAUUSD", "M1", bars)


def test_duplicate_timestamp_raises():
    b = _bar(0, 100, 101, 99, 100.5)
    with pytest.raises(FeatureError):
        compute_bar_features("XAUUSD", "M1", [b, b])


# --------------------------------------------------------------------------
# Minimal / insufficient data -> honest None, not fabricated values
# --------------------------------------------------------------------------

def test_single_bar_has_none_for_multi_bar_fields():
    bars = [_bar(0, 100, 102, 99, 101)]
    snap = compute_bar_features("XAUUSD", "M1", bars)
    assert snap.return_1 is None
    assert snap.log_return_1 is None
    assert snap.momentum is None
    assert snap.realized_volatility is None
    assert snap.recent_high == 102
    assert snap.recent_low == 99
    assert snap.body_ratio is not None  # computable from a single bar


def test_momentum_requires_more_than_lookback_bars():
    bars = _flat_series(4)  # lookback=3 needs len(closes) > 3, i.e. >= 4
    snap = compute_bar_features("XAUUSD", "M1", bars, lookback=3)
    assert snap.momentum == pytest.approx(bars[-1].close - bars[0].close)

    bars_short = _flat_series(3)
    snap_short = compute_bar_features("XAUUSD", "M1", bars_short, lookback=3)
    assert snap_short.momentum is None


# --------------------------------------------------------------------------
# No-lookahead contract
# --------------------------------------------------------------------------

def test_prefix_computation_unaffected_by_mutating_bars_beyond_it():
    bars = _flat_series(30)
    prefix = bars[:15]
    snap_before = compute_bar_features("XAUUSD", "M1", prefix, lookback=10)

    # Mutate the (separate) full series far beyond the prefix — a real
    # lookahead bug would show up if the function ever read module-level
    # state or a shared mutable structure instead of only its own `bars`
    # argument.
    bars[25] = _bar(25, 9999, 10000, 9998, 9999.5)

    snap_after = compute_bar_features("XAUUSD", "M1", prefix, lookback=10)
    assert snap_before == snap_after


# --------------------------------------------------------------------------
# Efficiency ratio (Kaufman ER = net change / path length)
# --------------------------------------------------------------------------

def test_efficiency_ratio_is_one_for_a_perfect_trend():
    bars = _flat_series(10, step=1.0)  # strictly monotonic +1 each bar
    snap = compute_bar_features("XAUUSD", "M1", bars, lookback=9)
    assert snap.efficiency_ratio == pytest.approx(1.0)


def test_efficiency_ratio_is_low_for_a_choppy_market():
    bars = []
    price = 100.0
    for i in range(11):
        step = 1.0 if i % 2 == 0 else -1.0
        c = price + step
        bars.append(_bar(i, price, max(price, c) + 0.1, min(price, c) - 0.1, c))
        price = c
    snap = compute_bar_features("XAUUSD", "M1", bars, lookback=10)
    assert snap.efficiency_ratio is not None
    assert snap.efficiency_ratio < 0.3


# --------------------------------------------------------------------------
# Directional persistence
# --------------------------------------------------------------------------

def test_directional_persistence_is_one_for_a_perfect_trend():
    bars = _flat_series(10)
    snap = compute_bar_features("XAUUSD", "M1", bars, lookback=9)
    assert snap.directional_persistence == pytest.approx(1.0)


# --------------------------------------------------------------------------
# Body/wick ratios always partition the full range exactly
# --------------------------------------------------------------------------

def test_body_and_wick_ratios_sum_to_one():
    bars = [_bar(0, 100, 105, 98, 102)]  # open=100 close=102 high=105 low=98
    snap = compute_bar_features("XAUUSD", "M1", bars)
    total = snap.body_ratio + snap.upper_wick_ratio + snap.lower_wick_ratio
    assert total == pytest.approx(1.0)
    assert snap.body_ratio == pytest.approx(2 / 7)   # |102-100| / (105-98)
    assert snap.upper_wick_ratio == pytest.approx(3 / 7)  # 105-102
    assert snap.lower_wick_ratio == pytest.approx(2 / 7)  # 100-98


def test_zero_range_bar_leaves_ratios_none():
    bars = [_bar(0, 100, 100, 100, 100)]
    snap = compute_bar_features("XAUUSD", "M1", bars)
    assert snap.body_ratio is None
    assert snap.upper_wick_ratio is None
    assert snap.lower_wick_ratio is None


# --------------------------------------------------------------------------
# Range expansion
# --------------------------------------------------------------------------

def test_range_expansion_ratio_above_one_for_a_wide_current_bar():
    bars = [_bar(i, 100, 100.5, 99.5, 100) for i in range(9)]  # range=1.0 each
    bars.append(_bar(9, 100, 110, 90, 100))  # range=20.0, much wider
    snap = compute_bar_features("XAUUSD", "M1", bars, lookback=9)
    assert snap.range_expansion_ratio == pytest.approx(20.0)


# --------------------------------------------------------------------------
# Spread percentile
# --------------------------------------------------------------------------

def test_spread_percentile_rank():
    bars = [_bar(i, 100, 101, 99, 100, spread=s) for i, s in enumerate([5, 10, 15, 20, 25])]
    snap = compute_bar_features("XAUUSD", "M1", bars, lookback=4)
    # current spread = 25, the max -> percentile rank = 1.0 (all <= 25)
    assert snap.spread_percentile == pytest.approx(1.0)


# --------------------------------------------------------------------------
# Movement-to-cost
# --------------------------------------------------------------------------

def test_movement_to_cost_is_none_without_point_size():
    bars = _flat_series(5)
    snap = compute_bar_features("XAUUSD", "M1", bars)
    assert snap.movement_to_cost is None


def test_movement_to_cost_computed_with_point_size():
    bars = _flat_series(5)
    snap = compute_bar_features("XAUUSD", "M1", bars, point_size=0.01)
    assert snap.movement_to_cost is not None
    expected_spread_price = bars[-1].spread * 0.01
    assert snap.movement_to_cost == pytest.approx(snap.atr / expected_spread_price)


# --------------------------------------------------------------------------
# Session bucketing (descriptive tag, not a performance claim)
# --------------------------------------------------------------------------

@pytest.mark.parametrize("hour,expected", [(2, "ASIAN"), (9, "LONDON"), (14, "LONDON_NEWYORK_OVERLAP"), (18, "NEWYORK"), (23, "ASIAN")])
def test_session_bucketing(hour, expected):
    import datetime
    dt = datetime.datetime(2026, 9, 15, hour, 0, tzinfo=datetime.timezone.utc)
    ts = int(dt.timestamp())
    bars = [Bar(time=ts, open=1, high=1.1, low=0.9, close=1, tick_volume=1, spread=1, real_volume=0)]
    snap = compute_bar_features("XAUUSD", "M1", bars)
    assert snap.session == expected
    assert snap.hour_of_day_utc == hour


# --------------------------------------------------------------------------
# Schema version / timestamps
# --------------------------------------------------------------------------

def test_schema_version_and_timestamps_recorded():
    bars = _flat_series(3)
    snap = compute_bar_features("XAUUSD", "M1", bars, now=9_999_999)
    assert snap.feature_schema_version == 1
    assert snap.data_timestamp == bars[-1].time
    assert snap.feature_timestamp == 9_999_999
    assert snap.resolution == "M1"
    assert snap.canonical_symbol == "XAUUSD"
    assert snap.bar_count_used == 3


def test_feature_timestamp_defaults_to_data_timestamp_when_now_omitted():
    bars = _flat_series(3)
    snap = compute_bar_features("XAUUSD", "M1", bars)
    assert snap.feature_timestamp == snap.data_timestamp


# --------------------------------------------------------------------------
# Multi-resolution composition
# --------------------------------------------------------------------------

def test_multi_resolution_features_one_snapshot_per_resolution():
    m1_bars = _flat_series(5)
    m5_bars = _flat_series(3)
    result = compute_multi_resolution_features("XAUUSD", {"M1": m1_bars, "M5": m5_bars, "M15": []})
    assert set(result.keys()) == {"M1", "M5"}  # M15 omitted: empty bar list
    assert result["M1"].resolution == "M1"
    assert result["M5"].resolution == "M5"
