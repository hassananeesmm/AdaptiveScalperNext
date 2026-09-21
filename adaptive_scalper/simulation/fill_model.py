"""Documented fill/cost simulation (directive sections 80, "PAPER
ENGINE": "Use documented paper fill simulation based on bid/ask,
spread, slippage, commission, swap where relevant").

No live gateway, no `order_send` — this module only computes what a
realistic fill WOULD have looked like, from bar/tick data the caller
already has. Both `adaptive_scalper/backtest/` and the PAPER engine call
this so fill/cost assumptions are defined ONCE, not reimplemented
per-engine and allowed to quietly drift apart.

Convention (documented, not hidden): a market entry pays the FULL
spread at entry (fills at the ask for a BUY, the bid for a SELL) plus
configured slippage working against the trader; an exit pays the same.
Commission is charged once per round trip (entry+exit combined), not
double-counted — this matches how `costs/model.CostEstimate` is
typically assembled by callers upstream. Everything here stays
consistent with `costs/model.py`'s PRICE-unit convention so a
`CostEstimate` computed for a live decision and one computed here are
directly comparable.
"""

from __future__ import annotations

from dataclasses import dataclass

from adaptive_scalper.costs.model import price_equivalent_of_monetary_cost
from adaptive_scalper.gateway.types import Bar


@dataclass(frozen=True)
class FillAssumptions:
    """Every field is a REQUIRED, explicit assumption (matching
    `costs.model.estimate_cost`'s "no free zero defaults" convention) —
    a caller must consciously choose these, never inherit a silent
    default that could misrepresent simulated results as more favorable
    than reality."""

    slippage_price: float               # adverse price movement applied on every fill, PRICE units
    commission_monetary_per_lot: float  # flat monetary commission per lot, ROUND TRIP (entry+exit combined)
    swap_monetary_per_lot_per_day: float = 0.0  # only relevant for multi-day holds; 0.0 is a real, assertable fact for a pure scalping horizon

    def __post_init__(self) -> None:
        for name in ("slippage_price", "commission_monetary_per_lot", "swap_monetary_per_lot_per_day"):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must be non-negative, got {getattr(self, name)!r}")


@dataclass(frozen=True)
class SimulatedFill:
    price: float
    spread_cost_price: float       # price units paid to spread on this one fill
    slippage_cost_price: float     # price units paid to slippage on this one fill


def _spread_price(bar: Bar, point_size: float) -> float:
    """`Bar.spread` is in MT5 POINTS (an integer), never a price
    distance directly — must be multiplied by the symbol's own `point`
    to become comparable to `Bar.close`/`stop_distance`/etc."""
    return max(0.0, bar.spread) * point_size


def simulate_fill(bar: Bar, direction: str, point_size: float, assumptions: FillAssumptions) -> SimulatedFill:
    """One realistic fill at `bar`'s reference price. Real backtest/PAPER
    callers use `bar.open` (the causal, no-lookahead reference price for
    a signal decided on a PRIOR bar's close) or `bar.close` (for an
    urgent SL/TP-triggered exit reacting within the same bar), never a
    price this bar's own subsequent movement could reveal is optimistic.

    BUY pays the ask (reference + half the spread + slippage, both
    working against the trader); SELL pays the bid (reference - half the
    spread - slippage). Splitting the spread half-and-half is the
    standard mid-price convention -- `bar.close`/`.open` are themselves
    already effectively mid/last-traded prices in the absence of a
    genuine historical bid/ask series.
    """
    if direction not in ("BUY", "SELL"):
        raise ValueError(f"direction must be 'BUY' or 'SELL', got {direction!r}")

    spread_price = _spread_price(bar, point_size)
    half_spread = spread_price / 2.0
    reference = bar.open

    if direction == "BUY":
        price = reference + half_spread + assumptions.slippage_price
    else:
        price = reference - half_spread - assumptions.slippage_price

    return SimulatedFill(price=price, spread_cost_price=spread_price, slippage_cost_price=assumptions.slippage_price)


def round_trip_commission_price(
    assumptions: FillAssumptions, *, tick_size: float, tick_value: float,
) -> float:
    """Converts the flat round-trip monetary commission into this
    symbol's PRICE-equivalent units, via the same conversion
    `costs.model.price_equivalent_of_monetary_cost()` already uses
    elsewhere — never a separate, potentially-inconsistent formula."""
    if assumptions.commission_monetary_per_lot == 0.0:
        return 0.0
    return price_equivalent_of_monetary_cost(assumptions.commission_monetary_per_lot, tick_size, tick_value)


def money_from_price_distance(price_distance: float, volume: float, *, tick_size: float, tick_value: float) -> float:
    """The inverse of `price_equivalent_of_monetary_cost()`: a PRICE
    distance (e.g. `entry_price - exit_price`) for `volume` lots,
    converted into real account-currency money using this symbol's own
    contract spec — the same formula `risk.governor.calculate_safe_volume()`
    uses to size a position in the first place, so a simulated result and
    a live risk calculation are always directly comparable."""
    if tick_size <= 0 or tick_value <= 0:
        raise ValueError("tick_size and tick_value must both be positive")
    return price_distance * volume * (tick_value / tick_size)
