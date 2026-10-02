"""ONE-SHOT H7: XAUUSD London/New York overlap (POST-HOC origin) on the
untouched XAUUSD M15 holdout (docs/research/V2_H6_H7_PREREGISTRATION_2026-09-29.md).

The range, symbol, resolution, entries and exit are fixed here, not
arguments. The script refuses to run when any `v2-H7:XAUUSD` trial already
exists in the ledger (the holdout is consumed by the first run, pass or
fail), when tracked files are modified, and -- as everywhere -- the
reserved OOS.

    python scripts/research_v2_h7.py --research-db data\\research\\v2_20260929.sqlite3 --out data\\research\\v2h7_XAUUSD.json
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from adaptive_scalper.backtest.dataset import compute_bars_checksum  # noqa: E402
from adaptive_scalper.backtest.engine import run_backtest  # noqa: E402
from adaptive_scalper.backtest.reserved_oos import assert_outside_reserved_oos  # noqa: E402
from adaptive_scalper.cli.research import _inputs, open_research_db  # noqa: E402
from adaptive_scalper.history.resolutions import resolution_seconds  # noqa: E402
from adaptive_scalper.research.independent import (  # noqa: E402
    assert_research_database,
    deflated_sharpe_for_family,
    fold_index_ranges,
)
from adaptive_scalper.research.ledger import family_trial_count, record_trial  # noqa: E402
from adaptive_scalper.research.v2.counterfactual import (  # noqa: E402
    ExitPolicy,
    build_fold_context,
    cohort_from_run,
    replay_entry,
)
from adaptive_scalper.research.v2.metrics import SPREAD_PERCENTILES, trial_metrics  # noqa: E402
from adaptive_scalper.strategies import build_active_registry, select_active_strategies  # noqa: E402

SYMBOL, RESOLUTION = "XAUUSD", "M15"
HOLDOUT_START, HOLDOUT_END = "2022-06-23", "2024-05-31"
SESSION_HOURS_UTC = range(12, 16)
FAMILY = "v2-H7:XAUUSD"
PREREGISTRATION = "docs/research/V2_H6_H7_PREREGISTRATION_2026-09-29.md"
ST = ExitPolicy("ST", "STOP_TARGET")


def _git_sha() -> str:
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    dirty = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=ROOT,
                           capture_output=True, text=True).stdout.strip()
    return sha + ("-dirty" if dirty else "")


def _pctl(values, q):
    s = sorted(values)
    return s[min(len(s) - 1, int(q / 100 * (len(s) - 1) + 0.5))]


def _acceptance(session: dict, control: dict) -> dict:
    st = session.get("statistics") or {}
    checks = {
        "net_r_positive": (session.get("net_r") or -1) > 0,
        "gross_ge_1_5x_cost": session.get("gross_r") is not None and bool(session.get("cost_r"))
        and session["gross_r"] >= 1.5 * session["cost_r"],
        "folds_8_of_12": session.get("positive_folds", 0) >= 8,
        "psr_ge_0_95": (st.get("psr_vs_zero") or 0) >= 0.95,
        "trades_ge_100": session.get("trades", 0) >= 100,
        "net_positive_at_cost_x1_2": ((session.get("stress_net_r") or {}).get("cost_x1.2") or -1) > 0,
        "session_beats_control": (session.get("net_r") or -1) > (control.get("net_r") or 0),
    }
    return {"checks": checks, "passes": all(checks.values())}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--config", default="config/default.toml")
    p.add_argument("--research-db", required=True)
    p.add_argument("--out", required=True)
    args = p.parse_args()
    code_sha = _git_sha()
    if code_sha.endswith("-dirty"):
        print("refusing to run: tracked files are modified; commit first so the trial is reproducible")
        return 2

    cfg, conn = open_research_db(args.config, args.research_db)
    try:
        assert_research_database(conn, cfg.database.path)
        if family_trial_count(conn, FAMILY):
            print(f"refusing: {FAMILY} already has trials -- the H7 holdout is consumed (one shot)")
            return 2
        inputs = SimpleNamespace(symbol=SYMBOL, resolution=RESOLUTION, start=HOLDOUT_START, end=HOLDOUT_END,
                                 equity=10_000.0)
        bars, spec, config, news = _inputs(inputs, conn, cfg)
        assert_outside_reserved_oos(bars[0].time, bars[-1].time)
        now = int(time.time())
        started = time.time()
        ranges = fold_index_ranges(len(bars), 12, config.feature_lookback)
        bar_seconds = resolution_seconds(RESOLUTION)
        strategies = select_active_strategies(None, build_active_registry())
        checksum = compute_bars_checksum(bars)
        spread_prices = {q: _pctl([b.spread * spec.point for b in bars], q) for q in SPREAD_PERCENTILES}

        entries, contexts = [], {}
        for f, (a, b) in enumerate(ranges):
            clog: list = []
            result = run_backtest(bars[a:b], SYMBOL, RESOLUTION, spec, config=config, now_utc=now, candidate_log=clog)
            entries.extend(cohort_from_run("__selector__", f, bars[a:b], result, clog))
            contexts[f] = build_fold_context(bars[a:b], SYMBOL, RESOLUTION, spec, config, index=f,
                                             keep_features_for=set())
        replays = [replay_entry(e, contexts[e.fold], ST, symbol_spec=spec, config=config, strategies=strategies,
                                bar_seconds=bar_seconds, fingerprint=f"h7:{code_sha[:12]}") for e in entries]
        session = [r for r in replays if (r.trade.entry_time_utc % 86_400) // 3600 in SESSION_HOURS_UTC]
        trials = {"H7-SESSION": session, "H7-ALL": replays}
        metrics = {k: trial_metrics(v, symbol=SYMBOL, symbol_spec=spec, n_folds=len(ranges),
                                    spread_percentile_prices=spread_prices) for k, v in trials.items()}
        versions = {s.key: s.version for s in strategies}
        for label, m in metrics.items():
            st = m.get("statistics") or {}
            record_trial(conn, trial_id=f"v2h7:{SYMBOL}:{label}", family=FAMILY, kind="RESEARCH_V2_POSTHOC_HOLDOUT",
                         strategy_versions=versions, dataset_id=checksum, status="COMPLETED", sharpe=st.get("sharpe"),
                         n_observations=st.get("n"),
                         params={"trial": label, "range": [bars[0].time, bars[-1].time], "resolution": RESOLUTION,
                                 "code_sha": code_sha, "preregistration": PREREGISTRATION, "origin": "POST-HOC"},
                         result={"gross_r": m.get("gross_r"), "cost_r": m.get("cost_r"), "net_r": m.get("net_r")},
                         now_utc=now)
        conn.commit()
        for m in metrics.values():
            st = m.get("statistics") or {}
            m["dsr"] = deflated_sharpe_for_family(conn, FAMILY, st.get("sharpe"), st.get("n") or 0,
                                                  st.get("skew") or 0.0, st.get("kurtosis") or 3.0)
        verdict = _acceptance(metrics["H7-SESSION"], metrics["H7-ALL"])
        out = {
            "kind": "RESEARCH_V2_H7_ONE_SHOT", "origin": "BACKTEST", "evidence": "POST-HOC hypothesis on an unseen holdout",
            "preregistration": PREREGISTRATION, "code_sha": code_sha, "symbol": SYMBOL, "resolution": RESOLUTION,
            "range": [bars[0].time, bars[-1].time], "bars": len(bars), "bars_checksum": checksum, "folds": len(ranges),
            "created_at_utc": now, "cost_provenance": config.fill_assumptions.provenance,
            "news_windows_applied": news, "holdout_status": "CONSUMED by this run", "verdict": verdict,
            "trials": metrics, "elapsed_seconds": time.time() - started,
        }
        Path(args.out).write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
        print(f"H7 verdict: {'PASS' if verdict['passes'] else 'FAIL'} {verdict['checks']}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
