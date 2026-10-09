"""Hard cut-loss backstop (config `[hard_stop]`): how far, in percent, the
price a position would close at has moved against its broker fill price.

BUY closes at the bid: adverse % = 100 * (price_open - bid) / price_open
SELL closes at the ask: adverse % = 100 * (ask - price_open) / price_open

Negative = the position is in profit. Pure; the runtime decides what to do.
"""

from __future__ import annotations

import math

BUY = "BUY"
SELL = "SELL"


def adverse_move_pct(direction: str, price_open: float, bid: float, ask: float) -> float | None:
    """None when it cannot be computed (unknown direction, non-positive or
    non-finite prices) -- the caller must not treat that as 'no loss'."""
    values = (price_open, bid, ask)
    if not all(math.isfinite(v) and v > 0 for v in values):
        return None
    if direction == BUY:
        return 100.0 * (price_open - bid) / price_open
    if direction == SELL:
        return 100.0 * (ask - price_open) / price_open
    return None


def breaches_hard_stop(adverse_pct: float | None, max_pct: float | None) -> bool:
    """True only for a computable move STRICTLY beyond the limit."""
    return max_pct is not None and adverse_pct is not None and adverse_pct > max_pct
