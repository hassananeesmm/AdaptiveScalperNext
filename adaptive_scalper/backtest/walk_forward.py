"""Sequential fixed-configuration fold evaluation (directive section 80).

What this is -- and is not: ONE fixed configuration is evaluated across N
sequential, strictly-forward folds; nothing is trained or re-fit between
folds. Results are labeled `SEQUENTIAL_FIXED_CONFIG_EVALUATION`. That is a
temporal stability check, NOT ML walk-forward validation (train -> purge/
embargo -> validate on the future -> advance -> retrain), which lives in
`learning/model_walk_forward.py`. The CLI command keeps the familiar
`walk-forward` name but prints this label.

Each fold is an independent `run_backtest()` call over that fold's own
contiguous bar slice -- the SAME production decision cores the single-shot
backtest uses, walked across N non-overlapping windows in time order.

Honest, named scope limit: each fold's feature engine warms up FRESH at
that fold's own start (it never reaches back into a PRIOR fold's bars for
its own `feature_lookback` warm-up window) -- this is deliberately the
simplest, most conservative design: a fold's behavior can never depend on
data outside its own declared range, so there is no way for an earlier
fold's characteristics to leak into a later one's decisions via a shared
feature window. The cost is that the first `feature_lookback` bars of
EVERY fold (not just the first) have degraded/None features, exactly like
the start of any single-shot backtest -- documented, not hidden.

An optional `embargo_bars` gap is DROPPED between consecutive folds
(neither fold sees those bars at all) -- a purge margin that further
guards against any residual boundary effect (e.g. a regime classifier's
hysteresis state or an indicator's tail influence) leaking across the
fold seam, standard practice in temporal cross-validation ("purged and
embargoed" splits).
"""

from __future__ import annotations

import sqlite3
import time

from adaptive_scalper.backtest.engine import _compute_metrics, run_backtest
from adaptive_scalper.backtest.persistence import record_backtest_run
from adaptive_scalper.backtest.types import BacktestConfig, WalkForwardFold, WalkForwardResult
from adaptive_scalper.gateway.types import Bar, SymbolSpec
from adaptive_scalper.strategies import build_active_registry


def _fold_ranges(n_bars: int, n_folds: int, min_fold_size: int, embargo_bars: int) -> list[tuple[int, int]]:
    """Contiguous, non-overlapping, strictly-increasing-in-time [start,
    end) index ranges, `embargo_bars` apart, covering as much of
    `n_bars` as divides evenly into `n_folds` folds of at least
    `min_fold_size` bars each."""
    usable = n_bars - embargo_bars * (n_folds - 1)
    fold_size = usable // n_folds
    if fold_size < min_fold_size:
        raise ValueError(
            f"{n_bars} bars / {n_folds} folds (embargo={embargo_bars}) yields fold_size={fold_size}, "
            f"below the minimum {min_fold_size} bars a fold needs"
        )
    ranges = []
    cursor = 0
    for fold_index in range(n_folds):
        start = cursor
        end = start + fold_size
        ranges.append((start, end))
        cursor = end + embargo_bars
    return ranges


def run_walk_forward(
    bars: list[Bar],
    canonical_symbol: str,
    resolution: str,
    symbol_spec: SymbolSpec,
    *,
    config: BacktestConfig = BacktestConfig(),
    n_folds: int = 5,
    embargo_bars: int = 0,
    conn: sqlite3.Connection | None = None,
    run_id_prefix: str | None = None,
    now_utc: int | None = None,
) -> WalkForwardResult:
    if n_folds < 2:
        raise ValueError(f"n_folds must be >= 2, got {n_folds!r}")
    if embargo_bars < 0:
        raise ValueError(f"embargo_bars must be >= 0, got {embargo_bars!r}")

    now = now_utc if now_utc is not None else int(time.time())
    min_fold_size = config.feature_lookback + 3
    ranges = _fold_ranges(len(bars), n_folds, min_fold_size, embargo_bars)

    strategies = tuple(s.key for s in build_active_registry().all_active())
    folds: list[WalkForwardFold] = []
    all_trades = []

    for fold_index, (start, end) in enumerate(ranges):
        fold_bars = bars[start:end]
        result = run_backtest(fold_bars, canonical_symbol, resolution, symbol_spec, config=config, now_utc=now)
        folds.append(WalkForwardFold(
            fold_index=fold_index, range_start_utc=fold_bars[0].time, range_end_utc=fold_bars[-1].time,
            result=result,
        ))
        all_trades.extend(result.trades)

        if conn is not None:
            prefix = run_id_prefix or f"wf:{canonical_symbol}:{resolution}:{now}"
            record_backtest_run(
                conn, result, fold_bars, run_id=f"{prefix}:fold{fold_index}", run_type="WALK_FORWARD_FOLD",
                used_for="WALK_FORWARD_FOLD", strategies=strategies, feature_schema_version=1, now_utc=now,
            )

    # Aggregate metrics are pooled across every fold's CLOSED trades in
    # chronological order -- "final_equity" here is config.initial_equity
    # run ONCE through the full trade sequence, i.e. what walking forward
    # through every fold in sequence would have compounded to, not a
    # per-fold average.
    equity = config.initial_equity
    peak_equity = equity
    max_drawdown = 0.0
    for trade in sorted(all_trades, key=lambda t: t.entry_time_utc):
        equity += trade.realized_pnl or 0.0
        peak_equity = max(peak_equity, equity)
        max_drawdown = max(max_drawdown, peak_equity - equity)
    aggregate_metrics = _compute_metrics(all_trades, config.initial_equity, equity, max_drawdown)

    return WalkForwardResult(
        canonical_symbol=canonical_symbol, resolution=resolution, folds=tuple(folds),
        aggregate_metrics=aggregate_metrics, embargo_bars=embargo_bars,
    )
