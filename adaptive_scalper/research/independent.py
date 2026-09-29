"""Independent per-strategy research sessions.

In live operation the selector picks ONE candidate per bar, so a strategy
that keeps losing the expected-net-edge ranking is never observed at all.
This module evaluates every active strategy ON ITS OWN, on the SAME bars,
with the SAME causal engine, cost model and risk policy:

- one session per strategy (`run_backtest(..., strategy_keys=(key,))`), each
  with its own positions, risk state, equity, drawdown, trade history and
  execution-cost accounting, and its own configuration fingerprint;
- a combined "selector" session over the identical bars with every
  candidate the selector saw logged (`CandidateRecord`), for the selector
  study;
- identical input is PROVEN, not assumed: every session records the bar
  checksum of the data it saw, and `IndependentStudy.inputs_identical`
  checks it.

Sessions are never pooled into one portfolio: each session's equity starts
at the configured initial equity and is reported on its own.

Safety boundaries:

- Research never touches a broker. There is no gateway here.
- Research runs refuse a range that overlaps the reserved untouched
  out-of-sample interval (`RESERVED_OOS_INTERVALS`); that interval may only
  ever be consumed once, through `backtest.oos.run_untouched_oos`.
- Persisting a study refuses the production database path: research
  writes go to a separate research database (a snapshot copy).
- The live risk ceilings apply unchanged (`RiskLimits` cannot be raised).
"""

from __future__ import annotations

import math
import os
import sqlite3
import time
from dataclasses import dataclass, field

from adaptive_scalper.backtest.dataset import compute_bars_checksum
from adaptive_scalper.backtest.engine import run_backtest
from adaptive_scalper.backtest.fingerprint import compute_config_fingerprint
from adaptive_scalper.backtest.persistence import record_backtest_run
from adaptive_scalper.backtest.types import BacktestConfig, BacktestResult, CandidateRecord
from adaptive_scalper.backtest.walk_forward import _fold_ranges

# Research never imports the gateway package (tests/test_research_validation.py): bars are
# gateway `Bar` records and the spec a `SymbolSpec`, passed through untouched.
Bars = list
Spec = object
from adaptive_scalper.research.ledger import family_sharpe_variance, family_trial_count, record_trial
from adaptive_scalper.research.stats import (
    deflated_sharpe_ratio,
    probabilistic_sharpe_ratio,
    probability_of_backtest_overfitting,
    return_moments,
)
from adaptive_scalper.research.trade_analysis import TradeView, from_simulated, summarize
from adaptive_scalper.strategies import select_active_strategies

# The reserved OOS holdout and its guard live in `backtest.reserved_oos` so the
# engine enforces them too (defense in depth); re-exported here unchanged.
from adaptive_scalper.backtest.reserved_oos import (  # noqa: E402,F401
    RESERVED_OOS_INTERVALS,
    ReservedOosOverlapError,
    assert_outside_reserved_oos,
)

SELECTOR_SESSION = "__selector__"
INDEPENDENT_TRIAL_KIND = "INDEPENDENT_STRATEGY"


class ProductionDatabaseError(ValueError):
    """A research study was about to be written to the production database."""


def _same_file(a: str, b: str) -> bool:
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def assert_research_database(conn: sqlite3.Connection, production_db_path: str | None) -> None:
    """Refuse to persist research into the production database file."""
    if production_db_path is None:
        return
    row = conn.execute("PRAGMA database_list").fetchone()
    target = row[2] if row is not None else ""
    if target and _same_file(target, production_db_path):
        raise ProductionDatabaseError(
            f"refusing to write research results into the production database {production_db_path!r}; "
            "use a separate research database (a snapshot copy)"
        )


@dataclass(frozen=True)
class SessionResult:
    session: str                     # strategy key, or SELECTOR_SESSION for the combined run
    strategy_keys: tuple[str, ...]
    canonical_symbol: str
    resolution: str
    config_fingerprint: str
    bars_checksum: str
    folds: tuple[BacktestResult, ...]
    fold_index_ranges: tuple[tuple[int, int], ...]
    error: str | None = None

    @property
    def trades(self) -> list[TradeView]:
        out = []
        for fold in self.folds:
            for trade in fold.trades:
                view = from_simulated(trade, self.canonical_symbol, source=self.session)
                if view is not None:
                    out.append(view)
        return out

    @property
    def halted_folds(self) -> int:
        return sum(1 for f in self.folds if f.risk_halted_scans > 0)

    @property
    def max_fold_drawdown(self) -> float:
        return max((f.metrics.max_drawdown for f in self.folds), default=0.0)

    @property
    def entry_rejections(self) -> int:
        return sum(len(f.entry_rejections) for f in self.folds)


@dataclass
class IndependentStudy:
    canonical_symbol: str
    resolution: str
    range_start_utc: int
    range_end_utc: int
    bars_checksum: str
    n_folds: int
    sessions: dict[str, SessionResult] = field(default_factory=dict)
    candidates: list[CandidateRecord] = field(default_factory=list)

    @property
    def inputs_identical(self) -> bool:
        return all(s.bars_checksum == self.bars_checksum for s in self.sessions.values())


