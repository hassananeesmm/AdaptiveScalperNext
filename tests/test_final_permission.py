"""Tests for the composed final trade-permission gate (directive
section 36) — the single point where every independent gate built so
far is wired into one ALLOW/BLOCK_* decision.
"""

from __future__ import annotations

import pytest

from adaptive_scalper.core.final_permission import (
    ALLOW,
    BLOCK_DATA_QUALITY,
    BLOCK_DUPLICATE,
    BLOCK_MODE,
    BLOCK_PORTFOLIO_RISK,
    BLOCK_RECONCILIATION,
    BLOCK_STALE_QUOTE,
    BLOCK_STRATEGY_RETIRED,
    BLOCK_SYMBOL_NOT_ALLOWED,
    BLOCK_UNKNOWN_ORDER,
    FinalPermissionInput,
    evaluate_and_journal_final_permission,
    evaluate_final_permission,
)
from adaptive_scalper.core.kill_switch import KillSwitchState, KillSwitchStatus
from adaptive_scalper.costs.model import estimate_cost
from adaptive_scalper.execution.reconciliation import BLOCKING_MISMATCH, CLEAN
from adaptive_scalper.gateway.demo_gate import BLOCK_ACCOUNT_NOT_DEMO, DemoVerificationResult
from adaptive_scalper.gateway.symbol_validation import (
    EXECUTION_STALE_QUOTE,
    EXECUTION_VALID,
    VALID,
    DirectionCheck,
    ExecutionQuoteCheck,
    SymbolValidationResult,
)
from adaptive_scalper.journal.queries import get_chain_events
from adaptive_scalper.news.blocking import ALLOW as NEWS_ALLOW
from adaptive_scalper.news.blocking import BLOCK_NEWS, NewsBlockResult
from adaptive_scalper.persistence import connect, migrate
from adaptive_scalper.portfolio.correlation import CorrelationResult
from adaptive_scalper.portfolio.exposure import PortfolioRiskLimits, PositionExposure
from adaptive_scalper.risk.governor import RiskGateInput, RiskLimits
from adaptive_scalper.strategies.base import StrategySignal
from edge_fixtures import TEST_PUBLIC_KEY, fixture_validated_evidence


def _signal(**overrides) -> StrategySignal:
    defaults = dict(
        strategy_key="momentum_continuation", strategy_version=1, canonical_symbol="XAUUSD",
        direction="BUY", raw_confidence=0.6, stop_distance=1.0, target_distance=2.0,
        expected_duration_seconds=300, entry_method="MARKET", regime="TRENDING_UP",
        rationale="test", feature_schema_version=1, data_timestamp=1000,
    )
    defaults.update(overrides)
    return StrategySignal(**defaults)


def _kill_switch(status=KillSwitchStatus.DISENGAGED) -> KillSwitchState:
    return KillSwitchState(status=status, reason="test", changed_at="2026-01-01T00:00:00Z", changed_by="operator")


def _demo_ok() -> DemoVerificationResult:
    return DemoVerificationResult(allowed=True, block_reason=None, detail="DEMO confirmed")


def _identity_ok() -> SymbolValidationResult:
    return SymbolValidationResult("XAUUSD", "XAUUSD", True, VALID, "ok")


def _direction_ok() -> DirectionCheck:
    return DirectionCheck(True, "direction_allowed", "ok")


def _quote_ok() -> ExecutionQuoteCheck:
    return ExecutionQuoteCheck(True, EXECUTION_VALID, "ok")


def _news_ok() -> NewsBlockResult:
    return NewsBlockResult(NEWS_ALLOW, "no applicable event", None, None, None)


def _good_cost():
    return estimate_cost(
        spread_price=0.1, commission_price_equivalent=0.0,
        expected_slippage_price=0.0, swap_price_equivalent=0.0, uncertainty_margin_pct=0.0,
    )


def _risk_limits(**overrides) -> RiskLimits:
    defaults = dict(
        risk_per_trade_pct=0.25, max_total_open_risk_pct=0.75, max_daily_loss_pct=2.0,
        max_drawdown_pct=5.0, max_open_positions=2, max_positions_per_symbol=1,
    )
    defaults.update(overrides)
    return RiskLimits(**defaults)


def _portfolio_risk_limits(**overrides) -> PortfolioRiskLimits:
    defaults = dict(
        max_total_open_risk_pct=5.0, max_symbol_risk_pct=5.0,
        max_currency_direction_risk_pct=5.0, max_correlated_cluster_risk_pct=5.0,
    )
    defaults.update(overrides)
    return PortfolioRiskLimits(**defaults)


