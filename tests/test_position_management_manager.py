"""End-to-end tests for the position manager review loop
(position_management/manager.py) — real FakeGateway, real DB."""

from __future__ import annotations

import pytest

from adaptive_scalper.gateway.fake_gateway import FakeGateway
from adaptive_scalper.gateway.types import (
    AccountSnapshot,
    OrderAction,
    OrderRequest,
    SymbolSpec,
    SymbolTradeMode,
    TerminalSnapshot,
    Tick,
    TradeMode,
)
from adaptive_scalper.journal.queries import get_chain_events
from adaptive_scalper.persistence import connect, migrate
from adaptive_scalper.position_management.manager import (
    FULL_CLOSE,
    HOLD,
    MOVE_PROTECTIVE_STOP,
    PositionReviewInput,
    _review_chain_key,
    review_position_once,
)
from adaptive_scalper.position_management.state_store import get_state, has_unresolved_risk_incident


@pytest.fixture()
def db(tmp_path):
    conn = connect(tmp_path / "test.sqlite3")
    migrate(conn)
    yield conn
    conn.close()


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
        trade_mode=SymbolTradeMode.FULL, filling_mode=3, trade_stops_level=0, trade_freeze_level=0,
    )
    defaults.update(overrides)
    return SymbolSpec(**defaults)


def _demo_gateway(bid: float = 2000.0, ask: float = 2000.20, tick_time: int = 1010, **gw_overrides) -> FakeGateway:
    # tick_time matches the now_utc most tests pass to review_position_once()
    # (1010) so the execution-quote-freshness check stop_modification.py
    # now performs (external review finding #3) sees a fresh quote.
    defaults = dict(
        account=_demo_account(), terminal=_demo_terminal(), symbols=[_symbol_spec()],
        ticks={"XAUUSDm": Tick(time=tick_time, bid=bid, ask=ask, last=bid, volume=1.0)},
    )
    defaults.update(gw_overrides)
    return FakeGateway(**defaults)


def _open_position(gw: FakeGateway, direction: str = "BUY", volume: float = 0.05, price: float = 2000.0) -> str:
    result = gw.order_send(OrderRequest(action=OrderAction.DEAL, symbol="XAUUSDm", direction=direction, volume=volume, price=price))
    assert result.retcode == 10009
    return gw.positions_get()[0].broker_position_id


def _insert_local_position(conn, broker_position_id: str, **overrides) -> int:
    row = dict(
        broker_position_id=broker_position_id, canonical_symbol="XAUUSD", direction="BUY", volume=0.05,
        entry_price=2000.0, initial_monetary_risk=20.0, strategy_key="momentum_continuation",
        status="OPEN", opened_at_utc=1000,
    )
    row.update(overrides)
    cursor = conn.execute(
        """
        INSERT INTO positions
            (broker_position_id, canonical_symbol, direction, volume, entry_price,
             initial_monetary_risk, strategy_key, status, opened_at_utc)
        VALUES (:broker_position_id, :canonical_symbol, :direction, :volume, :entry_price,
                :initial_monetary_risk, :strategy_key, :status, :opened_at_utc)
        """,
        row,
    )
    conn.commit()
    return cursor.lastrowid


def _base_input(position_id, ticket, **overrides) -> PositionReviewInput:
    defaults = dict(
        position_id=position_id, broker_position_id=ticket, canonical_symbol="XAUUSD", broker_symbol="XAUUSDm",
        direction="BUY", volume=0.05, entry_price=2000.0, initial_stop_distance_price=10.0,
        initial_monetary_risk=20.0, unrealized_pnl=0.0, entry_regime="TRENDING_UP", current_regime="TRENDING_UP",
        strategy_setup_still_valid=True, current_net_edge_price=0.5, min_required_edge_price=0.1,
        holding_seconds=10,
    )
    defaults.update(overrides)
    return PositionReviewInput(**defaults)


def test_healthy_neutral_position_holds(db):
    gw = _demo_gateway()
    ticket = _open_position(gw)
    position_id = _insert_local_position(db, ticket)

    result = review_position_once(db, gw, _base_input(position_id, ticket, unrealized_pnl=2.0), now_utc=1010, clock=lambda: 1010.0)
    assert result.action == HOLD
    assert result.current_r == pytest.approx(0.1)
    assert gw.order_send_calls[-1].action != "SLTP"  # nothing sent beyond the opening deal (no SLTP call)


