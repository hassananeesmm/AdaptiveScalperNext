"""Tests for the single execution orchestration service (execution-safety
review round 1 findings #7,#8,#9 and round 2 findings #1,#2)."""

from __future__ import annotations

import pytest

from adaptive_scalper.core.final_permission import FinalPermissionInput
from adaptive_scalper.core.kill_switch import KillSwitchState, KillSwitchStatus
from adaptive_scalper.costs.model import estimate_cost
from adaptive_scalper.execution.reconciliation import BLOCKING_MISMATCH, CLEAN
from adaptive_scalper.execution.service import (
    BLOCKED_BROKER_CONSTRAINT,
    BLOCKED_MARGIN,
    BLOCKED_PERMISSION,
    BLOCKED_PRESEND_RECHECK,
    CANCELLED,
    FILLED,
    PARTIAL,
    REJECTED,
    RESTING,
    UNKNOWN,
    FreshEvidence,
    submit_new_entry,
)
from adaptive_scalper.execution.state_machine import OrderState
from adaptive_scalper.gateway.demo_gate import DemoVerificationResult
from adaptive_scalper.gateway.fake_gateway import FakeGateway
from adaptive_scalper.gateway.symbol_validation import (
    EXECUTION_VALID,
    VALID,
    DirectionCheck,
    ExecutionQuoteCheck,
    SymbolValidationResult,
)
from adaptive_scalper.gateway.types import OrderCheckResult, OrderSendResult, SymbolSpec, SymbolTradeMode, Tick
from adaptive_scalper.journal.queries import get_chain_events
from adaptive_scalper.news.blocking import ALLOW as NEWS_ALLOW
from adaptive_scalper.news.blocking import BLOCK_NEWS, NewsBlockResult
from adaptive_scalper.persistence import connect, migrate
from adaptive_scalper.risk.governor import RiskGateInput, RiskLimits
from adaptive_scalper.strategies.base import StrategySignal


@pytest.fixture()
def db(tmp_path):
    conn = connect(tmp_path / "test.sqlite3")
    migrate(conn)
    yield conn
    conn.close()


def _symbol_spec(**overrides) -> SymbolSpec:
    defaults = dict(
        name="XAUUSDm", description="Gold", currency_base="XAU", currency_profit="USD", currency_margin="USD",
        digits=2, point=0.01, trade_contract_size=100.0, volume_min=0.01, volume_max=50.0, volume_step=0.01,
        trade_tick_size=0.01, trade_tick_value=1.0, spread=20, visible=True,
        trade_mode=SymbolTradeMode.FULL, filling_mode=3,
    )
    defaults.update(overrides)
    return SymbolSpec(**defaults)


def _signal(**overrides) -> StrategySignal:
    defaults = dict(
        strategy_key="momentum_continuation", strategy_version=1, canonical_symbol="XAUUSD",
        direction="BUY", raw_confidence=0.6, stop_distance=1.0, target_distance=2.0,
        expected_duration_seconds=300, entry_method="MARKET", regime="TRENDING_UP",
        rationale="test", feature_schema_version=1, data_timestamp=1000,
    )
    defaults.update(overrides)
    return StrategySignal(**defaults)


def _permission_input(**overrides) -> FinalPermissionInput:
    defaults = dict(
        mode="DEMO", signal=_signal(),
        kill_switch_state=KillSwitchState(status=KillSwitchStatus.DISENGAGED, reason="test",
                                           changed_at="2026-01-01T00:00:00Z", changed_by="operator"),
        demo_verification=DemoVerificationResult(allowed=True, block_reason=None, detail="DEMO confirmed"),
        reconciliation_status=CLEAN, has_dangerous_unknown_order=False, duplicate_active_order=False,
        asset_identity=SymbolValidationResult("XAUUSD", "XAUUSD", True, VALID, "ok"),
        direction_check=DirectionCheck(True, "direction_allowed", "ok"),
        execution_quote=ExecutionQuoteCheck(True, EXECUTION_VALID, "ok"),
        news_result=NewsBlockResult(NEWS_ALLOW, "no applicable event", None, None, None),
        cost_estimate=estimate_cost(spread_price=0.1, commission_price_equivalent=0.0,
                                     expected_slippage_price=0.0, swap_price_equivalent=0.0,
                                     uncertainty_margin_pct=0.0),
        open_symbols=[], correlation_matrix={},
        risk_gate_input=RiskGateInput(
            proposed_symbol="XAUUSD", proposed_monetary_risk=20.0, equity=10000,
            current_total_open_risk=0.0, current_total_pending_risk=0.0,
            current_positions_count=0, current_positions_for_symbol=0,
            daily_realized_pnl=0.0, peak_equity=10000,
        ),
        risk_limits=RiskLimits(
            risk_per_trade_pct=0.25, max_total_open_risk_pct=0.75, max_daily_loss_pct=2.0,
            max_drawdown_pct=5.0, max_open_positions=2, max_positions_per_symbol=1,
        ),
    )
    defaults.update(overrides)
    return FinalPermissionInput(**defaults)


