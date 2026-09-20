"""Tests for FakeGateway's order/position simulation (the same Gateway
protocol surface Mt5Gateway implements against the real broker)."""

from __future__ import annotations

import pytest

from adaptive_scalper.gateway.fake_gateway import FAKE_RETCODE_DONE, FAKE_RETCODE_NOT_FOUND, FakeGateway
from adaptive_scalper.gateway.types import OrderAction, OrderRequest, PendingOrderSnapshot, PositionSnapshot, Tick


def _deal_request(**overrides) -> OrderRequest:
    defaults = dict(action=OrderAction.DEAL, symbol="XAUUSDm", direction="BUY", volume=0.05, price=2000.0)
    defaults.update(overrides)
    return OrderRequest(**defaults)


# --------------------------------------------------------------------------
# positions_get / orders_get start empty, reflect injected/simulated state
# --------------------------------------------------------------------------

def test_positions_get_starts_empty():
    gw = FakeGateway()
    assert gw.positions_get() == []


def test_orders_get_starts_empty():
    gw = FakeGateway()
    assert gw.orders_get() == []


def test_inject_open_position_appears_in_positions_get():
    gw = FakeGateway()
    pos = PositionSnapshot("pos-1", "XAUUSD", "BUY", 0.05, 2000.0, 1990.0, 2010.0, 0.0, 0, "")
    gw.inject_open_position(pos)
    assert gw.positions_get() == [pos]


def test_remove_open_position_removes_it():
    gw = FakeGateway()
    pos = PositionSnapshot("pos-1", "XAUUSD", "BUY", 0.05, 2000.0, 0.0, 0.0, 0.0, 0, "")
    gw.inject_open_position(pos)
    gw.remove_open_position("pos-1")
    assert gw.positions_get() == []


# --------------------------------------------------------------------------
# order_send: default auto-fill simulation
# --------------------------------------------------------------------------

def test_deal_order_send_fills_and_opens_a_position():
    gw = FakeGateway()
    result = gw.order_send(_deal_request())
    assert result.retcode == FAKE_RETCODE_DONE
    assert result.volume_filled == 0.05
    assert result.price_filled == 2000.0
    # execution-safety review finding #1: MqlTradeResult carries NO
    # position ticket — this must never be invented from the order/deal
    # ticket. The true position ticket is only discoverable afterward via
    # positions_get()/history_deals_get().
    assert result.broker_position_id is None
    assert result.broker_order_id is not None
    assert result.broker_deal_id is not None

    positions = gw.positions_get()
    assert len(positions) == 1
    assert positions[0].symbol == "XAUUSDm"
    assert positions[0].direction == "BUY"


def test_deal_order_send_position_ticket_differs_from_order_and_deal_ticket():
    """Regression for finding #1: order ticket, deal ticket, and position
    ticket must be three DISTINCT identifiers, proving no code path could
    accidentally treat one as another."""
    gw = FakeGateway()
    result = gw.order_send(_deal_request())
    position = gw.positions_get()[0]
    assert result.broker_order_id != position.broker_position_id
    assert result.broker_deal_id != position.broker_position_id
    assert result.broker_order_id != result.broker_deal_id


def test_position_id_resolvable_from_history_deals():
    gw = FakeGateway()
    result = gw.order_send(_deal_request())
    deals = gw.history_deals_get(0, 999999999)
    match = next(d for d in deals if str(d.ticket) == result.broker_deal_id)
    position = gw.positions_get()[0]
    assert str(match.position_id) == position.broker_position_id


def test_deal_order_send_records_the_call():
    gw = FakeGateway()
    req = _deal_request()
    gw.order_send(req)
    assert gw.order_send_calls == [req]


def test_deal_order_send_uses_tick_price_when_price_is_none():
    tick = Tick(time=1000, bid=1999.0, ask=2001.0, last=2000.0, volume=1.0)
    gw = FakeGateway(ticks={"XAUUSDm": tick})
    result = gw.order_send(_deal_request(price=None, direction="BUY"))
    assert result.price_filled == 2001.0  # ask for a BUY

    result_sell = gw.order_send(_deal_request(price=None, direction="SELL"))
    assert result_sell.price_filled == 1999.0  # bid for a SELL


def test_two_deals_open_two_distinct_positions():
    gw = FakeGateway()
    r1 = gw.order_send(_deal_request())
    r2 = gw.order_send(_deal_request())
    assert r1.broker_order_id != r2.broker_order_id
    assert r1.broker_deal_id != r2.broker_deal_id
    positions = gw.positions_get()
    assert len(positions) == 2
    assert positions[0].broker_position_id != positions[1].broker_position_id


