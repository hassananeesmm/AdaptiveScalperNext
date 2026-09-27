"""Offline research commands over the stored history (no MT5 needed once
`history bootstrap` and `symbols` have run on the laptop): backtest,
walk-forward, oos, path-stress, purged-validation.

Every run uses the same causal engine and cost model as PAPER, with the
configured `[costs.SYMBOL]` evidence (unknown -> 0.0 labelled
UNVERIFIED_ASSUMPTION), news windows from the cached calendar where the
cache covers the range (reported), and records itself in the dataset
usage ledger and/or the append-only research trial ledger.
"""

from __future__ import annotations

import argparse
import dataclasses
import time

from adaptive_scalper.backtest.types import BacktestResult
from adaptive_scalper.cli.common import CliError, add_symbol_arg, open_db, parse_utc, print_json
from adaptive_scalper.gateway.spec_store import load_symbol_spec
from adaptive_scalper.history.store import get_bars
from adaptive_scalper.research.ledger import family_sharpe_variance, family_trial_count, record_trial
from adaptive_scalper.strategies import build_active_registry


def _inputs(args, conn, cfg):
    stored = load_symbol_spec(conn, args.symbol)
    if stored is None:
        raise CliError(f"no stored symbol spec for {args.symbol}: run `symbols` or `history bootstrap` on the laptop")
    start, end = parse_utc(args.start), parse_utc(args.end)
    bars = get_bars(conn, args.symbol, args.resolution, start, end)
    if len(bars) < 100:
        raise CliError(f"only {len(bars)} stored {args.resolution} bars for {args.symbol} in range; "
                       "run `history bootstrap` first")
    from adaptive_scalper.news.blocking import _event_applies_to_symbol, _event_window
    from adaptive_scalper.news.providers.cache import load_cached_events
    from adaptive_scalper.runtime.paper import paper_config

    events = [e for e in load_cached_events(conn, since_utc=start) if e.scheduled_at_utc <= end
              and e.impact.value == "HIGH" and _event_applies_to_symbol(e, args.symbol)]
    windows = tuple(sorted(_event_window(e, cfg.news.pre_high_impact_minutes, cfg.news.post_high_impact_minutes)
                           for e in events))
    config = paper_config(cfg, args.symbol, windows)
    config = dataclasses.replace(config, initial_equity=args.equity)
    return bars, stored[0], config, len(windows)


def _summary(result: BacktestResult) -> dict:
    return {
        "dataset_id": result.dataset_id, "range": [result.range_start_utc, result.range_end_utc],
        "metrics": dataclasses.asdict(result.metrics), "trades": len(result.trades),
        "entry_rejections": len(result.entry_rejections), "risk_halted_scans": result.risk_halted_scans,
        "cost_provenance": result.cost_provenance, "fill_model_version": result.fill_model_version,
        "config_fingerprint": result.config_fingerprint, "origin": result.origin.value,
        "news_limitation_note": result.news_limitation_note,
    }


def _versions() -> dict[str, int]:
    return {s.key: s.version for s in build_active_registry().all_active()}


def cmd_backtest(args: argparse.Namespace) -> int:
    from adaptive_scalper.backtest.engine import run_backtest
    from adaptive_scalper.backtest.persistence import record_backtest_run

    cfg, conn = open_db(args.config, require_utc_history=True)
    bars, spec, config, news = _inputs(args, conn, cfg)
    now = int(time.time())
    result = run_backtest(bars, args.symbol, args.resolution, spec, config=config, now_utc=now)
    run_id = f"backtest:{args.symbol}:{args.resolution}:{now}"
    record_backtest_run(conn, result, bars, run_id=run_id, run_type="BACKTEST", used_for="VALIDATION",
                        strategies=tuple(_versions()), feature_schema_version=1, now_utc=now)
    record_trial(conn, trial_id=run_id, family=f"strategy_set:{args.symbol}", kind="BACKTEST",
                 strategy_versions=_versions(), params={"resolution": args.resolution, "start": args.start,
                                                        "end": args.end}, status="COMPLETED",
                 dataset_id=result.dataset_id, n_observations=len(result.trades),
                 result={"net_pnl": result.metrics.net_pnl, "avg_r": result.metrics.avg_r}, now_utc=now)
    conn.close()
    print_json({"run_id": run_id, "news_windows_applied": news, **_summary(result),
                "note": "this range is now recorded as VALIDATION data and can never serve as untouched OOS"})
    return 0


