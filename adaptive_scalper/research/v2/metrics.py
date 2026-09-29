"""Per-trial measurements for H4/H5 (master prompt sections 35, 36, 40-42).

Input: `ReplayedTrade`s of ONE trial (cohort x exit variant x symbol).
R is always relative to the trade's fixed initial monetary risk. `exec_r`,
`mfe_r`, `mae_r` are executable-price moves in initial-stop units (entry
spread/slippage are inside the entry price), so giveback = MFE - exec R
and profit capture = mean exec R / mean MFE.

Stress figures rescale the cost components of the SAME trades; stop and
target touches are not re-simulated under the stressed prices (stated as
an approximation in every report).
"""

from __future__ import annotations

import math
import statistics
from collections import defaultdict

from adaptive_scalper.backtest.engine import BACKTEST_RANGE_ENDED, STOP_LOSS_HIT, TAKE_PROFIT_HIT
from adaptive_scalper.research.trade_analysis import confidence_bucket, from_simulated, session_bucket
from adaptive_scalper.research.v2.analysis import duration_bucket, session_stats
from adaptive_scalper.research.v2.counterfactual import FIXED_HOLD_REASON

COST_MULTIPLIERS = (1.1, 1.2, 1.3, 1.5)
SPREAD_PERCENTILES = (75, 90, 95)
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def exit_category(reason: str | None) -> str:
    r = reason or ""
    if r == TAKE_PROFIT_HIT:
        return "target"
    if r == STOP_LOSS_HIT:
        return "stop"
    if r.startswith("max holding time") or r.startswith(FIXED_HOLD_REASON):
        return "timeout"
    if r.startswith("original strategy thesis invalidated"):
        return "thesis_invalidated"
    if r.startswith("regime materially reversed"):
        return "regime_reversal"
    if r.startswith("early profit objective") or r.startswith("profit giveback"):
        return "early_close"
    if r == BACKTEST_RANGE_ENDED:
        return "range_end"
    return "other"


def _mean(values):
    values = list(values)
    return sum(values) / len(values) if values else None


def _median(values):
    values = list(values)
    return statistics.median(values) if values else None


def _ci95(values):
    n = len(values)
    if n < 2:
        return None
    m = sum(values) / n
    sd = statistics.stdev(values)
    half = 1.96 * sd / math.sqrt(n)
    return [m - half, m + half]


def _streaks(signs: list[int]) -> tuple[int, int]:
    best_w = best_l = cur_w = cur_l = 0
    for s in signs:
        cur_w, cur_l = (cur_w + 1, 0) if s > 0 else (0, cur_l + 1) if s < 0 else (0, 0)
        best_w, best_l = max(best_w, cur_w), max(best_l, cur_l)
    return best_w, best_l


def _max_drawdown(rs: list[float]) -> float:
    peak = cum = dd = 0.0
    for r in rs:
        cum += r
        peak = max(peak, cum)
        dd = max(dd, peak - cum)
    return dd


