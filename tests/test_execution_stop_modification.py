"""Tests for execution.stop_modification (directive: protective stop
execution, monotonic protection)."""

from __future__ import annotations

from adaptive_scalper.execution.stop_modification import (
    ALREADY_CLOSED,
    BROKER_CONSTRAINT,
    NO_CHANGE,
    NOT_DEMO,
    NO_QUOTE,
    REJECTED,
    SENT,
    TOO_CLOSE_TO_PRICE,
    UNKNOWN,
    VOLUME_MISMATCH,
    modify_protective_stop_safely,
)
from adaptive_scalper.gateway.fake_gateway import FakeGateway
from adaptive_scalper.gateway.types import (
    AccountSnapshot,
    OrderAction,
    OrderCheckResult,
    OrderRequest,
    OrderSendResult,
    SymbolSpec,
    SymbolTradeMode,
    TerminalSnapshot,
    Tick,
    TradeMode,
)


def _demo_account(**overrides) -> AccountSnapshot:
    defaults = dict(
        login=123, trade_mode=TradeMode.DEMO, balance=10000.0, equity=10000.0, margin_free=10000.0,
        currency="USD", server="ICMarketsSC-Demo", company="IC Markets", trade_allowed=True, trade_expert=True,
    )
    defaults.update(overrides)
    return AccountSnapshot(**defaults)


def _demo_terminal(**overrides) -> TerminalSnapshot:
    defaults = dict(connected=True, trade_allowed=True, build=1000, name="MT5", company="MetaQuotes", path="")
    defaults.update(overrides)
    return TerminalSnapshot(**defaults)


def _symbol_spec(**overrides) -> SymbolSpec:
    defaults = dict(
        name="XAUUSDm", description="Gold", currency_base="XAU", currency_profit="USD", currency_margin="USD",
        digits=2, point=0.01, trade_contract_size=100.0, volume_min=0.01, volume_max=50.0, volume_step=0.01,
        trade_tick_size=0.01, trade_tick_value=1.0, spread=20, visible=True,
        trade_mode=SymbolTradeMode.FULL, filling_mode=3, trade_stops_level=50, trade_freeze_level=0,
    )
    defaults.update(overrides)
    return SymbolSpec(**defaults)


def _tick(**overrides) -> Tick:
    defaults = dict(time=5000, bid=2000.0, ask=2000.20, last=2000.0, volume=1.0)
    defaults.update(overrides)
    return Tick(**defaults)


def _demo_gateway(**gw_overrides) -> FakeGateway:
    defaults = dict(
        account=_demo_account(), terminal=_demo_terminal(), symbols=[_symbol_spec()],
        ticks={"XAUUSDm": _tick()},
    )
    defaults.update(gw_overrides)
    return FakeGateway(**defaults)


def _open_position(gw: FakeGateway, direction: str = "BUY", volume: float = 0.05, stop_loss: float = 0.0) -> str:
    result = gw.order_send(OrderRequest(action=OrderAction.DEAL, symbol="XAUUSDm", direction=direction, volume=volume, price=2000.0, stop_loss=stop_loss))
    assert result.retcode == 10009
    return gw.positions_get()[0].broker_position_id


def _modify(gw, ticket, **overrides):
    defaults = dict(
        broker_position_id=ticket, expected_direction="BUY", expected_volume=0.05, broker_symbol="XAUUSDm",
        proposed_stop_price=1995.0, now=5000.0,  # matches _tick()'s default time=5000 -> age 0, always fresh
    )
    defaults.update(overrides)
    return modify_protective_stop_safely(gw, **defaults)


def test_advances_stop_from_none():
    gw = _demo_gateway()
    ticket = _open_position(gw, direction="BUY", stop_loss=0.0)
    outcome = _modify(gw, ticket, proposed_stop_price=1990.0)
    assert outcome.status == SENT
    assert gw.positions_get()[0].stop_loss == 1990.0


def test_buy_stop_never_moves_backward():
    gw = _demo_gateway()
    ticket = _open_position(gw, direction="BUY", stop_loss=1995.0)
    outcome = _modify(gw, ticket, proposed_stop_price=1990.0)  # worse than current 1995
    assert outcome.status == NO_CHANGE
    assert gw.positions_get()[0].stop_loss == 1995.0  # untouched


def test_buy_stop_advances_forward():
    gw = _demo_gateway()
    ticket = _open_position(gw, direction="BUY", stop_loss=1990.0)
    outcome = _modify(gw, ticket, proposed_stop_price=1998.0)
    assert outcome.status == SENT
    assert gw.positions_get()[0].stop_loss == 1998.0


def test_sell_stop_never_moves_backward():
    gw = _demo_gateway()
    ticket = _open_position(gw, direction="SELL", stop_loss=2005.0)
    outcome = _modify(gw, ticket, expected_direction="SELL", proposed_stop_price=2010.0)  # worse for a SELL
    assert outcome.status == NO_CHANGE
    assert gw.positions_get()[0].stop_loss == 2005.0