def cmd_walk_forward(args: argparse.Namespace) -> int:
    from adaptive_scalper.backtest.walk_forward import run_walk_forward

    cfg, conn = open_db(args.config, require_utc_history=True)
    bars, spec, config, news = _inputs(args, conn, cfg)
    now = int(time.time())
    prefix = f"wf:{args.symbol}:{args.resolution}:{now}"
    result = run_walk_forward(bars, args.symbol, args.resolution, spec, config=config, n_folds=args.folds,
                              embargo_bars=args.embargo_bars, conn=conn, run_id_prefix=prefix, now_utc=now)
    record_trial(conn, trial_id=prefix, family=f"strategy_set:{args.symbol}", kind="SEQUENTIAL_FOLDS",
                 strategy_versions=_versions(), params={"folds": args.folds, "embargo_bars": args.embargo_bars},
                 status="COMPLETED", n_observations=result.aggregate_metrics.trade_count,
                 result={"net_pnl": result.aggregate_metrics.net_pnl}, now_utc=now)
    conn.close()
    print_json({
        "run_id_prefix": prefix, "evaluation_kind": result.evaluation_kind, "news_windows_applied": news,
        "aggregate": dataclasses.asdict(result.aggregate_metrics),
        "folds": [{"fold": f.fold_index, "range": [f.range_start_utc, f.range_end_utc],
                   "net_pnl": f.result.metrics.net_pnl, "trades": len(f.result.trades)} for f in result.folds],
        "note": "fixed-config stability check across sequential folds; nothing is re-fit between folds",
    })
    return 0


def cmd_oos(args: argparse.Namespace) -> int:
    from adaptive_scalper.backtest.oos import DatasetContaminatedError, run_untouched_oos

    cfg, conn = open_db(args.config, require_utc_history=True)
    bars, spec, config, news = _inputs(args, conn, cfg)
    now = int(time.time())
    run_id = f"oos:{args.symbol}:{args.resolution}:{now}"
    try:
        result = run_untouched_oos(bars, args.symbol, args.resolution, spec, conn=conn, run_id=run_id,
                                   config=config, allow_oos_reuse=args.analysis_reuse, now_utc=now)
    except DatasetContaminatedError as exc:
        conn.close()
        raise CliError(f"refused: {exc}") from exc
    conn.close()
    print_json({"run_id": run_id, "analysis_reuse": args.analysis_reuse, "news_windows_applied": news,
                **_summary(result),
                "note": ("ANALYSIS REUSE: not untouched OOS evidence" if args.analysis_reuse
                         else "untouched OOS: this range is now spent and can never be used as OOS again")})
    return 0


def _latest_run_trades(conn, symbol: str, run_id: str | None):
    from adaptive_scalper.backtest.types import SimulatedTrade

    if run_id is None:
        row = conn.execute("SELECT run_id FROM backtest_runs WHERE canonical_symbol = ? AND run_type = 'BACKTEST' "
                           "ORDER BY created_at_utc DESC, id DESC LIMIT 1", (symbol,)).fetchone()
        if row is None:
            raise CliError(f"no recorded backtest run for {symbol}: run `backtest` first")
        run_id = row["run_id"]
    rows = conn.execute("SELECT * FROM backtest_trades WHERE run_id = ? ORDER BY entry_time_utc", (run_id,)).fetchall()
    fields = {f.name for f in dataclasses.fields(SimulatedTrade)}
    trades = tuple(SimulatedTrade(**{k: r[k] for k in r.keys() if k in fields}) for r in rows
                   if r["realized_pnl"] is not None)
    return run_id, trades


