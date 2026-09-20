"""Tests for execution.close (execution-safety review finding #2): proves
the safe close path cannot create reverse exposure."""

from __future__ import annotations

from adaptive_scalper.execution.close import ALREADY_CLOSED, SENT, VOLUME_MISMATCH, close_position_safely
from adaptive_scalper.gateway.fake_gateway import FakeGateway
from adaptive_scalper.gateway.types import OrderAction, OrderRequest, PositionSnapshot


def _open_position(gw: FakeGateway, direction: str = "BUY", volume: float = 0.05) -> str:
    result = gw.order_send(OrderRequest(action=OrderAction.DEAL, symbol="XAUUSDm", direction=direction, volume=volume, price=2000.0))
    assert result.retcode == 10009
    return gw.positions_get()[0].broker_position_id


def test_buy_position_closes_with_correct_sell_deal_and_ticket():
    gw = FakeGateway()
    ticket = _open_position(gw, direction="BUY", volume=0.05)

    outcome = close_position_safely(gw, broker_position_id=ticket, expected_direction="BUY", expected_volume=0.05)
    assert outcome.status == SENT
    assert gw.positions_get() == []
    sent_request = gw.order_send_calls[-1]
    assert sent_request.direction == "SELL"
    assert sent_request.position_ticket == int(ticket)
    assert sent_request.volume == 0.05


def test_sell_position_closes_with_correct_buy_deal_and_ticket():
    gw = FakeGateway()
    ticket = _open_position(gw, direction="SELL", volume=0.05)

    outcome = close_position_safely(gw, broker_position_id=ticket, expected_direction="SELL", expected_volume=0.05)
    assert outcome.status == SENT
    assert gw.positions_get() == []
    sent_request = gw.order_send_calls[-1]
    assert sent_request.direction == "BUY"
    assert sent_request.position_ticket == int(ticket)


def test_already_closed_race_never_sends_opposite_trade():
    """The position vanished (SL/TP/manual) between the decision and the
    close attempt — must be reported as ALREADY_CLOSED, and must NEVER
    call order_send at all (which would risk opening new exposure)."""
    gw = FakeGateway()
    outcome = close_position_safely(gw, broker_position_id="99999", expected_direction="BUY", expected_volume=0.05)
    assert outcome.status == ALREADY_CLOSED
    assert gw.order_send_calls == []


def test_wrong_direction_expectation_refuses_to_send():
    gw = FakeGateway()
    ticket = _open_position(gw, direction="BUY", volume=0.05)
    calls_before = len(gw.order_send_calls)
    outcome = close_position_safely(gw, broker_position_id=ticket, expected_direction="SELL", expected_volume=0.05)
    assert outcome.status == VOLUME_MISMATCH
    assert len(gw.order_send_calls) == calls_before  # no additional (close) call was sent
    assert gw.positions_get() != []  # nothing was sent, position still open


def test_volume_mismatch_refuses_to_send():
    gw = FakeGateway()
    ticket = _open_position(gw, direction="BUY", volume=0.05)
    calls_before = len(gw.order_send_calls)
    outcome = close_position_safely(gw, broker_position_id=ticket, expected_direction="BUY", expected_volume=0.10)
    assert outcome.status == VOLUME_MISMATCH
    assert len(gw.order_send_calls) == calls_before
    assert gw.positions_get() != []


def test_broker_position_disappeared_between_decision_and_send_is_never_a_reverse_open():
    """Simulates the exact race: caller decided to close based on stale
    state, but by send-time the broker no longer shows the position
    (closed by SL in between). Must detect ALREADY_CLOSED via the FRESH
    positions_get() fetch inside close_position_safely, not the stale
    caller-held snapshot."""
    gw = FakeGateway()
    ticket = _open_position(gw, direction="BUY", volume=0.05)
    gw.remove_open_position(ticket)  # simulate broker-side SL close, out of band
    calls_before = len(gw.order_send_calls)

    outcome = close_position_safely(gw, broker_position_id=ticket, expected_direction="BUY", expected_volume=0.05)
    assert outcome.status == ALREADY_CLOSED
    assert len(gw.order_send_calls) == calls_before
    assert gw.positions_get() == []  # still empty — no reverse position was ever opened


def test_close_never_opens_a_new_position_under_any_outcome():
    """Broad regression: across every branch (SENT, ALREADY_CLOSED,
    VOLUME_MISMATCH), the number of OPEN positions can only stay the
    same or decrease — never increase — proving the safe close path
    cannot create reverse exposure."""
    gw = FakeGateway()
    ticket = _open_position(gw, direction="BUY", volume=0.05)
    before = len(gw.positions_get())

    close_position_safely(gw, broker_position_id=ticket, expected_direction="SELL", expected_volume=0.05)
    assert len(gw.positions_get()) <= before

    close_position_safely(gw, broker_position_id=ticket, expected_direction="BUY", expected_volume=0.05)
    assert len(gw.positions_get()) <= before
