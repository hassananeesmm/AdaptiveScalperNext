"""Tests for execution.position_resolution (execution-safety review finding #1)."""

from __future__ import annotations

import pytest

from adaptive_scalper.execution.position_resolution import resolve_entry_fill_evidence, resolve_opened_position_id
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


# --------------------------------------------------------------------------
# resolve_entry_fill_evidence (external review finding #8/#9, 2026-09-21):
# never entry_price=0.0 -- entry price must come from positively proven
# broker deal evidence, and every matching entry deal must be returned.
# --------------------------------------------------------------------------

def test_entry_fill_evidence_resolves_single_deal():
    gw = FakeGateway(historical_deals=[_deal(ticket=500, order=100, position_id=900, price=2000.0, volume=0.05)])
    evidence = resolve_entry_fill_evidence(
        gw, broker_deal_id="500", broker_order_id="100", window_from_utc=0, window_to_utc=9999999999,
    )
    assert evidence.resolved
    assert evidence.broker_position_id == "900"
    assert len(evidence.deals) == 1
    assert evidence.total_filled_volume == 0.05
    assert evidence.weighted_avg_price == 2000.0


def test_entry_fill_evidence_aggregates_multiple_in_deals_for_the_same_position():
    gw = FakeGateway(historical_deals=[
        _deal(ticket=500, order=100, position_id=900, price=2000.0, volume=0.02),
        _deal(ticket=501, order=100, position_id=900, price=2010.0, volume=0.03),
    ])
    evidence = resolve_entry_fill_evidence(
        gw, broker_deal_id="500", broker_order_id="100", window_from_utc=0, window_to_utc=9999999999,
    )
    assert evidence.resolved
    assert len(evidence.deals) == 2
    assert evidence.total_filled_volume == 0.05
    # weighted avg = (2000*0.02 + 2010*0.03) / 0.05 = 2006.0
    assert evidence.weighted_avg_price == pytest.approx(2006.0)


def test_entry_fill_evidence_ignores_deals_for_other_positions():
    gw = FakeGateway(historical_deals=[
        _deal(ticket=500, order=100, position_id=900, price=2000.0, volume=0.02),
        _deal(ticket=600, order=200, position_id=901, price=1990.0, volume=0.05),  # different position
    ])
    evidence = resolve_entry_fill_evidence(
        gw, broker_deal_id="500", broker_order_id="100", window_from_utc=0, window_to_utc=9999999999,
    )
    assert evidence.resolved
    assert len(evidence.deals) == 1
    assert evidence.total_filled_volume == 0.02


def test_entry_fill_evidence_unresolved_when_only_zero_price_deal_exists():
    gw = FakeGateway(historical_deals=[_deal(ticket=500, order=100, position_id=900, price=0.0, volume=0.05)])
    evidence = resolve_entry_fill_evidence(
        gw, broker_deal_id="500", broker_order_id="100", window_from_utc=0, window_to_utc=9999999999,
    )
    assert not evidence.resolved
    assert evidence.weighted_avg_price is None


def test_entry_fill_evidence_ignores_out_deals():
    gw = FakeGateway(historical_deals=[
        _deal(ticket=500, order=100, position_id=900, entry=1, price=2010.0, volume=0.05),  # OUT, not IN
    ])
    evidence = resolve_entry_fill_evidence(
        gw, broker_deal_id="500", broker_order_id="100", window_from_utc=0, window_to_utc=9999999999,
    )
    # position resolution itself requires an IN/any deal match by ticket to
    # find the position id; here the only deal is OUT, so the position
    # resolves via deal ticket match, but no IN evidence exists to price it.
    assert not evidence.resolved


def test_entry_fill_evidence_unresolved_when_position_itself_unresolved():
    gw = FakeGateway()
    evidence = resolve_entry_fill_evidence(
        gw, broker_deal_id="500", broker_order_id="100", window_from_utc=0, window_to_utc=9999999999,
    )
    assert not evidence.resolved
    assert evidence.broker_position_id is None
