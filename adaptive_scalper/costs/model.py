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
    commission_price_equivalent: float,
    expected_slippage_price: float,
    swap_price_equivalent: float,
    uncertainty_margin_pct: float = 0.10,
) -> CostEstimate:
    """Combine every cost component into one estimate.

    Per external review: every component is a REQUIRED keyword argument,
    with no default of `0.0` — a caller must always make an explicit,
    deliberate choice for each one. A prior version defaulted commission/
    slippage/swap to `0.0`, which meant a caller that simply forgot to
    measure or pass one of them would silently get "this cost is known
    to be exactly zero" instead of an error — exactly the "unknown cost
    becomes zero" failure mode directive section 34 exists to prevent.
    Passing an explicit `0.0` remains correct when a component is
    genuinely, verifiably zero (e.g. a broker documented as commission-
    free) — that is a real fact the caller asserts, not a default this
    function assumes on the caller's behalf.

    For the common real-world case of "I don't yet know some of these
    values," use `estimate_cost_from_evidence()` instead, which returns
    `None` rather than ever calling this function with a guessed zero.

    `uncertainty_margin_pct` (directive section 34's "uncertainty_margin")
    is applied as a percentage buffer on top of the summed known costs —
    conservative by construction: the margin can only ever increase the
    effective cost bar a trade must clear, never reduce it.
    """
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


def estimate_cost_from_evidence(
    *,
    spread_price: float | None,
    commission_price_equivalent: float | None,
    expected_slippage_price: float | None,
    swap_price_equivalent: float | None,
    uncertainty_margin_pct: float = 0.10,
) -> CostEstimate | None:
    """The REQUIRED real-runtime entry point for cost estimation
    (external review: "never silently underestimate costs"). Each
    component is `float | None` — `None` means "not currently known,"
    e.g. no live quote to derive spread from, or no confirmed commission
    schedule for this account. If ANY component is `None`, this returns
    `None` rather than ever calling `estimate_cost()` with a guessed
    `0.0` substituted in. The composed final permission gate treats a
    `None` cost estimate as `BLOCK_COST` (`costs/edge.py`'s
    `evaluate_cost_gate`) — unknown cost blocks the trade, it never
    silently becomes free.

    Swap deliberately gets NO special-cased default either, even though
    it is often genuinely negligible for short-duration scalp holds:
    "genuinely negligible" must be an explicit, measured `0.0` the
    caller asserts (e.g. "this broker charges no swap on positions held
    under 24h, confirmed against its published schedule") passed in
    deliberately — never a parameter simply left out.
    """
    if spread_price is None or commission_price_equivalent is None or expected_slippage_price is None or swap_price_equivalent is None:
        return None
    return estimate_cost(
        spread_price=spread_price,
        commission_price_equivalent=commission_price_equivalent,
        expected_slippage_price=expected_slippage_price,
        swap_price_equivalent=swap_price_equivalent,
        uncertainty_margin_pct=uncertainty_margin_pct,
    )
