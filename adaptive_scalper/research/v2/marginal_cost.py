"""H5 -- marginal FUTURE cost for hold-versus-close decisions (research only).

V1's remaining-edge test (`backtest.engine._review_open_trade`) subtracts
the FULL round-trip estimate (`engine._estimate_cost`: both spreads,
round-trip commission, slippage, uncertainty margin) from the remaining
distance to target on every bar of an OPEN position. The entry half of
that is already paid (sunk): charging it again biases V1 toward closing.

`marginal_future_cost` is what still lies ahead of an open position:

- the exit half-spread at the current bar's spread,
- the expected exit slippage (the configured per-fill slippage),
- swap for rollovers still ahead (the caller states how many; 0 for the
  intraday horizons studied),
- the same uncertainty margin percentage V1 uses, on those components only.

Exit commission is EXCLUDED by default: the round-trip commission is paid
whether the position closes now or later, so it does not change the
hold-versus-close comparison. `include_exit_commission=True` adds half the
round trip for audit. All values are PRICE units, like the V1 estimate.
"""

from __future__ import annotations

from dataclasses import dataclass

from adaptive_scalper.simulation.fill_model import _spread_price, round_trip_commission_price


@dataclass(frozen=True)
class MarginalCost:
    exit_spread: float
    exit_slippage: float
    exit_commission: float
    swap: float
    uncertainty_margin: float
    total_cost: float


def marginal_future_cost(bar, symbol_spec, config, *, rollovers_ahead: int = 0,
                         include_exit_commission: bool = False) -> MarginalCost:
    if rollovers_ahead < 0:
        raise ValueError("rollovers_ahead must be >= 0")
    fills = config.fill_assumptions
    exit_spread = _spread_price(bar, symbol_spec.point) / 2.0
    exit_slippage = fills.slippage_price
    exit_commission = (
        round_trip_commission_price(fills, tick_size=symbol_spec.trade_tick_size,
                                    tick_value=symbol_spec.trade_tick_value) / 2.0
        if include_exit_commission else 0.0
    )
    swap = (fills.swap_monetary_per_lot_per_day * symbol_spec.trade_tick_size / symbol_spec.trade_tick_value
            * rollovers_ahead) if rollovers_ahead else 0.0
    base = exit_spread + exit_slippage + exit_commission + swap
    margin = base * config.uncertainty_margin_pct
    return MarginalCost(exit_spread, exit_slippage, exit_commission, swap, margin, base + margin)
