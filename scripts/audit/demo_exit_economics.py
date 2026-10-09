"""Read-only DEMO exit-economics audit (PROFITABILITY_ROOT_CAUSE_FINAL.md, section 4).

Opens the production database with mode=ro. One row per closed runtime
position: first FULL_CLOSE review reason (or the broker SL/TP/unknown exit
kind when no agent close was decided), broker net P/L from the runtime
`deals` table (profit + commission + swap + fee) and net R against the
recorded initial monetary risk. Never touches sealed OOS data: it reads
only runtime execution tables.
"""

from __future__ import annotations

import argparse
import collections
import json
import sqlite3
import statistics as st


def classify(reason: str) -> str:
    if "thesis invalidated" in reason or "setup condition" in reason or "below the minimum" in reason:
        trig = "setup condition no longer holds" in reason
        edge = "below the minimum required" in reason
        return "THESIS_INVALIDATED/" + ("both" if trig and edge else "trigger_gone" if trig else "edge_below_min" if edge else "other")
    if "max holding" in reason:
        return "MAX_HOLD_600S"
    if "early profit" in reason:
        return "EARLY_TP_1R"
    if "giveback" in reason:
        return "PROFIT_GIVEBACK"
    if "regime" in reason:
        return "REGIME_REVERSAL"
    return "OTHER"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/adaptive_scalper.sqlite3")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    c = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True, timeout=5)
    q = lambda s, *a: c.execute(s, a).fetchall()

    first_close: dict[str, tuple[str, str]] = {}
    for bpid, payload in q("SELECT broker_position_id, payload_json FROM journal_events "
                            "WHERE event_type='POSITION_REVIEWED' ORDER BY id"):
        d = json.loads(payload)
        if d.get("selected_action") == "FULL_CLOSE" and bpid not in first_close:
            first_close[bpid] = (classify(d.get("reason", "")), d.get("reason", ""))
    exit_kind = {str(b): k for b, k in q("SELECT broker_position_id, exit_kind FROM exit_cost_observations")}
    deals = {b: (pr, cost) for b, pr, cost in q(
        "SELECT broker_position_id, SUM(profit), SUM(commission + swap + COALESCE(fee, 0)) FROM deals GROUP BY 1")}
    rows = []
    for bpid, sym, strat, risk, opened, closed in q(
            "SELECT broker_position_id, canonical_symbol, strategy_key, initial_monetary_risk, opened_at_utc, "
            "closed_at_utc FROM positions WHERE status='CLOSED'"):
        if bpid not in deals:
            continue
        gross, cost = deals[bpid]
        klass = first_close[bpid][0] if bpid in first_close else "BROKER_" + (exit_kind.get(bpid) or "UNKNOWN")
        rows.append({"exit": klass, "symbol": sym, "strategy": strat, "gross": gross, "cost": cost,
                     "net": gross + cost, "net_r": (gross + cost) / risk if risk else None,
                     "gross_r": gross / risk if risk else None, "hold_s": (closed or opened) - opened})

    def summary(group):
        nr = [r["net_r"] for r in group if r["net_r"] is not None]
        gr = [r["gross_r"] for r in group if r["gross_r"] is not None]
        return {"n": len(group), "net_usd": round(sum(r["net"] for r in group), 2),
                "gross_usd": round(sum(r["gross"] for r in group), 2),
                "mean_gross_r": round(st.mean(gr), 4) if gr else None, "mean_net_r": round(st.mean(nr), 4) if nr else None,
                "win_pct": round(100 * sum(1 for x in nr if x > 0) / len(nr), 1) if nr else None,
                "median_hold_s": st.median(r["hold_s"] for r in group) if group else None}

    out = {"total": summary(rows), "by_exit": {}, "by_strategy_symbol": {}, "by_hold_bucket": {}}
    for key, fn in (("by_exit", lambda r: r["exit"]), ("by_strategy_symbol", lambda r: f"{r['strategy']}|{r['symbol']}"),
                    ("by_hold_bucket", lambda r: "<5m" if r["hold_s"] < 300 else "5-15m" if r["hold_s"] < 900 else ">=15m")):
        groups = collections.defaultdict(list)
        for r in rows:
            groups[fn(r)].append(r)
        out[key] = {k: summary(v) for k, v in sorted(groups.items(), key=lambda kv: -len(kv[1]))}
    if args.json:
        print(json.dumps(out, indent=2))
        return
    for section, data in out.items():
        print(f"\n== {section}")
        for k, v in ({"ALL": data} if section == "total" else data).items():
            print(f"  {k:46} {v}")


if __name__ == "__main__":
    main()
