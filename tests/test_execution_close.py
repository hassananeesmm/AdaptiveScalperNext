"""Tests for execution.close (execution-safety review round 1 finding #2,
round 2 finding #3; brought to stop_modification.py's hardening standard
by external review 2026-09-21 findings #3/#4): proves the safe close path
cannot create reverse exposure and carries the same pre-send protections
as a new entry."""

from __future__ import annotations

import pytest

from adaptive_scalper.execution.close import (
    ALREADY_CLOSED,
    BROKER_CONSTRAINT,
    CANCELLED,
    FULLY_CLOSED,
    NOT_DEMO,
    NO_QUOTE,
    PARTIAL_CLOSE,
    REJECTED,
    SYMBOL_MISMATCH,
    UNKNOWN,
    VOLUME_MISMATCH,
    close_position_safely,
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
from adaptive_scalper.persistence import connect, migrate


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
        trade_mode=SymbolTradeMode.FULL, filling_mode=3,
    )
    defaults.update(overrides)
    return SymbolSpec(**defaults)


def _demo_gateway(**gw_overrides) -> FakeGateway:
    defaults = dict(
        account=_demo_account(), terminal=_demo_terminal(), symbols=[_symbol_spec()],
        ticks={"XAUUSDm": Tick(time=5000, bid=1999.0, ask=2001.0, last=2000.0, volume=1.0)},
    )
    defaults.update(gw_overrides)
    return FakeGateway(**defaults)


def _open_position(gw: FakeGateway, direction: str = "BUY", volume: float = 0.05) -> str:
    result = gw.order_send(OrderRequest(action=OrderAction.DEAL, symbol="XAUUSDm", direction=direction, volume=volume, price=2000.0))
    assert result.retcode == 10009
    return gw.positions_get()[0].broker_position_id


def _close(gw, ticket, **overrides):
    defaults = dict(
        broker_position_id=ticket, expected_direction="BUY", expected_volume=0.05, broker_symbol="XAUUSDm",
        clock=lambda: 5000.0,  # matches _demo_gateway()'s default tick time=5000 -> always fresh
    )
    defaults.update(overrides)
    return close_position_safely(gw, **defaults)


def test_buy_position_closes_with_correct_sell_deal_and_ticket():
    gw = _demo_gateway()
    ticket = _open_position(gw, direction="BUY", volume=0.05)

    outcome = _close(gw, ticket)
    assert outcome.status == FULLY_CLOSED
    assert gw.positions_get() == []
    sent_request = gw.order_send_calls[-1]
    assert sent_request.direction == "SELL"
    assert sent_request.position_ticket == int(ticket)
    assert sent_request.volume == 0.05


def test_sell_position_closes_with_correct_buy_deal_and_ticket():
    gw = _demo_gateway()
    ticket = _open_position(gw, direction="SELL", volume=0.05)

    outcome = _close(gw, ticket, expected_direction="SELL")
    assert outcome.status == FULLY_CLOSED
    assert gw.positions_get() == []
    sent_request = gw.order_send_calls[-1]
    assert sent_request.direction == "BUY"
    assert sent_request.position_ticket == int(ticket)


def test_already_closed_race_never_sends_opposite_trade():
    gw = _demo_gateway()
    outcome = _close(gw, "99999")
    assert outcome.status == ALREADY_CLOSED
    assert gw.order_send_calls == []


def test_wrong_direction_expectation_refuses_to_send():
    gw = _demo_gateway()
    ticket = _open_position(gw, direction="BUY", volume=0.05)
    calls_before = len(gw.order_send_calls)
    outcome = _close(gw, ticket, expected_direction="SELL")
    assert outcome.status == VOLUME_MISMATCH
    assert len(gw.order_send_calls) == calls_before
    assert gw.positions_get() != []


def test_volume_mismatch_refuses_to_send():
    gw = _demo_gateway()
    ticket = _open_position(gw, direction="BUY", volume=0.05)
    calls_before = len(gw.order_send_calls)
    outcome = _close(gw, ticket, expected_volume=0.10)
    assert outcome.status == VOLUME_MISMATCH
    assert len(gw.order_send_calls) == calls_before
    assert gw.positions_get() != []


def test_broker_position_disappeared_between_decision_and_send_is_never_a_reverse_open():
    gw = _demo_gateway()
    ticket = _open_position(gw, direction="BUY", volume=0.05)
    gw.remove_open_position(ticket)  # simulate broker-side SL close, out of band
    calls_before = len(gw.order_send_calls)

    outcome = _close(gw, ticket)
    assert outcome.status == ALREADY_CLOSED
    assert len(gw.order_send_calls) == calls_before
    assert gw.positions_get() == []


