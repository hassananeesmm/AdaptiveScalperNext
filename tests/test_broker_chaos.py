"""Deterministic broker chaos tests (Phase 2).

Every scenario is driven by `chaos_harness.ChaosGateway`'s fault plan.
The invariant under test throughout: AMBIGUITY BLOCKS NEW EXPOSURE, while
risk reduction (safe close), reconciliation and recovery stay available.
Nothing here ever resends an order whose outcome is unknown.
"""

from __future__ import annotations

import sqlite3

import pytest

from adaptive_scalper.core.kill_switch import bootstrap as bootstrap_kill_switch
from adaptive_scalper.core.kill_switch import engage as engage_kill_switch
from adaptive_scalper.core.kill_switch import get_state as kill_switch_state
from adaptive_scalper.core.operator_authority import OperatorAuthority
from adaptive_scalper.execution.close import ALREADY_CLOSED, FULLY_CLOSED, NOT_DEMO, PARTIAL_CLOSE
from adaptive_scalper.execution.close import UNKNOWN as CLOSE_UNKNOWN
from adaptive_scalper.execution.close import close_position_safely
from adaptive_scalper.execution.reconciliation import (
    BLOCKING_MISMATCH,
    CLEAN,
    RECOVERED,
    get_open_positions,
    has_dangerous_unresolved_unknown,
    run_reconciliation,
)
from adaptive_scalper.execution.recovery import apply_unknown_resolutions, quarantine_interrupted_submissions
from adaptive_scalper.execution.service import (
    BLOCKED_BROKER_CONSTRAINT,
    BLOCKED_BROKER_STATE,
    FILLED,
    PARTIAL,
    RESTING,
    UNKNOWN,
    submit_new_entry,
)
from adaptive_scalper.execution.state_machine import OrderState
from adaptive_scalper.execution.stop_modification import UNKNOWN as STOP_UNKNOWN
from adaptive_scalper.execution.stop_modification import modify_protective_stop_safely
from adaptive_scalper.execution.store import create_order, get_order_by_client_request_id
from adaptive_scalper.gateway.types import (
    HistoricalDeal,
    HistoricalOrder,
    OrderSendResult,
    PositionSnapshot,
    SymbolTradeMode,
    TradeMode,
)
from adaptive_scalper.persistence import connect, migrate
from chaos_harness import (
    BROKER_SYMBOL,
    NOW,
    SimulatedCrash,
    chaos_gateway,
    default_then_raise,
    default_then_return,
    demo_account,
    demo_terminal,
    fresh_tick,
    gold_spec,
    mutate_then_default,
    raise_,
    returns,
)
from test_execution_service import _good_evidence

CLIENT_ID = "req-chaos-1"


@pytest.fixture()
def db(tmp_path):
    conn = connect(tmp_path / "chaos.sqlite3")
    migrate(conn)
    bootstrap_kill_switch(conn, OperatorAuthority("test-operator"), reason="test setup")
    yield conn
    conn.close()


def _submit(db, gw, **overrides):
    kwargs = dict(
        conn=db, gateway=gw, chain_key="chain-chaos", client_request_id=CLIENT_ID, canonical_symbol="XAUUSD",
        broker_symbol=BROKER_SYMBOL, direction="BUY", volume=0.05, stop_loss=1990.0, take_profit=2020.0,
        fetch_fresh_evidence=lambda: _good_evidence(), now_utc=NOW, history_window_seconds=600,
        clock=lambda: float(NOW),
    )
    kwargs.update(overrides)
    return submit_new_entry(**kwargs)


def _position(pid="7001", volume=0.05, direction="BUY", comment="") -> PositionSnapshot:
    return PositionSnapshot(broker_position_id=pid, symbol=BROKER_SYMBOL, direction=direction, volume=volume,
                            price_open=2000.0, stop_loss=1990.0, take_profit=2020.0, profit=0.0, magic=0,
                            comment=comment)


def _local_position(db, pid="7001", volume=0.05, direction="BUY"):
    db.execute(
        "INSERT INTO positions (broker_position_id, canonical_symbol, direction, volume, entry_price, "
        "initial_monetary_risk, strategy_key, status, opened_at_utc) VALUES (?, 'XAUUSD', ?, ?, 2000.0, 20.0, "
        "'momentum_continuation', 'OPEN', ?)", (pid, direction, volume, NOW - 60),
    )


