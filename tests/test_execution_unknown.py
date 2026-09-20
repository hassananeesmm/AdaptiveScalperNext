"""Tests for UNKNOWN order-outcome resolution (directive section 30)."""

from __future__ import annotations

import pytest

from adaptive_scalper.execution.state_machine import OrderState
from adaptive_scalper.execution.store import OrderRecord
from adaptive_scalper.execution.unknown import resolve_unknown_order
from adaptive_scalper.gateway.types import HistoricalOrder


def _order(**overrides) -> OrderRecord:
    defaults = dict(
        id=1, client_request_id="req-1", chain_key=None, canonical_symbol="XAUUSD",
        broker_symbol="XAUUSDm", direction="BUY", requested_volume=0.05, stop_loss=None,
        take_profit=None, state=OrderState.UNKNOWN, broker_order_id=None, broker_position_id=None,
        last_broker_retcode=None, last_broker_comment=None, created_at_utc=1000, updated_at_utc=1000,
    )
    defaults.update(overrides)
    return OrderRecord(**defaults)


def _history_order(ticket: int, state: int, position_id: int = 0, **overrides) -> HistoricalOrder:
    defaults = dict(
        ticket=ticket, time_setup=1000, time_done=1001, type=0, state=state, magic=0,
        position_id=position_id, volume_initial=0.05, volume_current=0.0, price_open=2000.0,
        sl=0.0, tp=0.0, price_current=2000.0, symbol="XAUUSDm", comment="", external_id="",
    )
    defaults.update(overrides)
    return HistoricalOrder(**defaults)


def test_no_broker_order_id_cannot_be_resolved():
    order = _order(broker_order_id=None)
    result = resolve_unknown_order(order, history_orders=[])
    assert result.resolved is False


def test_broker_order_id_not_found_in_history_stays_unresolved():
    order = _order(broker_order_id="999")
    result = resolve_unknown_order(order, history_orders=[_history_order(111, state=4)])
    assert result.resolved is False


def test_resolves_to_filled():
    order = _order(broker_order_id="111")
    result = resolve_unknown_order(order, history_orders=[_history_order(111, state=4, position_id=55)])
    assert result.resolved is True
    assert result.new_state == OrderState.FILLED
    assert result.matched_broker_position_id == "55"


def test_resolves_to_rejected():
    order = _order(broker_order_id="111")
    result = resolve_unknown_order(order, history_orders=[_history_order(111, state=5)])
    assert result.new_state == OrderState.REJECTED


def test_resolves_to_cancelled():
    order = _order(broker_order_id="111")
    result = resolve_unknown_order(order, history_orders=[_history_order(111, state=2)])
    assert result.new_state == OrderState.CANCELLED


def test_resolves_to_expired():
    order = _order(broker_order_id="111")
    result = resolve_unknown_order(order, history_orders=[_history_order(111, state=6)])
    assert result.new_state == OrderState.EXPIRED


def test_resolves_to_partial():
    order = _order(broker_order_id="111")
    result = resolve_unknown_order(order, history_orders=[_history_order(111, state=3)])
    assert result.new_state == OrderState.PARTIAL


def test_resolves_to_resting_when_still_placed():
    order = _order(broker_order_id="111")
    result = resolve_unknown_order(order, history_orders=[_history_order(111, state=1)])
    assert result.new_state == OrderState.RESTING


@pytest.mark.parametrize("transient_state", [0, 7, 8, 9])
def test_transient_broker_internal_states_do_not_resolve(transient_state):
    order = _order(broker_order_id="111")
    result = resolve_unknown_order(order, history_orders=[_history_order(111, state=transient_state)])
    assert result.resolved is False


def test_matches_by_ticket_not_position():
    order = _order(broker_order_id="111")
    # A different order (ticket 222) with the same position_id must NOT match.
    result = resolve_unknown_order(order, history_orders=[_history_order(222, state=4, position_id=999)])
    assert result.resolved is False
