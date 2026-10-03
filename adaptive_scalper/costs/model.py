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

# What a CostEstimate's total covers. Every estimate carries exactly one, and
# each consumer requires the one it means (costs.edge / selector refuse
# anything but a full round trip; the position reviews refuse anything but
# remaining exit friction), so one number can no longer mean "whole trade" at
# entry and "what is still payable" after entry.
HORIZON_FULL_ROUND_TRIP = "FULL_ROUND_TRIP"   # entry + exit, decided before entry
HORIZON_REMAINING_EXIT = "REMAINING_EXIT"     # exit friction still payable on an open position
COST_HORIZONS = (HORIZON_FULL_ROUND_TRIP, HORIZON_REMAINING_EXIT)

# A market entry followed by a market/stop exit fills twice; the shipped
# slippage evidence (`[costs.*] slippage_price`) is measured per fill.
FILLS_PER_ROUND_TRIP = 2


@dataclass(frozen=True)
class CostEstimate:
    spread_cost: float          # price units
    commission_cost: float      # price-equivalent units
    slippage_cost: float        # price units
    swap_cost: float            # price-equivalent units
    uncertainty_margin: float   # price units
    total_cost: float           # sum of all of the above
    horizon: str = HORIZON_FULL_ROUND_TRIP

    def __post_init__(self) -> None:
        if self.horizon not in COST_HORIZONS:
            raise ValueError(f"horizon must be one of {COST_HORIZONS}, got {self.horizon!r}")


def require_horizon(cost: CostEstimate | None, horizon: str, consumer: str) -> None:
    """Raise if a consumer is handed an estimate built for another horizon.
    `None` (unknown cost) passes through: the consumer's own fail-closed
    handling (BLOCK_COST / thesis unknown) applies to it."""
    if cost is not None and cost.horizon != horizon:
        raise ValueError(f"{consumer} needs a {horizon} cost estimate, got {cost.horizon}")


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
    horizon: str = HORIZON_FULL_ROUND_TRIP,
) -> CostEstimate:
    """Combine every cost component into one estimate.

    Low-level: the caller has already turned per-fill evidence into horizon
    totals. Runtime, PAPER and backtest code use `round_trip_cost_from_evidence`
    / `remaining_exit_cost_from_evidence` instead, which take PER-FILL inputs
    and apply the multiplicity themselves.

    expected_slippage_price is the TOTAL expected slippage for the
    decision horizon represented by this estimate. For a pre-entry market
    trade that expects a later market/stop exit, callers must include both
    fills (normally entry_slippage + exit_slippage). For an already-open
    position, callers pass only the remaining exit slippage. This explicit
    contract prevents a per-fill observation from being silently treated as
    a full round-trip cost.
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
        horizon=horizon,
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


def round_trip_cost_from_evidence(
    *,
    spread_price: float | None,
    per_fill_slippage_price: float | None,
    round_trip_commission_price: float | None,
    swap_price_equivalent: float | None,
    uncertainty_margin_pct: float = 0.10,
) -> CostEstimate | None:
    """PRE-ENTRY cost of the whole trade: a market entry plus a market/stop
    exit. `spread_price` is the full bid/ask spread (paid half at each fill
    against the mid, i.e. once in total); slippage is charged once PER FILL;
    commission is the configured round-trip amount. Unknown -> None."""
    if per_fill_slippage_price is None:
        return None
    return _tagged(estimate_cost_from_evidence(
        spread_price=spread_price, commission_price_equivalent=round_trip_commission_price,
        expected_slippage_price=FILLS_PER_ROUND_TRIP * per_fill_slippage_price,
        swap_price_equivalent=swap_price_equivalent, uncertainty_margin_pct=uncertainty_margin_pct,
    ), HORIZON_FULL_ROUND_TRIP)


def remaining_exit_cost_from_evidence(
    *,
    exit_spread_price: float | None,
    per_fill_slippage_price: float | None,
    round_trip_commission_price: float | None,
    swap_price_equivalent: float | None,
    uncertainty_margin_pct: float = 0.10,
) -> CostEstimate | None:
    """Friction still payable on an ALREADY OPEN position: one exit fill's
    slippage, the exit half of the round-trip commission, swap for the
    remaining horizon, and `exit_spread_price` -- 0.0 when the caller's mark
    is already the executable closing side of the quote (bid for a long,
    ask for a short), half the spread when the mark is a mid price. The
    entry spread, entry slippage and entry commission are sunk and are
    never part of this estimate. Unknown -> None."""
    if per_fill_slippage_price is None or round_trip_commission_price is None:
        return None
    return _tagged(estimate_cost_from_evidence(
        spread_price=exit_spread_price, commission_price_equivalent=round_trip_commission_price / 2.0,
        expected_slippage_price=per_fill_slippage_price, swap_price_equivalent=swap_price_equivalent,
        uncertainty_margin_pct=uncertainty_margin_pct,
    ), HORIZON_REMAINING_EXIT)


def _tagged(cost: CostEstimate | None, horizon: str) -> CostEstimate | None:
    if cost is None:
        return None
    return CostEstimate(cost.spread_cost, cost.commission_cost, cost.slippage_cost, cost.swap_cost,
                        cost.uncertainty_margin, cost.total_cost, horizon)