def _good_evidence(**permission_overrides) -> FreshEvidence:
    return FreshEvidence(permission_input=_permission_input(**permission_overrides), symbol_spec=_symbol_spec())


def _sequence(*evidences):
    """A fetch_fresh_evidence() stand-in that returns a DIFFERENT value
    on each successive call — the mechanism every finding-#1 regression
    test below uses to prove the service re-fetches independently rather
    than reusing a cached snapshot."""
    it = iter(evidences)

    def _fetch():
        return next(it)

    return _fetch


def _submit(db, gw, fetch_fresh_evidence, **overrides):
    defaults = dict(
        conn=db, gateway=gw, chain_key="chain-1", client_request_id="req-1", canonical_symbol="XAUUSD",
        broker_symbol="XAUUSDm", direction="BUY", volume=0.05, stop_loss=1990.0, take_profit=2020.0,
        fetch_fresh_evidence=fetch_fresh_evidence, now_utc=5000, history_window_seconds=10000,
    )
    defaults.update(overrides)
    return submit_new_entry(**defaults)


def _gw_with_tick():
    return FakeGateway(ticks={"XAUUSDm": Tick(time=5000, bid=1999.0, ask=2001.0, last=2000.0, volume=1.0)})


# --------------------------------------------------------------------------
# Happy path
# --------------------------------------------------------------------------

def test_happy_path_fills_and_journals_full_lifecycle(db):
    gw = _gw_with_tick()
    outcome = _submit(db, gw, _sequence(_good_evidence(), _good_evidence()))
    assert outcome.status == FILLED
    assert outcome.order.state == OrderState.FILLED
    assert outcome.order.broker_position_id is not None

    events = [e.event_type for e in get_chain_events(db, "chain-1")]
    assert events == [
        "ENTRY_ALLOWED", "ENTRY_ALLOWED", "ORDER_SUBMITTED", "ORDER_ACCEPTED", "ORDER_FILLED", "POSITION_OPENED",
    ]


def test_evidence_fetched_exactly_twice_on_the_happy_path(db):
    calls = []

    def fetch():
        calls.append(1)
        return _good_evidence()

    _submit(db, _gw_with_tick(), fetch)
    assert len(calls) == 2


# --------------------------------------------------------------------------
# Initial permission block
# --------------------------------------------------------------------------

def test_blocked_initial_permission_never_calls_order_send(db):
    bad = FreshEvidence(permission_input=_permission_input(mode="REAL"), symbol_spec=_symbol_spec())
    gw = FakeGateway()
    outcome = _submit(db, gw, _sequence(bad))
    assert outcome.status == BLOCKED_PERMISSION
    assert outcome.order.state == OrderState.PROPOSED
    assert gw.order_send_calls == []


# --------------------------------------------------------------------------
# Finding #1 (round 2): pre-send recheck must independently re-fetch and
# re-evaluate — a stale/cached ALLOW from the first check is not enough.
# --------------------------------------------------------------------------

def test_account_switches_to_real_before_send_blocks_presend_recheck(db):
    initial = _good_evidence()
    switched_to_real = FreshEvidence(permission_input=_permission_input(mode="REAL"), symbol_spec=_symbol_spec())
    gw = FakeGateway()
    outcome = _submit(db, gw, _sequence(initial, switched_to_real))
    assert outcome.status == BLOCKED_PRESEND_RECHECK
    assert gw.order_send_calls == []


def test_kill_switch_engages_after_order_check_blocks_send(db):
    initial = _good_evidence()
    engaged_ks = KillSwitchState(status=KillSwitchStatus.ENGAGED, reason="operator", changed_at="2026-01-01T00:00:00Z", changed_by="operator")
    engaged = _good_evidence(kill_switch_state=engaged_ks)
    gw = FakeGateway()
    outcome = _submit(db, gw, _sequence(initial, engaged))
    assert outcome.status == BLOCKED_PRESEND_RECHECK
    assert gw.order_send_calls == []


def test_quote_becomes_stale_before_send_blocks(db):
    initial = _good_evidence()
    stale = _good_evidence(execution_quote=ExecutionQuoteCheck(False, "execution_stale_quote", "too old"))
    gw = FakeGateway()
    outcome = _submit(db, gw, _sequence(initial, stale))
    assert outcome.status == BLOCKED_PRESEND_RECHECK
    assert gw.order_send_calls == []