def test_breakeven_trigger_moves_stop(db):
    gw = _demo_gateway()
    ticket = _open_position(gw)
    position_id = _insert_local_position(db, ticket)

    # current_r = unrealized_pnl / initial_monetary_risk = 8/20 = 0.40 -> breakeven trigger
    result = review_position_once(db, gw, _base_input(position_id, ticket, unrealized_pnl=8.0), now_utc=1010, clock=lambda: 1010.0)
    assert result.action == MOVE_PROTECTIVE_STOP
    assert result.stop_outcome is not None
    assert result.stop_outcome.status == "SENT"

    # new_stop_r = breakeven_floor_r default 0.05 -> price = entry(2000) + 0.05*10 = 2000.5
    updated_position = gw.positions_get()[0]
    assert updated_position.stop_loss == pytest.approx(2000.5)


def test_early_take_profit_triggers_full_close(db):
    gw = _demo_gateway()
    ticket = _open_position(gw)
    position_id = _insert_local_position(db, ticket)

    # current_r = 20/20 = 1.0 >= early_take_profit_r default 1.0
    result = review_position_once(db, gw, _base_input(position_id, ticket, unrealized_pnl=20.0), now_utc=1010, clock=lambda: 1010.0)
    assert result.action == FULL_CLOSE
    assert result.close_outcome is not None
    assert result.close_outcome.status == "FULLY_CLOSED"
    assert gw.positions_get() == []  # actually closed

    state = get_state(db, position_id)
    assert state.decision_r == pytest.approx(1.0)
    assert state.request_at_utc == 1010


def test_thesis_invalidated_triggers_full_close_even_when_profitable(db):
    gw = _demo_gateway()
    ticket = _open_position(gw)
    position_id = _insert_local_position(db, ticket)

    result = review_position_once(
        db, gw, _base_input(position_id, ticket, unrealized_pnl=4.0, strategy_setup_still_valid=False), now_utc=1010, clock=lambda: 1010.0,
    )
    assert result.action == FULL_CLOSE
    assert any("setup condition" in r for r in result.reasons)


def test_regime_reversal_triggers_full_close(db):
    gw = _demo_gateway()
    ticket = _open_position(gw)
    position_id = _insert_local_position(db, ticket)

    result = review_position_once(
        db, gw, _base_input(position_id, ticket, unrealized_pnl=4.0, current_regime="TRENDING_DOWN"), now_utc=1010, clock=lambda: 1010.0,
    )
    assert result.action == FULL_CLOSE


def test_max_holding_time_triggers_full_close(db):
    gw = _demo_gateway()
    ticket = _open_position(gw)
    position_id = _insert_local_position(db, ticket)

    result = review_position_once(
        db, gw, _base_input(position_id, ticket, unrealized_pnl=1.0, holding_seconds=600), now_utc=1010, clock=lambda: 1010.0,
    )
    assert result.action == FULL_CLOSE


def test_peak_r_persists_across_review_calls(db):
    gw = _demo_gateway()
    ticket = _open_position(gw)
    position_id = _insert_local_position(db, ticket)

    review_position_once(db, gw, _base_input(position_id, ticket, unrealized_pnl=8.0), now_utc=1010, clock=lambda: 1010.0)  # r=0.40, breakeven fires
    result2 = review_position_once(db, gw, _base_input(position_id, ticket, unrealized_pnl=2.0), now_utc=1020)  # r=0.10, dropped
    assert result2.action == HOLD
    assert result2.peak_r == pytest.approx(0.40)  # never dropped


def test_invalid_initial_risk_quarantines_r_and_holds(db):
    gw = _demo_gateway()
    ticket = _open_position(gw)
    position_id = _insert_local_position(db, ticket, initial_monetary_risk=0.0)

    result = review_position_once(db, gw, _base_input(position_id, ticket, initial_monetary_risk=0.0, unrealized_pnl=5.0), now_utc=1010, clock=lambda: 1010.0)
    assert result.action == HOLD
    assert result.current_r is None
    assert gw.order_send_calls == [gw.order_send_calls[0]]  # only the opening deal, nothing else sent


