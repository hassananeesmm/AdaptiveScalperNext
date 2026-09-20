"""Tests for execution.position_resolution (execution-safety review finding #1)."""

from __future__ import annotations

from adaptive_scalper.execution.position_resolution import resolve_opened_position_id
from adaptive_scalper.gateway.fake_gateway import FakeGateway
from adaptive_scalper.gateway.types import HistoricalDeal, HistoricalOrder


def _deal(ticket=500, order=100, position_id=900, **overrides) -> HistoricalDeal:
    defaults = dict(
        ticket=ticket, order=order, time=1000, type=0, entry=0, magic=0, position_id=position_id,
        volume=0.05, price=2000.0, commission=0.0, swap=0.0, profit=0.0, fee=0.0,
        symbol="XAUUSDm", comment="", external_id="",
    )
    defaults.update(overrides)
    return HistoricalDeal(**defaults)


def _order(ticket=100, position_id=900, **overrides) -> HistoricalOrder:
    defaults = dict(
        ticket=ticket, time_setup=1000, time_done=1000, type=0, state=4, magic=0, position_id=position_id,
        volume_initial=0.05, volume_current=0.05, price_open=2000.0, sl=0.0, tp=0.0, price_current=2000.0,
        symbol="XAUUSDm", comment="", external_id="",
    )
    defaults.update(overrides)
    return HistoricalOrder(**defaults)


def test_resolves_from_deal_history():
    gw = FakeGateway(historical_deals=[_deal(ticket=500, order=100, position_id=900)])
    result = resolve_opened_position_id(
        gw, broker_deal_id="500", broker_order_id="100", window_from_utc=0, window_to_utc=9999999999
    )
    assert result.resolved
    assert result.broker_position_id == "900"


def test_falls_back_to_order_history_when_deal_not_found():
    gw = FakeGateway(historical_orders=[_order(ticket=100, position_id=901)])
    result = resolve_opened_position_id(
        gw, broker_deal_id="does-not-exist", broker_order_id="100", window_from_utc=0, window_to_utc=9999999999
    )
    assert result.resolved
    assert result.broker_position_id == "901"


def test_unresolved_when_no_evidence():
    gw = FakeGateway()
    result = resolve_opened_position_id(
        gw, broker_deal_id="500", broker_order_id="100", window_from_utc=0, window_to_utc=9999999999
    )
    assert not result.resolved
    assert result.broker_position_id is None


def test_unresolved_when_no_ids_supplied():
    gw = FakeGateway()
    result = resolve_opened_position_id(
        gw, broker_deal_id=None, broker_order_id=None, window_from_utc=0, window_to_utc=9999999999
    )
    assert not result.resolved


def test_deal_evidence_preferred_over_order_evidence():
    """Both sources present and agreeing is fine; deal evidence is
    consulted first and is sufficient on its own without needing order
    history to also match."""
    gw = FakeGateway(historical_deals=[_deal(ticket=500, order=100, position_id=900)])
    result = resolve_opened_position_id(
        gw, broker_deal_id="500", broker_order_id=None, window_from_utc=0, window_to_utc=9999999999
    )
    assert result.resolved
    assert result.broker_position_id == "900"


def test_real_order_send_flow_resolves_correctly():
    """End-to-end against FakeGateway's own order_send simulation:
    proves resolve_opened_position_id() correctly recovers the SAME
    position ticket FakeGateway internally opened, purely from the
    broker_deal_id the OrderSendResult reported (never from
    broker_order_id == broker_position_id)."""
    from adaptive_scalper.gateway.types import OrderAction, OrderRequest

    gw = FakeGateway()
    result = gw.order_send(OrderRequest(action=OrderAction.DEAL, symbol="XAUUSDm", direction="BUY", volume=0.05, price=2000.0))
    actual_position_id = gw.positions_get()[0].broker_position_id

    resolution = resolve_opened_position_id(
        gw, broker_deal_id=result.broker_deal_id, broker_order_id=result.broker_order_id,
        window_from_utc=0, window_to_utc=9999999999,
    )
    assert resolution.resolved
    assert resolution.broker_position_id == actual_position_id
