"""Horizon-aware swap (GitHub issue #8).

Swap is charged by the broker at its daily rollover, which is the broker
SERVER's midnight (`[mt5] server_time_rule`, gateway/server_time.py), not UTC
midnight. Whether swap matters for a decision therefore depends on whether
the decision's maximum possible hold can cross that instant:

- cannot cross: the incremental swap of that decision is genuinely 0.0;
- can cross (or the hold is unbounded): broker swap evidence is REQUIRED; an
  unknown swap returns None, which every cost consumer treats as BLOCK_COST.

Unknown swap never silently becomes zero, and no website figure is hardcoded:
the per-lot-per-day amount must come from configuration backed by broker
evidence (`[costs.<SYMBOL>] swap_per_lot_per_day`).

Not modelled: the broker's weekday multiplier (e.g. triple swap on one
weekday). A configured value must already be the conservative per-rollover
amount; see docs/audits/PROFITABILITY_ROOT_CAUSE_FINAL.md.
"""

from __future__ import annotations

from adaptive_scalper.costs.model import price_equivalent_of_monetary_cost
from adaptive_scalper.gateway.server_time import utc_to_server

_DAY = 86_400


def rollovers_crossed(server_time_rule: str, start_utc: int, end_utc: int) -> int:
    """Number of broker-server midnights in (start_utc, end_utc]."""
    if end_utc <= start_utc:
        return 0
    return max(0, utc_to_server(server_time_rule, int(end_utc)) // _DAY
               - utc_to_server(server_time_rule, int(start_utc)) // _DAY)


def swap_price_for_horizon(
    *,
    server_time_rule: str,
    start_utc: int,
    max_hold_seconds: int | None,
    swap_per_lot_per_day: float | None,
    tick_size: float,
    tick_value: float,
) -> float | None:
    """Price-equivalent swap for a decision that may hold until
    `start_utc + max_hold_seconds`. `max_hold_seconds=None` means the hold
    is unbounded (no enforced maximum), which can always cross a rollover."""
    if max_hold_seconds is not None and max_hold_seconds < 0:
        raise ValueError(f"max_hold_seconds must be >= 0, got {max_hold_seconds!r}")
    if max_hold_seconds is not None and rollovers_crossed(server_time_rule, start_utc, start_utc + max_hold_seconds) == 0:
        return 0.0
    if swap_per_lot_per_day is None:
        return None
    crossings = 1 if max_hold_seconds is None else rollovers_crossed(server_time_rule, start_utc, start_utc + max_hold_seconds)
    return crossings * price_equivalent_of_monetary_cost(swap_per_lot_per_day, tick_size, tick_value)
