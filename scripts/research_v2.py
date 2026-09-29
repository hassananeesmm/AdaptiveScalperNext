"""Run the pre-registered Strategy V2 research grid for one symbol.

RESEARCH ONLY -- BACKTEST evidence on development data, research DB copy
only; never the production DB, never a broker, never the protected OOS.

    python scripts/research_v2.py --symbol XAUUSD --start 2025-06-01 --end 2026-06-30 ^
        --research-db data\\research\\v2_20260929.sqlite3 --tag v2r1 --out data\\research\\v2_XAUUSD_v2r1.json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from adaptive_scalper.cli.research import _inputs, open_research_db  # noqa: E402
from adaptive_scalper.research.independent import deflated_sharpe_for_family, fold_index_ranges  # noqa: E402
from adaptive_scalper.research.v2.analysis import analyse_session, session_stats  # noqa: E402
from adaptive_scalper.research.v2.calibration import fit_calibration, reliability_table  # noqa: E402
from adaptive_scalper.research.v2.runner import pbo_for_family, record_v2_trials, run_v2_study  # noqa: E402


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

    cfg, conn = open_research_db(args.config, args.research_db)
    try:
        bars, spec, config, news = _inputs(SimpleNamespace(**vars(args)), conn, cfg)
        now = int(time.time())
        started = time.time()

        def progress(session):
            status = session.error or f"{sum(len(f.trades) for f in session.folds)} trades"
            print(f"[{time.time() - started:7.1f}s] {session.label}: {status}", flush=True)

        sessions = run_v2_study(bars, args.symbol, args.resolution, spec, config=config, n_folds=args.folds,
                                now_utc=now, progress=progress)
        ranges = fold_index_ranges(len(bars), args.folds, config.feature_lookback)

        record_v2_trials(conn, sessions, args.symbol, bars, run_tag=args.tag,
                         production_db_path=cfg.database.path,
                         stats_fn=lambda s: session_stats(s.views(args.symbol)), now_utc=now)
        conn.commit()

        report_sessions = {}
        for label, session in sessions.items():
            entry = analyse_session(session, args.symbol, bars, ranges, spec)
            if session.family != "v1" and session.error is None:
                st = entry["statistics"]
                entry["dsr"] = deflated_sharpe_for_family(
                    conn, f"v2-{session.family}:{args.symbol}", st["sharpe"], st["n"],
                    st.get("skew") or 0.0, st.get("kurtosis") or 3.0)
            report_sessions[label] = entry

        families = {}
        for fam in ("H1", "H2", "H3"):
            members = [s for s in sessions.values() if s.family == fam]
            families[fam] = pbo_for_family(members, args.symbol, bars[0].time, bars[-1].time)

        # Out-of-fold reliability of raw_confidence per strategy: calibrate on
        # the first half of the folds, evaluate on the second half.
        mid = bars[ranges[len(ranges) // 2][0]].time
        reliability = {}
        for label, session in sessions.items():
            if session.family == "v1" and session.strategy != "__selector__" and session.error is None:
                views = session.views(args.symbol)
                cal = fit_calibration(views, session.strategy, before_utc=mid)
                later = [t for t in views if t.entry_time_utc >= mid]
                reliability[session.strategy] = reliability_table(later, cal) if cal else "UNKNOWN (too few trades)"

        report = {
            "kind": "RESEARCH_V2_STUDY", "origin": "BACKTEST", "evidence": "INDEPENDENT RESEARCH / V2 CANDIDATE",
            "symbol": args.symbol, "resolution": args.resolution, "range": [bars[0].time, bars[-1].time],
            "bars": len(bars), "folds": len(ranges), "tag": args.tag, "created_at_utc": now,
            "cost_provenance": config.fill_assumptions.provenance, "news_windows_applied": news,
            "protected_oos": "not read (assert_outside_reserved_oos)",
            "sessions": report_sessions, "family_pbo": families, "raw_confidence_reliability": reliability,
            "elapsed_seconds": time.time() - started,
        }
        Path(args.out).write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
        print(f"wrote {args.out}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
