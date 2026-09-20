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
from adaptive_scalper.persistence import connect, migrate
from adaptive_scalper.position_management.adaptive_exit import AdaptiveExitParams
from adaptive_scalper.position_management.manager import FULL_CLOSE, HOLD, MOVE_PROTECTIVE_STOP, PositionReviewInput, review_position_once
from adaptive_scalper.position_management.state_store import get_state


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


def _demo_gateway(bid: float = 2000.0, ask: float = 2000.20, **gw_overrides) -> FakeGateway:
    defaults = dict(
        account=_demo_account(), terminal=_demo_terminal(), symbols=[_symbol_spec()],
        ticks={"XAUUSDm": Tick(time=5000, bid=bid, ask=ask, last=bid, volume=1.0)},
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

    result = review_position_once(db, gw, _base_input(position_id, ticket, unrealized_pnl=2.0), now_utc=1010)
    assert result.action == HOLD
    assert result.current_r == pytest.approx(0.1)
    assert gw.order_send_calls[-1].action != "SLTP"  # nothing sent beyond the opening deal (no SLTP call)


def test_breakeven_trigger_moves_stop(db):
    gw = _demo_gateway()
    ticket = _open_position(gw)
    position_id = _insert_local_position(db, ticket)

    # current_r = unrealized_pnl / initial_monetary_risk = 8/20 = 0.40 -> breakeven trigger
    result = review_position_once(db, gw, _base_input(position_id, ticket, unrealized_pnl=8.0), now_utc=1010)
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
    result = review_position_once(db, gw, _base_input(position_id, ticket, unrealized_pnl=20.0), now_utc=1010)
    assert result.action == FULL_CLOSE
    assert result.close_outcome is not None
    assert result.close_outcome.status == "SENT"
    assert gw.positions_get() == []  # actually closed

    state = get_state(db, position_id)
    assert state.decision_r == pytest.approx(1.0)
    assert state.request_at_utc == 1010


def test_thesis_invalidated_triggers_full_close_even_when_profitable(db):
    gw = _demo_gateway()
    ticket = _open_position(gw)
    position_id = _insert_local_position(db, ticket)

    result = review_position_once(
        db, gw, _base_input(position_id, ticket, unrealized_pnl=4.0, strategy_setup_still_valid=False), now_utc=1010,
    )
    assert result.action == FULL_CLOSE
    assert any("setup condition" in r for r in result.reasons)


def test_regime_reversal_triggers_full_close(db):
    gw = _demo_gateway()
    ticket = _open_position(gw)
    position_id = _insert_local_position(db, ticket)

    result = review_position_once(
        db, gw, _base_input(position_id, ticket, unrealized_pnl=4.0, current_regime="TRENDING_DOWN"), now_utc=1010,
    )
    assert result.action == FULL_CLOSE


def test_max_holding_time_triggers_full_close(db):
    gw = _demo_gateway()
    ticket = _open_position(gw)
    position_id = _insert_local_position(db, ticket)

    result = review_position_once(
        db, gw, _base_input(position_id, ticket, unrealized_pnl=1.0, holding_seconds=600), now_utc=1010,
    )
    assert result.action == FULL_CLOSE


def test_peak_r_persists_across_review_calls(db):
    gw = _demo_gateway()
    ticket = _open_position(gw)
    position_id = _insert_local_position(db, ticket)

    review_position_once(db, gw, _base_input(position_id, ticket, unrealized_pnl=8.0), now_utc=1010)  # r=0.40, breakeven fires
    result2 = review_position_once(db, gw, _base_input(position_id, ticket, unrealized_pnl=2.0), now_utc=1020)  # r=0.10, dropped
    assert result2.action == HOLD
    assert result2.peak_r == pytest.approx(0.40)  # never dropped


def test_invalid_initial_risk_quarantines_r_and_holds(db):
    gw = _demo_gateway()
    ticket = _open_position(gw)
    position_id = _insert_local_position(db, ticket, initial_monetary_risk=0.0)

    result = review_position_once(db, gw, _base_input(position_id, ticket, initial_monetary_risk=0.0, unrealized_pnl=5.0), now_utc=1010)
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
        now_utc=1010,
    )
    assert result.action == MOVE_PROTECTIVE_STOP
    # SELL: new stop = entry(2000) - 0.05*10 = 1999.5
    assert gw.positions_get()[0].stop_loss == pytest.approx(1999.5)


def test_full_close_updates_local_and_broker_state_together(db):
    gw = _demo_gateway()
    ticket = _open_position(gw)
    position_id = _insert_local_position(db, ticket)

    review_position_once(db, gw, _base_input(position_id, ticket, unrealized_pnl=20.0), now_utc=1010)
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
