"""Tests for position_management.re_entry (directive sections 26-28)."""

from __future__ import annotations

from adaptive_scalper.position_management.re_entry import ALLOW, BLOCK_REENTRY_CHURN, ReentryParams, evaluate_reentry


def _check(**overrides):
    defaults = dict(
        proposed_direction="BUY", proposed_raw_confidence=0.75, original_raw_confidence=0.60,
        last_exit_utc=1000, last_exit_direction="BUY", now_utc=1100,
    )
    defaults.update(overrides)
    return evaluate_reentry(**defaults)


def test_cooldown_blocks_immediate_reentry():
    decision, reason = _check(now_utc=1030)  # 30s elapsed, default cooldown 60s
    assert decision == BLOCK_REENTRY_CHURN
    assert "cooldown" in reason


def test_cooldown_exactly_at_boundary_allows():
    decision, _ = _check(now_utc=1060)  # exactly 60s
    assert decision == ALLOW


def test_same_direction_requires_elevated_confidence():
    # original=0.60, +0.08 = 0.68, but same_direction_threshold=0.70 is higher -> required=0.70
    decision, reason = _check(proposed_raw_confidence=0.69, now_utc=1100)
    assert decision == BLOCK_REENTRY_CHURN
    assert "same-direction" in reason


def test_same_direction_clears_elevated_bar():
    decision, _ = _check(proposed_raw_confidence=0.70, now_utc=1100)
    assert decision == ALLOW


def test_same_direction_requires_min_improvement_over_original():
    # original=0.65 -> min required = max(0.70, 0.65+0.08=0.73) = 0.73
    decision, reason = _check(original_raw_confidence=0.65, proposed_raw_confidence=0.70, now_utc=1100)
    assert decision == BLOCK_REENTRY_CHURN
    assert "same-direction" in reason


def test_different_direction_is_treated_as_new_setup():
    decision, reason = _check(proposed_direction="SELL", last_exit_direction="BUY", proposed_raw_confidence=0.62, now_utc=1100)
    assert decision == ALLOW
    assert "new setup" in reason


def test_different_direction_still_requires_normal_confidence():
    decision, _ = _check(proposed_direction="SELL", last_exit_direction="BUY", proposed_raw_confidence=0.5, now_utc=1100)
    assert decision == BLOCK_REENTRY_CHURN


def test_different_direction_bypasses_same_direction_elevated_bar():
    # 0.65 would fail the same-direction bar (0.70) but passes the normal
    # bar (0.62) for a genuinely different direction.
    decision, _ = _check(proposed_direction="SELL", last_exit_direction="BUY", proposed_raw_confidence=0.65, now_utc=1100)
    assert decision == ALLOW


def test_cooldown_checked_before_direction_logic():
    decision, reason = _check(proposed_direction="SELL", last_exit_direction="BUY", proposed_raw_confidence=0.99, now_utc=1010)
    assert decision == BLOCK_REENTRY_CHURN
    assert "cooldown" in reason


def test_custom_params_respected():
    # required = max(same_direction_threshold=0.6, original(0.60)+min_improvement(0.05)=0.65) = 0.65
    params = ReentryParams(cooldown_seconds=10, normal_confidence_threshold=0.5,
                            same_direction_confidence_threshold=0.6, min_improvement=0.05)
    decision, _ = _check(now_utc=1011, params=params, proposed_raw_confidence=0.65)
    assert decision == ALLOW