def fold_index_ranges(n_bars: int, n_folds: int, feature_lookback: int) -> list[tuple[int, int]]:
    return _fold_ranges(n_bars, n_folds, feature_lookback + 3, 0) if n_folds > 1 else [(0, n_bars)]


def _run_session(
    session: str, strategy_keys: tuple[str, ...] | None, bars: Bars, ranges: list[tuple[int, int]],
    canonical_symbol: str, resolution: str, spec: Spec, config: BacktestConfig, now: int,
    candidate_log: list[CandidateRecord] | None, bars_checksum: str,
) -> SessionResult:
    strategies = select_active_strategies(strategy_keys)
    fingerprint, _ = compute_config_fingerprint(
        config, canonical_symbol=canonical_symbol, resolutions=(resolution,),
        strategies=tuple((s.key, s.version) for s in strategies),
    )
    folds = tuple(
        run_backtest(bars[start:end], canonical_symbol, resolution, spec, config=config, now_utc=now,
                     strategy_keys=strategy_keys, candidate_log=candidate_log)
        for start, end in ranges
    )
    return SessionResult(
        session=session, strategy_keys=tuple(s.key for s in strategies), canonical_symbol=canonical_symbol,
        resolution=resolution, config_fingerprint=fingerprint, bars_checksum=bars_checksum,
        folds=folds, fold_index_ranges=tuple(ranges),
    )


def run_independent_study(
    bars: Bars, canonical_symbol: str, resolution: str, spec: Spec, *,
    config: BacktestConfig, n_folds: int = 1, strategies: tuple[str, ...] | None = None,
    include_selector: bool = True, now_utc: int | None = None,
) -> IndependentStudy:
    """Runs one independent session per strategy (default: all six) plus,
    optionally, the combined selector session, over the SAME bars and the
    SAME fold boundaries. `n_folds > 1` evaluates each session as a
    sequential fixed-configuration walk (each fold starts flat with fresh
    equity, like `run_walk_forward`). A session that raises is kept as a
    failed session (its error recorded), never silently dropped."""
    if len(bars) < config.feature_lookback + 3:
        raise ValueError(f"need at least {config.feature_lookback + 3} bars, got {len(bars)}")
    assert_outside_reserved_oos(bars[0].time, bars[-1].time)
    now = now_utc if now_utc is not None else int(time.time())
    keys = tuple(s.key for s in select_active_strategies(strategies))
    ranges = fold_index_ranges(len(bars), n_folds, config.feature_lookback)
    checksum = compute_bars_checksum(bars)
    study = IndependentStudy(
        canonical_symbol=canonical_symbol, resolution=resolution, range_start_utc=bars[0].time,
        range_end_utc=bars[-1].time, bars_checksum=checksum, n_folds=len(ranges),
    )
    for key in keys:
        try:
            study.sessions[key] = _run_session(
                key, (key,), bars, ranges, canonical_symbol, resolution, spec, config, now, None, checksum,
            )
        except Exception as exc:  # recorded as a FAILED trial by persist_study
            study.sessions[key] = SessionResult(
                session=key, strategy_keys=(key,), canonical_symbol=canonical_symbol, resolution=resolution,
                config_fingerprint="", bars_checksum=checksum, folds=(), fold_index_ranges=(),
                error=f"{type(exc).__name__}: {exc}",
            )
    if include_selector:
        study.sessions[SELECTOR_SESSION] = _run_session(
            SELECTOR_SESSION, None, bars, ranges, canonical_symbol, resolution, spec, config, now,
            study.candidates, checksum,
        )
    return study


def session_statistics(result: SessionResult) -> dict:
    """PSR against zero on per-trade net R, plus fold stability. Undefined
    statistics (too few trades, zero variance) are None with the reason."""
    returns = [t.r(t.net) for t in result.trades if t.initial_risk > 0]
    out: dict = {"n": len(returns), "sharpe": None, "skew": None, "kurtosis": None, "psr_vs_zero": None,
                 "note": None}
    try:
        moments = return_moments(returns)
        out.update(sharpe=moments.sharpe, skew=moments.skew, kurtosis=moments.kurtosis)
        out["psr_vs_zero"] = probabilistic_sharpe_ratio(moments.sharpe, 0.0, moments.n, moments.skew,
                                                        moments.kurtosis)
    except ValueError as exc:
        out["note"] = str(exc)
    fold_net = [sum(t.realized_pnl or 0.0 for t in f.trades if t.exit_time_utc is not None) for f in result.folds]
    out["fold_net_pnl"] = fold_net
    out["positive_folds"] = sum(1 for v in fold_net if v > 0)
    out["folds"] = len(fold_net)
    return out