def test_sell_stop_advances_forward():
    gw = _demo_gateway()
    ticket = _open_position(gw, direction="SELL", stop_loss=2010.0)
    outcome = _modify(gw, ticket, expected_direction="SELL", proposed_stop_price=2003.0)
    assert outcome.status == SENT
    assert gw.positions_get()[0].stop_loss == 2003.0


def test_exact_same_stop_is_no_change():
    gw = _demo_gateway()
    ticket = _open_position(gw, direction="BUY", stop_loss=1995.0)
    outcome = _modify(gw, ticket, proposed_stop_price=1995.0)
    assert outcome.status == NO_CHANGE


def test_already_closed_position_refuses():
    gw = _demo_gateway()
    outcome = _modify(gw, "99999")
    assert outcome.status == ALREADY_CLOSED
    assert gw.order_send_calls == []


def test_wrong_direction_expectation_refuses():
    gw = _demo_gateway()
    ticket = _open_position(gw, direction="BUY")
    outcome = _modify(gw, ticket, expected_direction="SELL")
    assert outcome.status == VOLUME_MISMATCH


def test_account_not_demo_refuses():
    gw = _demo_gateway(account=_demo_account(trade_mode=TradeMode.REAL))
    outcome = _modify(gw, "1")
    assert outcome.status == NOT_DEMO
    assert gw.order_send_calls == []


def test_too_close_to_price_refuses():
    # trade_stops_level=50 points * point=0.01 = 0.50 min distance;
    # bid=2000.0, proposed stop=1999.9 -> distance=0.10 < 0.50
    gw = _demo_gateway()
    ticket = _open_position(gw, direction="BUY", stop_loss=0.0)
    outcome = _modify(gw, ticket, proposed_stop_price=1999.9)
    assert outcome.status == TOO_CLOSE_TO_PRICE
    assert gw.order_send_calls == [
        gw.order_send_calls[0]
    ]  # only the opening DEAL -- no SLTP request sent


def test_no_quote_refuses():
    gw = _demo_gateway(ticks={})
    ticket = _open_position(gw, direction="BUY", stop_loss=0.0)
    outcome = _modify(gw, ticket, proposed_stop_price=1990.0)
    assert outcome.status == NO_QUOTE


def test_broker_already_advanced_between_check_and_send_is_no_change():
    gw = _demo_gateway()
    ticket = _open_position(gw, direction="BUY", stop_loss=1990.0)

    original_positions_get = gw.positions_get
    calls = {"n": 0}

    def flaky_positions_get():
        calls["n"] += 1
        positions = original_positions_get()
        if calls["n"] >= 2 and positions:
            import dataclasses
            positions[0] = dataclasses.replace(positions[0], stop_loss=1999.0)  # already advanced further
        return positions

    gw.positions_get = flaky_positions_get
    outcome = _modify(gw, ticket, proposed_stop_price=1995.0)
    assert outcome.status == NO_CHANGE


def test_order_check_failure_blocks_send():
    gw = _demo_gateway()
    ticket = _open_position(gw, direction="BUY", stop_loss=0.0)
    calls_before = len(gw.order_send_calls)

    def rejecting_check(request):
        return OrderCheckResult(retcode=10016, comment="invalid stops", margin_required=None)

    gw.order_check = rejecting_check
    outcome = _modify(gw, ticket, proposed_stop_price=1990.0)
    assert outcome.status == BROKER_CONSTRAINT
    assert len(gw.order_send_calls) == calls_before


def test_ambiguous_retcode_becomes_unknown():
    gw = _demo_gateway()
    ticket = _open_position(gw, direction="BUY", stop_loss=0.0)
    gw._order_send_responses = [
        OrderSendResult(retcode=10012, comment="timeout", broker_order_id=None, broker_deal_id=None,
                         broker_position_id=None, volume_filled=0.0, price_filled=None, raw={}),
    ]
    outcome = _modify(gw, ticket, proposed_stop_price=1990.0)
    assert outcome.status == UNKNOWN


def test_definitive_rejection_reports_rejected():
    gw = _demo_gateway()
    ticket = _open_position(gw, direction="BUY", stop_loss=0.0)
    gw._order_send_responses = [
        OrderSendResult(retcode=10016, comment="invalid stops", broker_order_id=None, broker_deal_id=None,
                         broker_position_id=None, volume_filled=0.0, price_filled=None, raw={}),
    ]
    outcome = _modify(gw, ticket, proposed_stop_price=1990.0)
    assert outcome.status == REJECTED


# --- External review findings (2026-09-21): DEMO/TP/symbol-state must be
# reverified INDEPENDENTLY immediately before send, not reused from the
# order_check round. ---