def test_close_never_opens_a_new_position_under_any_outcome():
    gw = _demo_gateway()
    ticket = _open_position(gw, direction="BUY", volume=0.05)
    before = len(gw.positions_get())

    _close(gw, ticket, expected_direction="SELL")
    assert len(gw.positions_get()) <= before

    _close(gw, ticket)
    assert len(gw.positions_get()) <= before


# --------------------------------------------------------------------------
# Round-2 finding #3 / external review finding #3 (2026-09-21): same
# pre-send protections as a new entry, both rounds identical.
# --------------------------------------------------------------------------

def test_account_not_demo_blocks_close_entirely():
    from adaptive_scalper.gateway.types import TradeMode
    gw = _demo_gateway(account=_demo_account(trade_mode=TradeMode.REAL))
    ticket_gw = _demo_gateway()  # open the position on a genuinely DEMO gateway first
    ticket = _open_position(ticket_gw, direction="BUY", volume=0.05)
    # Reuse the same in-memory position state but with a REAL account snapshot:
    gw._open_positions = ticket_gw._open_positions  # noqa: SLF001 -- test-only state transplant

    outcome = _close(gw, ticket)
    assert outcome.status == NOT_DEMO
    assert gw.order_send_calls == []


def test_account_becomes_real_between_check_and_send_blocks():
    gw = _demo_gateway()
    ticket = _open_position(gw, direction="BUY", volume=0.05)
    calls_before = len(gw.order_send_calls)

    original_account_info = gw.account_info
    calls = {"n": 0}

    def flaky_account_info():
        calls["n"] += 1
        if calls["n"] >= 2:
            return _demo_account(trade_mode=TradeMode.REAL)
        return original_account_info()

    gw.account_info = flaky_account_info  # simulate the account switching mid-flight
    outcome = _close(gw, ticket)
    assert outcome.status == NOT_DEMO
    assert len(gw.order_send_calls) == calls_before
    assert gw.positions_get() != []  # nothing was sent, position untouched


def test_order_check_failure_blocks_close_before_send():
    gw = _demo_gateway()
    ticket = _open_position(gw, direction="BUY", volume=0.05)
    calls_before = len(gw.order_send_calls)

    def rejecting_check(request):
        return OrderCheckResult(retcode=10014, comment="invalid volume", margin_required=None)

    gw.order_check = rejecting_check
    outcome = _close(gw, ticket)
    assert outcome.status == BROKER_CONSTRAINT
    assert len(gw.order_send_calls) == calls_before


def test_position_disappears_between_check_and_send_is_caught_by_second_round():
    gw = _demo_gateway()
    ticket = _open_position(gw, direction="BUY", volume=0.05)
    calls_before = len(gw.order_send_calls)

    original_positions_get = gw.positions_get
    calls = {"n": 0}

    def flaky_positions_get():
        calls["n"] += 1
        if calls["n"] >= 2:
            return []
        return original_positions_get()

    gw.positions_get = flaky_positions_get
    outcome = _close(gw, ticket)
    assert outcome.status == ALREADY_CLOSED
    assert len(gw.order_send_calls) == calls_before


def test_no_supported_filling_mode_blocks_close():
    gw = _demo_gateway(symbols=[_symbol_spec(filling_mode=0)])
    ticket = _open_position(gw, direction="BUY", volume=0.05)
    calls_before = len(gw.order_send_calls)
    outcome = _close(gw, ticket)
    assert outcome.status == BROKER_CONSTRAINT
    assert len(gw.order_send_calls) == calls_before


def test_symbol_mismatch_blocks_close():
    gw = _demo_gateway()
    ticket = _open_position(gw, direction="BUY", volume=0.05)
    calls_before = len(gw.order_send_calls)
    outcome = _close(gw, ticket, broker_symbol="GBPJPYm")  # position is actually XAUUSDm
    assert outcome.status == SYMBOL_MISMATCH
    assert len(gw.order_send_calls) == calls_before


def test_stale_quote_blocks_close():
    gw = _demo_gateway(ticks={"XAUUSDm": Tick(time=1, bid=1999.0, ask=2001.0, last=2000.0, volume=1.0)})
    ticket = _open_position(gw, direction="BUY", volume=0.05)
    calls_before = len(gw.order_send_calls)
    outcome = _close(gw, ticket)  # clock=5000.0 vs tick.time=1 -> far stale
    assert outcome.status == NO_QUOTE
    assert len(gw.order_send_calls) == calls_before


