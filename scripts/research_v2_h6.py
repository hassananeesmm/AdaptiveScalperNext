"""Run the pre-registered H6 screen (slower, cost-efficient entries) for one
symbol on M15 (docs/research/V2_H6_H7_PREREGISTRATION_2026-09-29.md).

RESEARCH ONLY -- BACKTEST evidence on development data, research DB copy
only. Refused before anything is loaded: the reserved OOS, a dirty tree,
and any XAUUSD range that reaches into the H7 holdout (before 2024-06-01).

    python scripts/research_v2_h6.py --symbol XAUUSD --start 2024-06-01 --end 2026-06-30 ^
        --research-db data\\research\\v2_20260929.sqlite3 --tag v2r3 --out data\\research\\v2h6_XAUUSD_v2r3.json
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
from adaptive_scalper.cli.common import parse_utc  # noqa: E402
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
from adaptive_scalper.research.v2.counterfactual import (  # noqa: E402
    V1_POLICY,
    ExitPolicy,
    build_fold_context,
    cohort_from_run,
    replay_entry,
    review_feature_indices,
    same_exit,
)
from adaptive_scalper.research.v2.metrics import SPREAD_PERCENTILES, development_verdict, trial_metrics  # noqa: E402
from adaptive_scalper.research.v2.screen import donchian_entries, gate_by_cost_to_stop, screen_verdict  # noqa: E402
from adaptive_scalper.strategies import build_active_registry, select_active_strategies  # noqa: E402

TRIAL_KIND = "RESEARCH_V2_SCREEN"
PREREGISTRATION = "docs/research/V2_H6_H7_PREREGISTRATION_2026-09-29.md"
H7_HOLDOUT_END = parse_utc("2024-06-01")   # XAUUSD M15 before this belongs to H7 only
FIDELITY_MIN = 0.99
ST = ExitPolicy("ST", "STOP_TARGET")
FH4 = ExitPolicy("FH4", "FIXED_HOLD", hold_bars=4)
FH16 = ExitPolicy("FH16", "FIXED_HOLD", hold_bars=16)
H6A_EXITS = (ST, FH4, FH16)
H6B_RATIOS = (0.10, 0.05)
H6C_LOOKBACKS = (20, 48)
H6C_EXITS = (ST, FH16)


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
    p.add_argument("--resolution", default="M15")
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True)
    p.add_argument("--folds", type=int, default=12)
    p.add_argument("--equity", type=float, default=10_000.0)
    p.add_argument("--research-db", required=True)
    p.add_argument("--tag", required=True)
    p.add_argument("--out", required=True)
    args = p.parse_args()

    start, end = parse_utc(args.start), parse_utc(args.end)
    assert_outside_reserved_oos(start, end)
    if args.symbol == "XAUUSD" and start < H7_HOLDOUT_END:
        print("refusing: XAUUSD before 2024-06-01 is the H7 holdout; H6 must never read it")
        return 2
    code_sha = _git_sha()
    if code_sha.endswith("-dirty"):
        print("refusing to run: tracked files are modified; commit first so the trial is reproducible")
        return 2

    cfg, conn = open_research_db(args.config, args.research_db)
    try:
        assert_research_database(conn, cfg.database.path)
        bars, spec, config, news = _inputs(SimpleNamespace(**vars(args)), conn, cfg)
        assert_outside_reserved_oos(bars[0].time, bars[-1].time)
        if args.symbol == "XAUUSD" and bars[0].time < H7_HOLDOUT_END:
            raise RuntimeError("loaded bars reach into the H7 holdout")
        now = int(time.time())
        started = time.time()
        log = lambda msg: print(f"[{time.time() - started:7.1f}s] {msg}", flush=True)  # noqa: E731
        ranges = fold_index_ranges(len(bars), args.folds, config.feature_lookback)
        bar_seconds = resolution_seconds(args.resolution)
        strategies = select_active_strategies(None, build_active_registry())
        versions = {s.key: s.version for s in strategies}
        checksum = compute_bars_checksum(bars)
        spread_prices = {q: _pctl([b.spread * spec.point for b in bars], q) for q in SPREAD_PERCENTILES}

        # 1. V1 entry logic on M15 (frozen code), per cohort, with candidate logs.
        cohorts = [s.key for s in strategies] + [SELECTOR_SESSION]
        entries: dict = {c: [] for c in cohorts}
        for cohort in cohorts:
            keys = None if cohort == SELECTOR_SESSION else (cohort,)
            for f, (a, b) in enumerate(ranges):
                clog: list = []
                result = run_backtest(bars[a:b], args.symbol, args.resolution, spec, config=config, now_utc=now,
                                      strategy_keys=keys, candidate_log=clog)
                entries[cohort].extend(cohort_from_run(cohort, f, bars[a:b], result, clog))
            log(f"V1@M15 {cohort}: {len(entries[cohort])} entries")

        # 2. Fold contexts.
        contexts = {}
        for f, (a, b) in enumerate(ranges):
            keep = set()
            for cohort in cohorts:
                keep |= review_feature_indices([e for e in entries[cohort] if e.fold == f], config, bar_seconds)
            contexts[f] = build_fold_context(bars[a:b], args.symbol, args.resolution, spec, config, index=f,
                                             keep_features_for=keep)
        log("fold contexts built")

        def replay(cohort_entries, policy, fp):
            return [replay_entry(e, contexts[e.fold], policy, symbol_spec=spec, config=config, strategies=strategies,
                                 bar_seconds=bar_seconds, fingerprint=fp) for e in cohort_entries]

        # 3. Trials.
        trials: dict = {}       # label -> (family_part, replays, extra)
        errors: dict = {}
        fidelity: dict = {}
        references: dict = {}
        for cohort in cohorts:
            v1 = replay(entries[cohort], V1_POLICY, f"h6:{args.tag}:V1")
            same = sum(1 for r, e in zip(v1, entries[cohort]) if same_exit(r.trade, e.original))
            fidelity[cohort] = same / len(entries[cohort]) if entries[cohort] else 1.0
            references[cohort] = v1
            for policy in H6A_EXITS:
                label = f"H6a-{policy.name}:{cohort}"
                try:
                    trials[label] = (replay(entries[cohort], policy, f"h6:{args.tag}:{label}"), {})
                except Exception as exc:
                    errors[label] = f"{type(exc).__name__}: {exc}"
            for ratio in H6B_RATIOS:
                label = f"H6b-C{ratio:g}-ST:{cohort}"
                try:
                    gated = gate_by_cost_to_stop(entries[cohort], contexts, spec, config, ratio)
                    trials[label] = (replay(gated, ST, f"h6:{args.tag}:{label}"),
                                     {"entries_before_gate": len(entries[cohort]), "entries_after_gate": len(gated)})
                except Exception as exc:
                    errors[label] = f"{type(exc).__name__}: {exc}"
            log(f"replayed {cohort}: fidelity {fidelity[cohort]:.4f}")

        for lookback in H6C_LOOKBACKS:
            cohort = f"research_donchian_{lookback}"
            don, counts = [], {"signals": 0, "cost_gate_rejected": 0, "engine_rejected": 0}
            for f in contexts:
                e, c = donchian_entries(contexts[f], args.symbol, spec, config, lookback=lookback, fold=f,
                                        cohort=cohort, bar_seconds=bar_seconds, fingerprint=f"h6:{args.tag}:{cohort}")
                don.extend(e)
                for k in counts:
                    counts[k] += c[k]
            for policy in H6C_EXITS:
                label = f"H6c-N{lookback}-{policy.name}:{cohort}"
                try:
                    trials[label] = (replay(don, policy, f"h6:{args.tag}:{label}"), dict(counts, entries=len(don)))
                except Exception as exc:
                    errors[label] = f"{type(exc).__name__}: {exc}"
            log(f"donchian N={lookback}: {len(don)} entries {counts}")

        # 4. Metrics, screen, ledger.
        report: dict = {}
        family = f"v2-H6:{args.symbol}"
        for label, (reps, extra) in trials.items():
            cohort = label.split(":", 1)[1]
            baseline = None
            if cohort in references:
                baseline = {r.entry_key: (r.trade.realized_pnl + r.trade.total_cost) / r.trade.initial_monetary_risk
                            for r in references[cohort] if r.trade.initial_monetary_risk > 0}
            m = trial_metrics(reps, symbol=args.symbol, symbol_spec=spec, n_folds=len(ranges),
                              spread_percentile_prices=spread_prices, baseline_gross_r=baseline)
            m["extra"] = extra
            m["screen"] = screen_verdict(m) if m.get("trades") else {"passes": False, "checks": {}}
            report[label] = m
        for label in list(trials) + list(errors):
            cohort = label.split(":", 1)[1]
            params = {"trial": label, "bars_checksum": checksum, "range": [bars[0].time, bars[-1].time],
                      "resolution": args.resolution, "folds": len(ranges), "code_sha": code_sha,
                      "preregistration": PREREGISTRATION, "tag": args.tag}
            common = dict(trial_id=f"v2h6:{args.tag}:{args.symbol}:{label}", family=family, kind=TRIAL_KIND,
                          strategy_versions=versions, params=params, dataset_id=checksum,
                          cost_model={"provenance": config.fill_assumptions.provenance,
                                      "slippage_price": config.fill_assumptions.slippage_price},
                          now_utc=now)
            failed = errors.get(label) or (
                f"replay fidelity {fidelity[cohort]:.4f} < {FIDELITY_MIN}"
                if cohort in fidelity and fidelity[cohort] < FIDELITY_MIN else None)
            if failed:
                record_trial(conn, status="FAILED", notes=failed, **common)
                continue
            m = report[label]
            st = m.get("statistics") or {}
            record_trial(conn, status="COMPLETED", sharpe=st.get("sharpe"), n_observations=st.get("n"),
                         result={"gross_r": m.get("gross_r"), "cost_r": m.get("cost_r"), "net_r": m.get("net_r"),
                                 "screen_passes": m["screen"]["passes"]}, **common)
        conn.commit()

        labels = [k for k in report if report[k].get("trades")]
        span = bars[-1].time - bars[0].time + 1
        matrix = [[0.0] * len(labels) for _ in range(12)]
        for j, label in enumerate(labels):
            for r in trials[label][0]:
                t = r.trade
                if t.initial_monetary_risk > 0:
                    matrix[min(11, max(0, (t.entry_time_utc - bars[0].time) * 12 // span))][j] += \
                        t.realized_pnl / t.initial_monetary_risk
        pbo = probability_of_backtest_overfitting(matrix, 6) if len(labels) >= 2 else None
        pbo_value = pbo.pbo if pbo is not None else None
        for label in labels:
            m = report[label]
            st = m.get("statistics") or {}
            m["dsr"] = deflated_sharpe_for_family(conn, family, st.get("sharpe"), st.get("n") or 0,
                                                  st.get("skew") or 0.0, st.get("kurtosis") or 3.0)
            m["development_verdict"] = development_verdict(m, dsr=m["dsr"].get("dsr"), pbo=pbo_value)

        out = {
            "kind": "RESEARCH_V2_H6_SCREEN", "origin": "BACKTEST", "evidence": "INDEPENDENT RESEARCH / SCREEN",
            "preregistration": PREREGISTRATION, "code_sha": code_sha, "tag": args.tag, "symbol": args.symbol,
            "resolution": args.resolution, "range": [bars[0].time, bars[-1].time], "bars": len(bars),
            "bars_checksum": checksum, "folds": len(ranges), "created_at_utc": now,
            "cost_provenance": config.fill_assumptions.provenance, "news_windows_applied": news,
            "spread_percentile_prices": spread_prices, "replay_fidelity": fidelity,
            "protected_oos": "not read", "h7_holdout": "not read (XAUUSD before 2024-06-01 refused)",
            "family_pbo": {"pbo": pbo_value, "members": len(labels), "reason": pbo.reason if pbo else "n/a"},
            "screen_passers": sorted(k for k in labels if report[k]["screen"]["passes"]),
            "development_passers": sorted(k for k in labels if report[k]["development_verdict"]["passes"]),
            "errors": errors, "trials": report, "elapsed_seconds": time.time() - started,
        }
        Path(args.out).write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
        log(f"wrote {args.out}; screen passers: {out['screen_passers'] or 'NONE'}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
