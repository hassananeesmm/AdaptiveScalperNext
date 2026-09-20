"""Tests for UNKNOWN order-outcome resolution (directive section 30,
execution-safety review finding #3: full resolution against current
positions/orders AND history)."""

from __future__ import annotations

import pytest

from adaptive_scalper.execution.request_token import request_token
from adaptive_scalper.execution.state_machine import OrderState
from adaptive_scalper.execution.store import OrderRecord
from adaptive_scalper.execution.unknown import resolve_unknown_order, resolve_unknown_order_without_broker_id
from adaptive_scalper.gateway.types import HistoricalDeal, HistoricalOrder, PendingOrderSnapshot, PositionSnapshot


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


def _history_deal(order: int, position_id: int = 0, **overrides) -> HistoricalDeal:
    defaults = dict(
        ticket=500, order=order, time=1000, type=0, entry=0, magic=0, position_id=position_id,
        volume=0.05, price=2000.0, commission=0.0, swap=0.0, profit=0.0, fee=0.0,
        symbol="XAUUSDm", comment="", external_id="",
    )
    defaults.update(overrides)
    return HistoricalDeal(**defaults)


def _resolve(order, *, current_positions=(), current_pending_orders=(), history_orders=(), history_deals=()):
    return resolve_unknown_order(
        order,
        current_positions=list(current_positions),
        current_pending_orders=list(current_pending_orders),
        history_orders=list(history_orders),
        history_deals=list(history_deals),
    )


def test_no_broker_order_id_cannot_be_resolved():
    order = _order(broker_order_id=None)
    result = _resolve(order)
    assert result.resolved is False
    assert result.conflict is False


def test_no_evidence_anywhere_stays_unresolved():
    order = _order(broker_order_id="999")
    result = _resolve(order, history_orders=[_history_order(111, state=4)])
    assert result.resolved is False
    assert result.conflict is False


# -- historical-order-only resolution (legacy coverage, still supported) --

def test_resolves_to_filled_from_history_order():
    order = _order(broker_order_id="111")
    result = _resolve(order, history_orders=[_history_order(111, state=4, position_id=55)])
    assert result.resolved is True
    assert result.new_state == OrderState.FILLED
    assert result.matched_broker_position_id == "55"


def test_resolves_to_rejected():
    order = _order(broker_order_id="111")
    result = _resolve(order, history_orders=[_history_order(111, state=5)])
    assert result.new_state == OrderState.REJECTED


def test_resolves_to_cancelled():
    order = _order(broker_order_id="111")
    result = _resolve(order, history_orders=[_history_order(111, state=2)])
    assert result.new_state == OrderState.CANCELLED


def test_resolves_to_expired():
    order = _order(broker_order_id="111")
    result = _resolve(order, history_orders=[_history_order(111, state=6)])
    assert result.new_state == OrderState.EXPIRED


def test_resolves_to_partial():
    order = _order(broker_order_id="111")
    result = _resolve(order, history_orders=[_history_order(111, state=3)])
    assert result.new_state == OrderState.PARTIAL


def test_resolves_to_resting_when_still_placed():
    order = _order(broker_order_id="111")
    result = _resolve(order, history_orders=[_history_order(111, state=1)])
    assert result.new_state == OrderState.RESTING


@pytest.mark.parametrize("transient_state", [0, 7, 8, 9])
def test_transient_broker_internal_states_do_not_resolve(transient_state):
    order = _order(broker_order_id="111")
    result = _resolve(order, history_orders=[_history_order(111, state=transient_state)])
    assert result.resolved is False


def test_matches_by_ticket_not_position():
    order = _order(broker_order_id="111")
    # A different order (ticket 222) with the same position_id must NOT match.
    result = _resolve(order, history_orders=[_history_order(222, state=4, position_id=999)])
    assert result.resolved is False


# -- new: current broker state (positions_get/orders_get) --

def test_resolves_to_filled_from_current_open_position():
    order = _order(broker_order_id="111", broker_position_id="55")
    position = PositionSnapshot("55", "XAUUSDm", "BUY", 0.05, 2000.0, 1990.0, 2010.0, 0.0, 0, "")
    result = _resolve(order, current_positions=[position])
    assert result.resolved is True
    assert result.new_state == OrderState.FILLED
    assert result.matched_broker_position_id == "55"


def test_resolves_to_resting_from_current_pending_order():
    order = _order(broker_order_id="111")
    pending = PendingOrderSnapshot("111", "XAUUSDm", "BUY", 0.05, 1990.0, 0, "")
    result = _resolve(order, current_pending_orders=[pending])
    assert result.resolved is True
    assert result.new_state == OrderState.RESTING


def test_resolves_to_filled_from_history_deal():
    order = _order(broker_order_id="111")
    deal = _history_deal(order=111, position_id=77)
    result = _resolve(order, history_deals=[deal])
    assert result.resolved is True
    assert result.new_state == OrderState.FILLED
    assert result.matched_broker_position_id == "77"


def test_agreeing_evidence_across_multiple_sources_resolves_cleanly():
    order = _order(broker_order_id="111", broker_position_id="55")
    position = PositionSnapshot("55", "XAUUSDm", "BUY", 0.05, 2000.0, 1990.0, 2010.0, 0.0, 0, "")
    deal = _history_deal(order=111, position_id=55)
    order_hist = _history_order(111, state=4, position_id=55)
    result = _resolve(order, current_positions=[position], history_deals=[deal], history_orders=[order_hist])
    assert result.resolved is True
    assert result.new_state == OrderState.FILLED


