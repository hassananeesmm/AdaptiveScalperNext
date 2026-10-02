"""Runs the pre-registered V2 grid (docs/research/V2_PREREGISTRATION_2026-09-29.md)
over ONE symbol's development bars, next to the frozen V1 baseline on the
identical bars and fold boundaries.

Pure with respect to the broker: bars/spec/config come in, results and
trial-ledger rows (research DB only) go out. Protected OOS is refused by
`assert_outside_reserved_oos`; the research DB must not be production.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from adaptive_scalper.backtest.dataset import compute_bars_checksum
from adaptive_scalper.backtest.engine import run_backtest
from adaptive_scalper.backtest.types import BacktestConfig, BacktestResult
from adaptive_scalper.history.resolutions import resolution_seconds
from adaptive_scalper.research.independent import (
    SELECTOR_SESSION,
    assert_outside_reserved_oos,
    assert_research_database,
    fold_index_ranges,
)
from adaptive_scalper.research.ledger import record_trial
from adaptive_scalper.research.stats import probability_of_backtest_overfitting
from adaptive_scalper.research.trade_analysis import from_simulated
from adaptive_scalper.research.v2.calibration import fit_calibration
from adaptive_scalper.research.v2.holding import HOLDING_THESES
from adaptive_scalper.research.v2.selectors import calibrated_selector, friction_filter_selector
from adaptive_scalper.strategies import select_active_strategies

V2_TRIAL_KIND = "RESEARCH_V2"
H2_K_VALUES = (3.0, 5.0)
H2_COOLDOWN_BARS = 6
H3_MARGINS = (1.0, 1.5)


@dataclass
class V2Session:
    """One session (V1 baseline or a V2 trial) over the identical folds."""
    family: str                 # "v1" | "H1" | "H2" | "H3"
    variant: str                # e.g. "V1", "H1-DIR", "H2-K3", "H3-M1.0"
    strategy: str               # strategy key or SELECTOR_SESSION
    folds: tuple[BacktestResult, ...]
    error: str | None = None
    extra: dict | None = None

    @property
    def label(self) -> str:
        return f"{self.variant}:{self.strategy}"

    def views(self, symbol: str):
        out = []
        for fold in self.folds:
            for trade in fold.trades:
                view = from_simulated(trade, symbol, source=self.label)
                if view is not None:
                    out.append(view)
        return out


def _folds(bars, ranges, symbol, resolution, spec, config, now, **kwargs) -> tuple[BacktestResult, ...]:
    return tuple(run_backtest(bars[a:b], symbol, resolution, spec, config=config, now_utc=now, **kwargs)
                 for a, b in ranges)


def _guarded(family, variant, strategy, fn) -> V2Session:
    try:
        folds, extra = fn()
        return V2Session(family, variant, strategy, folds, extra=extra)
    except Exception as exc:  # a failed trial is recorded as FAILED, never dropped
        return V2Session(family, variant, strategy, (), error=f"{type(exc).__name__}: {exc}")


def run_v2_study(bars, symbol: str, resolution: str, spec, *, config: BacktestConfig,
                 n_folds: int = 12, now_utc: int | None = None, progress=None) -> dict[str, V2Session]:
    if len(bars) < config.feature_lookback + 3:
        raise ValueError("too few bars")
    assert_outside_reserved_oos(bars[0].time, bars[-1].time)
    now = now_utc if now_utc is not None else int(time.time())
    ranges = fold_index_ranges(len(bars), n_folds, config.feature_lookback)
    keys = tuple(s.key for s in select_active_strategies(None))
    bar_seconds = resolution_seconds(resolution)
    sessions: dict[str, V2Session] = {}

    def add(session: V2Session) -> None:
        sessions[session.label] = session
        if progress is not None:
            progress(session)

    # V1 baseline (frozen live logic) -- the reference for every comparison
    # AND the out-of-fold calibration source for H3.
    for key in keys:
        add(_guarded("v1", "V1", key, lambda key=key: (
            _folds(bars, ranges, symbol, resolution, spec, config, now, strategy_keys=(key,)), None)))
    add(_guarded("v1", "V1", SELECTOR_SESSION, lambda: (
        _folds(bars, ranges, symbol, resolution, spec, config, now), None)))

    for variant, thesis in HOLDING_THESES.items():
        for key in keys:
            add(_guarded("H1", variant, key, lambda key=key, variant=variant, thesis=thesis: (
                _folds(bars, ranges, symbol, resolution, spec, config, now, strategy_keys=(key,),
                       research_holding_thesis=thesis, research_variant=variant), None)))

    for k in H2_K_VALUES:
        variant = f"H2-K{k:g}"
        selector = friction_filter_selector(k, cooldown_bars=H2_COOLDOWN_BARS, bar_seconds=bar_seconds)
        for key in keys:
            add(_guarded("H2", variant, key, lambda key=key, variant=variant, selector=selector: (
                _folds(bars, ranges, symbol, resolution, spec, config, now, strategy_keys=(key,),
                       research_selector=selector, research_variant=variant), None)))

    v1_views = {key: sessions[f"V1:{key}"].views(symbol) for key in keys if sessions[f"V1:{key}"].error is None}
    for margin in H3_MARGINS:
        variant = f"H3-M{margin:g}"

        def run_h3(margin=margin, variant=variant):
            folds, calibration_log = [], []
            for index, (a, b) in enumerate(ranges):
                fold_start = bars[a].time
                cals = {key: fit_calibration(v1_views.get(key, ()), key, before_utc=fold_start) for key in keys}
                calibration_log.append({
                    "fold": index, "fold_start_utc": fold_start,
                    "calibrated": {k: (c.n if c is not None else None) for k, c in cals.items()},
                })
                folds.append(run_backtest(bars[a:b], symbol, resolution, spec, config=config, now_utc=now,
                                          research_selector=calibrated_selector(cals, margin=margin),
                                          research_variant=f"{variant}:fold{index}"))
            return tuple(folds), {"calibration_by_fold": calibration_log}

        add(_guarded("H3", variant, SELECTOR_SESSION, run_h3))
    return sessions


def pbo_for_family(sessions: list[V2Session], symbol: str, range_start: int, range_end: int,
                   *, n_blocks: int = 12, n_groups: int = 6) -> dict:
    usable = [s for s in sessions if s.error is None]
    if len(usable) < 2:
        return {"computable": False, "reason": "fewer than two completed variants"}
    span = range_end - range_start + 1
    matrix = [[0.0] * len(usable) for _ in range(n_blocks)]
    for j, session in enumerate(usable):
        for t in session.views(symbol):
            if t.initial_risk > 0:
                block = min(n_blocks - 1, max(0, (t.entry_time_utc - range_start) * n_blocks // span))
                matrix[block][j] += t.net / t.initial_risk
    result = probability_of_backtest_overfitting(matrix, n_groups)
    return {"computable": result.computable, "pbo": result.pbo, "combinations": result.n_combinations,
            "reason": result.reason, "variants": [s.label for s in usable]}


def record_v2_trials(conn, sessions: dict[str, V2Session], symbol: str, bars, *, run_tag: str,
                     production_db_path: str | None, stats_fn, now_utc: int) -> None:
    """Every V2 trial (failed ones included) goes into the research ledger
    under family `v2-<H>:<symbol>`; V1 baseline reruns are not new trials."""
    assert_research_database(conn, production_db_path)
    checksum = compute_bars_checksum(bars)
    for session in sessions.values():
        if session.family == "v1":
            continue
        trial_id = f"v2:{run_tag}:{symbol}:{session.label}"
        family = f"v2-{session.family}:{symbol}"
        versions = {s.key: s.version for s in select_active_strategies(None)}
        params = {"variant": session.variant, "strategy": session.strategy, "bars_checksum": checksum,
                  "range": [bars[0].time, bars[-1].time], "folds": len(session.folds)}
        if session.error is not None:
            record_trial(conn, trial_id=trial_id, family=family, kind=V2_TRIAL_KIND, strategy_versions=versions,
                         params=params, status="FAILED", notes=session.error, now_utc=now_utc)
            continue
        stats = stats_fn(session)
        fingerprints = sorted({f.config_fingerprint for f in session.folds})
        record_trial(conn, trial_id=trial_id, family=family, kind=V2_TRIAL_KIND, strategy_versions=versions,
                     params=params, status="COMPLETED", sharpe=stats["sharpe"], n_observations=stats["n"],
                     result={"avg_net_r": stats.get("avg_net_r"), "psr_vs_zero": stats["psr_vs_zero"],
                             "config_fingerprints": fingerprints},
                     now_utc=now_utc)
