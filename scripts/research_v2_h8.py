"""Run the pre-registered H8 trial ONCE (docs/research/V2_H8_PREREGISTRATION_2026-09-30.md).

RESEARCH ONLY -- BACKTEST evidence on development data, research DB copy only.
Refused BEFORE any rule is evaluated: a dirty tree, the production DB, any
range outside 2024-06-01..2026-06-30 (reserved OOS, consumed H7 holdout), a
second run (an existing v2h8:<tag> ledger row), and a failed pre-registered
data-completeness gate (exit code 3; nothing is recorded as COMPLETED).

    python scripts/research_v2_h8.py --research-db data\\research\\v2_20260929.sqlite3 ^
        --tag h8r1 --out data\\research\\v2h8_XAUUSD_h8r1.json
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from adaptive_scalper.backtest.dataset import compute_bars_checksum  # noqa: E402
from adaptive_scalper.cli.research import open_research_db  # noqa: E402
from adaptive_scalper.gateway.spec_store import load_symbol_spec  # noqa: E402
from adaptive_scalper.history.store import get_bars  # noqa: E402
from adaptive_scalper.research.independent import assert_research_database, fold_index_ranges  # noqa: E402
from adaptive_scalper.research.ledger import family_trial_count, record_trial  # noqa: E402
from adaptive_scalper.research.v2 import h8  # noqa: E402

TRIAL_KIND = "RESEARCH_V2_H8"
FAMILY = "v2-H8:XAUUSD"
H6_FAMILY = "v2-H6:XAUUSD"
PREREGISTRATION = "docs/research/V2_H8_PREREGISTRATION_2026-09-30.md"
H6_RESULTS = "data/research/v2h6_XAUUSD_v2r3.json"


def _git_sha() -> str:
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    dirty = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=ROOT,
                           capture_output=True, text=True).stdout.strip()
    return sha + ("-dirty" if dirty else "")


def _inputs(cfg, conn):
    """Symbol spec + the PAPER config with the HIGH-impact news windows of the window."""
    from adaptive_scalper.news.blocking import _event_applies_to_symbol, _event_window
    from adaptive_scalper.news.providers.cache import load_cached_events
    from adaptive_scalper.runtime.paper import paper_config

    stored = load_symbol_spec(conn, h8.SYMBOL)
    if stored is None:
        raise SystemExit("no stored XAUUSD symbol spec in the research DB")
    events = [e for e in load_cached_events(conn, since_utc=h8.DEV_START_UTC) if e.scheduled_at_utc <= h8.DEV_END_UTC
              and e.impact.value == "HIGH" and _event_applies_to_symbol(e, h8.SYMBOL)]
    windows = tuple(sorted(_event_window(e, cfg.news.pre_high_impact_minutes, cfg.news.post_high_impact_minutes)
                           for e in events))
    config = dataclasses.replace(paper_config(cfg, h8.SYMBOL, windows), initial_equity=10_000.0)
    return stored[0], config, len(windows)


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--config", default="config/default.toml")
    p.add_argument("--research-db", required=True)
    p.add_argument("--tag", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--h6-results", default=H6_RESULTS)
    args = p.parse_args(argv)

    h8.assert_development_range(h8.DEV_START_UTC, h8.DEV_END_UTC)
    code_sha = _git_sha()
    if code_sha.endswith("-dirty"):
        print("refusing to run: tracked files are modified; commit first so the trial is reproducible")
        return 2
    trial_id = f"v2h8:{args.tag}:{h8.SYMBOL}:H8-PRIMARY"
    cfg, conn = open_research_db(args.config, args.research_db)
    try:
        assert_research_database(conn, cfg.database.path)
        if conn.execute("SELECT 1 FROM research_trials WHERE trial_id LIKE ?", (f"v2h8:{args.tag}:%",)).fetchone():
            print(f"refusing: {trial_id} already exists in the ledger -- H8 runs once")
            return 2
        started = time.time()
        bars = {res: get_bars(conn, h8.SYMBOL, res, h8.DEV_START_UTC, h8.DEV_END_UTC) for res in ("M15", "M5", "M1")}
        for res, series in bars.items():
            if series:
                h8.assert_development_range(series[0].time, series[-1].time)
        forbidden = any(s and (s[0].time < h8.DEV_START_UTC or s[-1].time > h8.DEV_END_UTC) for s in bars.values())
        spec, config, news = _inputs(cfg, conn)
        m15, m5, m1 = bars["M15"], bars["M5"], bars["M1"]
        ranges = fold_index_ranges(len(m15), 12, config.feature_lookback)
        gate = h8.coverage_gate(m15, m5, m1, ranges)
        checksums = {res: compute_bars_checksum(s) if s else None for res, s in bars.items()}
        base = {"kind": "RESEARCH_V2_H8", "origin": "BACKTEST", "evidence": "HYPOTHESIS DEVELOPMENT (not validation)",
                "preregistration": PREREGISTRATION, "code_sha": code_sha, "tag": args.tag, "symbol": h8.SYMBOL,
                "window": [h8.DEV_START_UTC, h8.DEV_END_UTC], "bars": {r: len(s) for r, s in bars.items()},
                "bars_checksum": checksums, "folds": len(ranges), "data_gate": gate,
                "protected_oos": "not read", "h7_holdout": "not read"}
        if not gate["passes"]:
            base["status"] = "REFUSED_DATA_GATE"
            Path(args.out).write_text(json.dumps(base, indent=1, default=str), encoding="utf-8")
            failing = [i for i, f in enumerate(gate["folds"]) if not f["passes"]]
            print(f"REFUSED: pre-registered data-completeness gate failed in folds {failing}; no rule evaluated, "
                  f"nothing recorded in the ledger. Report: {args.out}")
            return 3

        store = h8.FingerprintStore()
        results, rejected, decisions = [], [], 0
        for f, (start, end) in enumerate(h8.fold_spans(m15, ranges)):
            a, b = ranges[f]
            out = h8.run_h8_fold(m15[a:b], h8.slice_by_span(m5, start, end), h8.slice_by_span(m1, start, end),
                                 spec, config, fold=f, store=store, fingerprint=f"h8:{args.tag}")
            results += out["trades"]
            rejected += out["rejected"]
            decisions += out["decisions"]
        h6 = json.loads(Path(args.h6_results).read_text(encoding="utf-8"))
        prior = [t["fold_net_r"] for t in h6["trials"].values() if t.get("trades") and t.get("fold_net_r")]
        n_trials = family_trial_count(conn, H6_FAMILY) + family_trial_count(conn, FAMILY) + 1
        kwargs = dict(n_folds=len(ranges), config=config, prior_fold_net_r=prior, family_trials=n_trials,
                      forbidden_access=forbidden)
        # pass 1 yields H8's own per-trade Sharpe; pass 2 deflates against the variance INCLUDING it
        first = h8.evaluate(results, rejected, family_sharpe_variance=_variance(conn, None), **kwargs)
        report = h8.evaluate(results, rejected,
                             family_sharpe_variance=_variance(conn, first["sharpe_per_trade"]), **kwargs)
        record_trial(
            conn, trial_id=trial_id, family=FAMILY, kind=TRIAL_KIND, strategy_versions={h8.STRATEGY_KEY: 1},
            params={"preregistration": PREREGISTRATION, "code_sha": code_sha, "tag": args.tag,
                    "bars_checksum": checksums, "folds": len(ranges)},
            status="COMPLETED", dataset_id=checksums["M1"],
            cost_model={"provenance": config.fill_assumptions.provenance,
                        "slippage_price": config.fill_assumptions.slippage_price},
            sharpe=report["sharpe_per_trade"], n_observations=report["trades"],
            result={k: report[k] for k in ("gross_r", "cost_r", "net_r", "classification")},
        )
        base.update({"status": "COMPLETED", "news_windows_applied": news, "elapsed_seconds": time.time() - started,
                     "report": report})
        Path(args.out).write_text(json.dumps(base, indent=1, default=str), encoding="utf-8")
        print(f"H8 {report['classification']}: trades {report['trades']}, gross {report['gross_r']}, "
              f"net {report['net_r']}; report {args.out}")
        return 0
    finally:
        conn.close()


def _variance(conn, report_sharpe):
    """Cross-trial Sharpe variance of the completed H6 + H8 XAUUSD trials (min 30 observations)."""
    values = [r[0] for r in conn.execute(
        "SELECT sharpe FROM research_trials WHERE family IN (?, ?) AND status = 'COMPLETED' AND sharpe IS NOT NULL "
        "AND COALESCE(n_observations, 0) >= 30", (H6_FAMILY, FAMILY))]
    if report_sharpe is not None:
        values.append(report_sharpe)
    if len(values) < 2:
        return None
    mean = sum(values) / len(values)
    return sum((v - mean) ** 2 for v in values) / (len(values) - 1)


if __name__ == "__main__":
    raise SystemExit(main())