def test_account_switches_to_real_between_check_and_send_blocks_send():
    gw = _demo_gateway()
    ticket = _open_position(gw, direction="BUY", stop_loss=0.0)
    calls_before = len(gw.order_send_calls)

    original_account_info = gw.account_info
    calls = {"n": 0}

    def flaky_account_info():
        calls["n"] += 1
        if calls["n"] >= 2:
            return _demo_account(trade_mode=TradeMode.REAL)
        return original_account_info()

    gw.account_info = flaky_account_info
    outcome = _modify(gw, ticket, proposed_stop_price=1990.0)
    assert outcome.status == NOT_DEMO
    # DEMO on first check, REAL on final check => zero SLTP order_send calls
    assert len(gw.order_send_calls) == calls_before


def test_terminal_trading_disabled_between_check_and_send_blocks_send():
    gw = _demo_gateway()
    ticket = _open_position(gw, direction="BUY", stop_loss=0.0)
    calls_before = len(gw.order_send_calls)

    original_terminal_info = gw.terminal_info
    calls = {"n": 0}

    def flaky_terminal_info():
        calls["n"] += 1
        if calls["n"] >= 2:
            return _demo_terminal(trade_allowed=False)
        return original_terminal_info()

    gw.terminal_info = flaky_terminal_info
    outcome = _modify(gw, ticket, proposed_stop_price=1990.0)
    assert outcome.status == NOT_DEMO
    assert len(gw.order_send_calls) == calls_before


def test_tp_refreshed_before_send_never_sends_stale_tp():
    gw = _demo_gateway()
    gw.order_send(OrderRequest(
        action=OrderAction.DEAL, symbol="XAUUSDm", direction="BUY", volume=0.05, price=2000.0,
        stop_loss=0.0, take_profit=2050.0,
    ))
    ticket = gw.positions_get()[0].broker_position_id

    original_positions_get = gw.positions_get
    original_order_check = gw.order_check
    calls = {"n": 0}
    order_check_calls = []

    def flaky_positions_get():
        calls["n"] += 1
        positions = original_positions_get()
        if calls["n"] >= 2 and positions:
            import dataclasses
            positions[0] = dataclasses.replace(positions[0], take_profit=2060.0)  # TP moved between rounds
        return positions

    def counting_order_check(request):
        order_check_calls.append(request)
        return original_order_check(request)

    gw.positions_get = flaky_positions_get
    gw.order_check = counting_order_check

    outcome = _modify(gw, ticket, proposed_stop_price=1990.0)
    assert outcome.status == SENT
    # the request actually SENT carries the fresh round-2 TP, never round 1's stale value
    assert gw.order_send_calls[-1].take_profit == 2060.0
    # the rebuilt (changed) request was independently order_check'd before send
    assert len(order_check_calls) == 2
    assert order_check_calls[0].take_profit == 2050.0
    assert order_check_calls[1].take_profit == 2060.0


def test_symbol_disabled_between_check_and_send_blocks_send():
    gw = _demo_gateway()
    ticket = _open_position(gw, direction="BUY", stop_loss=0.0)
    calls_before = len(gw.order_send_calls)

    original_symbol_info = gw.symbol_info
    calls = {"n": 0}

    def flaky_symbol_info(name):
        calls["n"] += 1
        if calls["n"] >= 2:
            return _symbol_spec(trade_mode=SymbolTradeMode.DISABLED)
        return original_symbol_info(name)

    gw.symbol_info = flaky_symbol_info
    outcome = _modify(gw, ticket, proposed_stop_price=1990.0)
    assert outcome.status == BROKER_CONSTRAINT
    assert len(gw.order_send_calls) == calls_before


def test_stale_quote_between_check_and_send_blocks_send():
    gw = _demo_gateway()
    ticket = _open_position(gw, direction="BUY", stop_loss=0.0)
    calls_before = len(gw.order_send_calls)

    original_symbol_info_tick = gw.symbol_info_tick
    calls = {"n": 0}

    def flaky_tick(name):
        calls["n"] += 1
        if calls["n"] >= 2:
            return _tick(time=1)  # far in the past relative to now=5000.0 -> stale
        return original_symbol_info_tick(name)

    gw.symbol_info_tick = flaky_tick
    outcome = _modify(gw, ticket, proposed_stop_price=1990.0)
    assert outcome.status == NO_QUOTE
    assert len(gw.order_send_calls) == calls_before


def test_freeze_level_blocks_too_close_stop():
    gw = _demo_gateway(symbols=[_symbol_spec(trade_stops_level=0, trade_freeze_level=50)])
    ticket = _open_position(gw, direction="BUY", stop_loss=0.0)
    calls_before = len(gw.order_send_calls)
    # freeze_level=50 points * point=0.01 = 0.50 min distance; bid=2000.0,
    # proposed stop=1999.9 -> distance=0.10 < 0.50
    outcome = _modify(gw, ticket, proposed_stop_price=1999.9)
    assert outcome.status == TOO_CLOSE_TO_PRICE
    assert len(gw.order_send_calls) == calls_before
