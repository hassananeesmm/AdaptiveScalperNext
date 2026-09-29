"""H3 -- out-of-fold calibration of a strategy's raw_confidence.

`raw_confidence` is a strategy SCORE, not a probability (V1 research: the
bucket that claims 1.00 wins 38-48 %). This module turns a strategy's own
EARLIER closed trades into empirical estimates for a new signal:

- `p_net_win`: out-of-fold probability that the trade is net-profitable;
- `mean_gross_r` +- `se_gross_r`: expected gross result in R;
- `mean_cost_r`: measured cost in R for that strategy/symbol.

Simplest method the sample supports: reliability bins on raw_confidence
(quintile edges from the calibration sample). A bin with fewer than
`MIN_BIN_TRADES` falls back to the strategy-level estimate; a strategy
with fewer than `MIN_STRATEGY_TRADES` is UNKNOWN (`None`) -- the caller
must abstain. No default probability is ever invented.

The caller guarantees the out-of-fold property: calibrate on trades
that CLOSED before the evaluated fold began (`fit_calibration(...,
before_utc=fold_start)` enforces it again).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

MIN_STRATEGY_TRADES = 50
MIN_BIN_TRADES = 30
N_BINS = 5


@dataclass(frozen=True)
class Estimate:
    level: str                 # "bin" or "strategy"
    n: int
    p_net_win: float
    mean_gross_r: float
    se_gross_r: float
    mean_cost_r: float
    mean_raw_confidence: float


@dataclass(frozen=True)
class StrategyCalibration:
    strategy_key: str
    n: int
    edges: tuple[float, ...]            # N_BINS - 1 interior quantile edges
    bins: tuple[Estimate | None, ...]   # None = bin too small -> strategy level
    overall: Estimate

    def lookup(self, raw_confidence: float) -> Estimate:
        index = sum(1 for edge in self.edges if raw_confidence > edge)
        return self.bins[index] or self.overall


def _estimate(rows: list[tuple[float, float, float, float]], level: str) -> Estimate:
    """rows: (raw_confidence, gross_r, cost_r, net_r)."""
    n = len(rows)
    gross = [r[1] for r in rows]
    mean_gross = sum(gross) / n
    variance = sum((g - mean_gross) ** 2 for g in gross) / (n - 1) if n > 1 else float("inf")
    return Estimate(
        level=level, n=n, p_net_win=sum(1 for r in rows if r[3] > 0) / n, mean_gross_r=mean_gross,
        se_gross_r=math.sqrt(variance / n), mean_cost_r=sum(r[2] for r in rows) / n,
        mean_raw_confidence=sum(r[0] for r in rows) / n,
    )


def _quantile(sorted_values: list[float], q: float) -> float:
    position = q * (len(sorted_values) - 1)
    low = math.floor(position)
    high = min(low + 1, len(sorted_values) - 1)
    return sorted_values[low] + (sorted_values[high] - sorted_values[low]) * (position - low)


def fit_calibration(trades, strategy_key: str, *, before_utc: int) -> StrategyCalibration | None:
    """`trades`: TradeView-like objects of THIS strategy's own session.
    Only trades that closed strictly before `before_utc` are used.
    Returns None (UNKNOWN -> abstain) when the sample is too small."""
    rows = []
    for t in trades:
        if t.strategy_key != strategy_key or t.exit_time_utc >= before_utc:
            continue
        if t.initial_risk <= 0 or t.raw_confidence is None or not math.isfinite(t.raw_confidence):
            continue
        rows.append((t.raw_confidence, t.gross / t.initial_risk, t.total_cost / t.initial_risk,
                     t.net / t.initial_risk))
    if len(rows) < MIN_STRATEGY_TRADES:
        return None
    confidences = sorted(r[0] for r in rows)
    edges = tuple(_quantile(confidences, k / N_BINS) for k in range(1, N_BINS))
    buckets: list[list] = [[] for _ in range(N_BINS)]
    for row in rows:
        buckets[sum(1 for edge in edges if row[0] > edge)].append(row)
    bins = tuple(_estimate(b, "bin") if len(b) >= MIN_BIN_TRADES else None for b in buckets)
    return StrategyCalibration(strategy_key, len(rows), edges, bins, _estimate(rows, "strategy"))


def reliability_table(trades, calibration: StrategyCalibration | None) -> list[dict]:
    """Evaluation-fold reliability: predicted p / gross R of each bin vs
    what the evaluated trades actually did (observability only)."""
    if calibration is None:
        return []
    groups: dict[int, list] = {}
    for t in trades:
        if t.initial_risk <= 0 or t.raw_confidence is None:
            continue
        groups.setdefault(sum(1 for e in calibration.edges if t.raw_confidence > e), []).append(t)
    out = []
    for index in sorted(groups):
        est = calibration.bins[index] or calibration.overall
        realized = groups[index]
        out.append({
            "bin": index, "level": est.level, "calibration_n": est.n, "evaluated_n": len(realized),
            "mean_raw_confidence": est.mean_raw_confidence, "predicted_p_net_win": est.p_net_win,
            "realized_p_net_win": sum(1 for t in realized if t.net > 0) / len(realized),
            "predicted_gross_r": est.mean_gross_r,
            "realized_gross_r": sum(t.gross / t.initial_risk for t in realized) / len(realized),
        })
    return out