def cmd_path_stress(args: argparse.Namespace) -> int:
    from adaptive_scalper.backtest.path_stress import run_trade_order_path_stress

    _, conn = open_db(args.config)
    run_id, trades = _latest_run_trades(conn, args.symbol, args.run_id)
    conn.close()
    if not trades:
        raise CliError(f"run {run_id} has no closed trades")
    result = run_trade_order_path_stress(trades, initial_equity=args.equity, n_simulations=args.simulations,
                                         seed=args.seed)
    print_json({"run_id": run_id, **dataclasses.asdict(result)})
    return 0


def cmd_purged_validation(args: argparse.Namespace) -> int:
    from adaptive_scalper.research.splits import LabelInterval, purged_kfold
    from adaptive_scalper.research.stats import deflated_sharpe_ratio, probabilistic_sharpe_ratio, return_moments

    _, conn = open_db(args.config)
    run_id, trades = _latest_run_trades(conn, args.symbol, args.run_id)
    trades = [t for t in trades if t.realized_r is not None and t.exit_time_utc is not None]
    if len(trades) < max(3 * args.folds, 10):
        conn.close()
        raise CliError(f"run {run_id} has only {len(trades)} closed trades with R; need >= {max(3 * args.folds, 10)}")
    intervals = [LabelInterval(t.entry_time_utc, t.exit_time_utc) for t in trades]
    returns = [t.realized_r for t in trades]
    splits = purged_kfold(intervals, args.folds, embargo_seconds=args.embargo_seconds)
    folds = []
    for i, split in enumerate(splits):
        test = [returns[j] for j in split.test]
        folds.append({"fold": i, "test_trades": len(test), "mean_r": sum(test) / len(test),
                      "purged": len(split.purged), "embargoed": len(split.embargoed)})
    moments = return_moments(returns)
    family = f"strategy_set:{args.symbol}"
    n_trials = max(1, family_trial_count(conn, family))
    variance = family_sharpe_variance(conn, family)
    psr = probabilistic_sharpe_ratio(moments.sharpe, 0.0, moments.n, moments.skew, moments.kurtosis)
    dsr = (deflated_sharpe_ratio(moments.sharpe, moments.n, n_trials=n_trials, trial_sharpe_variance=variance,
                                 skew=moments.skew, kurtosis=moments.kurtosis) if variance is not None else None)
    now = int(time.time())
    record_trial(conn, trial_id=f"purged-cv:{run_id}:{now}", family=family, kind="PURGED_CV",
                 strategy_versions=_versions(), params={"run_id": run_id, "folds": args.folds,
                                                        "embargo_seconds": args.embargo_seconds},
                 status="COMPLETED", sharpe=moments.sharpe, n_observations=moments.n,
                 result={"psr": psr, "dsr": dsr, "folds": folds}, now_utc=now)
    conn.close()
    print_json({
        "run_id": run_id, "trades": moments.n, "per_trade_sharpe": moments.sharpe, "psr_vs_zero": psr,
        "dsr": dsr, "family_trials": n_trials,
        "dsr_note": None if dsr is not None else "fewer than two completed trials in the family: DSR not computable",
        "folds": folds, "positive_folds": sum(1 for f in folds if f["mean_r"] > 0),
    })
    return 0


def open_research_db(config_path: str, research_db: str):
    """The separate research database (a snapshot copy of production).
    Refuses the production database path: research never writes there."""
    import os

    from adaptive_scalper.config.loader import load_config
    from adaptive_scalper.history.time_basis import TimeBasisError, require_utc
    from adaptive_scalper.persistence.database import connect, migrate
    from adaptive_scalper.research.independent import _same_file

    cfg = load_config(config_path)
    if _same_file(research_db, cfg.database.path):
        raise CliError(f"--research-db {research_db!r} is the production database; research needs a separate copy")
    if not os.path.exists(research_db):
        raise CliError(f"research database {research_db!r} does not exist; create it with `research-snapshot`")
    conn = connect(research_db)
    migrate(conn)
    try:
        require_utc(conn)
    except TimeBasisError as exc:
        conn.close()
        raise CliError(str(exc)) from exc
    return cfg, conn


