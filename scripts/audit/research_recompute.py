"""Recompute the V1 independent-research facts from the stored r1 artifacts
(PROFITABILITY_ROOT_CAUSE_FINAL.md, section 3).

Reads only `data/research/independent_<SYMBOL>_r1.json` (development-range
BACKTEST results produced 2026-09-27). It opens no database and no bar data,
so it cannot touch the sealed OOS interval.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

SYMBOLS = ("BTCUSD", "XAUUSD", "GBPJPY")
MICRO = "microstructure_acceleration"


def main(root: str = "data/research") -> None:
    sessions_negative = sessions_total = 0
    for symbol in SYMBOLS:
        d = json.loads(Path(root, f"independent_{symbol}_r1.json").read_text(encoding="utf-8"))
        print(f"\n=== {symbol} {d['resolution']} range={d['range']} bars={d['bars']} checksum={d['bars_checksum'][:16]} "
              f"cost_provenance={d['cost_provenance']}")
        for key, s in d["sessions"].items():
            m = s["summary"]
            tag = "selector" if key.startswith("__") else "independent"
            if tag == "independent":
                sessions_total += 1
                sessions_negative += m["net_pnl"] < 0
            print(f"  {key:30} {tag:11} n={m['trades']:5d} gross_r={m['avg_gross_r']:+.3f} cost_r={m['avg_cost_r']:.3f} "
                  f"net_r={m['avg_net_r']:+.3f} net={m['net_pnl']:+10.1f} PSR={s['statistics']['psr_vs_zero']:.3g} "
                  f"folds+={s['statistics']['positive_folds']}/{s['statistics']['folds']}")
        micro = d["sessions"][MICRO]["breakdown"]
        n = sum(v["trades"] for v in micro["regime"].values())
        print(f"  micro RANGE share of entries: {micro['regime'].get('RANGE', {}).get('trades', 0) / n:.1%} (n={n})")
        for bucket, v in micro["holding_duration"].items():
            print(f"  micro hold {bucket:8} n={v['trades']:4d} gross_r={v['avg_gross_r']:+.3f} cost_r={v['avg_cost_r']:.3f} net_r={v['avg_net_r']:+.3f}")
        thesis = {k: v for k, v in micro["exit_reason"].items() if "THESIS" in k.upper() or "SETUP" in k.upper()}
        for k, v in thesis.items():
            print(f"  micro exit {k}: n={v['trades']} net_r={v['avg_net_r']:+.3f}")
        sel = d["selector_study"]["per_strategy"][MICRO]
        print(f"  selector: micro share={sel['selection_share']:.1%} stated_p={sel['stated_p_selected']:.3f} "
              f"expected_net_r={sel['expected_net_r_selected']:+.3f} realized_net_r={sel['realized_net_r_selected_matched']:+.3f}")
        for qrow in d["selector_study"]["expected_vs_realized_quantiles"]:
            print(f"    quintile {qrow['quantile']}: expected_net_r={qrow['expected_net_r_mean']:+.3f} "
                  f"realized_net_r={qrow['realized_net_r_mean']:+.3f} n={qrow['candidates']}")
    print(f"\nindependent strategy x symbol sessions net-negative: {sessions_negative}/{sessions_total}")


if __name__ == "__main__":
    main(*sys.argv[1:])
