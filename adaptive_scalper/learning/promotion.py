"""Promotion gate (directive: promotion requires minimum samples, causal
features, temporal/purged split, walk-forward, untouched OOS, realistic
cost, calibration, subgroup stability, artifact checksum, rollback
capability).

Pure function, fail-closed: every one of directive's named requirements
is a REQUIRED boolean/count input with no default that could silently
mean "assumed satisfied" — a caller that hasn't actually verified a
requirement must pass `False` (or omit the count), not skip the
argument, exactly like `core.final_permission`'s pattern of no fake
"always clean" defaults. This function does not itself RUN a walk-
forward/OOS evaluation (that lives in the backtest/walk-forward
subsystem) — it is the deterministic decision core that subsystem's
verified results feed into before a model may become `CURRENT`.
"""

from __future__ import annotations

from dataclasses import dataclass

from adaptive_scalper.config.constants import RETIRED_STRATEGY_KEYS

ALLOW = "ALLOW"
BLOCK_RETIRED_STRATEGY = "BLOCK_RETIRED_STRATEGY"
BLOCK_INSUFFICIENT_SAMPLES = "BLOCK_INSUFFICIENT_SAMPLES"
BLOCK_NON_CAUSAL_FEATURES = "BLOCK_NON_CAUSAL_FEATURES"
BLOCK_SPLIT_NOT_VERIFIED = "BLOCK_SPLIT_NOT_VERIFIED"
BLOCK_WALK_FORWARD_NOT_VERIFIED = "BLOCK_WALK_FORWARD_NOT_VERIFIED"
BLOCK_OOS_NOT_UNTOUCHED = "BLOCK_OOS_NOT_UNTOUCHED"
BLOCK_COSTS_NOT_REALISTIC = "BLOCK_COSTS_NOT_REALISTIC"
BLOCK_CALIBRATION_NOT_CHECKED = "BLOCK_CALIBRATION_NOT_CHECKED"
BLOCK_SUBGROUP_STABILITY_NOT_CHECKED = "BLOCK_SUBGROUP_STABILITY_NOT_CHECKED"
BLOCK_ARTIFACT_CHECKSUM_MISSING = "BLOCK_ARTIFACT_CHECKSUM_MISSING"
BLOCK_ROLLBACK_UNAVAILABLE = "BLOCK_ROLLBACK_UNAVAILABLE"


@dataclass(frozen=True)
class PromotionEvidence:
    strategy_key: str | None
    training_sample_count: int
    min_required_samples: int
    uses_causal_features: bool
    temporal_purged_split_verified: bool
    walk_forward_verified: bool
    oos_untouched: bool
    realistic_costs_applied: bool
    calibration_checked: bool
    subgroup_stability_checked: bool
    artifact_checksum: str | None
    rollback_target_available: bool


def evaluate_promotion_gate(evidence: PromotionEvidence) -> tuple[str, str]:
    """Checked in a fixed order; returns the FIRST failing requirement."""
    if evidence.strategy_key is not None and evidence.strategy_key in RETIRED_STRATEGY_KEYS:
        return BLOCK_RETIRED_STRATEGY, f"{evidence.strategy_key!r} is permanently retired (directive section 8)"

    if evidence.training_sample_count < evidence.min_required_samples:
        return BLOCK_INSUFFICIENT_SAMPLES, (
            f"training_sample_count={evidence.training_sample_count} below "
            f"min_required_samples={evidence.min_required_samples}"
        )

    if not evidence.uses_causal_features:
        return BLOCK_NON_CAUSAL_FEATURES, "features are not verified causal/no-lookahead"

    if not evidence.temporal_purged_split_verified:
        return BLOCK_SPLIT_NOT_VERIFIED, "temporal/purged train-test split not verified"

    if not evidence.walk_forward_verified:
        return BLOCK_WALK_FORWARD_NOT_VERIFIED, "walk-forward evaluation not verified"

    if not evidence.oos_untouched:
        return BLOCK_OOS_NOT_UNTOUCHED, "out-of-sample data was not proven untouched during development"

    if not evidence.realistic_costs_applied:
        return BLOCK_COSTS_NOT_REALISTIC, "evaluation did not apply realistic transaction costs"

    if not evidence.calibration_checked:
        return BLOCK_CALIBRATION_NOT_CHECKED, "prediction calibration was not checked"

    if not evidence.subgroup_stability_checked:
        return BLOCK_SUBGROUP_STABILITY_NOT_CHECKED, "regime/strategy subgroup stability was not checked"

    if not evidence.artifact_checksum:
        return BLOCK_ARTIFACT_CHECKSUM_MISSING, "no artifact checksum recorded — cannot verify integrity later"

    if not evidence.rollback_target_available:
        return BLOCK_ROLLBACK_UNAVAILABLE, "no rollback target available — promotion must never be one-way"

    return ALLOW, "passed every promotion requirement"