def cmd_research_snapshot(args: argparse.Namespace) -> int:
    """Online SQLite backup of the production database into a NEW research
    file. The source is opened read-only; the running runtime is not
    stopped or blocked beyond SQLite's normal reader behaviour."""
    import os
    import sqlite3

    from adaptive_scalper.config.loader import load_config
    from adaptive_scalper.research.independent import _same_file

    cfg = load_config(args.config)
    if _same_file(args.out, cfg.database.path):
        raise CliError("--out must not be the production database")
    if os.path.exists(args.out):
        raise CliError(f"{args.out!r} already exists; research snapshots are never overwritten")
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    src = sqlite3.connect(f"file:{os.path.abspath(cfg.database.path)}?mode=ro", uri=True)
    dst = sqlite3.connect(args.out)
    try:
        src.backup(dst)
        checks = {name: c.execute("PRAGMA quick_check").fetchone()[0] for name, c in (("source", src), ("copy", dst))}
        counts = {t: [c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for c in (src, dst)]
                  for t in ("bars", "positions", "broker_account_deals", "backtest_trades", "research_trials")}
    finally:
        src.close()
        dst.close()
    ok = all(v == "ok" for v in checks.values()) and all(a == b for a, b in counts.values())
    print_json({"snapshot": args.out, "quick_check": checks, "row_counts_source_copy": counts, "verified": ok})
    return 0 if ok else 1


def cmd_independent_research(args: argparse.Namespace) -> int:
    """One independent session per active strategy (plus the combined
    selector session) over the SAME stored bars, in the separate research
    database. Never touches a broker or the production DB."""
    import json
    import os

    from adaptive_scalper.research.independent import (
        SELECTOR_SESSION,
        ReservedOosOverlapError,
        deflated_sharpe_for_family,
        pbo_across_strategies,
        persist_study,
        run_independent_study,
        session_statistics,
        trial_family,
    )
    from adaptive_scalper.research.selector_study import selector_study
    from adaptive_scalper.research.trade_analysis import calibration, full_breakdown, summarize

    cfg, conn = open_research_db(args.config, args.research_db)
    try:
        bars, spec, config, news = _inputs(args, conn, cfg)
        now = int(time.time())
        strategies = tuple(args.strategy) if args.strategy else None
        try:
            study = run_independent_study(bars, args.symbol, args.resolution, spec, config=config,
                                          n_folds=args.folds, strategies=strategies, now_utc=now)
        except ReservedOosOverlapError as exc:
            raise CliError(str(exc)) from exc
        tag = args.tag or str(now)
        prefixes = persist_study(conn, study, bars, run_tag=tag, production_db_path=cfg.database.path, now_utc=now)
        sessions = {}
        for key, result in study.sessions.items():
            if result.error is not None:
                sessions[key] = {"error": result.error}
                continue
            stats = session_statistics(result)
            trades = result.trades
            sessions[key] = {
                "run_id_prefix": prefixes[key], "config_fingerprint": result.config_fingerprint,
                "bars_checksum": result.bars_checksum, "summary": summarize(trades),
                "statistics": stats,
                "dsr": deflated_sharpe_for_family(conn, trial_family(args.symbol, key), stats["sharpe"], stats["n"],
                                                  stats.get("skew") or 0.0, stats.get("kurtosis") or 3.0),
                "halted_folds": result.halted_folds, "max_fold_drawdown": result.max_fold_drawdown,
                "entry_rejections": result.entry_rejections,
                "final_equity_by_fold": [f.metrics.final_equity for f in result.folds],
                "breakdown": full_breakdown(trades), "calibration": calibration(trades),
            }
        report = {
            "kind": "INDEPENDENT_STRATEGY_RESEARCH", "origin": "BACKTEST",
            "symbol": args.symbol, "resolution": args.resolution, "range": [study.range_start_utc, study.range_end_utc],
            "bars": len(bars), "bars_checksum": study.bars_checksum, "inputs_identical": study.inputs_identical,
            "folds": study.n_folds, "news_windows_applied": news, "cost_provenance": config.fill_assumptions.provenance,
            "initial_equity_per_session": config.initial_equity, "tag": tag, "created_at_utc": now,
            "sessions": sessions, "selector_session": SELECTOR_SESSION,
            "pbo": pbo_across_strategies(study), "selector_study": selector_study(study),
            "notes": [
                "each session is independent: own positions, risk state, equity, drawdown and costs; never pooled",
                "every fold starts flat at the initial equity; live risk ceilings apply unchanged",
                "reserved OOS 2026-07-01..2026-09-18 refused by construction",
            ],
        }
    finally:
        conn.close()
    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=1, default=str)
    print_json({
        "symbol": args.symbol, "folds": report["folds"], "inputs_identical": report["inputs_identical"],
        "out": args.out,
        "sessions": {k: ({"error": v["error"]} if "error" in v else {
            "trades": v["summary"]["trades"], "net": round(v["summary"]["net_pnl"], 2),
            "gross": round(v["summary"]["gross_pnl"], 2), "cost": round(v["summary"]["total_cost"], 2),
            "avg_net_r": v["summary"]["avg_net_r"], "psr": v["statistics"]["psr_vs_zero"], "dsr": v["dsr"]["dsr"]})
            for k, v in report["sessions"].items()},
        "pbo": report["pbo"]["pbo"],
    })
    return 0


