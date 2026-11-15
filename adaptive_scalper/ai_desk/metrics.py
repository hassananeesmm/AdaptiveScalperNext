"""Outcome statistics with explicit uncertainty (pure, deterministic).

Every summary carries its sample size and a bootstrap confidence interval of
the mean (fixed seed, so a replay reproduces the same numbers). The
`MIN_SAMPLE_*` constants are the desk's PRE-REGISTERED evidence thresholds:
they are code constants, versioned with `METRICS_VERSION`, never tuned on the
data they judge.
"""

from __future__ import annotations

import math
import random
import statistics
from collections.abc import Sequence

METRICS_VERSION = "1"
MIN_SAMPLE_DESCRIPTIVE = 30     # below this a per-group statistic is "insufficient"
MIN_SAMPLE_PROFITABILITY = 100  # below this no profitability claim is SUPPORTED
BOOTSTRAP_RESAMPLES = 2000
BOOTSTRAP_SEED = 20261114


def max_drawdown(values: Sequence[float]) -> float:
    peak = cum = 0.0
    worst = 0.0
    for v in values:
        cum += v
        peak = max(peak, cum)
        worst = max(worst, peak - cum)
    return worst


def bootstrap_mean_ci(values: Sequence[float], *, alpha: float = 0.05,
                      resamples: int = BOOTSTRAP_RESAMPLES, seed: int = BOOTSTRAP_SEED) -> tuple[float, float] | None:
    n = len(values)
    if n < 2:
        return None
    rng = random.Random(seed)
    vals = list(values)
    means = sorted(statistics.fmean(rng.choices(vals, k=n)) for _ in range(resamples))
    return (means[int((alpha / 2) * (resamples - 1))], means[int((1 - alpha / 2) * (resamples - 1))])


def summarize(values: Sequence[float | None]) -> dict:
    vals = [float(v) for v in values if v is not None and math.isfinite(float(v))]
    n = len(vals)
    if n == 0:
        return {"n": 0, "status": "NO_DATA"}
    wins = [v for v in vals if v > 0]
    losses = [v for v in vals if v < 0]
    gross_win, gross_loss = sum(wins), -sum(losses)
    ci = bootstrap_mean_ci(vals)
    return {
        "n": n,
        "status": "OK" if n >= MIN_SAMPLE_DESCRIPTIVE else "INSUFFICIENT_SAMPLE",
        "expectancy": round(statistics.fmean(vals), 5),
        "expectancy_ci95": None if ci is None else [round(ci[0], 5), round(ci[1], 5)],
        "win_rate": round(len(wins) / n, 4),
        "avg_win": round(statistics.fmean(wins), 5) if wins else None,
        "avg_loss": round(statistics.fmean(losses), 5) if losses else None,
        "profit_factor": round(gross_win / gross_loss, 4) if gross_loss > 0 else None,
        "total": round(sum(vals), 5),
        "max_drawdown": round(max_drawdown(vals), 5),
        "stdev": round(statistics.stdev(vals), 5) if n > 1 else None,
    }


def ci_excludes_zero_positive(summary: dict) -> bool:
    ci = summary.get("expectancy_ci95")
    return bool(ci) and ci[0] > 0


def ci_excludes_zero_negative(summary: dict) -> bool:
    ci = summary.get("expectancy_ci95")
    return bool(ci) and ci[1] < 0


def bootstrap_diff_ci(a: Sequence[float], b: Sequence[float], *, resamples: int = BOOTSTRAP_RESAMPLES,
                      seed: int = BOOTSTRAP_SEED) -> tuple[float, float] | None:
    """95 % CI of mean(a) - mean(b) by independent resampling."""
    if len(a) < 2 or len(b) < 2:
        return None
    rng = random.Random(seed)
    la, lb = list(a), list(b)
    diffs = sorted(statistics.fmean(rng.choices(la, k=len(la))) - statistics.fmean(rng.choices(lb, k=len(lb)))
                   for _ in range(resamples))
    return (diffs[int(0.025 * (resamples - 1))], diffs[int(0.975 * (resamples - 1))])


def psi(expected: Sequence[float], actual: Sequence[float], bins: int = 10) -> float | None:
    """Population stability index between two samples (drift measure)."""
    e = sorted(float(x) for x in expected if x is not None)
    a = [float(x) for x in actual if x is not None]
    if len(e) < 20 or len(a) < 20:
        return None
    edges = [e[int(i * (len(e) - 1) / bins)] for i in range(1, bins)]

    def hist(xs):
        counts = [0] * bins
        for x in xs:
            i = 0
            while i < len(edges) and x > edges[i]:
                i += 1
            counts[i] += 1
        return [max(c / len(xs), 1e-4) for c in counts]

    pe, pa = hist(e), hist(a)
    return round(sum((x - y) * math.log(x / y) for x, y in zip(pa, pe, strict=True)), 5)


def brier(pairs: Sequence[tuple[float, int]]) -> float | None:
    if not pairs:
        return None
    return round(statistics.fmean((p - o) ** 2 for p, o in pairs), 5)
