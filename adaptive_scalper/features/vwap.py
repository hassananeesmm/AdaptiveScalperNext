"""Rolling VWAP with standard-deviation bands over CLOSED bars (observer only).

For the closed bars whose open time lies in [end - window, end), where
`end` is the close time of the newest bar passed in:

    typical_k = (high_k + low_k + close_k) / 3,   w_k = tick_volume_k
    VWAP  = sum(w * typical) / sum(w)
    sigma = sqrt(sum(w * (typical - VWAP)^2) / sum(w))   (volume-weighted)
    bands = VWAP +/- k * sigma

Bars with zero tick volume carry no weight. No bars in the window, or zero
total volume -> None (never a guess). Pure and causal: reads only the bars
passed in. Nothing here feeds a trading decision.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import asdict, dataclass

from adaptive_scalper.gateway.types import Bar


@dataclass(frozen=True)
class VwapBands:
    vwap: float
    sigma: float
    upper: float
    lower: float
    band_std: float
    bar_count: int
    total_volume: int
    window_start_utc: int
    window_end_utc: int

    def as_dict(self) -> dict:
        return asdict(self)


def rolling_vwap_bands(
    bars: Sequence[Bar], *, bar_seconds: int, window_seconds: int, band_std: float,
) -> VwapBands | None:
    if bar_seconds <= 0 or window_seconds < bar_seconds:
        raise ValueError(f"need 0 < bar_seconds <= window_seconds, got {bar_seconds}, {window_seconds}")
    if not (math.isfinite(band_std) and band_std > 0):
        raise ValueError(f"band_std must be positive and finite, got {band_std!r}")
    if not bars:
        return None
    end = bars[-1].time + bar_seconds
    start = end - window_seconds
    window = [b for b in bars if start <= b.time < end and b.tick_volume > 0]
    volume = sum(b.tick_volume for b in window)
    if volume <= 0:
        return None
    typical = [((b.high + b.low + b.close) / 3.0, b.tick_volume) for b in window]
    vwap = sum(p * w for p, w in typical) / volume
    sigma = math.sqrt(max(0.0, sum(w * (p - vwap) ** 2 for p, w in typical) / volume))
    if not (math.isfinite(vwap) and math.isfinite(sigma)):
        return None
    return VwapBands(vwap=vwap, sigma=sigma, upper=vwap + band_std * sigma, lower=vwap - band_std * sigma,
                     band_std=band_std, bar_count=len(window), total_volume=volume,
                     window_start_utc=start, window_end_utc=end)