def _close(gw, db=None, pid="7001", volume=0.05, direction="BUY"):
    return close_position_safely(
        gw, broker_position_id=pid, expected_direction=direction, expected_volume=volume, broker_symbol=BROKER_SYMBOL,
        clock=lambda: float(NOW), conn=db, reconciliation_chain_key="recon-chaos" if db is not None else None,
    )


def _no_send(gw):
    return not gw.order_send_calls


# ---------------------------------------------------------------------------
# account / terminal / symbol state changing between the two pre-send rounds
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("mutation,expected_fragment", [
    (lambda gw: gw.set_account(demo_account(trade_mode=TradeMode.REAL)), "BLOCK_ACCOUNT_NOT_DEMO"),
    (lambda gw: gw.set_terminal(demo_terminal(connected=False)), "BLOCK_MT5_DISCONNECTED"),
    (lambda gw: gw.set_terminal(None), "BLOCK_MT5_DISCONNECTED"),
    (lambda gw: gw.set_terminal(demo_terminal(trade_allowed=False)), "BLOCK_TERMINAL_TRADING_DISABLED"),
    (lambda gw: gw.set_account(demo_account(trade_allowed=False)), "BLOCK_BROKER_TRADING_DISABLED"),
    (lambda gw: gw.set_symbol(gold_spec(trade_mode=SymbolTradeMode.CLOSEONLY)), "direction_closeonly"),
    (lambda gw: gw.set_symbol(gold_spec(trade_mode=SymbolTradeMode.SHORTONLY)), "direction_not_allowed"),
    (lambda gw: gw.set_symbol(gold_spec(description="Silver", currency_base="XAG")), "asset_identity_mismatch"),
    (lambda gw: gw.set_tick(BROKER_SYMBOL, fresh_tick(time=NOW - 3600)), "execution_stale_quote"),
    (lambda gw: gw.set_tick(BROKER_SYMBOL, fresh_tick(time=NOW + 3600)), "execution_future_timestamp"),
    (lambda gw: gw.set_tick(BROKER_SYMBOL, None), "execution_no_quote"),
])
def test_broker_state_change_between_check_and_send_blocks_the_send(db, mutation, expected_fragment):
    gw = chaos_gateway().on("order_check", mutate_then_default(mutation), call=1)
    outcome = _submit(db, gw)
    assert outcome.status == BLOCKED_BROKER_STATE
    assert expected_fragment in outcome.detail
    assert _no_send(gw)
    assert not has_dangerous_unresolved_unknown(db)  # nothing was sent: nothing is unknown


def test_demo_to_real_switch_before_the_first_round_never_reaches_order_check(db):
    gw = chaos_gateway(account=demo_account(trade_mode=TradeMode.REAL))
    outcome = _submit(db, gw)
    assert outcome.status == BLOCKED_BROKER_STATE and "NOT_DEMO" in outcome.detail
    assert gw.calls["order_check"] == 0 and _no_send(gw)


# ---------------------------------------------------------------------------
# order_check faults: nothing is sent, nothing becomes UNKNOWN
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("call", [1, 2])
def test_order_check_timeout_blocks_without_sending(db, call):
    gw = chaos_gateway().on("order_check", raise_(TimeoutError("check timed out")), call=call)
    outcome = _submit(db, gw)
    assert outcome.status == BLOCKED_BROKER_CONSTRAINT and "TimeoutError" in outcome.detail
    assert _no_send(gw)
    assert outcome.order.state == OrderState.PROPOSED
    assert not has_dangerous_unresolved_unknown(db)


def test_order_check_unexpected_retcode_blocks_without_sending(db):
    from adaptive_scalper.gateway.types import OrderCheckResult
    gw = chaos_gateway().on("order_check", returns(OrderCheckResult(retcode=10013, comment="invalid", margin_required=None)))
    outcome = _submit(db, gw)
    assert outcome.status == BLOCKED_BROKER_CONSTRAINT and "10013" in outcome.detail
    assert _no_send(gw)