def _risk_input(**overrides) -> RiskGateInput:
    defaults = dict(
        proposed_symbol="XAUUSD", proposed_monetary_risk=20.0, equity=10000,
        current_total_open_risk=0.0, current_total_pending_risk=0.0,
        current_positions_count=0, current_positions_for_symbol=0,
        daily_realized_pnl=0.0, peak_equity=10000,
    )
    defaults.update(overrides)
    return RiskGateInput(**defaults)


def _full_allow_input(**overrides) -> FinalPermissionInput:
    defaults = dict(
        mode="DEMO", signal=_signal(), kill_switch_state=_kill_switch(),
        demo_verification=_demo_ok(), reconciliation_status=CLEAN,
        has_dangerous_unknown_order=False, duplicate_active_order=False,
        asset_identity=_identity_ok(),
        direction_check=_direction_ok(), execution_quote=_quote_ok(),
        news_result=_news_ok(), cost_estimate=_good_cost(),
        open_or_pending_symbols=[], correlation_matrix={},
        risk_gate_input=_risk_input(), risk_limits=_risk_limits(),
        open_positions=[], pending_positions=[], portfolio_risk_limits=_portfolio_risk_limits(),
        # Gates AFTER the edge gate are under test here; the edge gate's
        # fail-closed default is tested in test_edge_gate_* below.
        edge_evidence=fixture_validated_evidence(),
        public_key=TEST_PUBLIC_KEY, now_utc=10_000, proposal_features={},
    )
    defaults.update(overrides)
    return FinalPermissionInput(**defaults)


# --------------------------------------------------------------------------
# Happy path
# --------------------------------------------------------------------------

def test_allows_when_every_gate_passes():
    result = evaluate_final_permission(_full_allow_input())
    assert result.decision == ALLOW


# --------------------------------------------------------------------------
# Each gate's block reason propagates correctly, in fixed order
# --------------------------------------------------------------------------

def test_blocks_on_invalid_mode():
    result = evaluate_final_permission(_full_allow_input(mode="REAL"))
    assert result.decision == BLOCK_MODE


def test_blocks_on_symbol_not_allowed():
    bad_signal = _signal(canonical_symbol="XAUUSD")
    # Force an out-of-allowlist symbol via direct construction bypass —
    # StrategySignal itself doesn't validate canonical_symbol, so this
    # exercises the final gate's OWN independent defense.
    import dataclasses
    bad_signal = dataclasses.replace(bad_signal, canonical_symbol="EURUSD")
    result = evaluate_final_permission(_full_allow_input(signal=bad_signal))
    assert result.decision == BLOCK_SYMBOL_NOT_ALLOWED


def test_blocks_on_retired_strategy_key_even_though_registry_already_blocks_it():
    signal = _signal(strategy_key="failed_breakout_fade")
    result = evaluate_final_permission(_full_allow_input(signal=signal))
    assert result.decision == BLOCK_STRATEGY_RETIRED


def test_blocks_on_demo_verification_failure():
    demo_fail = DemoVerificationResult(allowed=False, block_reason=BLOCK_ACCOUNT_NOT_DEMO, detail="not demo")
    result = evaluate_final_permission(_full_allow_input(demo_verification=demo_fail))
    assert result.decision == BLOCK_ACCOUNT_NOT_DEMO


@pytest.mark.parametrize("status", [
    KillSwitchStatus.ENGAGED, KillSwitchStatus.UNINITIALIZED, KillSwitchStatus.INVALID,
])
def test_blocks_on_kill_switch_not_disengaged(status):
    result = evaluate_final_permission(_full_allow_input(kill_switch_state=_kill_switch(status)))
    assert result.decision == "BLOCK_KILL_SWITCH"


def test_allows_on_kill_switch_disengaged():
    result = evaluate_final_permission(_full_allow_input(kill_switch_state=_kill_switch(KillSwitchStatus.DISENGAGED)))
    assert result.decision == ALLOW


def test_blocks_on_invalid_asset_identity():
    bad_identity = SymbolValidationResult("XAUUSD", "XAUUSD", False, "asset_identity_mismatch", "mismatch detail")
    result = evaluate_final_permission(_full_allow_input(asset_identity=bad_identity))
    assert result.decision == BLOCK_DATA_QUALITY


def test_blocks_on_invalid_direction():
    bad_direction = DirectionCheck(False, "direction_not_allowed", "SHORTONLY does not permit BUY")
    result = evaluate_final_permission(_full_allow_input(direction_check=bad_direction))
    assert result.decision == BLOCK_DATA_QUALITY


def test_blocks_stale_quote_with_specific_reason():
    stale = ExecutionQuoteCheck(False, EXECUTION_STALE_QUOTE, "quote too old")
    result = evaluate_final_permission(_full_allow_input(execution_quote=stale))
    assert result.decision == BLOCK_STALE_QUOTE


