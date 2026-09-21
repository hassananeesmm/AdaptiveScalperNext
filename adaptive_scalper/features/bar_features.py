"""Causal, no-lookahead bar-based feature engine (directive section 11).

CAUSALITY CONTRACT: every function here treats the LAST element of its
input `bars` list as "now" and only ever looks backward from there — the
list itself IS the causal boundary. Callers must never pass bars from
after the actual decision point; nothing in a type signature can enforce
that from the outside, so it is a documented contract, locked in by
`tests/test_bar_features.py`'s no-lookahead regression tests (computing
from a prefix must be unaffected by whatever bars come after it).

Coverage note (directive section 11 lists a long feature catalogue —
this is a real, tested v1 subset, not the complete list): implemented —
returns/log-returns, realized volatility, ATR/normalized range, momentum,
velocity/acceleration, efficiency ratio, directional persistence,
range expansion/compression, candle body/wick ratios, recent high/low,
spread + spread percentile, movement-to-cost, session/time-of-day.
Deliberately NOT yet implemented here: swing/support-resistance
structure, tick-frequency-derived features (need raw ticks, not bars),
and cross-symbol correlation (belongs to the portfolio/correlation
module once it exists, not a single-symbol feature engine). Tracked as
real, named gaps, not silently absent.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from datetime import datetime, timezone

from adaptive_scalper.gateway.types import Bar

FEATURE_SCHEMA_VERSION = 1
DEFAULT_LOOKBACK = 20


class FeatureError(ValueError):
    """Raised when input bars violate the ordering/causality contract."""


@dataclass(frozen=True)
class FeatureSnapshot:
    canonical_symbol: str
    resolution: str
    feature_schema_version: int
    data_timestamp: int          # epoch seconds of the last bar used ("now")
    feature_timestamp: int       # epoch seconds this snapshot was computed
    bar_count_used: int
    lookback: int

    close: float
    return_1: float | None
    log_return_1: float | None
    realized_volatility: float | None   # stdev of log returns over lookback
    atr: float | None                   # simple mean of true range over lookback
    normalized_range: float | None      # atr / close
    momentum: float | None              # close - close[lookback bars ago]
    velocity: float | None              # short-window mean per-bar price change
    acceleration: float | None          # change in velocity between two windows
    efficiency_ratio: float | None      # Kaufman ER: net change / path length
    directional_persistence: float | None  # fraction of recent moves matching latest direction
    range_expansion_ratio: float | None    # current bar range / prior average range
    body_ratio: float | None            # |close-open| / (high-low)
    upper_wick_ratio: float | None
    lower_wick_ratio: float | None
    recent_high: float | None
    recent_low: float | None
    spread_current: float | None        # MT5 points, from the current bar
    spread_percentile: float | None     # 0..1 rank of spread_current within lookback
    movement_to_cost: float | None      # atr / (spread_current * point_size), needs point_size
    session: str
    hour_of_day_utc: int
    weekday_utc: int


def _validate_bars(bars: list[Bar]) -> None:
    if not bars:
        raise FeatureError("bars must be non-empty")
    for prev, cur in zip(bars, bars[1:]):
        if cur.time <= prev.time:
            raise FeatureError(
                f"bars must be strictly ascending by time; got {prev.time} then {cur.time} "
                "(duplicate or out-of-order bar — possible lookahead bug)"
            )


def _log_returns(closes: list[float]) -> list[float]:
    out = []
    for prev, cur in zip(closes, closes[1:]):
        if prev > 0 and cur > 0:
            out.append(math.log(cur / prev))
    return out


def _true_range(bar: Bar, prev_close: float | None) -> float:
    if prev_close is None:
        return bar.high - bar.low
    return max(bar.high - bar.low, abs(bar.high - prev_close), abs(bar.low - prev_close))


def _percentile_rank(value: float, population: list[float]) -> float | None:
    if not population:
        return None
    return sum(1 for p in population if p <= value) / len(population)


def _session(hour_utc: int) -> str:
    # Approximate UTC-hour buckets — a descriptive tag, not a performance
    # claim (directive section 73 explicitly warns against hardcoding
    # "London session is always better"; this only labels which session a
    # bar falls in, it says nothing about which is better).
    if 0 <= hour_utc < 7:
        return "ASIAN"
    if 7 <= hour_utc < 12:
        return "LONDON"
    if 12 <= hour_utc < 16:
        return "LONDON_NEWYORK_OVERLAP"
    if 16 <= hour_utc < 21:
        return "NEWYORK"
    return "ASIAN"


def compute_bar_features(
    canonical_symbol: str,
    resolution: str,
    bars: list[Bar],
    *,
    lookback: int = DEFAULT_LOOKBACK,
    point_size: float | None = None,
    now: int | None = None,
) -> FeatureSnapshot:
    """Compute a FeatureSnapshot as of `bars[-1]` ("now").

    `bars` must be sorted strictly ascending by `.time` and non-empty;
    violations raise `FeatureError` rather than silently reordering or
    dropping data. Individual fields that need more history than `bars`
    provides are `None` (directive's "insufficient evidence is honest
    N/A" pattern used throughout this codebase), not fabricated.

    `point_size` (the symbol's price-per-point, from `SymbolSpec.point`)
    is optional; without it, `movement_to_cost` stays `None` rather than
    mixing MT5 "points" and price units incorrectly.

    `now`: epoch seconds this snapshot was computed at. Defaults to the
    last bar's own time when omitted (deterministic for tests/backtests);
    real-time callers should pass `time.time()` explicitly so
    `feature_timestamp` reflects actual compute time, not data time.
    """
    _validate_bars(bars)
    current = bars[-1]
    closes = [b.close for b in bars]
    window = bars[-(lookback + 1):] if len(bars) >= 2 else bars

    return_1 = closes[-1] - closes[-2] if len(closes) >= 2 else None
    log_return_1 = None
    if len(closes) >= 2 and closes[-2] > 0 and closes[-1] > 0:
        log_return_1 = math.log(closes[-1] / closes[-2])

    log_rets = _log_returns(window_closes := [b.close for b in window])
    realized_volatility = statistics.pstdev(log_rets) if len(log_rets) >= 2 else None

    true_ranges = []
    for i in range(1, len(window)):
        true_ranges.append(_true_range(window[i], window[i - 1].close))
    atr = statistics.fmean(true_ranges) if true_ranges else None
    normalized_range = (atr / current.close) if (atr is not None and current.close > 0) else None

    momentum = None
    if len(closes) > lookback:
        momentum = closes[-1] - closes[-1 - lookback]

    vel_window = min(3, len(closes) - 1)
    velocity = None
    if vel_window >= 1:
        diffs = [closes[-i] - closes[-i - 1] for i in range(1, vel_window + 1)]
        velocity = statistics.fmean(diffs)

    acceleration = None
    if len(closes) - 1 > vel_window >= 1:
        prev_diffs = [closes[-i - 1] - closes[-i - 2] for i in range(1, vel_window + 1)]
        prev_velocity = statistics.fmean(prev_diffs)
        acceleration = velocity - prev_velocity if velocity is not None else None

    efficiency_ratio = None
    if len(window_closes) > 1:
        net = abs(window_closes[-1] - window_closes[0])
        path = sum(abs(b - a) for a, b in zip(window_closes, window_closes[1:]))
        efficiency_ratio = (net / path) if path > 0 else None

    directional_persistence = None
    diffs_all = [b - a for a, b in zip(window_closes, window_closes[1:])]
    if diffs_all and diffs_all[-1] != 0:
        latest_sign = diffs_all[-1] > 0
        matching = sum(1 for d in diffs_all if d != 0 and (d > 0) == latest_sign)
        nonzero = sum(1 for d in diffs_all if d != 0)
        directional_persistence = matching / nonzero if nonzero else None

    range_expansion_ratio = None
    if len(window) >= 2:
        current_range = current.high - current.low
        prior_ranges = [b.high - b.low for b in window[:-1]]
        avg_prior_range = statistics.fmean(prior_ranges) if prior_ranges else None
        if avg_prior_range:
            range_expansion_ratio = current_range / avg_prior_range

    full_range = current.high - current.low
    body_ratio = upper_wick_ratio = lower_wick_ratio = None
    if full_range > 0:
        body_ratio = abs(current.close - current.open) / full_range
        upper_wick_ratio = (current.high - max(current.open, current.close)) / full_range
        lower_wick_ratio = (min(current.open, current.close) - current.low) / full_range

    recent_high = max(b.high for b in window)
    recent_low = min(b.low for b in window)

    spread_current = float(current.spread)
    spreads = [float(b.spread) for b in window]
    spread_percentile = _percentile_rank(spread_current, spreads)

    movement_to_cost = None
    if atr is not None and point_size and point_size > 0 and spread_current > 0:
        spread_price = spread_current * point_size
        if spread_price > 0:
            movement_to_cost = atr / spread_price

    dt = datetime.fromtimestamp(current.time, tz=timezone.utc)
    effective_now = now if now is not None else current.time

    return FeatureSnapshot(
        canonical_symbol=canonical_symbol,
        resolution=resolution,
        feature_schema_version=FEATURE_SCHEMA_VERSION,
        data_timestamp=current.time,
        feature_timestamp=effective_now,
        bar_count_used=len(bars),
        lookback=lookback,
        close=current.close,
        return_1=return_1,
        log_return_1=log_return_1,
        realized_volatility=realized_volatility,
        atr=atr,
        normalized_range=normalized_range,
        momentum=momentum,
        velocity=velocity,
        acceleration=acceleration,
        efficiency_ratio=efficiency_ratio,
        directional_persistence=directional_persistence,
        range_expansion_ratio=range_expansion_ratio,
        body_ratio=body_ratio,
        upper_wick_ratio=upper_wick_ratio,
        lower_wick_ratio=lower_wick_ratio,
        recent_high=recent_high,
        recent_low=recent_low,
        spread_current=spread_current,
        spread_percentile=spread_percentile,
        movement_to_cost=movement_to_cost,
        session=_session(dt.hour),
        hour_of_day_utc=dt.hour,
        weekday_utc=dt.weekday(),
    )


def compute_multi_resolution_features(
    canonical_symbol: str,
    bars_by_resolution: dict[str, list[Bar]],
    *,
    lookback: int = DEFAULT_LOOKBACK,
    point_size: float | None = None,
    now: int | None = None,
) -> dict[str, FeatureSnapshot]:
    """One FeatureSnapshot per resolution present in `bars_by_resolution`
    (directive section 12: no resolution is privileged as "the" answer;
    persist and expose the full resolution set used for a decision, not
    just one). A resolution with an empty bar list is simply omitted —
    the caller decides whether that's acceptable for the decision at hand."""
    return {
        resolution: compute_bar_features(
            canonical_symbol, resolution, bars, lookback=lookback, point_size=point_size, now=now
        )
        for resolution, bars in bars_by_resolution.items()
        if bars
    }


# The stationary, cross-time-comparable numeric fields a CPU-friendly ML
# model (directive section 64) can actually learn from -- deliberately
# EXCLUDES absolute price levels (`close`/`recent_high`/`recent_low`,
# which are not comparable across different price regimes/instruments)
# and the categorical `session` string (would need its own encoding, not
# added here to keep this v1 vector purely numeric). `hour_of_day_utc` is
# included as a plain integer -- a simple, honest session-proxy a model
# can learn structure from without a categorical encoder.
NUMERIC_FEATURE_FIELDS: tuple[str, ...] = (
    "return_1", "log_return_1", "realized_volatility", "atr", "normalized_range", "momentum",
    "velocity", "acceleration", "efficiency_ratio", "directional_persistence", "range_expansion_ratio",
    "body_ratio", "upper_wick_ratio", "lower_wick_ratio", "spread_percentile", "movement_to_cost",
    "hour_of_day_utc",
)


def numeric_feature_vector(snapshot: FeatureSnapshot) -> dict[str, float | None]:
    """`None` for any field the underlying snapshot couldn't compute
    (insufficient lookback, missing spread data, etc.) -- never a
    fabricated 0.0/mean-imputed stand-in. A training-dataset builder must
    treat a `None` value as a reason to EXCLUDE that row, not to guess."""
    return {name: getattr(snapshot, name) for name in NUMERIC_FEATURE_FIELDS}
