"""Tests for aligned-return correlation (directive section 35)."""

from __future__ import annotations

import pytest

from adaptive_scalper.portfolio.correlation import (
    ALLOW,
    BLOCK_CORRELATION,
    CorrelationResult,
    compute_correlation_matrix,
    compute_pairwise_correlation,
    evaluate_correlation_gate,
)


def _series(values: list[float], start_t: int = 0) -> dict[int, float]:
    return {start_t + i: v for i, v in enumerate(values)}


def test_perfectly_correlated_series():
    a = _series([1.0, 2.0, 3.0, 4.0] * 10)
    b = _series([2.0, 4.0, 6.0, 8.0] * 10)  # exactly 2x -> correlation 1.0
    result = compute_pairwise_correlation(a, b, min_sample_size=10)
    assert result.correlation == pytest.approx(1.0)


def test_perfectly_anti_correlated_series():
    a = _series([1.0, 2.0, 3.0, 4.0] * 10)
    b = _series([-1.0, -2.0, -3.0, -4.0] * 10)
    result = compute_pairwise_correlation(a, b, min_sample_size=10)
    assert result.correlation == pytest.approx(-1.0)


def test_insufficient_sample_size_is_none_not_zero():
    a = _series([1.0, 2.0, 3.0])
    b = _series([1.0, 2.0, 3.0])
    result = compute_pairwise_correlation(a, b, min_sample_size=30)
    assert result.correlation is None
    assert result.sample_size == 3


def test_zero_variance_series_is_none_not_zero():
    a = _series([5.0] * 40)  # constant series, undefined correlation
    b = _series([1.0, 2.0] * 20)
    result = compute_pairwise_correlation(a, b, min_sample_size=30)
    assert result.correlation is None


def test_only_aligned_timestamps_are_used():
    a = {0: 1.0, 1: 2.0, 2: 3.0, 3: 4.0, 999: 1000.0}  # 999 has no match in b
    b = {0: 1.0, 1: 2.0, 2: 3.0, 3: 4.0}
    # extend to reach min_sample_size via repeated distinct timestamps
    a.update({i: float(i % 5) for i in range(10, 40)})
    b.update({i: float(i % 5) for i in range(10, 40)})
    result = compute_pairwise_correlation(a, b, min_sample_size=30)
    assert result.sample_size == 34  # 4 matched + 30 matched, NOT 35 (999 excluded)


def test_mismatched_timestamps_never_silently_zipped():
    # Two series of equal LENGTH but completely disjoint timestamps must
    # align to zero shared observations, not be zipped positionally.
    a = {i: float(i) for i in range(40)}
    b = {i + 1000: float(i) for i in range(40)}
    result = compute_pairwise_correlation(a, b, min_sample_size=30)
    assert result.sample_size == 0
    assert result.correlation is None


def test_correlation_matrix_contains_self_correlation_of_one():
    data = {
        "XAUUSD": _series([1.0, 2.0, 3.0] * 15),
        "GBPJPY": _series([1.0, -1.0, 1.0] * 15),
        "BTCUSD": _series([2.0, 4.0, 6.0] * 15),
    }
    matrix = compute_correlation_matrix(data, min_sample_size=30)
    assert matrix[("XAUUSD", "XAUUSD")].correlation == 1.0


def test_correlation_matrix_is_symmetric():
    data = {
        "XAUUSD": _series([1.0, 2.0, 3.0, 1.5] * 10),
        "GBPJPY": _series([2.0, 1.0, 4.0, 0.5] * 10),
    }
    matrix = compute_correlation_matrix(data, min_sample_size=10)
    assert matrix[("XAUUSD", "GBPJPY")].correlation == matrix[("GBPJPY", "XAUUSD")].correlation


def test_correlation_matrix_covers_all_three_canonical_symbols():
    data = {
        "XAUUSD": _series([1.0, 2.0] * 20),
        "GBPJPY": _series([2.0, 1.0] * 20),
        "BTCUSD": _series([1.0, 1.0] * 20),
    }
    matrix = compute_correlation_matrix(data, min_sample_size=30)
    pairs = {frozenset(k) for k in matrix if len(set(k)) == 2}
    assert frozenset({"XAUUSD", "GBPJPY"}) in pairs
    assert frozenset({"XAUUSD", "BTCUSD"}) in pairs
    assert frozenset({"GBPJPY", "BTCUSD"}) in pairs


# --------------------------------------------------------------------------
# evaluate_correlation_gate
# --------------------------------------------------------------------------

def test_gate_blocks_on_genuinely_high_correlation():
    matrix = {("BTCUSD", "XAUUSD"): CorrelationResult(0.85, 100)}
    decision, reason = evaluate_correlation_gate("BTCUSD", ["XAUUSD"], matrix)
    assert decision == BLOCK_CORRELATION
    assert "0.85" in reason


def test_gate_allows_low_correlation():
    matrix = {("BTCUSD", "XAUUSD"): CorrelationResult(0.2, 100)}
    decision, reason = evaluate_correlation_gate("BTCUSD", ["XAUUSD"], matrix)
    assert decision == ALLOW


def test_gate_blocks_conservatively_on_missing_correlation_data_by_default():
    # External review: N/A is not proof of safety — the default policy
    # (what the composed final permission gate uses) blocks rather than
    # assumes no conflict.
    matrix = {("BTCUSD", "XAUUSD"): CorrelationResult(None, 5)}
    decision, reason = evaluate_correlation_gate("BTCUSD", ["XAUUSD"], matrix)
    assert decision == BLOCK_CORRELATION
    assert "unknown" in reason.lower() or "N/A" in reason


def test_gate_missing_correlation_data_is_informational_only_when_opted_out():
    matrix = {("BTCUSD", "XAUUSD"): CorrelationResult(None, 5)}
    decision, reason = evaluate_correlation_gate(
        "BTCUSD", ["XAUUSD"], matrix, treat_missing_as_blocking=False
    )
    assert decision == ALLOW


def test_gate_allows_when_no_open_or_pending_symbols():
    decision, reason = evaluate_correlation_gate("BTCUSD", [], {})
    assert decision == ALLOW


def test_gate_ignores_self_comparison():
    matrix = {}
    decision, reason = evaluate_correlation_gate("BTCUSD", ["BTCUSD"], matrix)
    assert decision == ALLOW


def test_gate_blocks_on_strong_negative_correlation_too():
    matrix = {("GBPJPY", "XAUUSD"): CorrelationResult(-0.9, 50)}
    decision, reason = evaluate_correlation_gate("GBPJPY", ["XAUUSD"], matrix)
    assert decision == BLOCK_CORRELATION