def test_blocks_other_quote_failures_as_data_quality():
    missing = ExecutionQuoteCheck(False, "execution_no_quote", "no tick")
    result = evaluate_final_permission(_full_allow_input(execution_quote=missing))
    assert result.decision == BLOCK_DATA_QUALITY


def test_blocks_on_news():
    blocked_news = NewsBlockResult(BLOCK_NEWS, "FOMC window", None, 900, 2700)
    result = evaluate_final_permission(_full_allow_input(news_result=blocked_news))
    assert result.decision == BLOCK_NEWS


def test_blocks_on_unknown_cost():
    result = evaluate_final_permission(_full_allow_input(cost_estimate=None))
    assert result.decision == "BLOCK_COST"


def test_blocks_on_insufficient_expected_edge():
    # Validated evidence whose EV can't clear even a tiny cost (issue #6: the
    # edge comes from the evidence, not from the signal's raw score):
    # 0.4*1.0 - 0.6*2.0 < 0.
    poor = fixture_validated_evidence(p=0.4, avg_win=1.0, avg_loss=2.0)
    result = evaluate_final_permission(_full_allow_input(edge_evidence=poor))
    assert result.decision == "BLOCK_EXPECTED_EDGE"


def test_blocks_on_high_correlation_with_open_position():
    matrix = {("XAUUSD", "BTCUSD"): CorrelationResult(0.9, 100)}
    result = evaluate_final_permission(
        _full_allow_input(open_or_pending_symbols=["BTCUSD"], correlation_matrix=matrix)
    )
    assert result.decision == "BLOCK_CORRELATION"


def test_blocks_on_na_correlation_with_open_position_by_default():
    # External review fix #4, exercised end to end through the composed gate.
    matrix = {("XAUUSD", "BTCUSD"): CorrelationResult(None, 3)}
    result = evaluate_final_permission(
        _full_allow_input(open_or_pending_symbols=["BTCUSD"], correlation_matrix=matrix)
    )
    assert result.decision == "BLOCK_CORRELATION"


def test_blocks_on_portfolio_risk_before_risk_gate():
    # tight per-symbol portfolio ceiling breached, while the ordinary
    # risk gate's own ceiling (0.25% of equity) is nowhere near it --
    # proves BLOCK_PORTFOLIO_RISK is checked BEFORE BLOCK_RISK.
    tight_portfolio = _portfolio_risk_limits(max_symbol_risk_pct=0.01)
    result = evaluate_final_permission(_full_allow_input(portfolio_risk_limits=tight_portfolio))
    assert result.decision == BLOCK_PORTFOLIO_RISK


def test_blocks_on_correlated_cluster_via_final_permission():
    matrix = {
        ("XAUUSD", "BTCUSD"): CorrelationResult(0.9, 100), ("BTCUSD", "XAUUSD"): CorrelationResult(0.9, 100),
    }
    result = evaluate_final_permission(_full_allow_input(
        open_positions=[PositionExposure("BTCUSD", "BUY", 15.0)],
        correlation_matrix=matrix,
        portfolio_risk_limits=_portfolio_risk_limits(max_correlated_cluster_risk_pct=0.1),
    ))
    assert result.decision == BLOCK_PORTFOLIO_RISK


def test_allows_when_portfolio_risk_within_limits():
    result = evaluate_final_permission(_full_allow_input())
    assert result.decision == ALLOW


def test_blocks_on_risk_limit():
    over_limit_risk = _risk_input(proposed_monetary_risk=1000.0)  # way over 0.25% of 10000
    # Generous portfolio ceiling so THIS test isolates the risk gate specifically
    # (BLOCK_PORTFOLIO_RISK is now checked earlier and would otherwise catch it first).
    generous_portfolio = _portfolio_risk_limits(
        max_total_open_risk_pct=100.0, max_symbol_risk_pct=100.0,
        max_currency_direction_risk_pct=100.0, max_correlated_cluster_risk_pct=100.0,
    )
    result = evaluate_final_permission(
        _full_allow_input(risk_gate_input=over_limit_risk, portfolio_risk_limits=generous_portfolio)
    )
    assert result.decision == "BLOCK_RISK"


# --------------------------------------------------------------------------
# execution-safety review finding #5: reconciliation/unknown/duplicate/re-entry
# --------------------------------------------------------------------------

def test_blocks_on_reconciliation_not_clean():
    result = evaluate_final_permission(_full_allow_input(reconciliation_status=BLOCKING_MISMATCH))
    assert result.decision == BLOCK_RECONCILIATION


