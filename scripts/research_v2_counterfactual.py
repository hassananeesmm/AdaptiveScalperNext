"""Run the pre-registered H4 (same-entry counterfactual exits) and H5
(marginal future cost) study for one symbol, next to a fresh V1 baseline
(docs/research/V2_H4_H5_PREREGISTRATION_2026-09-29.md).

RESEARCH ONLY -- BACKTEST evidence on development data, research DB copy
only; never the production DB, never a broker, never the protected OOS
(the requested range is refused BEFORE any bar is loaded, and again by the
counterfactual engine and by `run_backtest`).

    python scripts/research_v2_counterfactual.py --symbol XAUUSD --start 2025-06-01 --end 2026-06-30 ^
        --research-db data\\research\\v2_20260929.sqlite3 --tag v2r2 --out data\\research\\v2cf_XAUUSD_v2r2.json
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from collections import defaultdict
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
    SELECTOR_SESSION,
    assert_research_database,
    deflated_sharpe_for_family,
    fold_index_ranges,
)
from adaptive_scalper.research.ledger import record_trial  # noqa: E402
from adaptive_scalper.research.stats import probability_of_backtest_overfitting  # noqa: E402
from adaptive_scalper.research.trade_analysis import from_simulated, summarize  # noqa: E402
from adaptive_scalper.research.v2.analysis import session_stats  # noqa: E402
from adaptive_scalper.research.v2.counterfactual import (  # noqa: E402
    H4_POLICIES,
    H5_POLICIES,
    V1_POLICY,
    build_fold_context,
    cohort_from_run,
    replay_entry,
    review_feature_indices,
    same_exit,
)
from adaptive_scalper.research.v2.metrics import (  # noqa: E402
    SPREAD_PERCENTILES,
    development_verdict,
    trial_metrics,
)
from adaptive_scalper.strategies import build_active_registry, select_active_strategies  # noqa: E402
from adaptive_scalper.cli.common import parse_utc  # noqa: E402

TRIAL_KIND = "RESEARCH_V2_COUNTERFACTUAL"
PREREGISTRATION = "docs/research/V2_H4_H5_PREREGISTRATION_2026-09-29.md"
FIDELITY_MIN = 0.99


def _git_sha() -> str:
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    dirty = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=ROOT,
                           capture_output=True, text=True).stdout.strip()
    return sha + ("-dirty" if dirty else "")


def _pctl(values, q):
    s = sorted(values)
    return s[min(len(s) - 1, int(q / 100 * (len(s) - 1) + 0.5))]


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--config", default="config/default.toml")
    p.add_argument("--symbol", required=True)
    p.add_argument("--resolution", default="M5")
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True)
    p.add_argument("--folds", type=int, default=12)
    p.add_argument("--equity", type=float, default=10_000.0)
    p.add_argument("--research-db", required=True)
    p.add_argument("--tag", required=True)
    p.add_argument("--out", required=True)
    args = p.parse_args()

    # Layer 1: the requested range, before anything is loaded.
    assert_outside_reserved_oos(parse_utc(args.start), parse_utc(args.end))
    code_sha = _git_sha()
    if code_sha.endswith("-dirty"):
        print("refusing to run: tracked files are modified; commit first so the trial is reproducible")
        return 2

    cfg, conn = open_research_db(args.config, args.research_db)
    try:
        assert_research_database(conn, cfg.database.path)
        bars, spec, config, news = _inputs(SimpleNamespace(**vars(args)), conn, cfg)
        assert_outside_reserved_oos(bars[0].time, bars[-1].time)
        now = int(time.time())
        started = time.time()
        log = lambda msg: print(f"[{time.time() - started:7.1f}s] {msg}", flush=True)  # noqa: E731
        ranges = fold_index_ranges(len(bars), args.folds, config.feature_lookback)
        bar_seconds = resolution_seconds(args.resolution)
        strategies = select_active_strategies(None, build_active_registry())
        versions = {s.key: s.version for s in strategies}
        checksum = compute_bars_checksum(bars)
        spread_prices = {q: _pctl([b.spread * spec.point for b in bars], q) for q in SPREAD_PERCENTILES}

        # 1. Fresh V1 baseline per cohort (source of truth), with candidate logs.
        cohorts = [s.key for s in strategies] + [SELECTOR_SESSION]
        v1_results: dict = {}
        entries: dict = defaultdict(list)          # cohort -> [CohortEntry]
        for cohort in cohorts:
            keys = None if cohort == SELECTOR_SESSION else (cohort,)
            folds = []
            for f, (a, b) in enumerate(ranges):
                clog: list = []
                result = run_backtest(bars[a:b], args.symbol, args.resolution, spec, config=config, now_utc=now,
                                      strategy_keys=keys, candidate_log=clog)
                folds.append(result)
                entries[cohort].extend(cohort_from_run(cohort, f, bars[a:b], result, clog))
            v1_results[cohort] = folds
            log(f"V1 {cohort}: {len(entries[cohort])} entries")

        # 2. Fold contexts (causal features/regime), once per fold.
        contexts = []
        for f, (a, b) in enumerate(ranges):
            keep = set()
            for cohort in cohorts:
                keep |= review_feature_indices([e for e in entries[cohort] if e.fold == f], config, bar_seconds)
            contexts.append(build_fold_context(bars[a:b], args.symbol, args.resolution, spec, config,
                                               index=f, keep_features_for=keep))
        log("fold contexts built")

        # 3. Replays.
        policies = (V1_POLICY,) + H4_POLICIES + H5_POLICIES
        replays: dict = {}
        fidelity: dict = {}
        errors: dict = {}
        for cohort in cohorts:
            for policy in policies:
                label = f"{policy.name}:{cohort}"
                try:
                    replays[label] = [
                        replay_entry(e, contexts[e.fold], policy, symbol_spec=spec, config=config,
                                     strategies=strategies, bar_seconds=bar_seconds,
                                     fingerprint=f"counterfactual:{args.tag}:{policy.name}")
                        for e in entries[cohort]]
                except Exception as exc:  # a failed trial is recorded, never dropped
                    errors[label] = f"{type(exc).__name__}: {exc}"
            v1 = replays.get(f"V1:{cohort}")
            if v1 is not None and entries[cohort]:
                same = sum(1 for r, e in zip(v1, entries[cohort]) if same_exit(r.trade, e.original))
                fidelity[cohort] = same / len(entries[cohort])
            else:
                fidelity[cohort] = 0.0 if entries[cohort] else 1.0
            log(f"replayed {cohort}: fidelity {fidelity[cohort]:.4f}")

        # 4. Metrics + ledger (every H4/H5 trial, failures included).
        report_cohorts: dict = {}
        family_of = {p.name: "H4" for p in H4_POLICIES} | {p.name: "H5" for p in H5_POLICIES}
        trial_stats: dict = {}
        for cohort in cohorts:
            v1_views = [v for v in (from_simulated(t, args.symbol) for fr in v1_results[cohort] for t in fr.trades)
                        if v is not None]
            baseline = {r.entry_key: (r.trade.realized_pnl + r.trade.total_cost) / r.trade.initial_monetary_risk
                        for r in replays.get(f"V1:{cohort}", []) if r.trade.initial_monetary_risk > 0}
            block = {
                "entries": len(entries[cohort]), "replay_fidelity": fidelity[cohort],
                "v1_engine": {"summary": summarize(v1_views), "statistics": session_stats(v1_views),
                              "fold_net_pnl": [sum(t.realized_pnl or 0.0 for t in fr.trades) for fr in v1_results[cohort]],
                              "config_fingerprints": sorted({fr.config_fingerprint for fr in v1_results[cohort]})},
                "variants": {},
            }
            for policy in policies:
                label = f"{policy.name}:{cohort}"
                if label in errors:
                    block["variants"][policy.name] = {"error": errors[label]}
                    continue
                m = trial_metrics(replays[label], symbol=args.symbol, symbol_spec=spec, n_folds=len(ranges),
                                  spread_percentile_prices=spread_prices,
                                  baseline_gross_r=None if policy is V1_POLICY else baseline)
                block["variants"][policy.name] = m
                if policy is not V1_POLICY:
                    trial_stats[label] = m
            report_cohorts[cohort] = block

        for cohort in cohorts:
            for policy in H4_POLICIES + H5_POLICIES:
                label = f"{policy.name}:{cohort}"
                family = f"v2-{family_of[policy.name]}:{args.symbol}"
                params = {"variant": policy.name, "cohort": cohort, "bars_checksum": checksum,
                          "range": [bars[0].time, bars[-1].time], "folds": len(ranges), "code_sha": code_sha,
                          "preregistration": PREREGISTRATION, "tag": args.tag, "hold_bars": policy.hold_bars,
                          "cost_model": policy.cost_model, "thesis": getattr(policy.thesis, "__name__", None)}
                trial_id = f"v2cf:{args.tag}:{args.symbol}:{label}"
                common = dict(trial_id=trial_id, family=family, kind=TRIAL_KIND, strategy_versions=versions,
                              params=params, dataset_id=checksum,
                              cost_model={"provenance": config.fill_assumptions.provenance,
                                          "slippage_price": config.fill_assumptions.slippage_price,
                                          "commission_per_lot_round_trip":
                                              config.fill_assumptions.commission_monetary_per_lot},
                              now_utc=now)
                if label in errors or fidelity[cohort] < FIDELITY_MIN:
                    note = errors.get(label) or f"replay fidelity {fidelity[cohort]:.4f} < {FIDELITY_MIN}"
                    record_trial(conn, status="FAILED", notes=note, **common)
                    continue
                m = trial_stats[label]
                st = m.get("statistics") or {}
                record_trial(conn, status="COMPLETED", sharpe=st.get("sharpe"), n_observations=st.get("n"),
                             result={"net_r": m.get("net_r"), "gross_r": m.get("gross_r"), "cost_r": m.get("cost_r"),
                                     "psr_vs_zero": st.get("psr_vs_zero"), "positive_folds": m.get("positive_folds")},
                             **common)
        conn.commit()

        # 5. DSR within family and PBO per family (12 time blocks, all family members).
        span = bars[-1].time - bars[0].time + 1
        family_pbo = {}
        for fam, members in (("H4", H4_POLICIES), ("H5", H5_POLICIES)):
            labels = [f"{p.name}:{c}" for c in cohorts for p in members if f"{p.name}:{c}" in trial_stats]
            if len(labels) < 2:
                family_pbo[fam] = {"computable": False, "reason": "fewer than two completed variants"}
                continue
            matrix = [[0.0] * len(labels) for _ in range(12)]
            for j, label in enumerate(labels):
                for r in replays[label]:
                    t = r.trade
                    if t.initial_monetary_risk > 0:
                        blk = min(11, max(0, (t.entry_time_utc - bars[0].time) * 12 // span))
                        matrix[blk][j] += t.realized_pnl / t.initial_monetary_risk
            res = probability_of_backtest_overfitting(matrix, 6)
            family_pbo[fam] = {"computable": res.computable, "pbo": res.pbo, "combinations": res.n_combinations,
                               "reason": res.reason, "members": len(labels)}

        verdicts = {}
        for cohort in cohorts:
            for policy in H4_POLICIES + H5_POLICIES:
                label = f"{policy.name}:{cohort}"
                if label not in trial_stats:
                    continue
                m = trial_stats[label]
                st = m.get("statistics") or {}
                dsr = deflated_sharpe_for_family(conn, f"v2-{family_of[policy.name]}:{args.symbol}", st.get("sharpe"),
                                                 st.get("n") or 0, st.get("skew") or 0.0, st.get("kurtosis") or 3.0)
                m["dsr"] = dsr
                v = development_verdict(m, dsr=dsr.get("dsr"), pbo=(family_pbo[family_of[policy.name]] or {}).get("pbo"))
                if policy in H4_POLICIES and v["passes"]:
                    v["note"] = "H4 is diagnostic: a causal full-run confirmation trial is required first"
                m["development_verdict"] = v
                verdicts[label] = v["passes"]

        report = {
            "kind": "RESEARCH_V2_COUNTERFACTUAL_STUDY", "origin": "BACKTEST",
            "evidence": "INDEPENDENT RESEARCH / DIAGNOSTIC (same-entry replay)",
            "preregistration": PREREGISTRATION, "code_sha": code_sha, "tag": args.tag,
            "symbol": args.symbol, "resolution": args.resolution, "range": [bars[0].time, bars[-1].time],
            "bars": len(bars), "bars_checksum": checksum, "folds": len(ranges), "created_at_utc": now,
            "research_db": args.research_db, "cost_provenance": config.fill_assumptions.provenance,
            "news_windows_applied": news, "spread_percentile_prices": spread_prices,
            "protected_oos": "not read (refused at request, engine and run_backtest layers)",
            "replay_fidelity_min": FIDELITY_MIN, "cohorts": report_cohorts, "family_pbo": family_pbo,
            "passing_trials": sorted(k for k, ok in verdicts.items() if ok), "errors": errors,
            "elapsed_seconds": time.time() - started,
        }
        Path(args.out).write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
        log(f"wrote {args.out}; passing trials: {report['passing_trials'] or 'NONE'}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