def test_sell_position_breakeven_stop_moves_correct_direction(db):
    gw = _demo_gateway(bid=1990.0, ask=1990.20)
    ticket = _open_position(gw, direction="SELL", price=2000.0)
    position_id = _insert_local_position(db, ticket, direction="SELL")

    result = review_position_once(
        db, gw,
        _base_input(position_id, ticket, direction="SELL", unrealized_pnl=8.0, entry_price=2000.0),
        now_utc=1010, clock=lambda: 1010.0,
    )
    assert result.action == MOVE_PROTECTIVE_STOP
    # SELL: new stop = entry(2000) - 0.05*10 = 1999.5
    assert gw.positions_get()[0].stop_loss == pytest.approx(1999.5)


def test_full_close_updates_local_and_broker_state_together(db):
    gw = _demo_gateway()
    ticket = _open_position(gw)
    position_id = _insert_local_position(db, ticket)

    review_position_once(db, gw, _base_input(position_id, ticket, unrealized_pnl=20.0), now_utc=1010, clock=lambda: 1010.0)
    assert gw.positions_get() == []
    # review_position_once() DOES trigger a real reconciliation pass via
    # close_position_safely(conn=..., reconciliation_chain_key=...), which
    # would normally repair local state from the real closing deal (see
    # test_execution_reconciliation.py's recovery tests). Here it stays
    # OPEN only because FakeGateway's simulated closing deal is stamped
    # time=0 (it doesn't model a clock), which falls outside the
    # reconciliation window anchored on this position's real
    # opened_at_utc=1000 -- a FakeGateway simulation artifact, not a gap
    # in this module; against the real gateway, deal timestamps are real.
    row = db.execute("SELECT status FROM positions WHERE id = ?", (position_id,)).fetchone()
    assert row["status"] == "OPEN"


# --- External review findings (2026-09-21) ---


def test_pre_send_close_block_does_not_record_a_request_timestamp(db):
    # Finding #4: a PRE-SEND block (position already gone by the time
    # close_position_safely re-fetches it -> ALREADY_CLOSED) must never
    # record request_at_utc -- no request ever reached the broker.
    gw = _demo_gateway()
    ticket = _open_position(gw)
    position_id = _insert_local_position(db, ticket)
    gw._open_positions.clear()  # sabotage: broker no longer reports this position

    result = review_position_once(db, gw, _base_input(position_id, ticket, unrealized_pnl=20.0), now_utc=1010, clock=lambda: 1010.0)
    assert result.action == FULL_CLOSE
    assert result.close_outcome.status == "ALREADY_CLOSED"

    state = get_state(db, position_id)
    assert state.decision_at_utc == 1010  # the decision itself is real and recorded
    assert state.request_at_utc is None  # but no request was ever transmitted


def test_full_close_records_a_request_timestamp_when_sent(db):
    gw = _demo_gateway()
    ticket = _open_position(gw)
    position_id = _insert_local_position(db, ticket)

    result = review_position_once(db, gw, _base_input(position_id, ticket, unrealized_pnl=20.0), now_utc=1010, clock=lambda: 1010.0)
    assert result.close_outcome.status == "FULLY_CLOSED"
    state = get_state(db, position_id)
    assert state.request_at_utc == 1010


def test_position_reviewed_journaled_on_hold(db):
    gw = _demo_gateway()
    ticket = _open_position(gw)
    position_id = _insert_local_position(db, ticket)

    review_position_once(db, gw, _base_input(position_id, ticket, unrealized_pnl=2.0), now_utc=1010, clock=lambda: 1010.0)
    events = get_chain_events(db, _review_chain_key(position_id))
    reviewed = [e for e in events if e.event_type == "POSITION_REVIEWED"]
    assert len(reviewed) == 1
    assert reviewed[0].payload["selected_action"] == HOLD
    assert reviewed[0].payload["current_r"] == pytest.approx(0.1)
    assert reviewed[0].payload["quarantined"] is False
    assert reviewed[0].broker_position_id == ticket


def test_stop_advanced_journaled_on_breakeven(db):
    gw = _demo_gateway()
    ticket = _open_position(gw)
    position_id = _insert_local_position(db, ticket)

    review_position_once(db, gw, _base_input(position_id, ticket, unrealized_pnl=8.0), now_utc=1010, clock=lambda: 1010.0)
    events = get_chain_events(db, _review_chain_key(position_id))
    advanced = [e for e in events if e.event_type == "STOP_ADVANCED"]
    assert len(advanced) == 1
    assert advanced[0].payload["new_stop_price"] == pytest.approx(2000.5)