# ---------------------------------------------------------------------------
# order_send faults: UNKNOWN, never resent, new exposure blocked until
# broker truth resolves it
# ---------------------------------------------------------------------------

def test_order_send_timeout_before_reaching_the_broker_stays_unknown_and_blocks(db):
    gw = chaos_gateway().on("order_send", raise_(TimeoutError("transport timeout")))
    outcome = _submit(db, gw)
    assert outcome.status == UNKNOWN
    assert outcome.order.state == OrderState.UNKNOWN
    assert has_dangerous_unresolved_unknown(db)

    # No broker evidence exists anywhere: resolution must not guess.
    results = apply_unknown_resolutions(db, gw, now_utc=NOW + 30)
    assert [r.resolved for r in results] == [False]
    assert has_dangerous_unresolved_unknown(db)
    assert gw.calls["order_send"] == 1  # never resent


def test_lost_acknowledgement_after_a_real_fill_is_recovered_from_broker_truth(db):
    # The broker filled it; the ack never arrived and no broker order id
    # was recorded. Token correlation + entry-deal evidence resolve it.
    gw = chaos_gateway().on("order_send", default_then_raise(TimeoutError("ack lost")))
    outcome = _submit(db, gw)
    assert outcome.status == UNKNOWN and outcome.order.broker_order_id is None
    assert has_dangerous_unresolved_unknown(db)

    results = apply_unknown_resolutions(db, gw, now_utc=NOW + 30)
    assert [r.resolved for r in results] == [True]
    assert results[0].new_state == "FILLED"
    order = get_order_by_client_request_id(db, CLIENT_ID)
    assert order.state == OrderState.FILLED
    local = get_open_positions(db)
    assert len(local) == 1 and local[0].broker_position_id == results[0].broker_position_id
    assert local[0].strategy_key == "momentum_continuation"  # recovered from the decision chain
    assert local[0].entry_price == pytest.approx(2001.0)      # from the IN deal, never invented
    assert not has_dangerous_unresolved_unknown(db)
    assert run_reconciliation(db, gw, "recon").status == CLEAN
    assert gw.calls["order_send"] == 1


def test_timeout_retcode_after_a_real_fill_is_recovered_the_same_way(db):
    ambiguous = OrderSendResult(retcode=10012, comment="timeout", broker_order_id=None, broker_deal_id=None,
                                broker_position_id=None, volume_filled=0.0, price_filled=None, raw={})
    gw = chaos_gateway().on("order_send", default_then_return(ambiguous))
    assert _submit(db, gw).status == UNKNOWN
    assert apply_unknown_resolutions(db, gw, now_utc=NOW + 30)[0].new_state == "FILLED"
    assert not has_dangerous_unresolved_unknown(db)


def test_unknown_with_a_broker_order_id_resolves_to_rejected_from_order_history(db):
    ambiguous = OrderSendResult(retcode=10012, comment="timeout", broker_order_id="9001", broker_deal_id=None,
                                broker_position_id=None, volume_filled=0.0, price_filled=None, raw={})
    gw = chaos_gateway().on("order_send", returns(ambiguous))
    assert _submit(db, gw).status == UNKNOWN
    assert apply_unknown_resolutions(db, gw, now_utc=NOW + 30)[0].resolved is False  # no evidence yet

    gw._historical_orders.append(HistoricalOrder(
        ticket=9001, time_setup=NOW, time_done=NOW, type=0, state=5, magic=0, position_id=0, volume_initial=0.05,
        volume_current=0.0, price_open=0.0, sl=0.0, tp=0.0, price_current=0.0, symbol=BROKER_SYMBOL, comment="",
        external_id="",
    ))
    result = apply_unknown_resolutions(db, gw, now_utc=NOW + 60)[0]
    assert result.resolved and result.new_state == "REJECTED"
    assert get_open_positions(db) == []
    assert not has_dangerous_unresolved_unknown(db)