def test_news_window_begins_before_send_blocks(db):
    initial = _good_evidence()
    blocked_news = _good_evidence(news_result=NewsBlockResult(BLOCK_NEWS, "FOMC window", None, 60, 2700))
    gw = FakeGateway()
    outcome = _submit(db, gw, _sequence(initial, blocked_news))
    assert outcome.status == BLOCKED_PRESEND_RECHECK
    assert gw.order_send_calls == []


def test_reconciliation_becomes_blocking_before_send_blocks(db):
    initial = _good_evidence()
    unclean = _good_evidence(reconciliation_status=BLOCKING_MISMATCH)
    gw = FakeGateway()
    outcome = _submit(db, gw, _sequence(initial, unclean))
    assert outcome.status == BLOCKED_PRESEND_RECHECK
    assert gw.order_send_calls == []


def test_unknown_appears_before_send_blocks(db):
    initial = _good_evidence()
    with_unknown = _good_evidence(has_dangerous_unknown_order=True)
    gw = FakeGateway()
    outcome = _submit(db, gw, _sequence(initial, with_unknown))
    assert outcome.status == BLOCKED_PRESEND_RECHECK
    assert gw.order_send_calls == []


def test_risk_state_changes_before_send_blocks(db):
    initial = _good_evidence()
    over_limit_risk = RiskGateInput(
        proposed_symbol="XAUUSD", proposed_monetary_risk=1000.0, equity=10000,
        current_total_open_risk=0.0, current_total_pending_risk=0.0,
        current_positions_count=0, current_positions_for_symbol=0,
        daily_realized_pnl=0.0, peak_equity=10000,
    )
    risky = _good_evidence(risk_gate_input=over_limit_risk)
    gw = FakeGateway()
    outcome = _submit(db, gw, _sequence(initial, risky))
    assert outcome.status == BLOCKED_PRESEND_RECHECK
    assert gw.order_send_calls == []


def test_duplicate_appears_before_send_blocks(db):
    initial = _good_evidence()
    dup = _good_evidence(duplicate_active_order=True)
    gw = FakeGateway()
    outcome = _submit(db, gw, _sequence(initial, dup))
    assert outcome.status == BLOCKED_PRESEND_RECHECK
    assert gw.order_send_calls == []


# --------------------------------------------------------------------------
# order_check / margin blocks
# --------------------------------------------------------------------------

def test_order_check_failure_blocks_before_send(db):
    class RejectingCheckGateway(FakeGateway):
        def order_check(self, request):
            return OrderCheckResult(retcode=10014, comment="invalid volume", margin_required=None)

    gw = RejectingCheckGateway()
    outcome = _submit(db, gw, _sequence(_good_evidence()))
    assert outcome.status == BLOCKED_BROKER_CONSTRAINT
    assert gw.order_send_calls == []
    assert outcome.order.state == OrderState.PROPOSED


def test_insufficient_margin_blocks_before_send(db):
    class HighMarginGateway(FakeGateway):
        def order_check(self, request):
            return OrderCheckResult(retcode=10009, comment="ok", margin_required=99999.0)

    gw = HighMarginGateway()
    evidence = FreshEvidence(permission_input=_permission_input(), symbol_spec=_symbol_spec(), available_margin_free=100.0)
    outcome = _submit(db, gw, _sequence(evidence))
    assert outcome.status == BLOCKED_MARGIN
    assert gw.order_send_calls == []


def test_no_supported_filling_mode_blocks_before_check_or_send(db):
    gw = FakeGateway()
    evidence = FreshEvidence(permission_input=_permission_input(), symbol_spec=_symbol_spec(filling_mode=0))
    outcome = _submit(db, gw, _sequence(evidence))
    assert outcome.status == BLOCKED_BROKER_CONSTRAINT
    assert gw.order_send_calls == []


# --------------------------------------------------------------------------
# Finding #2 (round 2): authoritative retcode interpretation
# --------------------------------------------------------------------------

def test_done_partial_becomes_partial_never_rejected(db):
    gw = FakeGateway(order_send_responses=[
        OrderSendResult(retcode=10010, comment="partial fill", broker_order_id="111", broker_deal_id="222",
                         broker_position_id=None, volume_filled=0.02, price_filled=2000.0, raw={}),
    ])
    outcome = _submit(db, gw, _sequence(_good_evidence(), _good_evidence()))
    assert outcome.status == PARTIAL
    assert outcome.order.state == OrderState.PARTIAL
    events = [e for e in get_chain_events(db, "chain-1") if e.event_type == "ORDER_PARTIAL"]
    assert len(events) == 1
    assert events[0].payload["volume_filled"] == 0.02


