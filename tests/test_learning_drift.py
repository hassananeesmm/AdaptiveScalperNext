"""Tests for the drift-response guarantee (learning/drift.py) — directive:
drift lowers model influence, never raises risk."""

from __future__ import annotations

import itertools

from adaptive_scalper.learning.drift import apply_drift_response


def test_no_drift_leaves_weight_unchanged():
    assert apply_drift_response(0.8, drift_detected=False) == 0.8


def test_full_severity_drift_reduces_toward_floor():
    result = apply_drift_response(0.8, drift_detected=True, drift_severity=1.0)
    assert result == 0.0


def test_partial_severity_reduces_proportionally():
    result = apply_drift_response(1.0, drift_detected=True, drift_severity=0.5)
    assert result == 0.5


def test_zero_severity_drift_detected_is_a_no_op():
    result = apply_drift_response(0.8, drift_detected=True, drift_severity=0.0)
    assert result == 0.8


def test_weight_clamped_to_valid_range():
    assert apply_drift_response(1.5, drift_detected=False) == 1.0
    assert apply_drift_response(-0.5, drift_detected=False) == 0.0


def test_severity_clamped_to_valid_range():
    result_over = apply_drift_response(1.0, drift_detected=True, drift_severity=5.0)
    assert result_over == 0.0
    result_under = apply_drift_response(1.0, drift_detected=True, drift_severity=-5.0)
    assert result_under == 1.0  # negative severity clamps to 0 -> no reduction


def test_never_raises_influence_property_across_many_inputs():
    """Property-style regression: for every combination tried, the
    output must never exceed the input — this IS the safety guarantee,
    not an incidental property of one example."""
    weights = [0.0, 0.1, 0.25, 0.5, 0.75, 0.9, 1.0]
    severities = [0.0, 0.1, 0.3, 0.5, 0.7, 0.9, 1.0]
    for weight, detected, severity in itertools.product(weights, [True, False], severities):
        result = apply_drift_response(weight, drift_detected=detected, drift_severity=severity)
        assert result <= weight + 1e-12, (
            f"apply_drift_response({weight}, drift_detected={detected}, drift_severity={severity}) "
            f"returned {result} > input weight {weight}"
        )
