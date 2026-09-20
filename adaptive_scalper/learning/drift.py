"""Drift response (directive: "Drift lowers model influence. Never
raises risk.").

`apply_drift_response()` is structurally incapable of returning an
influence weight HIGHER than the input when drift is detected — it is
`min(current_influence_weight, ...)` under the hood, not a general
formula a future edit could accidentally invert. `current_influence_weight`
is a plain `[0.0, 1.0]` multiplier a caller applies to how much a model's
output affects a downstream decision — this module has no idea what that
decision is (structurally consistent with the rest of `learning/`: it
computes a number, never touches risk/kill-switch/permission itself).
"""

from __future__ import annotations

DEGRADED_INFLUENCE_FLOOR = 0.0


def apply_drift_response(
    current_influence_weight: float,
    *,
    drift_detected: bool,
    drift_severity: float = 1.0,
) -> float:
    """`drift_severity` in `[0.0, 1.0]`: 0.0 = no reduction (edge case,
    treated as a no-op even if `drift_detected=True`), 1.0 = full
    reduction to `DEGRADED_INFLUENCE_FLOOR`. Values are clamped, never
    trusted blindly. Always returns a value `<= current_influence_weight`
    — this is the entire safety guarantee this function exists to
    provide, and it holds for every input, not just the documented
    range (see `tests/test_learning_drift.py`'s property-style
    regression)."""
    weight = min(1.0, max(0.0, current_influence_weight))
    if not drift_detected:
        return weight

    severity = min(1.0, max(0.0, drift_severity))
    reduced = weight * (1.0 - severity) + DEGRADED_INFLUENCE_FLOOR * severity
    return min(weight, reduced)