# -- new: conflicting evidence -> PENDING_RECONCILIATION, never guessed --

def test_conflicting_evidence_is_never_resolved():
    """History says REJECTED, but the broker currently reports an open
    position for the same order id — must NOT be silently resolved
    either way; requires PENDING_RECONCILIATION."""
    order = _order(broker_order_id="111", broker_position_id="55")
    position = PositionSnapshot("55", "XAUUSDm", "BUY", 0.05, 2000.0, 1990.0, 2010.0, 0.0, 0, "")
    order_hist = _history_order(111, state=5)  # REJECTED
    result = _resolve(order, current_positions=[position], history_orders=[order_hist])
    assert result.resolved is False
    assert result.conflict is True


def test_conflicting_evidence_pending_vs_history_filled():
    order = _order(broker_order_id="111")
    pending = PendingOrderSnapshot("111", "XAUUSDm", "BUY", 0.05, 1990.0, 0, "")
    order_hist = _history_order(111, state=4)  # FILLED
    result = _resolve(order, current_pending_orders=[pending], history_orders=[order_hist])
    assert result.resolved is False
    assert result.conflict is True


# --------------------------------------------------------------------------
# resolve_unknown_order_without_broker_id (execution-safety review round 2
# finding #7): secondary correlation when broker_order_id was never recorded
# --------------------------------------------------------------------------

def _resolve_no_id(order, *, current_positions=(), current_pending_orders=(), history_orders=(), history_deals=()):
    return resolve_unknown_order_without_broker_id(
        order,
        current_positions=list(current_positions),
        current_pending_orders=list(current_pending_orders),
        history_orders=list(history_orders),
        history_deals=list(history_deals),
    )


def test_raises_if_broker_order_id_is_known():
    order = _order(broker_order_id="111", client_request_id="req-abc")
    import pytest as _pytest
    with _pytest.raises(ValueError):
        _resolve_no_id(order)


def test_no_match_at_all_stays_unknown():
    order = _order(broker_order_id=None, client_request_id="req-abc")
    result = _resolve_no_id(order)
    assert result.resolved is False
    assert result.conflict is False


def test_resolves_from_current_position_comment_token():
    order = _order(broker_order_id=None, client_request_id="req-abc-123", broker_symbol="XAUUSDm")
    token = request_token("req-abc-123")
    position = PositionSnapshot("55", "XAUUSDm", "BUY", 0.05, 2000.0, 1990.0, 2010.0, 0.0, 0, f"{token} note")
    result = _resolve_no_id(order, current_positions=[position])
    assert result.resolved is True
    assert result.new_state == OrderState.FILLED
    assert result.matched_broker_position_id == "55"


def test_wrong_symbol_comment_match_is_not_evidence():
    order = _order(broker_order_id=None, client_request_id="req-abc-123", broker_symbol="XAUUSDm")
    token = request_token("req-abc-123")
    position = PositionSnapshot("55", "GBPJPYm", "BUY", 0.05, 2000.0, 1990.0, 2010.0, 0.0, 0, token)
    result = _resolve_no_id(order, current_positions=[position])
    assert result.resolved is False


def test_resolves_from_pending_order_comment_token():
    order = _order(broker_order_id=None, client_request_id="req-xyz", broker_symbol="XAUUSDm")
    token = request_token("req-xyz")
    pending = PendingOrderSnapshot("77", "XAUUSDm", "BUY", 0.05, 1990.0, 0, token)
    result = _resolve_no_id(order, current_pending_orders=[pending])
    assert result.resolved is True
    assert result.new_state == OrderState.RESTING


def test_resolves_from_history_deal_comment_token():
    order = _order(broker_order_id=None, client_request_id="req-deal", broker_symbol="XAUUSDm")
    token = request_token("req-deal")
    deal = _history_deal(order=999, position_id=88, comment=token)
    result = _resolve_no_id(order, history_deals=[deal])
    assert result.resolved is True
    assert result.new_state == OrderState.FILLED
    assert result.matched_broker_position_id == "88"


def test_ambiguous_when_matches_disagree_on_position():
    order = _order(broker_order_id=None, client_request_id="req-amb", broker_symbol="XAUUSDm")
    token = request_token("req-amb")
    position_a = PositionSnapshot("55", "XAUUSDm", "BUY", 0.05, 2000.0, 1990.0, 2010.0, 0.0, 0, token)
    deal_b = _history_deal(order=1, position_id=99, comment=token)
    result = _resolve_no_id(order, current_positions=[position_a], history_deals=[deal_b])
    assert result.resolved is False
    assert result.conflict is True


def test_agreeing_matches_across_sources_resolve_cleanly():
    order = _order(broker_order_id=None, client_request_id="req-agree", broker_symbol="XAUUSDm")
    token = request_token("req-agree")
    position = PositionSnapshot("55", "XAUUSDm", "BUY", 0.05, 2000.0, 1990.0, 2010.0, 0.0, 0, token)
    deal = _history_deal(order=1, position_id=55, comment=token)
    result = _resolve_no_id(order, current_positions=[position], history_deals=[deal])
    assert result.resolved is True
    assert result.matched_broker_position_id == "55"