# --------------------------------------------------------------------------
# order_send: SLTP
# --------------------------------------------------------------------------

def test_sltp_updates_matching_position():
    gw = FakeGateway()
    gw.order_send(_deal_request())
    ticket = int(gw.positions_get()[0].broker_position_id)

    result = gw.order_send(OrderRequest(
        action=OrderAction.SLTP, symbol="XAUUSDm", direction="BUY", volume=0.0,
        position_ticket=ticket, stop_loss=1995.0, take_profit=2020.0,
    ))
    assert result.retcode == FAKE_RETCODE_DONE
    updated = gw.positions_get()[0]
    assert updated.stop_loss == 1995.0
    assert updated.take_profit == 2020.0


def test_sltp_on_nonexistent_position_fails():
    gw = FakeGateway()
    result = gw.order_send(OrderRequest(
        action=OrderAction.SLTP, symbol="XAUUSDm", direction="BUY", volume=0.0,
        position_ticket=99999, stop_loss=1995.0,
    ))
    assert result.retcode == FAKE_RETCODE_NOT_FOUND


# --------------------------------------------------------------------------
# order_send: REMOVE
# --------------------------------------------------------------------------

def test_remove_cancels_matching_pending_order():
    gw = FakeGateway()
    gw.inject_pending_order(PendingOrderSnapshot("ord-1", "XAUUSD", "BUY", 0.05, 1990.0, 0, ""))
    result = gw.order_send(OrderRequest(
        action=OrderAction.REMOVE, symbol="XAUUSDm", direction="BUY", volume=0.0, order_ticket=1,
    ))
    # Note: injected pending order key is "ord-1" (string), not the int
    # ticket "1" REMOVE requested — this deliberately proves REMOVE only
    # matches on the exact ticket, not "any pending order."
    assert result.retcode == FAKE_RETCODE_NOT_FOUND
    assert len(gw.orders_get()) == 1  # untouched


def test_remove_cancels_when_ticket_matches():
    gw = FakeGateway()
    gw.inject_pending_order(PendingOrderSnapshot("1", "XAUUSD", "BUY", 0.05, 1990.0, 0, ""))
    result = gw.order_send(OrderRequest(
        action=OrderAction.REMOVE, symbol="XAUUSDm", direction="BUY", volume=0.0, order_ticket=1,
    ))
    assert result.retcode == FAKE_RETCODE_DONE
    assert gw.orders_get() == []


# --------------------------------------------------------------------------
# Queued order_send_responses override the default simulation
# --------------------------------------------------------------------------

def test_queued_responses_override_default_simulation():
    from adaptive_scalper.gateway.types import OrderSendResult

    canned = OrderSendResult(
        retcode=10004, comment="fake: requote", broker_order_id=None, broker_deal_id=None,
        broker_position_id=None, volume_filled=0.0, price_filled=None, raw={},
    )
    gw = FakeGateway(order_send_responses=[canned])
    result = gw.order_send(_deal_request())
    assert result is canned
    assert gw.positions_get() == []  # the canned response never opened anything


def test_queued_responses_exhausted_raises():
    gw = FakeGateway(order_send_responses=[])
    with pytest.raises(AssertionError):
        gw.order_send(_deal_request())


# --------------------------------------------------------------------------
# order_check
# --------------------------------------------------------------------------

def test_order_check_returns_ok_by_default():
    gw = FakeGateway()
    result = gw.order_check(_deal_request())
    assert result.retcode == FAKE_RETCODE_DONE


# --------------------------------------------------------------------------
# DEAL with position_ticket set = a CLOSE, never a new open (finding #2)
# --------------------------------------------------------------------------

def test_deal_with_position_ticket_closes_not_opens():
    gw = FakeGateway()
    gw.order_send(_deal_request())
    ticket = int(gw.positions_get()[0].broker_position_id)

    result = gw.order_send(_deal_request(direction="SELL", position_ticket=ticket))
    assert result.retcode == FAKE_RETCODE_DONE
    assert gw.positions_get() == []  # closed, not left open, and no new position opened


def test_deal_with_unknown_position_ticket_fails_not_found():
    gw = FakeGateway()
    result = gw.order_send(_deal_request(position_ticket=99999))
    assert result.retcode == FAKE_RETCODE_NOT_FOUND
    assert gw.positions_get() == []  # never opens a new position as a side effect of a failed close