def test_ambiguous_correlation_two_broker_positions_one_token_stays_blocked(db):
    # Two broker positions both carry this order's request token (a
    # duplicate fill). Resolution must refuse to pick one.
    gw = chaos_gateway().on("order_send", default_then_raise(TimeoutError("ack lost")))
    _submit(db, gw)
    token_comment = gw.order_send_calls[0].comment
    gw.inject_open_position(_position(pid="8888", comment=token_comment))
    result = apply_unknown_resolutions(db, gw, now_utc=NOW + 30)[0]
    assert result.resolved is False and "ambiguous" in result.detail
    assert get_order_by_client_request_id(db, CLIENT_ID).state == OrderState.PENDING_RECONCILIATION
    assert has_dangerous_unresolved_unknown(db)


def test_partial_fill_and_resting_results_are_real_broker_state(db):
    partial = OrderSendResult(retcode=10010, comment="partial", broker_order_id="5001", broker_deal_id="5002",
                              broker_position_id=None, volume_filled=0.02, price_filled=2001.0, raw={})
    gw = chaos_gateway().on("order_send", returns(partial))
    gw._historical_deals.append(HistoricalDeal(
        ticket=5002, order=5001, time=NOW, type=0, entry=0, magic=0, position_id=5003, volume=0.02, price=2001.0,
        commission=0.0, swap=0.0, profit=0.0, fee=0.0, symbol=BROKER_SYMBOL, comment="", external_id="",
    ))
    outcome = _submit(db, gw)
    assert outcome.status == PARTIAL
    assert get_open_positions(db)[0].volume == pytest.approx(0.02)

    placed = OrderSendResult(retcode=10008, comment="placed", broker_order_id="6001", broker_deal_id=None,
                             broker_position_id=None, volume_filled=0.0, price_filled=None, raw={})
    gw2 = chaos_gateway().on("order_send", returns(placed))
    assert _submit(db, gw2, client_request_id="req-resting").status == RESTING


def test_duplicate_callback_never_sends_twice(db):
    gw = chaos_gateway()
    first = _submit(db, gw)
    second = _submit(db, gw)
    assert first.status == FILLED
    assert second.order.id == first.order.id
    assert "not re-evaluating or re-sending" in second.detail
    assert len(gw.order_send_calls) == 1


# ---------------------------------------------------------------------------
# crash / restart at every step of the entry lifecycle
# ---------------------------------------------------------------------------

def test_crash_between_proposal_and_check_is_retried_once_safely(db):
    gw = chaos_gateway().on("order_check", raise_(SimulatedCrash()), call=1)
    with pytest.raises(SimulatedCrash):
        _submit(db, gw)
    assert get_order_by_client_request_id(db, CLIENT_ID).state == OrderState.PROPOSED
    assert quarantine_interrupted_submissions(db, now_utc=NOW + 5) == []  # nothing was ever sent
    assert _submit(db, chaos_gateway()).status == FILLED


def test_crash_after_send_before_acknowledgement_is_quarantined_then_recovered(db):
    gw = chaos_gateway().on("order_send", default_then_raise(SimulatedCrash()))
    with pytest.raises(SimulatedCrash):
        _submit(db, gw)
    assert get_order_by_client_request_id(db, CLIENT_ID).state == OrderState.SUBMITTED
    assert not has_dangerous_unresolved_unknown(db)  # the gap: nothing blocked yet

    # "restart"
    assert len(quarantine_interrupted_submissions(db, now_utc=NOW + 5)) == 1
    assert has_dangerous_unresolved_unknown(db)
    assert apply_unknown_resolutions(db, gw, now_utc=NOW + 10)[0].new_state == "FILLED"
    assert not has_dangerous_unresolved_unknown(db)
    assert len(gw.order_send_calls) == 1


def test_crash_after_acknowledgement_before_position_resolution_recovers(db):
    gw = chaos_gateway().on("history_deals_get", raise_(SimulatedCrash()), call=1)
    with pytest.raises(SimulatedCrash):
        _submit(db, gw)
    order = get_order_by_client_request_id(db, CLIENT_ID)
    assert order.state == OrderState.ACCEPTED and order.broker_order_id is not None
    assert get_open_positions(db) == []

    quarantine_interrupted_submissions(db, now_utc=NOW + 5)
    assert apply_unknown_resolutions(db, gw, now_utc=NOW + 10)[0].new_state == "FILLED"
    assert len(get_open_positions(db)) == 1


