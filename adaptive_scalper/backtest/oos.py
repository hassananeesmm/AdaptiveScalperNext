"""Untouched out-of-sample (OOS) validation (directive section 65: "An OOS
dataset that influenced design is no longer untouched").

`run_untouched_oos()` is the ONLY sanctioned way to run a backtest over a
range meant to serve as genuine OOS evidence. Before running anything, it
checks the dataset-usage ledger (`dataset_usage`, migration 0014) for ANY
recorded dataset of the same symbol whose time range OVERLAPS this one
(any resolution, any content checksum -- including the bars used only as
feature warm-up, so a holdout must be separated from design data by at
least `feature_lookback` bars): if such a range was EVER used for
`TRAINING`, `VALIDATION`, or `WALK_FORWARD_FOLD` (i.e. it influenced any
design/tuning decision), or already consumed as `OOS` once before, this
raises `DatasetContaminatedError` rather than silently proceeding --
a contaminated or already-spent OOS result is not evidence, it's a
selection-bias trap.
"""

from __future__ import annotations

import sqlite3
import time

from adaptive_scalper.backtest.dataset import (
    build_dataset_snapshot,
    find_overlapping_usage,
    record_dataset,
    record_dataset_usage,
)
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
    """`allow_oos_reuse=True` is an explicit, recorded exception for
    ANALYSIS ONLY: the run is logged as `OOS_ANALYSIS_REUSE` in the usage
    ledger, never as a second untouched OOS, and it still refuses any
    overlap with TRAINING/VALIDATION/WALK_FORWARD_FOLD data."""
    now = now_utc if now_utc is not None else int(time.time())
    strategies = tuple(s.key for s in build_active_registry().all_active())

    snapshot = build_dataset_snapshot(
        bars, canonical_symbol=canonical_symbol, resolution=resolution, strategies=strategies,
        feature_schema_version=1, origin=EvidenceOrigin.BACKTEST, now_utc=now,
    )

    def overlapping(used_for: str) -> list:
        return find_overlapping_usage(
            conn, canonical_symbol, snapshot.range_start_utc, snapshot.range_end_utc, used_for,
        )

    # Check-and-reserve is ONE write transaction (BUG_BACKLOG #11): two
    # concurrent OOS runs over overlapping ranges can never both pass. The
    # reservation happens BEFORE the backtest runs, so a run that crashes
    # midway still spends its range -- the conservative outcome.
    used_for_label = "OOS_ANALYSIS_REUSE" if allow_oos_reuse else "OOS"
    conn.execute("BEGIN IMMEDIATE")
    try:
        for used_for in _CONTAMINATING_USES:
            hits = overlapping(used_for)
            if hits:
                first = hits[0]
                raise DatasetContaminatedError(
                    f"requested OOS range {snapshot.range_start_utc}..{snapshot.range_end_utc} overlaps dataset "
                    f"{first['dataset_id']!r} ({first['resolution']}, origin={first['origin']}, "
                    f"{first['range_start_utc']}..{first['range_end_utc']}) already used for {used_for} by run "
                    f"{first['used_by_run_id']!r} -- "
                    "it can no longer serve as untouched OOS evidence"
                )
        if not allow_oos_reuse:
            # An analysis-only reuse run has LOOKED at the range too, so it
            # spends it exactly like an OOS run would.
            hits = overlapping("OOS") + overlapping("OOS_ANALYSIS_REUSE")
            if hits:
                raise DatasetContaminatedError(
                    f"requested OOS range overlaps dataset {hits[0]['dataset_id']!r}, which was already run as OOS "
                    "once before -- repeated OOS runs over the same holdout defeat its purpose "
                    "(pass allow_oos_reuse=True only if this is a deliberate, documented exception)"
                )
        record_dataset(conn, snapshot, commit=False)
        record_dataset_usage(conn, snapshot.dataset_id, run_id, used_for_label, now_utc=now, commit=False)
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise

    result = run_backtest(bars, canonical_symbol, resolution, symbol_spec, config=config, now_utc=now)
    record_backtest_run(
        conn, result, bars, run_id=run_id, run_type="OOS", used_for=used_for_label,
        strategies=strategies, feature_schema_version=1, now_utc=now, record_usage=False,
    )
    return result
