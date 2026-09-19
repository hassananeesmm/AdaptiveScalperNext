"""3x3 aligned-return correlation (directive section 35).

Correlation is computed over ALIGNED observations — the same set of
timestamps must be present in both series — never on series of
different lengths silently zipped together (which would misalign
returns and produce a meaningless number that looks valid). A pair with
fewer than `min_sample_size` aligned observations, or where either
series has zero variance (correlation undefined), reports `None`
("N/A") rather than a fabricated 0.0 — directive section 35's explicit
requirement: "Missing/invalid correlation = N/A, not zero."
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass

DEFAULT_MIN_SAMPLE_SIZE = 30


@dataclass(frozen=True)
class CorrelationResult:
    correlation: float | None   # None = N/A (insufficient sample or undefined)
    sample_size: int


def _aligned_pairs(a: dict[int, float], b: dict[int, float]) -> tuple[list[float], list[float]]:
    common = sorted(set(a) & set(b))
    return [a[t] for t in common], [b[t] for t in common]


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    mean_x = statistics.fmean(xs)
    mean_y = statistics.fmean(ys)
    cov = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    var_x = sum((x - mean_x) ** 2 for x in xs)
    var_y = sum((y - mean_y) ** 2 for y in ys)
    if var_x == 0 or var_y == 0:
        return None  # a constant series has undefined correlation, not 0
    return cov / (var_x * var_y) ** 0.5


def compute_pairwise_correlation(
    returns_a: dict[int, float], returns_b: dict[int, float], min_sample_size: int = DEFAULT_MIN_SAMPLE_SIZE
) -> CorrelationResult:
    """`returns_a`/`returns_b`: {timestamp: return}. Aligns on shared
    timestamps only — a timestamp present in one series but not the
    other is dropped from the comparison rather than guessed at."""
    xs, ys = _aligned_pairs(returns_a, returns_b)
    n = len(xs)
    if n < min_sample_size:
        return CorrelationResult(None, n)
    return CorrelationResult(_pearson(xs, ys), n)


def compute_correlation_matrix(
    returns_by_symbol: dict[str, dict[int, float]], min_sample_size: int = DEFAULT_MIN_SAMPLE_SIZE
) -> dict[tuple[str, str], CorrelationResult]:
    """Every symbol pair, both orderings (so callers can look up
    `matrix[(a, b)]` or `matrix[(b, a)]` without caring which came
    first) plus each symbol's self-correlation (always 1.0)."""
    symbols = sorted(returns_by_symbol)
    matrix: dict[tuple[str, str], CorrelationResult] = {}
    for symbol in symbols:
        matrix[(symbol, symbol)] = CorrelationResult(1.0, len(returns_by_symbol[symbol]))
    for i, a in enumerate(symbols):
        for b in symbols[i + 1:]:
            result = compute_pairwise_correlation(returns_by_symbol[a], returns_by_symbol[b], min_sample_size)
            matrix[(a, b)] = result
            matrix[(b, a)] = result
    return matrix


BLOCK_CORRELATION = "BLOCK_CORRELATION"
ALLOW = "ALLOW"


def evaluate_correlation_gate(
    proposed_symbol: str,
    open_symbols: list[str],
    correlation_matrix: dict[tuple[str, str], CorrelationResult],
    high_correlation_threshold: float = 0.7,
) -> tuple[str, str]:
    """Blocks a NEW proposal only when it is GENUINELY, measurably highly
    correlated (|corr| >= threshold) with an already-open position.
    Missing/insufficient correlation data (N/A) does NOT itself block —
    directive section 35 requires N/A to be reported honestly, not
    treated as either "safe" or "dangerous" by assumption."""
    for other in open_symbols:
        if other == proposed_symbol:
            continue
        result = correlation_matrix.get((proposed_symbol, other), CorrelationResult(None, 0))
        if result.correlation is None:
            continue
        if abs(result.correlation) >= high_correlation_threshold:
            return BLOCK_CORRELATION, (
                f"{proposed_symbol} correlates {result.correlation:.2f} with already-open "
                f"{other} (n={result.sample_size}), exceeding threshold {high_correlation_threshold}"
            )
    return ALLOW, "no high-correlation conflict with open positions"