def test_placed_becomes_resting_never_rejected(db):
    gw = FakeGateway(order_send_responses=[
        OrderSendResult(retcode=10008, comment="placed", broker_order_id="111", broker_deal_id=None,
                         broker_position_id=None, volume_filled=0.0, price_filled=None, raw={}),
    ])
    outcome = _submit(db, gw, _sequence(_good_evidence(), _good_evidence()))
    assert outcome.status == RESTING
    assert outcome.order.state == OrderState.RESTING


def test_cancel_retcode_becomes_cancelled(db):
    gw = FakeGateway(order_send_responses=[
        OrderSendResult(retcode=10007, comment="cancelled", broker_order_id="111", broker_deal_id=None,
                         broker_position_id=None, volume_filled=0.0, price_filled=None, raw={}),
    ])
    outcome = _submit(db, gw, _sequence(_good_evidence(), _good_evidence()))
    assert outcome.status == CANCELLED
    assert outcome.order.state == OrderState.CANCELLED


def test_timeout_retcode_becomes_unknown_never_blindly_resent(db):
    gw = FakeGateway(order_send_responses=[
        OrderSendResult(retcode=10012, comment="timeout", broker_order_id=None, broker_deal_id=None,
                         broker_position_id=None, volume_filled=0.0, price_filled=None, raw={}),
    ])
    outcome = _submit(db, gw, _sequence(_good_evidence(), _good_evidence()))
    assert outcome.status == UNKNOWN
    assert outcome.order.state == OrderState.UNKNOWN
    assert len(gw.order_send_calls) == 1  # exactly one attempt, never auto-resent

    from adaptive_scalper.execution.reconciliation import has_dangerous_unresolved_unknown
    assert has_dangerous_unresolved_unknown(db) is True


def test_invalid_volume_retcode_becomes_rejected(db):
    gw = FakeGateway(order_send_responses=[
        OrderSendResult(retcode=10014, comment="invalid volume", broker_order_id=None, broker_deal_id=None,
                         broker_position_id=None, volume_filled=0.0, price_filled=None, raw={}),
    ])
    outcome = _submit(db, gw, _sequence(_good_evidence(), _good_evidence()))
    assert outcome.status == REJECTED


def test_broker_rejection_transitions_to_rejected(db):
    gw = FakeGateway(order_send_responses=[
        OrderSendResult(retcode=10006, comment="rejected", broker_order_id=None, broker_deal_id=None,
                         broker_position_id=None, volume_filled=0.0, price_filled=None, raw={}),
    ])
    outcome = _submit(db, gw, _sequence(_good_evidence(), _good_evidence()))
    assert outcome.status == REJECTED
    assert outcome.order.state == OrderState.REJECTED
    events = [e.event_type for e in get_chain_events(db, "chain-1")]
    assert "ORDER_REJECTED" in events


def test_unresolvable_position_becomes_unknown_and_records_incident(db):
    gw = FakeGateway(order_send_responses=[
        OrderSendResult(retcode=10009, comment="done", broker_order_id="111", broker_deal_id="222",
                         broker_position_id=None, volume_filled=0.05, price_filled=2000.0, raw={}),
    ])
    outcome = _submit(db, gw, _sequence(_good_evidence(), _good_evidence()))
    assert outcome.status == UNKNOWN
    assert outcome.order.state == OrderState.UNKNOWN

    from adaptive_scalper.execution.reconciliation import has_dangerous_unresolved_unknown
    assert has_dangerous_unresolved_unknown(db) is True


# --------------------------------------------------------------------------
# General invariants
# --------------------------------------------------------------------------

def test_exact_request_never_mutated_between_check_and_send(db):
    gw = _gw_with_tick()
    _submit(db, gw, _sequence(_good_evidence(), _good_evidence()))
    assert len(gw.order_send_calls) == 1
    sent = gw.order_send_calls[0]
    assert sent.symbol == "XAUUSDm"
    assert sent.direction == "BUY"
    assert sent.volume == 0.05
    assert sent.stop_loss == 1990.0
    assert sent.take_profit == 2020.0
    assert sent.filling_type == "IOC"


def test_already_progressed_order_is_not_resubmitted(db):
    gw = _gw_with_tick()
    first = _submit(db, gw, _sequence(_good_evidence(), _good_evidence()))
    assert first.status == FILLED
    calls_after_first = len(gw.order_send_calls)

    second = _submit(db, gw, _sequence(_good_evidence(), _good_evidence()), client_request_id="req-1")
    assert len(gw.order_send_calls) == calls_after_first  # no second send
    assert second.order.id == first.order.id