def test_real_wall_clock_advancing_past_freshness_blocks_round_2():
    # External review finding #1 (second round): the freshness clock must
    # be independently re-read at each round.
    gw = _demo_gateway()
    ticket = _open_position(gw, direction="BUY", volume=0.05)
    calls_before = len(gw.order_send_calls)
    clock_calls = {"n": 0}

    def advancing_clock():
        clock_calls["n"] += 1
        return 5000.0 if clock_calls["n"] == 1 else 5010.0

    outcome = _close(gw, ticket, clock=advancing_clock)
    assert outcome.status == NO_QUOTE
    assert len(gw.order_send_calls) == calls_before
    assert clock_calls["n"] >= 2


def test_ambiguous_close_retcode_becomes_unknown_never_blindly_resent():
    gw = _demo_gateway()
    ticket = _open_position(gw, direction="BUY", volume=0.05)

    gw._order_send_responses = [
        OrderSendResult(retcode=10012, comment="timeout", broker_order_id=None, broker_deal_id=None,
                         broker_position_id=None, volume_filled=0.0, price_filled=None, raw={}),
    ]
    outcome = _close(gw, ticket)
    assert outcome.status == UNKNOWN
    assert len(gw.order_send_calls) == 2  # the opening DEAL + this one close attempt only


def test_definitive_rejection_on_close_reports_rejected():
    gw = _demo_gateway()
    ticket = _open_position(gw, direction="BUY", volume=0.05)

    gw._order_send_responses = [
        OrderSendResult(retcode=10019, comment="no money", broker_order_id=None, broker_deal_id=None,
                         broker_position_id=None, volume_filled=0.0, price_filled=None, raw={}),
    ]
    outcome = _close(gw, ticket)
    assert outcome.status == REJECTED


# --------------------------------------------------------------------------
# External review finding #4 (2026-09-21): CANCELLED/PARTIAL_CLOSE must
# never be collapsed into a blanket success.
# --------------------------------------------------------------------------

def test_cancelled_close_retcode_is_not_reported_as_fully_closed():
    gw = _demo_gateway()
    ticket = _open_position(gw, direction="BUY", volume=0.05)

    gw._order_send_responses = [
        OrderSendResult(retcode=10007, comment="cancelled", broker_order_id=None, broker_deal_id=None,
                         broker_position_id=None, volume_filled=0.0, price_filled=None, raw={}),
    ]
    outcome = _close(gw, ticket)
    assert outcome.status == CANCELLED
    assert outcome.status != FULLY_CLOSED


def test_partial_close_reports_partial_close_not_fully_closed():
    gw = _demo_gateway()
    ticket = _open_position(gw, direction="BUY", volume=0.05)

    gw._order_send_responses = [
        OrderSendResult(retcode=10010, comment="partial", broker_order_id="111", broker_deal_id="222",
                         broker_position_id=None, volume_filled=0.02, price_filled=2000.0, raw={}),
    ]
    outcome = _close(gw, ticket)
    assert outcome.status == PARTIAL_CLOSE
    assert outcome.status != FULLY_CLOSED


def test_partial_close_updates_local_volume_and_risk_from_broker_truth():
    conn = connect(":memory:")
    migrate(conn)

    gw = _demo_gateway()
    ticket = _open_position(gw, direction="BUY", volume=0.05)

    conn.execute(
        """
        INSERT INTO positions
            (broker_position_id, canonical_symbol, direction, volume, entry_price,
             initial_monetary_risk, strategy_key, status, opened_at_utc)
        VALUES (?, 'XAUUSD', 'BUY', 0.05, 2000.0, 20.0, 'momentum_continuation', 'OPEN', 1000)
        """,
        (ticket,),
    )
    conn.commit()

    gw._order_send_responses = [
        OrderSendResult(retcode=10010, comment="partial", broker_order_id="111", broker_deal_id="222",
                         broker_position_id=None, volume_filled=0.02, price_filled=2000.0, raw={}),
    ]
    outcome = _close(gw, ticket, conn=conn)
    assert outcome.status == PARTIAL_CLOSE

    row = conn.execute("SELECT volume, initial_monetary_risk FROM positions WHERE broker_position_id = ?", (ticket,)).fetchone()
    assert row["volume"] == pytest.approx(0.03)  # 0.05 - 0.02 filled
    assert row["initial_monetary_risk"] == pytest.approx(20.0 * (0.03 / 0.05))
    conn.close()
