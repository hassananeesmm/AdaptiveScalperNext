"""Tests for the promotion gate (learning/promotion.py)."""

from __future__ import annotations

from adaptive_scalper.learning.promotion import (
    ALLOW,
    BLOCK_ARTIFACT_CHECKSUM_MISSING,
    BLOCK_CALIBRATION_NOT_CHECKED,
    BLOCK_COSTS_NOT_REALISTIC,
    BLOCK_INSUFFICIENT_SAMPLES,
    BLOCK_NON_CAUSAL_FEATURES,
    BLOCK_OOS_NOT_UNTOUCHED,
    BLOCK_RETIRED_STRATEGY,
    BLOCK_ROLLBACK_UNAVAILABLE,
    BLOCK_SPLIT_NOT_VERIFIED,
    BLOCK_SUBGROUP_STABILITY_NOT_CHECKED,
    BLOCK_WALK_FORWARD_NOT_VERIFIED,
    PromotionEvidence,
    evaluate_promotion_gate,
)


def _evidence(**overrides) -> PromotionEvidence:
    defaults = dict(
        strategy_key="momentum_continuation", training_sample_count=1000, min_required_samples=500,
        uses_causal_features=True, temporal_purged_split_verified=True, walk_forward_verified=True,
        oos_untouched=True, realistic_costs_applied=True, calibration_checked=True,
        subgroup_stability_checked=True, artifact_checksum="abc123", rollback_target_available=True,
    )
    defaults.update(overrides)
    return PromotionEvidence(**defaults)


def test_allows_when_every_requirement_met():
    decision, _ = evaluate_promotion_gate(_evidence())
    assert decision == ALLOW


def test_blocks_retired_strategy_first():
    decision, _ = evaluate_promotion_gate(_evidence(strategy_key="failed_breakout_fade", training_sample_count=0))
    assert decision == BLOCK_RETIRED_STRATEGY


def test_blocks_insufficient_samples():
    decision, _ = evaluate_promotion_gate(_evidence(training_sample_count=100, min_required_samples=500))
    assert decision == BLOCK_INSUFFICIENT_SAMPLES


def test_blocks_non_causal_features():
    decision, _ = evaluate_promotion_gate(_evidence(uses_causal_features=False))
    assert decision == BLOCK_NON_CAUSAL_FEATURES


def test_blocks_unverified_split():
    decision, _ = evaluate_promotion_gate(_evidence(temporal_purged_split_verified=False))
    assert decision == BLOCK_SPLIT_NOT_VERIFIED


def test_blocks_unverified_walk_forward():
    decision, _ = evaluate_promotion_gate(_evidence(walk_forward_verified=False))
    assert decision == BLOCK_WALK_FORWARD_NOT_VERIFIED


def test_blocks_touched_oos():
    decision, _ = evaluate_promotion_gate(_evidence(oos_untouched=False))
    assert decision == BLOCK_OOS_NOT_UNTOUCHED


def test_blocks_unrealistic_costs():
    decision, _ = evaluate_promotion_gate(_evidence(realistic_costs_applied=False))
    assert decision == BLOCK_COSTS_NOT_REALISTIC


def test_blocks_uncalibrated():
    decision, _ = evaluate_promotion_gate(_evidence(calibration_checked=False))
    assert decision == BLOCK_CALIBRATION_NOT_CHECKED


def test_blocks_subgroup_instability_unchecked():
    decision, _ = evaluate_promotion_gate(_evidence(subgroup_stability_checked=False))
    assert decision == BLOCK_SUBGROUP_STABILITY_NOT_CHECKED


def test_blocks_missing_checksum():
    decision, _ = evaluate_promotion_gate(_evidence(artifact_checksum=None))
    assert decision == BLOCK_ARTIFACT_CHECKSUM_MISSING


def test_blocks_missing_checksum_empty_string():
    decision, _ = evaluate_promotion_gate(_evidence(artifact_checksum=""))
    assert decision == BLOCK_ARTIFACT_CHECKSUM_MISSING


def test_blocks_no_rollback_target():
    decision, _ = evaluate_promotion_gate(_evidence(rollback_target_available=False))
    assert decision == BLOCK_ROLLBACK_UNAVAILABLE


def test_none_strategy_key_is_valid_for_cross_strategy_models():
    decision, _ = evaluate_promotion_gate(_evidence(strategy_key=None))
    assert decision == ALLOW


def test_samples_checked_before_causal_features():
    decision, _ = evaluate_promotion_gate(
        _evidence(training_sample_count=10, min_required_samples=500, uses_causal_features=False)
    )
    assert decision == BLOCK_INSUFFICIENT_SAMPLES
