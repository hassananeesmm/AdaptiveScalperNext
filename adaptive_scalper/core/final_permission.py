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

Execution-safety review round 1 finding #5 (see PROJECT_STATUS.md/
BUG_BACKLOG.md): this gate integrates real evidence for
`BLOCK_RECONCILIATION` (reconciliation status is not CLEAN),
`BLOCK_UNKNOWN_ORDER` (a dangerous unresolved UNKNOWN order exists),
`BLOCK_DUPLICATE` (an active order already exists for this same logical
proposal), and `BLOCK_REENTRY_CHURN` (the safe re-entry hysteresis —
`position_management.re_entry` — blocked this candidate).

Round 2 finding #6: `BLOCK_PORTFOLIO_RISK` is now also integrated,
via `portfolio.exposure.evaluate_portfolio_risk_gate()` — a deterministic
per-symbol/net-currency-direction/correlated-cluster heat policy, every
ceiling bounded by (never independently higher than) the same
`max_total_open_risk_pct` `risk.governor.evaluate_risk_gate()` already
enforces.

Every one of these is a REQUIRED, non-optional input: there is no
"always clean" default for any of them, so a caller that cannot actually
prove reconciliation is clean, or that a dangerous UNKNOWN doesn't exist,
must pass the conservative (blocking) value rather than skip the check.

STILL NOT YET INTEGRATED (named gaps, not fabricated "always clean"
defaults):
`BLOCK_MARGIN`/`BLOCK_BROKER_CONSTRAINT` are evaluated one step LATER
than this function, in `execution.service` — they depend on the EXACT
broker request and a fresh `order_check()` call, which by construction
cannot happen until after this gate's ALLOW produces that exact request
(see the execution flow diagram in `execution/service.py`). This gate
being an ALLOW is therefore necessary but not sufficient to submit an
order — `execution.service` re-runs this ENTIRE gate fresh immediately
before every `order_send`, plus `order_check`, plus margin/broker-
constraint evaluation, before anything is actually sent.

`order_send` exists in `gateway/`, but nothing outside `execution.service`
may call it directly for a NEW entry (enforced by
`tests/test_architecture_execution_boundary.py`). This gate is the
single point every such call must pass through fresh, immediately before
submission.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass

from adaptive_scalper.config.constants import ALLOWED_CANONICAL_SYMBOLS, ALLOWED_MODES, RETIRED_STRATEGY_KEYS
from adaptive_scalper.core.kill_switch import KillSwitchState
from adaptive_scalper.core.permission import ActionKind, evaluate_kill_switch_permission
from adaptive_scalper.costs.edge import ALLOW as _COST_ALLOW
from adaptive_scalper.costs.edge import BLOCK_EDGE_UNVALIDATED as _COST_BLOCK_EDGE_UNVALIDATED
from adaptive_scalper.costs.edge import evaluate_cost_gate, executable_edge_check
from adaptive_scalper.costs.edge_evidence import EdgeEvidence
from adaptive_scalper.costs.model import CostEstimate
from adaptive_scalper.execution.reconciliation import CLEAN as _RECONCILIATION_CLEAN
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
from adaptive_scalper.portfolio.exposure import ALLOW as _PORTFOLIO_RISK_ALLOW
from adaptive_scalper.portfolio.exposure import PortfolioRiskLimits, PositionExposure, evaluate_portfolio_risk_gate
from adaptive_scalper.risk.governor import ALLOW as _RISK_ALLOW
from adaptive_scalper.risk.governor import RiskGateInput, RiskLimits, evaluate_risk_gate
from adaptive_scalper.strategies.base import StrategySignal

ALLOW = "ALLOW"
BLOCK_MODE = "BLOCK_MODE"
BLOCK_SYMBOL_NOT_ALLOWED = "BLOCK_SYMBOL_NOT_ALLOWED"
BLOCK_STRATEGY_RETIRED = "BLOCK_STRATEGY_RETIRED"
BLOCK_DATA_QUALITY = "BLOCK_DATA_QUALITY"
BLOCK_STALE_QUOTE = "BLOCK_STALE_QUOTE"
BLOCK_RECONCILIATION = "BLOCK_RECONCILIATION"
BLOCK_UNKNOWN_ORDER = "BLOCK_UNKNOWN_ORDER"
BLOCK_DUPLICATE = "BLOCK_DUPLICATE"
BLOCK_REENTRY_CHURN = "BLOCK_REENTRY_CHURN"
BLOCK_PORTFOLIO_RISK = "BLOCK_PORTFOLIO_RISK"
BLOCK_OTHER = "BLOCK_OTHER"
BLOCK_STRATEGY_SUSPENDED = "BLOCK_STRATEGY_SUSPENDED"


