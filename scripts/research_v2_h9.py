"""Run the pre-registered H9 trial ONCE (docs/research/V2_H9_PREREGISTRATION_2026-10-02.md).

RESEARCH ONLY -- BACKTEST evidence on EXPOSED development data, research DB copy only.
Refused BEFORE any rule is evaluated: a dirty tree, the production DB, a second run
(any existing v2h9: ledger row), any loaded bar outside the H6 BTCUSD M15 dataset or in
the reserved OOS, and a failed pre-registered data gate (exit 3, "H9 DATA BLOCKED",
nothing recorded in the ledger).

    python scripts/research_v2_h9.py --research-db C:\\AdaptiveScalperNext\\data\\research\\v2_20260929.sqlite3 ^
        --h6-results C:\\AdaptiveScalperNext\\data\\research\\v2h6_BTCUSD_v2r3.json ^
        --tag h9r1 --out C:\\AdaptiveScalperNext\\data\\research\\v2h9_BTCUSD_h9r1_run.json
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import platform
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from adaptive_scalper.backtest.dataset import compute_bars_checksum  # noqa: E402
from adaptive_scalper.backtest.fingerprint import compute_config_fingerprint  # noqa: E402
from adaptive_scalper.cli.research import open_research_db  # noqa: E402
from adaptive_scalper.gateway.spec_store import load_symbol_spec  # noqa: E402
from adaptive_scalper.history.store import get_bars  # noqa: E402
from adaptive_scalper.research.independent import assert_research_database, fold_index_ranges  # noqa: E402
from adaptive_scalper.research.ledger import family_trial_count, record_trial  # noqa: E402
from adaptive_scalper.research.v2 import h9  # noqa: E402

TRIAL_KIND = "RESEARCH_V2_H9"
FAMILY = "v2-H9:BTCUSD"
H6_FAMILY = "v2-H6:BTCUSD"
PREREGISTRATION = "docs/research/V2_H9_PREREGISTRATION_2026-10-02.md"
DSR_VARIANCE_MIN_OBSERVATIONS = 30


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True).stdout.strip()


def _code_sha() -> str:
    dirty = _git("status", "--porcelain", "--untracked-files=no")
    return _git("rev-parse", "HEAD") + ("-dirty" if dirty else "")


def _inputs(cfg, conn):
    """Symbol spec + the PAPER config with the HIGH-impact news windows (as H6)."""
    from adaptive_scalper.news.blocking import _event_applies_to_symbol, _event_window
    from adaptive_scalper.news.providers.cache import load_cached_events
    from adaptive_scalper.runtime.paper import paper_config

    stored = load_symbol_spec(conn, h9.SYMBOL)
    if stored is None:
        raise SystemExit("no stored BTCUSD symbol spec in the research DB")
    events = [e for e in load_cached_events(conn, since_utc=h9.DATA_START_UTC)
              if e.scheduled_at_utc <= h9.DATA_END_UTC and e.impact.value == "HIGH"
              and _event_applies_to_symbol(e, h9.SYMBOL)]
    windows = tuple(sorted(_event_window(e, cfg.news.pre_high_impact_minutes, cfg.news.post_high_impact_minutes)
                           for e in events))
    config = dataclasses.replace(paper_config(cfg, h9.SYMBOL, windows), initial_equity=10_000.0)
    limits = config.risk_limits
    if {k: getattr(limits, k) for k in h9.RISK_LIMITS} != h9.RISK_LIMITS or config.risk_per_trade_pct != 0.25:
        raise SystemExit(f"risk limits differ from the pre-registered ceilings: {limits}")
    return stored[0], config, len(windows)


def load_h6_fold_vectors(path: str) -> tuple[list[list[float]] | None, str]:
    """The 39 immutable H6 BTCUSD fold vectors, only if the file hash matches the pre-registration."""
    p = Path(path)
    if not p.exists():
        return None, f"missing {path}"
    digest = hashlib.sha256(p.read_bytes()).hexdigest()
    if digest != h9.H6_RESULTS_SHA256:
        return None, f"sha256 {digest} != pre-registered {h9.H6_RESULTS_SHA256}"
    h6 = json.loads(p.read_text(encoding="utf-8"))
    if h6.get("bars_checksum") != h9.EXPECTED_CHECKSUM or h6.get("folds") != h9.N_FOLDS:
        return None, "H6 artifact is not the H9 dataset / 12 folds"
    vectors = [t["fold_net_r"] for t in h6["trials"].values() if t.get("trades") and t.get("fold_net_r")]
    return vectors, f"ok ({p}, sha256 {digest}, {len(vectors)} vectors)"


def _variance(conn, own_sharpe):
    """Sample variance of the completed H6 BTCUSD Sharpes (n >= 30) plus H9's own."""
    values = [r[0] for r in conn.execute(
        "SELECT sharpe FROM research_trials WHERE family = ? AND status = 'COMPLETED' AND sharpe IS NOT NULL "
        "AND COALESCE(n_observations, 0) >= ?", (H6_FAMILY, DSR_VARIANCE_MIN_OBSERVATIONS))]
    if own_sharpe is not None:
        values.append(own_sharpe)
    if len(values) < 2:
        return None
    mean = sum(values) / len(values)
    return sum((v - mean) ** 2 for v in values) / (len(values) - 1)


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--config", default=str(ROOT / "config" / "default.toml"))
    p.add_argument("--research-db", required=True)
    p.add_argument("--h6-results", required=True)
    p.add_argument("--tag", required=True)
    p.add_argument("--out", required=True)
    args = p.parse_args(argv)

    h9.assert_development_range(h9.DATA_START_UTC, h9.DATA_END_UTC)
    code_sha = _code_sha()
    if code_sha.endswith("-dirty"):
        print("refusing to run: tracked files are modified; commit first so the trial is reproducible")
        return 2
    trial_id = f"v2h9:{args.tag}:{h9.SYMBOL}:H9-PRIMARY"
    cfg, conn = open_research_db(args.config, args.research_db)
    try:
        assert_research_database(conn, cfg.database.path)
        if conn.execute("SELECT 1 FROM research_trials WHERE trial_id LIKE 'v2h9:%' OR family = ?",
                        (FAMILY,)).fetchone():
            print(f"refusing: an H9 trial already exists in the ledger -- H9 runs once ({trial_id})")
            return 2
        ledger_before = conn.execute("SELECT COUNT(*) FROM research_trials").fetchone()[0]
        started = time.time()
        bars = get_bars(conn, h9.SYMBOL, "M15", h9.DATA_START_UTC, h9.DATA_END_UTC)
        forbidden = h9.forbidden_bars(bars)
        if forbidden:
            raise SystemExit(f"ABORT: {len(forbidden)} loaded bars outside the H9 dataset / in the reserved OOS")
        gate = h9.data_gate(bars)
        if gate["checksum"] != compute_bars_checksum(bars):
            raise SystemExit("ABORT: h9.checksum disagrees with compute_bars_checksum")
        spec, config, news = _inputs(cfg, conn)
        config_fp, _ = compute_config_fingerprint(config, canonical_symbol=h9.SYMBOL, resolutions=("M15",),
                                                  strategies=((h9.STRATEGY_KEY, 1),))
        base = {"kind": TRIAL_KIND, "origin": "BACKTEST", "evidence": "EXPOSED DEVELOPMENT DATA (not validation)",
                "preregistration": PREREGISTRATION,
                "preregistration_sha": _git("log", "-1", "--format=%H", "--", PREREGISTRATION),
                "code_sha": code_sha, "tag": args.tag, "trial_id": trial_id, "family": FAMILY,
                "symbol": h9.SYMBOL, "resolution": "M15", "config_fingerprint": config_fp,
                "environment": {"python": platform.python_version()},
                "window": [h9.DATA_START_UTC, h9.DATA_END_UTC], "data_gate": gate,
                "ledger_rows_before": ledger_before, "protected_oos": "not read by the runner",
                "h7_holdout": "not applicable (XAUUSD)", "h8": "not rerun"}
        if not gate["passes"]:
            base["status"] = "H9 DATA BLOCKED"
            Path(args.out).write_text(json.dumps(base, indent=1, default=str), encoding="utf-8")
            print(f"H9 DATA BLOCKED: failed checks {[k for k, v in gate['checks'].items() if not v]}; no rule "
                  f"evaluated, nothing recorded in the ledger. Report: {args.out}")
            return 3
        ranges = fold_index_ranges(len(bars), h9.N_FOLDS, config.feature_lookback)
        base["folds"] = [[a, b, bars[a].time, bars[b - 1].time] for a, b in ranges]

        # ---- the single counted evaluation starts here
        series = h9.compute_series(bars)
        events = h9.detect_events(bars, series.tstat)
        store = h9.SqliteFingerprintStore(conn)
        results, rejected, fold_equity = [], [], []
        for f, (a, b) in enumerate(ranges):
            out = h9.run_h9_fold(bars, series, events, (a, b), spec, config, fold=f, store=store,
                                 fingerprint=f"h9:{args.tag}")
            results += out["trades"]
            rejected += out["rejected"]
            fold_equity.append(out["final_equity"])
        prior, prior_note = load_h6_fold_vectors(args.h6_results)
        n_trials = family_trial_count(conn, H6_FAMILY) + 1
        span = bars[ranges[-1][1] - 1].time + h9.M15 - bars[ranges[0][0]].time
        kwargs = dict(config=config, prior_fold_net_r=prior, family_trials=n_trials, forbidden_access=bool(forbidden),
                      span_seconds=span)
        # pass 1 yields H9's own per-trade Sharpe; pass 2 deflates against the variance INCLUDING it
        first = h9.evaluate(results, rejected, family_sharpe_variance=_variance(conn, None), **kwargs)
        report = h9.evaluate(results, rejected, family_sharpe_variance=_variance(conn, first["sharpe_per_trade"]),
                             **kwargs)
        other_btc = conn.execute("SELECT family, COUNT(*) FROM research_trials WHERE family LIKE '%BTCUSD' "
                                 "AND family NOT IN (?, ?) GROUP BY family", (H6_FAMILY, FAMILY)).fetchall()
        record_trial(
            conn, trial_id=trial_id, family=FAMILY, kind=TRIAL_KIND, strategy_versions={h9.STRATEGY_KEY: 1},
            params={"preregistration": PREREGISTRATION, "preregistration_sha": base["preregistration_sha"],
                    "code_sha": code_sha, "tag": args.tag, "config_fingerprint": config_fp,
                    "bars_checksum": gate["checksum"], "folds": len(ranges)},
            status="COMPLETED", dataset_id=gate["checksum"], split_config={"folds": base["folds"]},
            cost_model={"provenance": config.fill_assumptions.provenance,
                        "slippage_price": config.fill_assumptions.slippage_price,
                        "commission_per_lot": config.fill_assumptions.commission_monetary_per_lot,
                        "uncertainty_margin_pct": config.uncertainty_margin_pct,
                        "swap": "unknown; holds crossing the broker rollover rejected"},
            sharpe=report["sharpe_per_trade"], n_observations=report["trades"],
            result={k: report[k] for k in ("trades", "gross_r", "cost_r", "net_r", "classification", "checks")},
        )
        base.update({"status": "COMPLETED", "news_windows_applied": news, "elapsed_seconds": time.time() - started,
                     "events_total": len(events), "fold_final_equity": fold_equity,
                     "h6_fold_vectors": prior_note, "dsr_other_btc_families_excluded": [list(r) for r in other_btc],
                     "ledger_rows_after": conn.execute("SELECT COUNT(*) FROM research_trials").fetchone()[0],
                     "consumed_fingerprints": [list(r) for r in conn.execute(
                         "SELECT fingerprint, outcome, consumed_at_utc FROM h9_consumed_fingerprints ORDER BY 1")],
                     "rejected": rejected, "report": report})
        Path(args.out).write_text(json.dumps(base, indent=1, default=str), encoding="utf-8")
        print(f"{report['classification']}: trades {report['trades']}, gross {report['gross_r']}, "
              f"net {report['net_r']}; report {args.out}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
