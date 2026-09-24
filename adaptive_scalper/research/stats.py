"""Probabilistic and Deflated Sharpe Ratio, Probability of Backtest
Overfitting.

- PSR (Bailey & Lopez de Prado 2012): probability that the TRUE Sharpe
  ratio exceeds a benchmark `sr_benchmark`, given the observed per-period
  Sharpe `sr`, sample length `n`, skewness and (non-excess) kurtosis of
  the returns:

      PSR = Phi( (sr - sr*) * sqrt(n - 1) / sqrt(1 - skew*sr + (kurt - 1)/4 * sr^2) )

- DSR (Bailey & Lopez de Prado 2014): PSR against the Sharpe ratio one
  would EXPECT as the maximum of `n_trials` unskilled trials with the
  observed cross-trial Sharpe variance:

      sr0 = sqrt(V[SR]) * ((1 - g) * Phi^-1(1 - 1/N) + g * Phi^-1(1 - 1/(N e)))

  with g the Euler-Mascheroni constant. `n_trials` must be the number of
  configurations ACTUALLY tried -- take it from the research trial
  ledger, including the failures, or the deflation is meaningless.

- PBO via CSCV (Bailey, Borwein, Lopez de Prado & Zhu 2017): split a
  T x N performance matrix (T time blocks, N trial configurations) into
  S equal groups; for every half/half combination pick the in-sample
  best configuration and find its relative rank out of sample. PBO is
  the fraction of combinations where the in-sample winner is at or below
  the out-of-sample median (logit <= 0). Only meaningful with several
  genuinely different trials and an even number of groups; otherwise
  the result is reported as not computable, never as 0.

Sharpe ratios here are per-period (per trade or per bar), not annualized.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from itertools import combinations

from scipy.stats import norm

EULER_MASCHERONI = 0.5772156649015329


@dataclass(frozen=True)
class ReturnMoments:
    n: int
    mean: float
    std: float
    sharpe: float
    skew: float
    kurtosis: float  # non-excess: 3.0 for a normal distribution


def return_moments(returns: list[float]) -> ReturnMoments:
    n = len(returns)
    if n < 3:
        raise ValueError(f"need at least 3 returns, got {n}")
    mean = sum(returns) / n
    var = sum((r - mean) ** 2 for r in returns) / n
    if var <= 0:
        raise ValueError("returns have zero variance; Sharpe ratio is undefined")
    std = math.sqrt(var)
    skew = sum((r - mean) ** 3 for r in returns) / n / std ** 3
    kurt = sum((r - mean) ** 4 for r in returns) / n / std ** 4
    return ReturnMoments(n=n, mean=mean, std=std, sharpe=mean / std, skew=skew, kurtosis=kurt)


def probabilistic_sharpe_ratio(
    sr: float, sr_benchmark: float, n: int, skew: float = 0.0, kurtosis: float = 3.0,
) -> float:
    if n < 2:
        raise ValueError("n must be >= 2")
    denom_sq = 1.0 - skew * sr + (kurtosis - 1.0) / 4.0 * sr ** 2
    if denom_sq <= 0:
        raise ValueError("non-positive PSR variance term; moments are inconsistent")
    return float(norm.cdf((sr - sr_benchmark) * math.sqrt(n - 1) / math.sqrt(denom_sq)))


def expected_max_sharpe(n_trials: int, sharpe_variance: float) -> float:
    """Expected maximum Sharpe ratio among `n_trials` trials of zero true
    skill whose Sharpe estimates have variance `sharpe_variance`."""
    if n_trials < 1:
        raise ValueError("n_trials must be >= 1")
    if sharpe_variance < 0:
        raise ValueError("sharpe_variance must be >= 0")
    if n_trials == 1:
        return 0.0
    g = EULER_MASCHERONI
    return math.sqrt(sharpe_variance) * (
        (1 - g) * norm.ppf(1 - 1.0 / n_trials) + g * norm.ppf(1 - 1.0 / (n_trials * math.e))
    )


def deflated_sharpe_ratio(
    sr: float, n: int, *, n_trials: int, trial_sharpe_variance: float, skew: float = 0.0, kurtosis: float = 3.0,
) -> float:
    return probabilistic_sharpe_ratio(
        sr, expected_max_sharpe(n_trials, trial_sharpe_variance), n, skew=skew, kurtosis=kurtosis,
    )


@dataclass(frozen=True)
class PboResult:
    computable: bool
    pbo: float | None
    n_combinations: int
    logits: tuple[float, ...]
    reason: str


def probability_of_backtest_overfitting(performance: list[list[float]], n_groups: int) -> PboResult:
    """`performance[t][j]`: performance of trial configuration j in time
    block t (e.g. mean net R of that block). Rows are time-ordered."""
    t_blocks = len(performance)
    n_trials = len(performance[0]) if performance else 0
    if n_trials < 2:
        return PboResult(False, None, 0, (), "PBO needs at least 2 trial configurations")
    if any(len(row) != n_trials for row in performance):
        raise ValueError("performance matrix rows must all have one value per trial")
    if n_groups < 4 or n_groups % 2:
        return PboResult(False, None, 0, (), "PBO needs an even number of groups >= 4")
    if t_blocks < n_groups:
        return PboResult(False, None, 0, (), f"{t_blocks} time blocks cannot form {n_groups} groups")

    base, extra = divmod(t_blocks, n_groups)
    groups, cursor = [], 0
    for g in range(n_groups):
        size = base + (1 if g < extra else 0)
        groups.append(list(range(cursor, cursor + size)))
        cursor += size

    def mean_by_trial(rows: list[int]) -> list[float]:
        return [sum(performance[r][j] for r in rows) / len(rows) for j in range(n_trials)]

    logits = []
    for chosen in combinations(range(n_groups), n_groups // 2):
        is_rows = [r for g in chosen for r in groups[g]]
        oos_rows = [r for g in range(n_groups) if g not in chosen for r in groups[g]]
        is_perf, oos_perf = mean_by_trial(is_rows), mean_by_trial(oos_rows)
        best = max(range(n_trials), key=lambda j: (is_perf[j], -j))
        # Relative OOS rank of the IS winner in (0, 1): 1 = best OOS.
        rank = sum(1 for j in range(n_trials) if oos_perf[j] < oos_perf[best]) + 1
        omega = rank / (n_trials + 1)
        logits.append(math.log(omega / (1 - omega)))
    pbo = sum(1 for lam in logits if lam <= 0) / len(logits)
    return PboResult(True, pbo, len(logits), tuple(logits), "ok")