def test_invalid_initial_risk_records_incident_and_journals_quarantined(db):
    gw = _demo_gateway()
    ticket = _open_position(gw)
    position_id = _insert_local_position(db, ticket, initial_monetary_risk=0.0)

    result = review_position_once(
        db, gw, _base_input(position_id, ticket, initial_monetary_risk=0.0, unrealized_pnl=5.0), now_utc=1010, clock=lambda: 1010.0,
    )
    assert result.action == HOLD
    assert result.quarantined is True
    assert has_unresolved_risk_incident(db, position_id) is True

    events = get_chain_events(db, _review_chain_key(position_id))
    reviewed = [e for e in events if e.event_type == "POSITION_REVIEWED"]
    assert len(reviewed) == 1
    assert reviewed[0].payload["quarantined"] is True

    # a repeated quarantined review does not spam a second incident row
    review_position_once(
        db, gw, _base_input(position_id, ticket, initial_monetary_risk=0.0, unrealized_pnl=5.0), now_utc=1020,
    )
    count = db.execute(
        "SELECT COUNT(*) AS n FROM position_risk_incidents WHERE position_id = ?", (position_id,)
    ).fetchone()["n"]
    assert count == 1


def test_full_close_records_real_broker_fill_when_reconciliation_recovers(db):
    # Finding #8: fill_r/giveback_fill/broker_response_at_utc must come
    # from the REAL closing deal reconciliation recovers, never a guess.
    # FakeGateway's simulated close deal hardcodes time=0/profit=0.0 (a
    # documented simulator limitation -- see
    # test_full_close_updates_local_and_broker_state_together above), so
    # this test patches the deal it appends to a realistic time/profit to
    # exercise the real wiring end to end.
    import dataclasses as dc

    gw = _demo_gateway()
    ticket = _open_position(gw)
    position_id = _insert_local_position(db, ticket)

    original_order_send = gw.order_send

    def patched_order_send(request):
        result = original_order_send(request)
        if request.action == OrderAction.DEAL and request.position_ticket is not None:
            last = gw._historical_deals[-1]
            gw._historical_deals[-1] = dc.replace(last, time=1010, profit=20.0, commission=-1.0, swap=-0.5)
        return result

    gw.order_send = patched_order_send

    result = review_position_once(db, gw, _base_input(position_id, ticket, unrealized_pnl=20.0), now_utc=1010, clock=lambda: 1010.0)
    assert result.action == FULL_CLOSE
    assert result.close_outcome.status == "FULLY_CLOSED"
    assert result.close_outcome.reconciliation is not None
    assert ticket in result.close_outcome.reconciliation.recovered_position_ids

    state = get_state(db, position_id)
    # realized_net = profit(20.0) + commission(-1.0) + swap(-0.5) = 18.5; fill_r = 18.5/20.0
    assert state.fill_r == pytest.approx(0.925)
    assert state.broker_response_at_utc == 1010
    assert state.giveback_fill == pytest.approx(state.peak_r - 0.925)


def test_full_close_realized_slippage_computed_from_decision_reference_price(db):
    # Finding #14: realized_slippage = decision-time reference price vs
    # the broker-authoritative exit price, signed so positive = worse for
    # the trader (BUY position closes lower than expected).
    import dataclasses as dc

    gw = _demo_gateway()
    ticket = _open_position(gw)
    position_id = _insert_local_position(db, ticket)

    original_order_send = gw.order_send

    def patched_order_send(request):
        result = original_order_send(request)
        if request.action == OrderAction.DEAL and request.position_ticket is not None:
            last = gw._historical_deals[-1]
            # exit fill price 1995.0 -- worse than the decision reference below
            gw._historical_deals[-1] = dc.replace(last, time=1010, price=1995.0, profit=20.0, commission=0.0, swap=0.0)
        return result

    gw.order_send = patched_order_send

    result = review_position_once(
        db, gw, _base_input(position_id, ticket, unrealized_pnl=20.0, current_price_at_review=2000.0),
        now_utc=1010, clock=lambda: 1010.0,
    )
    assert result.action == FULL_CLOSE
    state = get_state(db, position_id)
    # BUY position: slippage = decision_reference(2000.0) - exit_price(1995.0) = 5.0 (worse)
    assert state.realized_slippage == pytest.approx(5.0)


