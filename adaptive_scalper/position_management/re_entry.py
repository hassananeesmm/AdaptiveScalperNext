"""Safe re-entry hysteresis (directive sections 26-28).

Closing a position NEVER directly reopens one — a proposed new entry
that happens to follow a recent exit on the same symbol is treated as an
entirely NEW decision, required to clear the FULL pipeline (strategy
signal, selector, news, cost, risk, and this check too) again from
scratch. This module's only job is the ADDITIONAL hysteresis layer on
top of that: a short cooldown, plus a higher confidence bar for a
same-direction re-entry (avoiding "immediately re-open the same idea
right after being stopped out" churn), while treating a genuinely
different direction as a new setup requiring only the ordinary bar.
"""

from __future__ import annotations

from dataclasses import dataclass

ALLOW = "ALLOW"
BLOCK_REENTRY_CHURN = "BLOCK_REENTRY_CHURN"


@dataclass(frozen=True)
class ReentryParams:
    cooldown_seconds: int = 60
    normal_confidence_threshold: float = 0.62
    same_direction_confidence_threshold: float = 0.70
    min_improvement: float = 0.08


def evaluate_reentry(
    *,
    proposed_direction: str,
    proposed_raw_confidence: float,
    original_raw_confidence: float,
    last_exit_utc: int,
    last_exit_direction: str,
    now_utc: int,
    params: ReentryParams = ReentryParams(),
) -> tuple[str, str]:
    """`original_raw_confidence` is the raw confidence of the strategy
    signal that produced the position being exited from — a same-
    direction re-entry must show a MEANINGFULLY improved setup
    (`>= original_raw_confidence + min_improvement`), not just barely
    clear the bar again, which would be indistinguishable from noise
    reopening the same trade.

    Returns `(ALLOW, reason)` or `(BLOCK_REENTRY_CHURN, reason)` — this
    return shape matches `core.final_permission.FinalPermissionInput
    .reentry_check` directly.
    """
    elapsed = now_utc - last_exit_utc
    if elapsed < params.cooldown_seconds:
        return BLOCK_REENTRY_CHURN, (
            f"cooldown active: {elapsed}s since last exit on this symbol, "
            f"requires >= {params.cooldown_seconds}s"
        )

    if proposed_direction == last_exit_direction:
        required = max(params.same_direction_confidence_threshold, original_raw_confidence + params.min_improvement)
        if proposed_raw_confidence < required:
            return BLOCK_REENTRY_CHURN, (
                f"same-direction re-entry requires raw_confidence >= {required:.4f} "
                f"(same_direction_threshold={params.same_direction_confidence_threshold}, "
                f"original={original_raw_confidence:.4f} + min_improvement={params.min_improvement}); "
                f"got {proposed_raw_confidence:.4f}"
            )
        return ALLOW, (
            f"same-direction re-entry cleared the elevated bar: raw_confidence={proposed_raw_confidence:.4f} "
            f">= required={required:.4f}"
        )

    # Different direction: a genuinely new setup, only the ordinary bar applies.
    if proposed_raw_confidence < params.normal_confidence_threshold:
        return BLOCK_REENTRY_CHURN, (
            f"raw_confidence {proposed_raw_confidence:.4f} below normal_confidence_threshold "
            f"{params.normal_confidence_threshold}"
        )
    return ALLOW, "different-direction re-entry treated as a new setup; cleared the normal confidence bar"
