"""Monte Carlo trade-order resampling (directive section 80: "MONTE
CARLO").

This does NOT simulate new market data or new trade outcomes -- it takes
the REALIZED trade P/L sequence a real `run_backtest()`/`run_walk_forward()`
already produced and asks a narrower, honest question: "how much does the
RESULT (final equity, max drawdown, ruin risk) depend on the particular
ORDER those trades happened to occur in?" Each simulation is a random
permutation of the SAME multiset of realized P/Ls (never resampled WITH
replacement, which would fabricate trade counts/outcomes that never
happened) replayed as a fresh equity curve.

Deterministic given the same `seed` -- a documented, reproducible `random.
Random(seed)` instance, never hidden global RNG state, so re-running with
the same seed always reproduces the exact same distribution.
"""

from __future__ import annotations

import random

from adaptive_scalper.backtest.types import DistributionStats, MonteCarloResult, SimulatedTrade


def _percentile(sorted_values: list[float], pct: float) -> float:
    if len(sorted_values) == 1:
        return sorted_values[0]
    k = pct * (len(sorted_values) - 1)
    lo, hi = int(k), min(int(k) + 1, len(sorted_values) - 1)
    frac = k - lo
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * frac


def _distribution_stats(values: list[float]) -> DistributionStats:
    ordered = sorted(values)
    n = len(ordered)
    return DistributionStats(
        mean=sum(ordered) / n, median=_percentile(ordered, 0.5), p5=_percentile(ordered, 0.05),
        p95=_percentile(ordered, 0.95), minimum=ordered[0], maximum=ordered[-1],
    )


def run_monte_carlo(
    trades: tuple[SimulatedTrade, ...],
    *,
    initial_equity: float,
    n_simulations: int = 1000,
    seed: int,
    ruin_equity_fraction: float = 0.5,
) -> MonteCarloResult:
    if initial_equity <= 0:
        raise ValueError(f"initial_equity must be positive, got {initial_equity!r}")
    if n_simulations < 1:
        raise ValueError(f"n_simulations must be >= 1, got {n_simulations!r}")
    if not (0.0 < ruin_equity_fraction < 1.0):
        raise ValueError(f"ruin_equity_fraction must be in (0, 1), got {ruin_equity_fraction!r}")

    pnls = [t.realized_pnl for t in trades if t.is_closed and t.realized_pnl is not None]
    if not pnls:
        raise ValueError("run_monte_carlo() requires at least one closed trade with a realized P/L")

    rng = random.Random(seed)
    ruin_threshold = initial_equity * ruin_equity_fraction

    final_equities: list[float] = []
    max_drawdowns: list[float] = []
    ruin_count = 0

    for _ in range(n_simulations):
        order = pnls[:]
        rng.shuffle(order)

        equity = initial_equity
        peak_equity = equity
        max_drawdown = 0.0
        ruined = False
        for pnl in order:
            equity += pnl
            peak_equity = max(peak_equity, equity)
            max_drawdown = max(max_drawdown, peak_equity - equity)
            if equity <= ruin_threshold:
                ruined = True

        final_equities.append(equity)
        max_drawdowns.append(max_drawdown)
        if ruined:
            ruin_count += 1

    return MonteCarloResult(
        n_simulations=n_simulations, seed=seed, initial_equity=initial_equity,
        final_equity=_distribution_stats(final_equities), max_drawdown=_distribution_stats(max_drawdowns),
        probability_of_ruin=ruin_count / n_simulations, ruin_equity_fraction=ruin_equity_fraction,
    )
