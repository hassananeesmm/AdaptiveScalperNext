"""Wilder's ADX (Average Directional Index) over CLOSED bars.

Pure and causal: the value describes the last bar passed in and reads
nothing after it. Wilder's definition, period `p` (default 14):

    TR  = max(h - l, |h - c_prev|, |l - c_prev|)
    +DM = up   if up > down and up > 0 else 0     (up = h - h_prev)
    -DM = down if down > up and down > 0 else 0   (down = l_prev - l)
    smoothed X: first = sum of the first p values, then X_s = X_s - X_s / p + x
    +DI = 100 * +DM_s / TR_s,  -DI = 100 * -DM_s / TR_s
    DX  = 100 * |+DI - -DI| / (+DI + -DI)        (0 when both are 0)
    ADX: first = mean of the first p DX values, then ADX = (ADX * (p - 1) + DX) / p

Needs at least 2 * p + 1 bars; fewer -> None (never a guess).
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from adaptive_scalper.gateway.types import Bar

DEFAULT_ADX_PERIOD = 14


def wilder_adx(bars: Sequence[Bar], period: int = DEFAULT_ADX_PERIOD) -> float | None:
    if period < 2:
        raise ValueError(f"ADX period must be >= 2, got {period}")
    if len(bars) < 2 * period + 1:
        return None

    tr: list[float] = []
    plus_dm: list[float] = []
    minus_dm: list[float] = []
    for prev, bar in zip(bars, bars[1:]):
        tr.append(max(bar.high - bar.low, abs(bar.high - prev.close), abs(bar.low - prev.close)))
        up, down = bar.high - prev.high, prev.low - bar.low
        plus_dm.append(up if up > down and up > 0 else 0.0)
        minus_dm.append(down if down > up and down > 0 else 0.0)

    tr_s, plus_s, minus_s = sum(tr[:period]), sum(plus_dm[:period]), sum(minus_dm[:period])
    dx: list[float] = []
    for k in range(period, len(tr) + 1):
        if k > period:
            tr_s += tr[k - 1] - tr_s / period
            plus_s += plus_dm[k - 1] - plus_s / period
            minus_s += minus_dm[k - 1] - minus_s / period
        if tr_s <= 0:
            dx.append(0.0)
            continue
        plus_di, minus_di = 100.0 * plus_s / tr_s, 100.0 * minus_s / tr_s
        total = plus_di + minus_di
        dx.append(0.0 if total <= 0 else 100.0 * abs(plus_di - minus_di) / total)

    adx = sum(dx[:period]) / period
    for value in dx[period:]:
        adx = (adx * (period - 1) + value) / period
    return adx if math.isfinite(adx) else None
