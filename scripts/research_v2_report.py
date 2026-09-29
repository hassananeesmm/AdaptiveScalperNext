"""Render the reproducible tables of the profitability analysis report from
the saved study JSONs (no recomputation, no DB access).

    python scripts/research_v2_report.py --dir data\\research --tag v2r2 > tables.md

POST-HOC sensitivity (labelled as such, never a trial): the entry-side
slippage the DEMO broker actually charged (`v2_costs_*.json`, p90 of the
signed realized slippage per fill) replacing the configured per-fill
assumption on the ENTRY fill only; exit-side slippage is not observed and
stays at the assumption. Derived from each trial's recorded slippage
stress: mean slippage R = net R - net R at slippage x2.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

SYMBOLS = ("XAUUSD", "BTCUSD")
VARIANTS = ("V1", "ST", "FH3", "FH12", "FH36", "THESIS", "MEV", "V1-MC")


def f(x, nd=3):
    return "n/a" if x is None else f"{x:+.{nd}f}" if isinstance(x, float) else str(x)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--dir", required=True)
    p.add_argument("--tag", default="v2r2")
    p.add_argument("--costs", default="v2_costs_20260929.json")
    args = p.parse_args()
    d = Path(args.dir)
    costs = json.loads((d / args.costs).read_text(encoding="utf-8"))

    for sym in SYMBOLS:
        path = d / f"v2cf_{sym}_{args.tag}.json"
        if not path.exists():
            print(f"\n(no {path.name})")
            continue
        rep = json.loads(path.read_text(encoding="utf-8"))
        demo = (costs["symbols"].get(sym) or {}).get("all") or {}
        observed = ((demo.get("slippage_realized_signed") or {}).get("p90"))
        assumed = ((demo.get("slippage_predicted") or {}).get("median"))
        print(f"\n## {sym}  (code {rep['code_sha'][:10]}, bars {rep['bars']}, checksum {rep['bars_checksum'][:12]}, "
              f"folds {rep['folds']}, PBO H4 {f(rep['family_pbo']['H4'].get('pbo'), 2)}, "
              f"H5 {f(rep['family_pbo']['H5'].get('pbo'), 2)})")
        print(f"\nPassing trials: {rep['passing_trials'] or 'NONE'}; errors: {rep['errors'] or 'none'}")
        print("\n| cohort | variant | n | gross R [95% CI] | cost R | net R | PF | win % | +folds | PSR | DSR | "
              "hold (med s) | MFE | giveback | capture | paired dGross vs V1 (t) | net@cost x1.2 | net@p90 spread | "
              "POST-HOC net@DEMO entry slip |")
        print("|" + "---|" * 19)
        for cohort, block in rep["cohorts"].items():
            for v in VARIANTS:
                m = block["variants"].get(v)
                if not m or "error" in m or not m.get("trades"):
                    print(f"| {cohort} | {v} | {m.get('error') if m else 'missing'} |" + " |" * 17)
                    continue
                ci = m.get("gross_r_ci95")
                ci_s = f"[{ci[0]:+.3f}, {ci[1]:+.3f}]" if ci else ""
                st = m.get("statistics") or {}
                dsr = (m.get("dsr") or {}).get("dsr") if isinstance(m.get("dsr"), dict) else None
                pair = m.get("paired_vs_v1_gross_r") or {}
                stress = m.get("stress_net_r") or {}
                slip_r = (m["net_r"] - stress["slippage_x2"]) if stress.get("slippage_x2") is not None else None
                post_hoc = None
                if slip_r is not None and observed is not None and assumed:
                    post_hoc = m["net_r"] + (1 - max(0.0, observed) / assumed) * slip_r / 2.0
                print(f"| {cohort} | {v} | {m['trades']} | {f(m['gross_r'])} {ci_s} | {f(m['cost_r'])} | "
                      f"{f(m['net_r'])} | {f(m.get('profit_factor'), 2)} | {m['win_rate'] * 100:.1f} | "
                      f"{m['positive_folds']}/{m['folds']} | {f(st.get('psr_vs_zero'), 3)} | {f(dsr, 3)} | "
                      f"{m.get('median_holding_seconds')} | {f(m.get('mfe_r'), 2)} | {f(m.get('giveback_r'), 2)} | "
                      f"{f(m.get('profit_capture_ratio'), 2)} | "
                      f"{f(pair.get('mean_diff'))} ({f(pair.get('t_stat'), 1)}) | {f(stress.get('cost_x1.2'))} | "
                      f"{f(stress.get('spread_p90'))} | {f(post_hoc)} |")
            print(f"| {cohort} | replay fidelity | {block['replay_fidelity']:.4f} |" + " |" * 16)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
