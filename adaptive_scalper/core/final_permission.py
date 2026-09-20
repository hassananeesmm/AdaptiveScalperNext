"""Composed final trade-permission gate (directive section 36).

This is the point where every independent gate built so far — canonical
symbol allow-list, retired-strategy firewall, DEMO account verification,
the kill switch, asset identity/directional trade mode, execution-grade
quote freshness, news, cost/expected-net-edge, correlation, and the risk
governor's hard ceilings — is wired into ONE deterministic ALLOW/BLOCK_*
decision for a proposed NEW entry. No strategy, ML, RAG, or dashboard
code path may bypass this function to reach an order.

Deliberately a PURE function: every dependency is a pre-computed result,
not a live call this function makes itself (matching every other gate
built so far — `evaluate_news_block`, `evaluate_cost_gate`,
`evaluate_correlation_gate`, `evaluate_risk_gate` are all pure too).
Callers do the real I/O (fresh quote, fresh account info, current
correlation matrix, current open positions) immediately beforehand and
pass the results in — this keeps the actual decision logic fully
testable without a live gateway or database.

NOT YET INTEGRATED (named gaps, not fabricated "always clean" defaults):
directive's full `BLOCK_*` vocabulary includes reasons for subsystems
that do not exist in this codebase yet —
`BLOCK_RECONCILIATION`/`BLOCK_UNKNOWN_ORDER` (no execution/reconciliation
layer), `BLOCK_MARGIN`/`BLOCK_BROKER_CONSTRAINT` (no live broker
order-validation call), `BLOCK_DUPLICATE`/`BLOCK_REENTRY_CHURN` (no
idempotency/position-history layer), `BLOCK_PORTFOLIO_RISK` (no
distinct cluster-level heat ceiling beyond what `BLOCK_RISK`/
`BLOCK_CORRELATION` already cover). Each is added when its real
subsystem exists — this function does not pretend they're already
covered.

`order_send` does not exist anywhere in this codebase. This gate exists
ahead of it deliberately, but an `ALLOW` from this function is not yet
sufficient on its own to submit an order — the execution state machine,
idempotency, UNKNOWN handling, and reconciliation this docstring lists
as gaps must also exist first (see PROJECT_STATUS.md).
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass, field

from adaptive_scalper.config.constants import ALLOWED_CANONICAL_SYMBOLS, ALLOWED_MODES, RETIRED_STRATEGY_KEYS
from adaptive_scalper.core.kill_switch import KillSwitchState
from adaptive_scalper.core.permission import ActionKind, evaluate_kill_switch_permission
from adaptive_scalper.costs.edge import ALLOW as _COST_ALLOW
from adaptive_scalper.costs.edge import evaluate_cost_gate
from adaptive_scalper.costs.model import CostEstimate
from adaptive_scalper.gateway.demo_gate import DemoVerificationResult
from adaptive_scalper.gateway.symbol_validation import (
    EXECUTION_STALE_QUOTE,
    DirectionCheck,
    ExecutionQuoteCheck,
    SymbolValidationResult,
)
from adaptive_scalper.journal.events import append_event
from adaptive_scalper.news.blocking import ALLOW as _NEWS_ALLOW
from adaptive_scalper.news.blocking import NewsBlockResult
from adaptive_scalper.portfolio.correlation import ALLOW as _CORRELATION_ALLOW
from adaptive_scalper.portfolio.correlation import CorrelationResult, evaluate_correlation_gate
from adaptive_scalper.risk.governor import ALLOW as _RISK_ALLOW
from adaptive_scalper.risk.governor import RiskGateInput, RiskLimits, evaluate_risk_gate
from adaptive_scalper.strategies.base import StrategySignal

ALLOW = "ALLOW"
BLOCK_MODE = "BLOCK_MODE"
BLOCK_SYMBOL_NOT_ALLOWED = "BLOCK_SYMBOL_NOT_ALLOWED"
BLOCK_STRATEGY_RETIRED = "BLOCK_STRATEGY_RETIRED"
BLOCK_DATA_QUALITY = "BLOCK_DATA_QUALITY"
BLOCK_STALE_QUOTE = "BLOCK_STALE_QUOTE"
BLOCK_OTHER = "BLOCK_OTHER"


@dataclass(frozen=True)
class FinalPermissionInput:
    mode: str
    signal: StrategySignal
    kill_switch_state: KillSwitchState
    demo_verification: DemoVerificationResult
    asset_identity: SymbolValidationResult
    direction_check: DirectionCheck
    execution_quote: ExecutionQuoteCheck
    news_result: NewsBlockResult
    cost_estimate: CostEstimate | None
    open_symbols: list[str]
    correlation_matrix: dict[tuple[str, str], CorrelationResult]
    risk_gate_input: RiskGateInput
    risk_limits: RiskLimits
    min_net_edge_price: float = 0.0


@dataclass(frozen=True)
class FinalPermissionResult:
    decision: str
    reason: str


def evaluate_final_permission(inp: FinalPermissionInput) -> FinalPermissionResult:
    """Check every composed gate in a fixed order, returning the FIRST
    block reason encountered — an ALLOW means every single gate passed,
    not merely that this function ran without error."""
    if inp.mode not in ALLOWED_MODES:
        return FinalPermissionResult(BLOCK_MODE, f"mode={inp.mode!r} not in {sorted(ALLOWED_MODES)}")

    if inp.signal.canonical_symbol not in ALLOWED_CANONICAL_SYMBOLS:
        return FinalPermissionResult(
            BLOCK_SYMBOL_NOT_ALLOWED,
            f"{inp.signal.canonical_symbol!r} is not in the canonical allow-list "
            f"{sorted(ALLOWED_CANONICAL_SYMBOLS)}",
        )

    # Independent final-gate defense (directive section 8) — the strategy
    # registry (strategies/registry.py) already refuses to REGISTER a
    # retired key, but this check must stand on its own too, in case a
    # signal ever reaches this function through any other path.
    if inp.signal.strategy_key in RETIRED_STRATEGY_KEYS:
        return FinalPermissionResult(
            BLOCK_STRATEGY_RETIRED, f"{inp.signal.strategy_key!r} is permanently retired (directive section 8)"
        )

    if not inp.demo_verification.allowed:
        return FinalPermissionResult(inp.demo_verification.block_reason, inp.demo_verification.detail)

    kill_result = evaluate_kill_switch_permission(inp.kill_switch_state, ActionKind.NEW_ENTRY)
    if not kill_result.allowed:
        return FinalPermissionResult(
            kill_result.block_reason, f"kill switch status={inp.kill_switch_state.status.value}"
        )

    if not inp.asset_identity.valid:
        return FinalPermissionResult(BLOCK_DATA_QUALITY, inp.asset_identity.detail)

    if not inp.direction_check.valid:
        return FinalPermissionResult(BLOCK_DATA_QUALITY, inp.direction_check.detail)

    if not inp.execution_quote.valid:
        reason = BLOCK_STALE_QUOTE if inp.execution_quote.reason == EXECUTION_STALE_QUOTE else BLOCK_DATA_QUALITY
        return FinalPermissionResult(reason, inp.execution_quote.detail)

    if inp.news_result.decision != _NEWS_ALLOW:
        return FinalPermissionResult(inp.news_result.decision, inp.news_result.reason)

    cost_decision, edge_eval = evaluate_cost_gate(inp.signal, inp.cost_estimate, inp.min_net_edge_price)
    if cost_decision != _COST_ALLOW:
        return FinalPermissionResult(
            cost_decision, edge_eval.reason if edge_eval is not None else "cost could not be determined"
        )

    corr_decision, corr_reason = evaluate_correlation_gate(
        inp.signal.canonical_symbol, inp.open_symbols, inp.correlation_matrix
    )
    if corr_decision != _CORRELATION_ALLOW:
        return FinalPermissionResult(corr_decision, corr_reason)

    risk_decision, risk_reason = evaluate_risk_gate(inp.risk_gate_input, inp.risk_limits)
    if risk_decision != _RISK_ALLOW:
        return FinalPermissionResult(risk_decision, risk_reason)

    return FinalPermissionResult(ALLOW, "passed every composed gate")


def evaluate_and_journal_final_permission(
    conn: sqlite3.Connection, chain_key: str, inp: FinalPermissionInput, *, now_utc: int | None = None
) -> FinalPermissionResult:
    """Same as `evaluate_final_permission()`, plus appending the
    corresponding `ENTRY_ALLOWED`/`ENTRY_BLOCKED` journal event —
    directive sections 54-56: every permission decision must be part of
    the traceable decision chain, not just computed and discarded."""
    result = evaluate_final_permission(inp)
    now = now_utc if now_utc is not None else int(time.time())
    event_type = "ENTRY_ALLOWED" if result.decision == ALLOW else "ENTRY_BLOCKED"
    append_event(
        conn, chain_key, event_type, now, inp.signal.canonical_symbol,
        {"decision": result.decision, "reason": result.reason, "direction": inp.signal.direction},
        strategy_key=inp.signal.strategy_key,
    )
    return result
