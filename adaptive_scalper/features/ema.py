"""Exponential moving average of closes (causal).

alpha = 2 / (period + 1), seeded with the simple mean of the first `period`
values; fewer than `period` values -> None.
"""

from __future__ import annotations

import math
from collections.abc import Sequence


def ema_last(values: Sequence[float], period: int) -> float | None:
    if period < 1:
        raise ValueError(f"EMA period must be >= 1, got {period}")
    if len(values) < period or not all(math.isfinite(v) for v in values):
        return None
    alpha = 2.0 / (period + 1)
    ema = sum(values[:period]) / period
    for v in values[period:]:
        ema += alpha * (v - ema)
    return ema