def test_postsend_history_query_error_is_quarantined_unknown_immediately(db):
    gw = chaos_gateway().on("history_deals_get", raise_(RuntimeError("MT5 history unavailable")), call=1)
    outcome = _submit(db, gw)
    assert outcome.status == UNKNOWN
    assert outcome.order.state == OrderState.UNKNOWN
    assert "accepted-order broker-truth resolution" in outcome.detail
    assert has_dangerous_unresolved_unknown(db)
    assert len(gw.order_send_calls) == 1

    recovered = apply_unknown_resolutions(db, gw, now_utc=NOW + 10)
    assert recovered[0].new_state == "FILLED"
    assert len(get_open_positions(db)) == 1
    assert len(gw.order_send_calls) == 1


def test_partial_fill_history_query_error_is_quarantined_unknown_immediately(db):
    partial = OrderSendResult(
        retcode=10010, comment="partial", broker_order_id="5001", broker_deal_id="5002",
        broker_position_id=None, volume_filled=0.02, price_filled=2001.0, raw={},
    )
    gw = chaos_gateway().on("order_send", returns(partial))
    gw._historical_deals.append(HistoricalDeal(
        ticket=5002, order=5001, time=NOW, type=0, entry=0, magic=0, position_id=5003, volume=0.02,
        price=2001.0, commission=0.0, swap=0.0, profit=0.0, fee=0.0, symbol=BROKER_SYMBOL,
        comment="", external_id="",
    ))
    gw.on("history_deals_get", raise_(RuntimeError("MT5 history unavailable")), call=1)
    outcome = _submit(db, gw)
    assert outcome.status == UNKNOWN
    assert outcome.order.state == OrderState.UNKNOWN
    assert "partial-fill broker-truth resolution" in outcome.detail
    assert has_dangerous_unresolved_unknown(db)
    assert len(gw.order_send_calls) == 1


def test_crash_during_close_is_reconciled_from_broker_truth_after_restart(db):
    gw = chaos_gateway()
    gw.inject_open_position(_position())
    _local_position(db)
    gw.on("order_send", default_then_raise(SimulatedCrash()))
    with pytest.raises(SimulatedCrash):
        _close(gw)
    # "restart": the broker closed it; local state still says OPEN.
    report = run_reconciliation(db, gw, "recon-restart", now_utc=NOW + 5)
    assert report.status == RECOVERED
    assert get_open_positions(db) == []


# ---------------------------------------------------------------------------
# risk reduction stays available; the kill switch only blocks NEW exposure
# ---------------------------------------------------------------------------

def test_engaged_kill_switch_blocks_entries_but_never_liquidates_or_blocks_closes(db):
    engage_kill_switch(db, "operator test", "operator")
    gw = chaos_gateway()
    gw.inject_open_position(_position())
    entry = _submit(db, gw)
    assert entry.status == BLOCKED_BROKER_STATE and "BLOCK_KILL_SWITCH" in entry.detail
    assert gw.positions_get(), "engaging the kill switch must not close anything"
    assert _close(gw).status == FULLY_CLOSED  # risk reduction remains available
    assert kill_switch_state(db).status.value == "ENGAGED"


def test_closeonly_symbol_blocks_entries_but_allows_the_close(db):
    gw = chaos_gateway(symbols=[gold_spec(trade_mode=SymbolTradeMode.CLOSEONLY)])
    gw.inject_open_position(_position())
    assert _submit(db, gw).status == BLOCKED_BROKER_STATE
    assert _close(gw).status == FULLY_CLOSED


def test_close_is_refused_on_a_real_account(db):
    gw = chaos_gateway(account=demo_account(trade_mode=TradeMode.REAL))
    gw.inject_open_position(_position())
    assert _close(gw).status == NOT_DEMO
    assert _no_send(gw)


def test_close_order_send_timeout_is_unknown_and_reconciled_not_resent(db):
    gw = chaos_gateway()
    gw.inject_open_position(_position())
    _local_position(db)
    gw.on("order_send", raise_(TimeoutError("send timeout")))
    outcome = _close(gw, db)
    assert outcome.status == CLOSE_UNKNOWN
    assert outcome.reconciliation is not None and outcome.reconciliation.status == CLEAN  # still open at broker
    assert gw.calls["order_send"] == 1