def pbo_across_strategies(study: IndependentStudy, *, n_blocks: int = 12, n_groups: int = 6) -> dict:
    """Probability of backtest overfitting of "pick the strategy that did
    best in-sample": a T x N matrix of summed net R per equal time block
    (T blocks) and per independent strategy session (N), via CSCV."""
    keys = [k for k, s in study.sessions.items() if k != SELECTOR_SESSION and s.error is None]
    span = study.range_end_utc - study.range_start_utc + 1
    matrix = [[0.0] * len(keys) for _ in range(n_blocks)]
    for j, key in enumerate(keys):
        for t in study.sessions[key].trades:
            if t.initial_risk <= 0:
                continue
            block = min(n_blocks - 1, max(0, (t.entry_time_utc - study.range_start_utc) * n_blocks // span))
            matrix[block][j] += t.r(t.net)
    result = probability_of_backtest_overfitting(matrix, n_groups)
    return {"computable": result.computable, "pbo": result.pbo, "combinations": result.n_combinations,
            "reason": result.reason, "strategies": keys, "blocks": n_blocks, "groups": n_groups}


def trial_family(canonical_symbol: str, session: str) -> str:
    return f"selector:{canonical_symbol}" if session == SELECTOR_SESSION else f"independent:{canonical_symbol}"


def persist_study(
    conn: sqlite3.Connection, study: IndependentStudy, bars: Bars, *, run_tag: str,
    production_db_path: str | None, now_utc: int | None = None,
) -> dict[str, str]:
    """Records every session's folds (`backtest_runs`/`backtest_trades`,
    run_type WALK_FORWARD_FOLD) and one research trial per session in the
    family `independent:<symbol>` (the selector session: `selector:<symbol>`)
    -- failed sessions included, so the multiple-testing count behind DSR is
    honest. Returns session -> run id prefix. Refuses the production DB."""
    assert_research_database(conn, production_db_path)
    now = now_utc if now_utc is not None else int(time.time())
    prefixes: dict[str, str] = {}
    for session, result in study.sessions.items():
        prefix = f"indep:{run_tag}:{study.canonical_symbol}:{study.resolution}:{session}"
        prefixes[session] = prefix
        versions = {s.key: s.version for s in select_active_strategies(result.strategy_keys)}
        family = trial_family(study.canonical_symbol, session)
        params = {"session": session, "folds": study.n_folds, "range": [study.range_start_utc, study.range_end_utc],
                  "bars_checksum": study.bars_checksum}
        if result.error is not None:
            record_trial(conn, trial_id=prefix, family=family, kind=INDEPENDENT_TRIAL_KIND,
                         strategy_versions=versions, params=params, status="FAILED", notes=result.error,
                         now_utc=now)
            continue
        for i, (fold, (start, end)) in enumerate(zip(result.folds, result.fold_index_ranges)):
            record_backtest_run(
                conn, fold, bars[start:end], run_id=f"{prefix}:fold{i}", run_type="WALK_FORWARD_FOLD",
                used_for="WALK_FORWARD_FOLD", strategies=result.strategy_keys, feature_schema_version=1,
                now_utc=now,
            )
        stats = session_statistics(result)
        summary = summarize(result.trades)
        record_trial(
            conn, trial_id=prefix, family=family, kind=INDEPENDENT_TRIAL_KIND, strategy_versions=versions,
            params=params, status="COMPLETED", sharpe=stats["sharpe"], n_observations=stats["n"],
            result={"net_pnl": summary["net_pnl"], "gross_pnl": summary["gross_pnl"],
                    "total_cost": summary["total_cost"], "avg_net_r": summary["avg_net_r"],
                    "psr_vs_zero": stats["psr_vs_zero"], "config_fingerprint": result.config_fingerprint},
            now_utc=now,
        )
    return prefixes


# Trials with fewer closed trades than this do not enter the cross-trial
# Sharpe VARIANCE behind DSR (their Sharpe is estimation noise); they still
# count in the number of trials.
DSR_VARIANCE_MIN_OBSERVATIONS = 30


def deflated_sharpe_for_family(
    conn: sqlite3.Connection, family: str, sharpe: float | None, n: int, skew: float = 0.0, kurtosis: float = 3.0,
) -> dict:
    """DSR of one session against every trial recorded in its family
    (earlier runs and FAILED trials count toward the number of trials)."""
    trials = family_trial_count(conn, family)
    variance = family_sharpe_variance(conn, family, min_observations=DSR_VARIANCE_MIN_OBSERVATIONS)
    if sharpe is None or variance is None or n < 2:
        return {"dsr": None, "family_trials": trials,
                "note": "not computable: needs a Sharpe and >= 2 completed trials with a Sharpe in the family"}
    try:
        dsr = deflated_sharpe_ratio(sharpe, n, n_trials=max(1, trials), trial_sharpe_variance=variance,
                                    skew=skew, kurtosis=kurtosis)
    except ValueError as exc:
        return {"dsr": None, "family_trials": trials, "note": str(exc)}
    return {"dsr": dsr, "family_trials": trials, "trial_sharpe_std": math.sqrt(variance), "note": None}