def _tercile_labeller(values: list[float]):
    s = sorted(v for v in values if v is not None)
    if len(s) < 3:
        return lambda v: "UNKNOWN"
    lo, hi = s[len(s) // 3], s[(2 * len(s)) // 3]
    return lambda v: "UNKNOWN" if v is None else ("low" if v < lo else "high" if v >= hi else "mid")


def _compact(rows) -> dict:
    n = len(rows)
    return {"n": n, "gross_r": _mean(r["gross_r"] for r in rows), "cost_r": _mean(r["cost_r"] for r in rows),
            "net_r": _mean(r["net_r"] for r in rows),
            "win_rate": (sum(1 for r in rows if r["net_r"] > 0) / n) if n else None}


def trial_metrics(replayed, *, symbol: str, symbol_spec, n_folds: int, spread_percentile_prices: dict,
                  baseline_gross_r: dict | None = None) -> dict:
    rows = []
    per_unit = symbol_spec.trade_tick_value / symbol_spec.trade_tick_size
    for rt in sorted(replayed, key=lambda r: (r.trade.entry_time_utc, r.entry_key)):
        t = rt.trade
        risk = t.initial_monetary_risk
        if not risk or risk <= 0:
            continue
        view = from_simulated(t, symbol)
        spread_cost = (t.entry_spread_cost or 0.0) + (t.exit_spread_cost or 0.0)
        slippage_cost = (t.entry_slippage_cost or 0.0) + (t.exit_slippage_cost or 0.0)
        rows.append({
            "rt": rt, "view": view, "fold": rt.fold, "gross": view.gross, "net": view.net, "cost": view.total_cost,
            "gross_r": view.gross / risk, "cost_r": view.total_cost / risk, "net_r": view.net / risk,
            "spread_cost": spread_cost, "slippage_cost": slippage_cost, "money_per_price": t.volume * per_unit,
            "risk": risk, "exit": exit_category(t.exit_reason), "holding": view.holding_seconds,
            "atr": (t.entry_features or {}).get("atr"), "spread_r": spread_cost / risk,
        })
    n = len(rows)
    if not n:
        return {"trades": 0}
    net_r = [r["net_r"] for r in rows]
    gross_r = [r["gross_r"] for r in rows]
    wins = [r for r in rows if r["net"] > 0]
    losses = [r for r in rows if r["net"] < 0]
    gross_win, gross_loss = sum(r["net"] for r in wins), sum(r["net"] for r in losses)
    best_w, best_l = _streaks([1 if r["net"] > 0 else -1 if r["net"] < 0 else 0 for r in rows])
    mfe = [r["rt"].mfe_r for r in rows]
    exec_r = [r["rt"].exec_r for r in rows]
    fold_net = defaultdict(float)
    for r in rows:
        fold_net[r["fold"]] += r["net_r"]
    exits = defaultdict(int)
    for r in rows:
        exits[r["exit"]] += 1

    stats = session_stats([r["view"] for r in rows])
    out = {
        "trades": n,
        "gross_pnl": sum(r["gross"] for r in rows), "costs": sum(r["cost"] for r in rows),
        "net_pnl": sum(r["net"] for r in rows),
        "gross_r": _mean(gross_r), "gross_r_ci95": _ci95(gross_r), "cost_r": _mean(r["cost_r"] for r in rows),
        "net_r": _mean(net_r), "net_r_ci95": _ci95(net_r), "total_net_r": sum(net_r),
        "win_rate": len(wins) / n, "loss_rate": len(losses) / n,
        "profit_factor": (gross_win / abs(gross_loss)) if gross_loss < 0 else None,
        "avg_win_r": _mean(r["net_r"] for r in wins), "avg_loss_r": _mean(r["net_r"] for r in losses),
        "expectancy_r": _mean(net_r),
        "max_drawdown_r": _max_drawdown(net_r),
        "max_consecutive_wins": best_w, "max_consecutive_losses": best_l,
        "avg_holding_seconds": _mean(r["holding"] for r in rows),
        "median_holding_seconds": _median(r["holding"] for r in rows),
        "mae_r": _mean(r["rt"].mae_r for r in rows), "mae_r_median": _median(r["rt"].mae_r for r in rows),
        "mfe_r": _mean(mfe), "mfe_r_median": _median(mfe),
        "peak_r": _mean(mfe), "realized_exec_r": _mean(exec_r),
        "giveback_r": _mean(m - e for m, e in zip(mfe, exec_r)),
        "profit_capture_ratio": (_mean(exec_r) / _mean(mfe)) if _mean(mfe) else None,
        "exit_shares": {k: v / n for k, v in sorted(exits.items())},
        "fold_net_r": [fold_net.get(f, 0.0) for f in range(n_folds)],
        "positive_folds": sum(1 for f in range(n_folds) if fold_net.get(f, 0.0) > 0), "folds": n_folds,
        "statistics": stats,
        "gross_to_cost": (_mean(gross_r) / _mean(r["cost_r"] for r in rows)) if _mean(r["cost_r"] for r in rows) else None,
    }

    stress = {}
    for k in COST_MULTIPLIERS:
        stress[f"cost_x{k:g}"] = _mean((r["gross"] - k * r["cost"]) / r["risk"] for r in rows)
    for p, price in spread_percentile_prices.items():
        stress[f"spread_p{p}"] = _mean(
            (r["net"] + r["spread_cost"] - price * r["money_per_price"]) / r["risk"] for r in rows)
    stress["slippage_x2"] = _mean((r["net"] - r["slippage_cost"]) / r["risk"] for r in rows)
    out["stress_net_r"] = stress
    out["stress_note"] = "cost components rescaled on the same trades; SL/TP touches not re-simulated"

    if baseline_gross_r is not None:
        diffs = [r["gross_r"] - baseline_gross_r[r["rt"].entry_key] for r in rows if r["rt"].entry_key in baseline_gross_r]
        if len(diffs) >= 2:
            sd = statistics.stdev(diffs)
            out["paired_vs_v1_gross_r"] = {
                "n": len(diffs), "mean_diff": _mean(diffs),
                "t_stat": (_mean(diffs) / (sd / math.sqrt(len(diffs)))) if sd > 0 else None}

    vol = _tercile_labeller([r["atr"] for r in rows])
    spr = _tercile_labeller([r["spread_r"] for r in rows])
    dims = {
        "direction": lambda r: r["view"].direction,
        "strategy": lambda r: r["view"].strategy_key,
        "session": lambda r: session_bucket(r["view"]),
        "regime": lambda r: r["view"].entry_regime or "UNKNOWN",
        "volatility_tercile": lambda r: vol(r["atr"]),
        "spread_cost_tercile": lambda r: spr(r["spread_r"]),
        "confidence": lambda r: confidence_bucket(r["view"]),
        "holding_duration": lambda r: duration_bucket(r["holding"]),
        "hour_utc": lambda r: f"{(r['view'].entry_time_utc % 86_400) // 3600:02d}",
        "weekday": lambda r: WEEKDAYS[((r["view"].entry_time_utc // 86_400) + 3) % 7],
        "exit": lambda r: r["exit"],
    }
    segments = {}
    for name, fn in dims.items():
        groups = defaultdict(list)
        for r in rows:
            groups[fn(r)].append(r)
        segments[name] = {k: _compact(v) for k, v in sorted(groups.items())}
    out["segments"] = segments
    return out


def development_verdict(m: dict, *, dsr: float | None, pbo: float | None) -> dict:
    """The unchanged development criteria (master prompt section 39)."""
    st = m.get("statistics") or {}
    checks = {
        "net_r_positive": (m.get("net_r") or -1) > 0,
        "gross_ge_1_5x_cost": m.get("gross_r") is not None and m.get("cost_r") is not None
        and m["gross_r"] >= 1.5 * m["cost_r"],
        "folds_8_of_12": m.get("positive_folds", 0) >= 8,
        "psr_ge_0_95": (st.get("psr_vs_zero") or 0) >= 0.95,
        "dsr_ge_0_95": (dsr or 0) >= 0.95,
        "pbo_le_0_20": pbo is not None and pbo <= 0.20,
        "trades_ge_100": m.get("trades", 0) >= 100,
        "net_positive_at_cost_x1_2": ((m.get("stress_net_r") or {}).get("cost_x1.2") or -1) > 0,
    }
    return {"checks": checks, "passes": all(checks.values())}
