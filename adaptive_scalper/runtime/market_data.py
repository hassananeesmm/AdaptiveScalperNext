"""Live market data for the runtime: CLOSED bars only, plus returns for
correlation.

Decisions are taken at a bar's CLOSE, exactly like the backtest and
PAPER engines, so the bar MT5 is still forming must never be fed to a
strategy. Closure is judged against the symbol's OWN fresh tick time --
the same broker clock the bar timestamps use -- rather than this machine's
UTC clock: brokers commonly stamp bars in server time, and mixing the two
clocks would either feed a forming bar (lookahead) or discard every bar.
Without a usable tick the newest returned bar is conservatively treated as
still forming.
"""

from __future__ import annotations

import math

from adaptive_scalper.gateway.protocol import Gateway
from adaptive_scalper.gateway.types import Bar, Tick
from adaptive_scalper.history.resolutions import resolution_seconds


def closed_bars(
    gateway: Gateway, broker_symbol: str, resolution: str, *, now_utc: int, count: int, tick: Tick | None,
) -> list[Bar]:
    step = resolution_seconds(resolution)
    # A generous window so weekend/session gaps still leave `count` bars.
    raw = gateway.copy_rates_range(broker_symbol, resolution, now_utc - count * step * 4, now_utc + 2 * step)
    by_time = {b.time: b for b in raw}
    bars = [by_time[t] for t in sorted(by_time)]
    if tick is not None and tick.time > 0:
        bars = [b for b in bars if b.time + step <= tick.time]
    elif bars:
        bars = bars[:-1]
    return bars[-count:]


def log_returns(bars: list[Bar]) -> dict[int, float]:
    out = {}
    for prev, cur in zip(bars, bars[1:]):
        if prev.close > 0 and cur.close > 0:
            out[cur.time] = math.log(cur.close / prev.close)
    return out