def test_full_close_aggregates_multiple_closing_deals(db):
    # Finding #13: fill_r/giveback_fill must reflect ALL closing deals
    # recorded for the position, never just the latest one.
    import dataclasses as dc

    gw = _demo_gateway()
    ticket = _open_position(gw)
    position_id = _insert_local_position(db, ticket)

    original_order_send = gw.order_send

    def patched_order_send(request):
        result = original_order_send(request)
        if request.action == OrderAction.DEAL and request.position_ticket is not None:
            last = gw._historical_deals[-1]
            # the real close deal FakeGateway just recorded
            gw._historical_deals[-1] = dc.replace(
                last, ticket=9001, time=1010, price=2000.0, volume=0.03, profit=12.0, commission=-0.3, swap=0.0, fee=0.0,
            )
            # plus an EARLIER partial-close deal for the same broker
            # position that reconciliation should also pick up (a
            # multi-deal close, external review finding #13/#15)
            gw._historical_deals.append(dc.replace(
                last, ticket=9000, time=1005, price=1998.0, volume=0.02, profit=8.0, commission=-0.2, swap=0.0, fee=0.0,
            ))
        return result

    gw.order_send = patched_order_send

    result = review_position_once(db, gw, _base_input(position_id, ticket, unrealized_pnl=20.0), now_utc=1010, clock=lambda: 1010.0)
    assert result.action == FULL_CLOSE

    state = get_state(db, position_id)
    # total_profit=12.0+8.0=20.0; total_commission=-0.3-0.2=-0.5; realized_net=19.5; fill_r=19.5/20.0
    assert state.fill_r == pytest.approx(19.5 / 20.0)
    assert state.broker_response_at_utc == 1010  # the LATEST deal's time


# --------------------------------------------------------------------------
# review journaling volume (BUG_BACKLOG #16)
# --------------------------------------------------------------------------

def _reviewed(db, position_id):
    return [e for e in get_chain_events(db, _review_chain_key(position_id)) if e.event_type == "POSITION_REVIEWED"]


def test_an_unchanged_hold_is_journaled_at_most_once_per_heartbeat(db):
    from adaptive_scalper.position_management.manager import REVIEW_JOURNAL_HEARTBEAT_SECONDS
    from adaptive_scalper.position_management.state_store import get_state as get_pm_state

    gw = _demo_gateway()
    ticket = _open_position(gw)
    position_id = _insert_local_position(db, ticket)
    for now in range(1010, 1010 + 30):  # one review per second
        review_position_once(db, gw, _base_input(position_id, ticket, unrealized_pnl=2.0), now_utc=now,
                             clock=lambda: float(now))
    assert len(_reviewed(db, position_id)) == 1
    assert get_pm_state(db, position_id).last_review_at_utc == 1039  # every review still recorded in state
    later = 1010 + REVIEW_JOURNAL_HEARTBEAT_SECONDS
    review_position_once(db, gw, _base_input(position_id, ticket, unrealized_pnl=2.0), now_utc=later,
                         clock=lambda: float(later))
    assert len(_reviewed(db, position_id)) == 2  # heartbeat


def test_a_regime_change_or_an_action_is_always_journaled(db):
    gw = _demo_gateway()
    ticket = _open_position(gw)
    position_id = _insert_local_position(db, ticket)
    review_position_once(db, gw, _base_input(position_id, ticket, unrealized_pnl=2.0), now_utc=1010,
                         clock=lambda: 1010.0)
    changed = _base_input(position_id, ticket, unrealized_pnl=2.0)
    changed = type(changed)(**{**changed.__dict__, "current_regime": "RANGE"})
    review_position_once(db, gw, changed, now_utc=1011, clock=lambda: 1011.0)
    assert len(_reviewed(db, position_id)) == 2
    review_position_once(db, gw, _base_input(position_id, ticket, unrealized_pnl=8.0), now_utc=1012,
                         clock=lambda: 1012.0)  # breakeven stop move: an action
    reviewed = _reviewed(db, position_id)
    assert len(reviewed) == 3 and reviewed[-1].payload["selected_action"] != "HOLD"
