"""Estimated-vs-realized cost tracking (directive section 34).

`record_estimated_cost()` is called at decision time (every time a cost
estimate is produced for a proposal). `record_realized_cost()` is called
once — later — when the actual fill's real costs are known (from the
not-yet-built execution/reconciliation layer). A row's realized fields
are set exactly once; there is no update-realized-again function,
because a fill's realized cost is a historical fact, not a re-estimate.
"""

from __future__ import annotations

import sqlite3
import time

from adaptive_scalper.costs.model import CostEstimate


def record_estimated_cost(
    conn: sqlite3.Connection,
    canonical_symbol: str,
    cost: CostEstimate,
    *,
    chain_key: str | None = None,
    now_utc: int | None = None,
) -> int:
    now = now_utc if now_utc is not None else int(time.time())
    cursor = conn.execute(
        """
        INSERT INTO cost_observations
            (canonical_symbol, chain_key, estimated_spread_cost, estimated_commission_cost,
             estimated_slippage_cost, estimated_swap_cost, estimated_uncertainty_margin,
             estimated_total_cost, recorded_at_utc)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            canonical_symbol, chain_key, cost.spread_cost, cost.commission_cost,
            cost.slippage_cost, cost.swap_cost, cost.uncertainty_margin, cost.total_cost, now,
        ),
    )
    return cursor.lastrowid


def record_realized_cost(
    conn: sqlite3.Connection,
    observation_id: int,
    *,
    realized_spread_cost: float,
    realized_commission_cost: float,
    realized_slippage_cost: float,
    realized_swap_cost: float = 0.0,
    now_utc: int | None = None,
) -> float:
    """Records the real, observed cost for a previously-estimated
    observation and returns `prediction_error` (realized - estimated;
    positive means costs were worse than predicted). Raises `ValueError`
    if `observation_id` doesn't exist, or if its realized fields are
    already set — a realized fact is recorded once, not overwritten."""
    row = conn.execute(
        "SELECT estimated_total_cost, realized_total_cost FROM cost_observations WHERE id = ?",
        (observation_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"no cost_observations row with id={observation_id}")
    if row["realized_total_cost"] is not None:
        raise ValueError(f"cost_observations row {observation_id} already has a realized cost recorded")

    realized_total = realized_spread_cost + realized_commission_cost + realized_slippage_cost + realized_swap_cost
    prediction_error = realized_total - row["estimated_total_cost"]
    now = now_utc if now_utc is not None else int(time.time())

    conn.execute(
        """
        UPDATE cost_observations
        SET realized_spread_cost = ?, realized_commission_cost = ?, realized_slippage_cost = ?,
            realized_swap_cost = ?, realized_total_cost = ?, prediction_error = ?, realized_at_utc = ?
        WHERE id = ?
        """,
        (
            realized_spread_cost, realized_commission_cost, realized_slippage_cost,
            realized_swap_cost, realized_total, prediction_error, now, observation_id,
        ),
    )
    return prediction_error