@dataclass(frozen=True)
class FinalPermissionInput:
    mode: str
    signal: StrategySignal
    kill_switch_state: KillSwitchState
    demo_verification: DemoVerificationResult
    reconciliation_status: str                # execution.reconciliation.{CLEAN,BLOCKING_MISMATCH,RECOVERED}
    has_dangerous_unknown_order: bool          # execution.reconciliation.has_dangerous_unresolved_unknown()
    duplicate_active_order: bool               # True if an active order already exists for this proposal
    asset_identity: SymbolValidationResult
    direction_check: DirectionCheck
    execution_quote: ExecutionQuoteCheck
    news_result: NewsBlockResult
    cost_estimate: CostEstimate | None
    open_or_pending_symbols: list[str]
    correlation_matrix: dict[tuple[str, str], CorrelationResult]
    risk_gate_input: RiskGateInput
    risk_limits: RiskLimits
    open_positions: list[PositionExposure]          # per-position breakdown for portfolio-heat evaluation
    pending_positions: list[PositionExposure]
    portfolio_risk_limits: PortfolioRiskLimits
    min_net_edge_price: float = 0.0
    # VALIDATED expected-edge evidence for this proposal (costs/edge_evidence.py).
    # None -- the default, and the only possibility until a calibration has
    # passed a preregistered protocol -- blocks with BLOCK_EDGE_UNVALIDATED.
    edge_evidence: EdgeEvidence | None = None
    # Certificate verification inputs (validation/certificate.py): the Ed25519
    # PUBLIC key only (no key -> nothing verifies) and the proposal's causal
    # feature vector, from which P(win) is recomputed with the certified
    # model. The protocol is NOT an input: verification always uses the
    # module's CURRENT_PROTOCOL.
    public_key: bytes | None = None
    now_utc: int | None = None
    proposal_features: dict | None = None
    # Config `strategies.entry_suspended` (release 0.2.8). Checked here too, so
    # a suspended strategy can never reach broker exposure even if a caller
    # skipped the pre-selector filter or holds verified evidence for it.
    entry_suspended_strategy_keys: frozenset[str] = frozenset()
    # (decision, reason) from position_management.re_entry.evaluate_reentry,
    # or None when this proposal is not a re-entry scenario (no relevant
    # prior exit exists) and the check is simply not applicable.
    reentry_check: tuple[str, str] | None = None


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

    if inp.reconciliation_status != _RECONCILIATION_CLEAN:
        return FinalPermissionResult(
            BLOCK_RECONCILIATION,
            f"reconciliation status={inp.reconciliation_status!r} (not CLEAN) — possible unaccounted "
            f"broker exposure; new entries blocked until resolved",
        )

    if inp.has_dangerous_unknown_order:
        return FinalPermissionResult(
            BLOCK_UNKNOWN_ORDER,
            "a dangerous unresolved UNKNOWN order exists — duplicate exposure cannot be ruled out; "
            "new entries blocked until resolved (directive section 30)",
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

    if inp.signal.strategy_key in inp.entry_suspended_strategy_keys:
        return FinalPermissionResult(BLOCK_STRATEGY_SUSPENDED,
                                     f"{inp.signal.strategy_key} is entry-suspended (strategies.entry_suspended)")

    # executable=True: only certificate-verified edge evidence can authorize a
    # broker order; missing/legacy/test/forged/expired evidence is
    # BLOCK_EDGE_UNVALIDATED (issue #6, audit section 8).
    cost_decision, edge_eval = evaluate_cost_gate(
        inp.signal, inp.cost_estimate, inp.min_net_edge_price, evidence=inp.edge_evidence, executable=True,
        public_key=inp.public_key, now_utc=inp.now_utc, proposal_features=inp.proposal_features,
    )
    if cost_decision != _COST_ALLOW:
        if edge_eval is not None:
            detail = edge_eval.reason
        elif cost_decision == _COST_BLOCK_EDGE_UNVALIDATED:
            check = executable_edge_check(inp.signal, inp.edge_evidence, public_key=inp.public_key,
                                          now_utc=inp.now_utc, proposal_features=inp.proposal_features)
            detail = (f"no verified edge evidence for this proposal ({check.reason}); "
                      f"a raw score is not a probability")
        else:
            detail = "cost could not be determined"
        return FinalPermissionResult(cost_decision, detail)

    corr_decision, corr_reason = evaluate_correlation_gate(
        inp.signal.canonical_symbol, inp.open_or_pending_symbols, inp.correlation_matrix
    )
    if corr_decision != _CORRELATION_ALLOW:
        return FinalPermissionResult(corr_decision, corr_reason)

    portfolio_decision, portfolio_reason = evaluate_portfolio_risk_gate(
        proposed_symbol=inp.signal.canonical_symbol,
        proposed_direction=inp.signal.direction,
        proposed_monetary_risk=inp.risk_gate_input.proposed_monetary_risk,
        equity=inp.risk_gate_input.equity,
        open_positions=inp.open_positions,
        pending_positions=inp.pending_positions,
        correlation_matrix=inp.correlation_matrix,
        limits=inp.portfolio_risk_limits,
    )
    if portfolio_decision != _PORTFOLIO_RISK_ALLOW:
        return FinalPermissionResult(portfolio_decision, portfolio_reason)

    risk_decision, risk_reason = evaluate_risk_gate(inp.risk_gate_input, inp.risk_limits)
    if risk_decision != _RISK_ALLOW:
        return FinalPermissionResult(risk_decision, risk_reason)

    if inp.duplicate_active_order:
        return FinalPermissionResult(
            BLOCK_DUPLICATE,
            "an active (non-terminal) order already exists for this same logical proposal — refusing "
            "to submit a second one",
        )

    if inp.reentry_check is not None:
        reentry_decision, reentry_reason = inp.reentry_check
        if reentry_decision != "ALLOW":
            return FinalPermissionResult(reentry_decision, reentry_reason)

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
