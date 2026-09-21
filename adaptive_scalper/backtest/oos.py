"""Untouched out-of-sample (OOS) validation (directive section 65: "An OOS
dataset that influenced design is no longer untouched").

`run_untouched_oos()` is the ONLY sanctioned way to run a backtest over a
range meant to serve as genuine OOS evidence. Before running anything, it
checks the dataset-usage ledger (`dataset_usage`, migration 0014) for this
EXACT content-checksummed bar range: if it was EVER previously used for
`TRAINING`, `VALIDATION`, or `WALK_FORWARD_FOLD` (i.e. it influenced any
design/tuning decision), or already consumed as `OOS` once before, this
raises `DatasetContaminatedError` rather than silently proceeding --
a contaminated or already-spent OOS result is not evidence, it's a
selection-bias trap.
"""

from __future__ import annotations

import sqlite3
import time

from adaptive_scalper.backtest.dataset import build_dataset_snapshot, has_dataset_been_used_as
from adaptive_scalper.backtest.engine import run_backtest
from adaptive_scalper.backtest.persistence import record_backtest_run
from adaptive_scalper.backtest.types import BacktestConfig, BacktestResult
from adaptive_scalper.gateway.types import Bar, SymbolSpec
from adaptive_scalper.simulation.types import EvidenceOrigin
from adaptive_scalper.strategies import build_active_registry

# Any prior use for one of these purposes means the design process has
# already "seen" this exact data, directly or indirectly -- it can no
# longer serve as an unbiased holdout.
_CONTAMINATING_USES = ("TRAINING", "VALIDATION", "WALK_FORWARD_FOLD")


class DatasetContaminatedError(Exception):
    """Raised when a bar range requested as OOS has already influenced a
    design decision, or has already been spent as OOS once before."""


def run_untouched_oos(
    bars: list[Bar],
    canonical_symbol: str,
    resolution: str,
    symbol_spec: SymbolSpec,
    *,
    conn: sqlite3.Connection,
    run_id: str,
    config: BacktestConfig = BacktestConfig(),
    allow_oos_reuse: bool = False,
    now_utc: int | None = None,
) -> BacktestResult:
    now = now_utc if now_utc is not None else int(time.time())
    strategies = tuple(s.key for s in build_active_registry().all_active())

    snapshot = build_dataset_snapshot(
        bars, canonical_symbol=canonical_symbol, resolution=resolution, strategies=strategies,
        feature_schema_version=1, origin=EvidenceOrigin.BACKTEST, now_utc=now,
    )

    for used_for in _CONTAMINATING_USES:
        if has_dataset_been_used_as(conn, snapshot.dataset_id, used_for):
            raise DatasetContaminatedError(
                f"dataset {snapshot.dataset_id!r} was already used for {used_for} -- "
                "it can no longer serve as untouched OOS evidence"
            )
    if not allow_oos_reuse and has_dataset_been_used_as(conn, snapshot.dataset_id, "OOS"):
        raise DatasetContaminatedError(
            f"dataset {snapshot.dataset_id!r} was already run as OOS once before -- "
            "repeated OOS runs over the same holdout defeat its purpose "
            "(pass allow_oos_reuse=True only if this is a deliberate, documented exception)"
        )

    result = run_backtest(bars, canonical_symbol, resolution, symbol_spec, config=config, now_utc=now)
    record_backtest_run(
        conn, result, bars, run_id=run_id, run_type="OOS", used_for="OOS",
        strategies=strategies, feature_schema_version=1, now_utc=now,
    )
    return result
