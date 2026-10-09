"""Mathematical candle properties of ONE closed bar (no pattern matching).

With range = high - low > 0:

    lower_wick_ratio = (min(open, close) - low)  / range
    upper_wick_ratio = (high - max(open, close)) / range
    body_ratio       = |close - open|            / range
    close_location   = (close - low)             / range   (0 = at the low, 1 = at the high)

lower + upper + body == 1. A bar with no range (high == low), an
inconsistent bar (low > min(open, close) or high < max(open, close)) or a
non-finite price -> None: never a 0.0 that would read as "no wick".
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass

from adaptive_scalper.gateway.types import Bar


@dataclass(frozen=True)
class CandleProperties:
    bar_time_utc: int
    range: float
    lower_wick_ratio: float
    upper_wick_ratio: float
    body_ratio: float
    close_location: float
    direction: str  # "UP" (close > open), "DOWN" (close < open), "FLAT"

    def as_dict(self) -> dict:
        return asdict(self)


def candle_properties(bar: Bar) -> CandleProperties | None:
    o, h, low, c = bar.open, bar.high, bar.low, bar.close
    if not all(math.isfinite(v) for v in (o, h, low, c)):
        return None
    rng = h - low
    if rng <= 0 or low > min(o, c) or h < max(o, c):
        return None
    return CandleProperties(
        bar_time_utc=bar.time,
        range=rng,
        lower_wick_ratio=(min(o, c) - low) / rng,
        upper_wick_ratio=(h - max(o, c)) / rng,
        body_ratio=abs(c - o) / rng,
        close_location=(c - low) / rng,
        direction="UP" if c > o else "DOWN" if c < o else "FLAT",
    )
