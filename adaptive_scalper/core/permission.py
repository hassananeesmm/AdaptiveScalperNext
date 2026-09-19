"""Kill-switch slice of the final trade-permission gate.

IMPORTANT: this is NOT the complete final trade-permission gate from
MASTER_BUILD_DIRECTIVE.md section 36. That gate also depends on account
health, news, cost, correlation, portfolio, risk, broker validation,
symbol-allowlist, and retired-strategy checks — none of which are
implemented or wired in yet. Do not treat this module as "the permission
gate is done"; it is one slice, added because the kill-switch policy
itself is fully specified and testable today. Building the whole gate
now against nonexistent subsystems would be exactly the "showpiece
module" section 118 forbids.

What IS fully specified and testable today is the kill-switch policy
(section 37): ENGAGED blocks NEW exposure, but existing-position
management (monitoring, protective-stop moves, application close) and
reconciliation must continue regardless. This module implements exactly
that slice, using the directive's own block-reason vocabulary
(BLOCK_KILL_SWITCH, section 36) so the eventual full gate can compose it
directly instead of re-implementing the same check.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from adaptive_scalper.core.kill_switch import KillSwitchState

BLOCK_KILL_SWITCH = "BLOCK_KILL_SWITCH"


class ActionKind(str, Enum):
    """The kinds of engine action the kill switch discriminates between."""

    NEW_ENTRY = "NEW_ENTRY"
    POSITION_MANAGEMENT = "POSITION_MANAGEMENT"
    RECONCILIATION = "RECONCILIATION"


@dataclass(frozen=True)
class PermissionResult:
    allowed: bool
    block_reason: str | None


def evaluate_kill_switch_permission(
    state: KillSwitchState, action: ActionKind
) -> PermissionResult:
    """Apply the kill-switch policy to one proposed engine action.

    Only NEW_ENTRY is ever blocked by an engaged kill switch.
    POSITION_MANAGEMENT and RECONCILIATION are always allowed here —
    directive section 37: "Safe existing-position management and
    reconciliation continue where appropriate."
    """
    if action is ActionKind.NEW_ENTRY and state.engaged:
        return PermissionResult(allowed=False, block_reason=BLOCK_KILL_SWITCH)
    return PermissionResult(allowed=True, block_reason=None)
