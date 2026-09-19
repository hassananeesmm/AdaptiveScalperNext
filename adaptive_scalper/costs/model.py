"""Per-symbol transaction cost estimation (directive section 34).

Everything here stays in PRICE units — the same units `StrategySignal`'s
`stop_distance`/`target_distance` use — so cost can be compared directly
against a strategy's own hypothesis without ever needing to know
position size or account risk. Spread and slippage are naturally price
distances already; commission and swap are usually quoted as a flat
monetary amount per lot, so `price_equivalent_of_monetary_cost()`
converts them into "how much price movement would be needed to earn
back this monetary cost for one lot" using the symbol's own contract
data (`trade_tick_size`/`trade_tick_value`) — a real, symbol-specific
conversion, not a generic forex-wide constant (directive section 34:
"Do not use one generic forex cost for all markets").
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CostEstimate:
    spread_cost: float          # price units
    commission_cost: float      # price-equivalent units
    slippage_cost: float        # price units
    swap_cost: float            # price-equivalent units
    uncertainty_margin: float   # price units
    total_cost: float           # sum of all of the above


def price_equivalent_of_monetary_cost(monetary_cost_per_lot: float, tick_size: float, tick_value: float) -> float:
    """Convert a flat per-lot monetary cost (commission, swap) into an
    equivalent price distance for THIS symbol's contract specification.
    `tick_value` must be positive (SymbolSpec's contract-sanity check
    already enforces this upstream — see gateway/symbol_validation.py)."""
    if tick_value <= 0:
        raise ValueError(f"tick_value must be positive, got {tick_value!r}")
    if tick_size <= 0:
        raise ValueError(f"tick_size must be positive, got {tick_size!r}")
    if monetary_cost_per_lot < 0:
        raise ValueError(f"monetary_cost_per_lot must be non-negative, got {monetary_cost_per_lot!r}")
    return monetary_cost_per_lot * tick_size / tick_value


def estimate_cost(
    *,
    spread_price: float,
    commission_price_equivalent: float = 0.0,
    expected_slippage_price: float = 0.0,
    swap_price_equivalent: float = 0.0,
    uncertainty_margin_pct: float = 0.10,
) -> CostEstimate:
    """Combine every cost component into one estimate. `uncertainty_margin_pct`
    (directive section 34's "uncertainty_margin") is applied as a percentage
    buffer on top of the summed known costs — conservative by construction:
    the margin can only ever increase the effective cost bar a trade must
    clear, never reduce it."""
    for name, value in (
        ("spread_price", spread_price),
        ("commission_price_equivalent", commission_price_equivalent),
        ("expected_slippage_price", expected_slippage_price),
        ("swap_price_equivalent", swap_price_equivalent),
    ):
        if value < 0:
            raise ValueError(f"{name} must be non-negative, got {value!r}")
    if uncertainty_margin_pct < 0:
        raise ValueError(f"uncertainty_margin_pct must be non-negative, got {uncertainty_margin_pct!r}")

    subtotal = spread_price + commission_price_equivalent + expected_slippage_price + swap_price_equivalent
    uncertainty_margin = subtotal * uncertainty_margin_pct
    total_cost = subtotal + uncertainty_margin

    return CostEstimate(
        spread_cost=spread_price,
        commission_cost=commission_price_equivalent,
        slippage_cost=expected_slippage_price,
        swap_cost=swap_price_equivalent,
        uncertainty_margin=uncertainty_margin,
        total_cost=total_cost,
    )
