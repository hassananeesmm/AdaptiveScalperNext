"""Tests for the single execution orchestration service (execution-safety
review findings #7, #8, #9)."""

from __future__ import annotations

import pytest

from adaptive_scalper.core.final_permission import ALLOW, FinalPermissionInput
from adaptive_scalper.core.kill_switch import KillSwitchState, KillSwitchStatus
from adaptive_scalper.costs.model import estimate_cost
from adaptive_scalper.execution.reconciliation import CLEAN
from adaptive_scalper.execution.service import (
    BLOCKED_BROKER_CONSTRAINT,
    BLOCKED_MARGIN,
    BLOCKED_PERMISSION,
    FILLED,
    REJECTED,
    UNKNOWN,
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
from adaptive_scalper.gateway.types import OrderSendResult, SymbolSpec
from adaptive_scalper.journal.queries import get_chain_events
from adaptive_scalper.news.blocking import ALLOW as NEWS_ALLOW
from adaptive_scalper.news.blocking import NewsBlockResult
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
        trade_mode=__import__("adaptive_scalper.gateway.types", fromlist=["SymbolTradeMode"]).SymbolTradeMode.FULL,
        filling_mode=3,
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


def _submit(db, gw, **overrides):
    defaults = dict(
        conn=db, gateway=gw, chain_key="chain-1", client_request_id="req-1", canonical_symbol="XAUUSD",
        broker_symbol="XAUUSDm", direction="BUY", volume=0.05, stop_loss=1990.0, take_profit=2020.0,
        symbol_spec=_symbol_spec(), permission_input=_permission_input(), now_utc=5000,
        # FakeGateway's simulated fills stamp historical deals/orders at
        # time=0 (it doesn't model a clock) — a wide window keeps that
        # covered without weakening the real-world default in
        # execution/service.py itself.
        history_window_seconds=10000,
    )
    defaults.update(overrides)
    return submit_new_entry(**defaults)


def test_happy_path_fills_and_journals_full_lifecycle(db):
    gw = FakeGateway(ticks={"XAUUSDm": __import__("adaptive_scalper.gateway.types", fromlist=["Tick"]).Tick(
        time=5000, bid=1999.0, ask=2001.0, last=2000.0, volume=1.0)})
    outcome = _submit(db, gw)
    assert outcome.status == FILLED
    assert outcome.order.state == OrderState.FILLED
    assert outcome.order.broker_position_id is not None

    events = [e.event_type for e in get_chain_events(db, "chain-1")]
    assert events == ["ENTRY_ALLOWED", "ORDER_SUBMITTED", "ORDER_ACCEPTED", "ORDER_FILLED", "POSITION_OPENED"]


def test_blocked_permission_never_calls_order_send(db):
    permission = _permission_input(mode="REAL")  # will fail BLOCK_MODE
    outcome = _submit(db, FakeGateway(), permission_input=permission)
    assert outcome.status == BLOCKED_PERMISSION
    assert outcome.order.state == OrderState.PROPOSED


def test_order_check_failure_blocks_before_send(db):
    from adaptive_scalper.gateway.types import OrderCheckResult

    class RejectingCheckGateway(FakeGateway):
        def order_check(self, request):
            return OrderCheckResult(retcode=10014, comment="invalid volume", margin_required=None)

    gw = RejectingCheckGateway()
    outcome = _submit(db, gw)
    assert outcome.status == BLOCKED_BROKER_CONSTRAINT
    assert gw.order_send_calls == []
    assert outcome.order.state == OrderState.PROPOSED  # never even reached SUBMITTED


def test_insufficient_margin_blocks_before_send(db):
    from adaptive_scalper.gateway.types import OrderCheckResult

    class HighMarginGateway(FakeGateway):
        def order_check(self, request):
            return OrderCheckResult(retcode=10009, comment="ok", margin_required=99999.0)

    gw = HighMarginGateway()
    outcome = _submit(db, gw, available_margin_free=100.0)
    assert outcome.status == BLOCKED_MARGIN
    assert gw.order_send_calls == []


def test_no_supported_filling_mode_blocks_before_check_or_send(db):
    gw = FakeGateway()
    outcome = _submit(db, gw, symbol_spec=_symbol_spec(filling_mode=0))
    assert outcome.status == BLOCKED_BROKER_CONSTRAINT
    assert gw.order_send_calls == []


def test_broker_rejection_transitions_to_rejected(db):
    gw = FakeGateway(order_send_responses=[
        OrderSendResult(retcode=10006, comment="rejected", broker_order_id=None, broker_deal_id=None,
                         broker_position_id=None, volume_filled=0.0, price_filled=None, raw={}),
    ])
    outcome = _submit(db, gw)
    assert outcome.status == REJECTED
    assert outcome.order.state == OrderState.REJECTED
    events = [e.event_type for e in get_chain_events(db, "chain-1")]
    assert "ORDER_REJECTED" in events


def test_unresolvable_position_becomes_unknown_and_records_incident(db):
    # Broker acknowledges (order/deal tickets exist), but neither
    # history_deals_get nor history_orders_get has anything to resolve
    # the position from — must land in UNKNOWN, never guess.
    gw = FakeGateway(order_send_responses=[
        OrderSendResult(retcode=10009, comment="done", broker_order_id="111", broker_deal_id="222",
                         broker_position_id=None, volume_filled=0.05, price_filled=2000.0, raw={}),
    ])
    outcome = _submit(db, gw)
    assert outcome.status == UNKNOWN
    assert outcome.order.state == OrderState.UNKNOWN

    from adaptive_scalper.execution.reconciliation import has_dangerous_unresolved_unknown
    assert has_dangerous_unresolved_unknown(db) is True


def test_exact_request_never_mutated_between_check_and_send(db):
    gw = FakeGateway()
    _submit(db, gw)
    assert len(gw.order_send_calls) == 1
    sent = gw.order_send_calls[0]
    assert sent.symbol == "XAUUSDm"
    assert sent.direction == "BUY"
    assert sent.volume == 0.05
    assert sent.stop_loss == 1990.0
    assert sent.take_profit == 2020.0
    assert sent.filling_type == "IOC"


def test_already_progressed_order_is_not_resubmitted(db):
    gw = FakeGateway()
    first = _submit(db, gw)
    assert first.status == FILLED
    calls_after_first = len(gw.order_send_calls)

    second = _submit(db, gw)  # same client_request_id, same chain
    assert len(gw.order_send_calls) == calls_after_first  # no second send
    assert second.order.id == first.order.id