def test_recovered_status_still_blocks_until_re_reconciled_clean():
    # RECOVERED means "locally resolvable" (e.g. a stale local OPEN record
    # needs updating to CLOSED from broker truth) — it is intentionally
    # NOT treated as equivalent to CLEAN here. The caller must apply the
    # recovery and re-run reconciliation to CLEAN before new entries
    # resume; this gate never takes RECOVERED as sufficient on its own.
    from adaptive_scalper.execution.reconciliation import RECOVERED
    result = evaluate_final_permission(_full_allow_input(reconciliation_status=RECOVERED))
    assert result.decision == BLOCK_RECONCILIATION


def test_blocks_on_dangerous_unknown_order():
    result = evaluate_final_permission(_full_allow_input(has_dangerous_unknown_order=True))
    assert result.decision == BLOCK_UNKNOWN_ORDER


def test_blocks_on_duplicate_active_order():
    result = evaluate_final_permission(_full_allow_input(duplicate_active_order=True))
    assert result.decision == BLOCK_DUPLICATE


def test_blocks_on_reentry_churn():
    result = evaluate_final_permission(_full_allow_input(reentry_check=("BLOCK_REENTRY_CHURN", "cooldown active")))
    assert result.decision == "BLOCK_REENTRY_CHURN"


def test_allows_when_reentry_check_allows():
    result = evaluate_final_permission(_full_allow_input(reentry_check=("ALLOW", "new setup")))
    assert result.decision == ALLOW


def test_reentry_check_none_means_not_applicable_and_still_allows():
    result = evaluate_final_permission(_full_allow_input(reentry_check=None))
    assert result.decision == ALLOW


def test_reconciliation_checked_before_asset_identity():
    bad_identity = SymbolValidationResult("XAUUSD", "XAUUSD", False, "asset_identity_mismatch", "mismatch")
    result = evaluate_final_permission(
        _full_allow_input(reconciliation_status=BLOCKING_MISMATCH, asset_identity=bad_identity)
    )
    assert result.decision == BLOCK_RECONCILIATION


# --------------------------------------------------------------------------
# Ordering: mode/symbol/retirement checked before anything requiring
# real evidence (kill switch, demo, etc.)
# --------------------------------------------------------------------------

def test_retired_strategy_checked_before_kill_switch():
    signal = _signal(strategy_key="support_resistance_reaction")
    result = evaluate_final_permission(
        _full_allow_input(signal=signal, kill_switch_state=_kill_switch(KillSwitchStatus.ENGAGED))
    )
    assert result.decision == BLOCK_STRATEGY_RETIRED


# --------------------------------------------------------------------------
# evaluate_and_journal_final_permission
# --------------------------------------------------------------------------

@pytest.fixture()
def db(tmp_path):
    conn = connect(tmp_path / "test.sqlite3")
    migrate(conn)
    yield conn
    conn.close()


def test_journals_entry_allowed_on_allow(db):
    result = evaluate_and_journal_final_permission(db, "chain-1", _full_allow_input(), now_utc=1000)
    assert result.decision == ALLOW
    events = get_chain_events(db, "chain-1")
    assert len(events) == 1
    assert events[0].event_type == "ENTRY_ALLOWED"
    assert events[0].payload["decision"] == ALLOW


def test_journals_entry_blocked_on_block(db):
    result = evaluate_and_journal_final_permission(
        db, "chain-2", _full_allow_input(mode="REAL"), now_utc=1000
    )
    assert result.decision == BLOCK_MODE
    events = get_chain_events(db, "chain-2")
    assert len(events) == 1
    assert events[0].event_type == "ENTRY_BLOCKED"
    assert events[0].payload["decision"] == BLOCK_MODE


def test_journaled_event_carries_strategy_key(db):
    evaluate_and_journal_final_permission(db, "chain-3", _full_allow_input(), now_utc=1000)
    events = get_chain_events(db, "chain-3")
    assert events[0].strategy_key == "momentum_continuation"


# --------------------------------------------------------------------------
# Issue #6: only VALIDATED edge evidence can authorize an order
# --------------------------------------------------------------------------

def test_edge_gate_blocks_without_evidence_even_when_everything_else_passes():
    result = evaluate_final_permission(_full_allow_input(edge_evidence=None))
    assert result.decision == "BLOCK_EDGE_UNVALIDATED"
    assert "not a probability" in result.reason


def test_edge_gate_refuses_legacy_raw_score_evidence():
    from adaptive_scalper.costs.edge_evidence import LEGACY_V1_RAW_SCORE_EVIDENCE

    signal = _full_allow_input().signal
    result = evaluate_final_permission(_full_allow_input(edge_evidence=LEGACY_V1_RAW_SCORE_EVIDENCE.for_signal(signal)))
    assert result.decision == "BLOCK_EDGE_UNVALIDATED"