def _range_args(p: argparse.ArgumentParser) -> None:
    add_symbol_arg(p)
    p.add_argument("--resolution", default="M5")
    p.add_argument("--start", required=True, help="epoch seconds or ISO date (UTC)")
    p.add_argument("--end", required=True, help="epoch seconds or ISO date (UTC)")
    p.add_argument("--equity", type=float, default=10_000.0)


def register(sub) -> None:
    bt = sub.add_parser("backtest", help="causal backtest over stored history (recorded as VALIDATION)")
    _range_args(bt)
    bt.set_defaults(func=cmd_backtest)

    wf = sub.add_parser("walk-forward", help="fixed-config sequential-fold stability check")
    _range_args(wf)
    wf.add_argument("--folds", type=int, default=5)
    wf.add_argument("--embargo-bars", type=int, default=0)
    wf.set_defaults(func=cmd_walk_forward)

    oos = sub.add_parser("oos", help="ONE-SHOT untouched out-of-sample run (refuses contaminated/spent ranges)")
    _range_args(oos)
    oos.add_argument("--analysis-reuse", action="store_true", help="explicit, recorded non-evidence re-analysis")
    oos.set_defaults(func=cmd_oos)

    ps = sub.add_parser("path-stress", help="trade-order path stress of a recorded backtest run")
    add_symbol_arg(ps)
    ps.add_argument("--run-id")
    ps.add_argument("--equity", type=float, default=10_000.0)
    ps.add_argument("--simulations", type=int, default=1000)
    ps.add_argument("--seed", type=int, default=0)
    ps.set_defaults(func=cmd_path_stress)

    pv = sub.add_parser("purged-validation", help="purged K-fold stability + PSR/DSR of a recorded backtest run")
    add_symbol_arg(pv)
    pv.add_argument("--run-id")
    pv.add_argument("--folds", type=int, default=5)
    pv.add_argument("--embargo-seconds", type=int, default=0)
    pv.set_defaults(func=cmd_purged_validation)

    snap = sub.add_parser("research-snapshot", help="online read-only backup of the production DB into a NEW research DB")
    snap.add_argument("--out", required=True)
    snap.set_defaults(func=cmd_research_snapshot)

    ind = sub.add_parser("independent-research",
                         help="one independent session per active strategy + the selector, same bars, research DB only")
    _range_args(ind)
    ind.add_argument("--research-db", required=True, help="separate research database (never the production DB)")
    ind.add_argument("--folds", type=int, default=12)
    ind.add_argument("--strategy", action="append", help="limit to these strategies (default: all six)")
    ind.add_argument("--tag")
    ind.add_argument("--out", help="write the full JSON report here")
    ind.set_defaults(func=cmd_independent_research)
