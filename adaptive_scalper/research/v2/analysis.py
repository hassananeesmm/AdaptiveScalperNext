"""Per-session measurements for the V1-vs-V2 comparison: gross/cost/net R,
PSR, exit reasons, holding-duration buckets, MAE/MFE in R (from the bars
between entry and exit) and, for selector sessions, which strategy was
selected. Evidence class: BACKTEST."""

from __future__ import annotations

import bisect
import math
from collections import Counter, defaultdict

from adaptive_scalper.research.stats import probabilistic_sharpe_ratio, return_moments
from adaptive_scalper.research.trade_analysis import summarize

DURATION_BUCKETS = ((300, "<5m"), (900, "5-15m"), (3600, "15-60m"), (None, ">=60m"))


def duration_bucket(seconds: int) -> str:
    for limit, label in DURATION_BUCKETS:
        if limit is None or seconds < limit:
            return label
    return DURATION_BUCKETS[-1][1]


def exit_family(reason: str | None) -> str:
    """Collapse parametrised exit reasons ("max holding time reached (600s
    >= 600s)") to a stable label."""
    if not reason:
        return "unknown"
    return reason.split(" (")[0].split(":")[0].strip()


def session_stats(views) -> dict:
    returns = [t.net / t.initial_risk for t in views if t.initial_risk > 0]
    out = {"n": len(returns), "sharpe": None, "psr_vs_zero": None, "skew": None, "kurtosis": None, "note": None}
    try:
        m = return_moments(returns)
        out.update(sharpe=m.sharpe, skew=m.skew, kurtosis=m.kurtosis,
                   psr_vs_zero=probabilistic_sharpe_ratio(m.sharpe, 0.0, m.n, m.skew, m.kurtosis))
    except ValueError as exc:
        out["note"] = str(exc)
    return out


def _r_group(views) -> dict:
    with_r = [t for t in views if t.initial_risk > 0]
    n = len(with_r)
    if not n:
        return {"n": 0}
    return {
        "n": n,
        "gross_r": sum(t.gross / t.initial_risk for t in with_r) / n,
        "cost_r": sum(t.total_cost / t.initial_risk for t in with_r) / n,
        "net_r": sum(t.net / t.initial_risk for t in with_r) / n,
        "net_win_rate": sum(1 for t in with_r if t.net > 0) / n,
    }


def excursions(folds, bar_folds, spec) -> list[tuple[float, float]]:
    """(MAE_R, MFE_R) per closed trade, from the M5 bars it was open over.
    R = price excursion / the trade's initial stop distance in price."""
    out = []
    per_unit = spec.trade_tick_value / spec.trade_tick_size
    for result, bars in zip(folds, bar_folds):
        times = [b.time for b in bars]
        for t in result.trades:
            if t.exit_time_utc is None or t.initial_monetary_risk <= 0 or t.volume <= 0:
                continue
            stop_distance = t.initial_monetary_risk / (t.volume * per_unit)
            if stop_distance <= 0 or not math.isfinite(stop_distance):
                continue
            lo = bisect.bisect_left(times, t.entry_time_utc)
            hi = max(lo + 1, bisect.bisect_left(times, t.exit_time_utc) + 1)
            window = bars[lo:hi]
            if not window:
                continue
            high, low = max(b.high for b in window), min(b.low for b in window)
            if t.direction == "BUY":
                fav, adv = high - t.entry_price, t.entry_price - low
            else:
                fav, adv = t.entry_price - low, high - t.entry_price
            out.append((max(0.0, adv) / stop_distance, max(0.0, fav) / stop_distance))
    return out


def _percentiles(values: list[float]) -> dict:
    if not values:
        return {}
    s = sorted(values)
    pick = lambda q: s[min(len(s) - 1, int(q * (len(s) - 1) + 0.5))]  # noqa: E731
    return {"mean": sum(s) / len(s), "p25": pick(0.25), "median": pick(0.5), "p75": pick(0.75)}


def analyse_session(session, symbol: str, bars, ranges, spec) -> dict:
    if session.error is not None:
        return {"error": session.error}
    views = session.views(symbol)
    by_exit, by_duration = defaultdict(list), defaultdict(list)
    for t in views:
        by_exit[exit_family(t.exit_reason)].append(t)
        by_duration[duration_bucket(t.holding_seconds)].append(t)
    exc = excursions(session.folds, [bars[a:b] for a, b in ranges], spec)
    fold_net = [sum(tr.realized_pnl or 0.0 for tr in f.trades if tr.exit_time_utc is not None) for f in session.folds]
    report = {
        "summary": summarize(views),
        "r": _r_group(views),
        "statistics": session_stats(views),
        "positive_folds": sum(1 for v in fold_net if v > 0), "folds": len(fold_net), "fold_net_pnl": fold_net,
        "halted_folds": sum(1 for f in session.folds if f.risk_halted_scans > 0),
        "max_fold_drawdown": max((f.metrics.max_drawdown for f in session.folds), default=0.0),
        "turnover_trades_per_fold": len(views) / len(session.folds) if session.folds else None,
        "exit_reasons": {k: _r_group(v) for k, v in sorted(by_exit.items())},
        "holding_duration": {label: _r_group(by_duration[label]) for _, label in DURATION_BUCKETS if by_duration[label]},
        "mae_r": _percentiles([a for a, _ in exc]), "mfe_r": _percentiles([f for _, f in exc]),
        "config_fingerprints": sorted({f.config_fingerprint for f in session.folds}),
    }
    if session.strategy == "__selector__":
        counts = Counter(t.strategy_key for t in views)
        report["selection_distribution"] = {k: {"trades": c, "share": c / len(views)} for k, c in counts.most_common()}
        report["by_strategy"] = {k: _r_group([t for t in views if t.strategy_key == k]) for k in counts}
    if session.extra:
        report["extra"] = session.extra
    return report