def test_position_disappearing_mid_close_is_already_closed_not_an_opposite_trade(db):
    gw = chaos_gateway()
    gw.inject_open_position(_position())
    gw.on("order_check", mutate_then_default(lambda g: g.remove_open_position("7001")), call=1)
    assert _close(gw).status == ALREADY_CLOSED
    assert _no_send(gw)


def test_partial_closing_fill_updates_local_volume_from_broker_truth(db):
    gw = chaos_gateway()
    gw.inject_open_position(_position(volume=0.05))
    _local_position(db, volume=0.05)
    partial = OrderSendResult(retcode=10010, comment="partial close", broker_order_id="1", broker_deal_id="2",
                              broker_position_id=None, volume_filled=0.02, price_filled=1999.0, raw={})
    gw.on("order_send", returns(partial))
    assert _close(gw, db).status == PARTIAL_CLOSE
    assert get_open_positions(db)[0].volume == pytest.approx(0.03)


def test_stop_modification_send_timeout_is_unknown(db):
    gw = chaos_gateway()
    gw.inject_open_position(_position())
    gw.on("order_send", raise_(TimeoutError("sltp timeout")))
    outcome = modify_protective_stop_safely(
        gw, broker_position_id="7001", expected_direction="BUY", expected_volume=0.05, broker_symbol=BROKER_SYMBOL,
        proposed_stop_price=1995.0, clock=lambda: float(NOW),
    )
    assert outcome.status == STOP_UNKNOWN


# ---------------------------------------------------------------------------
# reconciliation mismatches
# ---------------------------------------------------------------------------

def test_orphan_broker_position_blocks_new_entries(db):
    gw = chaos_gateway()
    gw.inject_open_position(_position(pid="4242"))
    assert run_reconciliation(db, gw, "recon").status == BLOCKING_MISMATCH


def test_local_position_vanished_without_closing_deal_is_blocking(db):
    gw = chaos_gateway()
    _local_position(db, pid="5555")
    report = run_reconciliation(db, gw, "recon")
    assert report.status == BLOCKING_MISMATCH and report.unrepaired_position_ids == ["5555"]


def test_resting_order_disappearing_without_history_is_blocking(db):
    create_order(db, "req-rest", "XAUUSD", BROKER_SYMBOL, "BUY", 0.05, chain_key="c", now_utc=NOW)
    from adaptive_scalper.execution.store import transition_order_state
    order = get_order_by_client_request_id(db, "req-rest")
    transition_order_state(db, order.id, OrderState.SUBMITTED, now_utc=NOW)
    transition_order_state(db, order.id, OrderState.RESTING, broker_order_id="3131", now_utc=NOW)
    assert run_reconciliation(db, chaos_gateway(), "recon").status == BLOCKING_MISMATCH


# ---------------------------------------------------------------------------
# database faults
# ---------------------------------------------------------------------------

def test_database_locked_before_submission_sends_nothing(db, tmp_path):
    blocker = connect(tmp_path / "chaos.sqlite3")
    db.execute("PRAGMA busy_timeout = 50")
    blocker.execute("BEGIN IMMEDIATE")
    try:
        gw = chaos_gateway()
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            _submit(db, gw)
        assert _no_send(gw)
    finally:
        blocker.execute("ROLLBACK")
        blocker.close()


def test_kill_switch_engage_under_a_lock_fails_loudly_and_leaves_state_intact(db, tmp_path):
    blocker = connect(tmp_path / "chaos.sqlite3")
    db.execute("PRAGMA busy_timeout = 50")
    blocker.execute("BEGIN IMMEDIATE")
    try:
        with pytest.raises(sqlite3.OperationalError):
            engage_kill_switch(db, "under lock", "engine")
    finally:
        blocker.execute("ROLLBACK")
        blocker.close()
    assert kill_switch_state(db).status.value == "DISENGAGED"
    assert engage_kill_switch(db, "after lock", "engine").status.value == "ENGAGED"
