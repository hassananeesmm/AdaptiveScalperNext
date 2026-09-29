"""Close-side UNKNOWN durability (0.2.6; master prompt section 13).

A close that may have reached the broker but whose outcome cannot be proven
must leave a durable UNRESOLVED `close_requests` row that blocks new
exposure and forbids another close for that position; only fresh positive
broker evidence may resolve it. Deterministic chaos: scripted gateway
failures, no MT5.
"""

from __future__ import annotations

import sqlite3

import pytest

from adaptive_scalper.execution.close import CLOSE_UNRESOLVED, UNKNOWN, close_position_safely
from adaptive_scalper.execution.close_requests import (
    RESOLVED_CLOSED,
    RESOLVED_NOT_EXECUTED,
    RESOLVED_PARTIALLY_CLOSED,
    RESOLVED_STILL_OPEN,
    UNRESOLVED,
    CloseRequestConflict,
    has_unresolved_close,
    record_close_intent,
    resolve_unresolved_closes,
)
from adaptive_scalper.execution.reconciliation import has_dangerous_unresolved_unknown
from adaptive_scalper.gateway.fake_gateway import FakeGateway
from adaptive_scalper.gateway.mt5_gateway import Mt5QueryError
from adaptive_scalper.gateway.types import (
    AccountSnapshot,
    HistoricalDeal,
    OrderAction,
    OrderRequest,
    OrderSendResult,
    SymbolSpec,
    SymbolTradeMode,
    TerminalSnapshot,
    Tick,
    TradeMode,
)
from adaptive_scalper.persistence import connect, migrate

NOW = 5000
MAGIC = 770116


class TruthGateway(FakeGateway):
    """FakeGateway whose broker-truth reads can be switched off
    (`truth_down`) and whose close sends can run a scripted action."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.truth_down = False
        self.send_action = None

    def _truth(self, name: str) -> None:
        if self.truth_down:
            raise Mt5QueryError(f"{name} returned None (MT5 query error -10004)")

    def positions_get(self):
        self._truth("positions_get")
        return super().positions_get()

    def orders_get(self):
        self._truth("orders_get")
        return super().orders_get()

    def history_deals_get(self, date_from_utc, date_to_utc):
        self._truth("history_deals_get")
        return super().history_deals_get(date_from_utc, date_to_utc)

    def order_send(self, request):
        if self.send_action is not None and request.position_ticket is not None:
            self.order_send_calls.append(request)
            return self.send_action(self, request)
        return super().order_send(request)


def _gateway() -> TruthGateway:
    spec = SymbolSpec(
        name="XAUUSDm", description="Gold", currency_base="XAU", currency_profit="USD", currency_margin="USD",
        digits=2, point=0.01, trade_contract_size=100.0, volume_min=0.01, volume_max=50.0, volume_step=0.01,
        trade_tick_size=0.01, trade_tick_value=1.0, spread=20, visible=True,
        trade_mode=SymbolTradeMode.FULL, filling_mode=3,
    )
    return TruthGateway(
        account=AccountSnapshot(login=1, trade_mode=TradeMode.DEMO, balance=10000.0, equity=10000.0,
                                margin_free=10000.0, currency="USD", server="Demo", company="Broker",
                                trade_allowed=True, trade_expert=True),
        terminal=TerminalSnapshot(connected=True, trade_allowed=True, build=1000, name="MT5", company="MQ", path=""),
        symbols=[spec], ticks={"XAUUSDm": Tick(time=NOW, bid=1999.0, ask=2001.0, last=2000.0, volume=1.0)},
    )


def _setup(db=":memory:", volume=0.05):
    conn = connect(db)
    migrate(conn)
    gw = _gateway()
    gw.order_send(OrderRequest(action=OrderAction.DEAL, symbol="XAUUSDm", direction="BUY", volume=volume,
                               price=2000.0, magic=MAGIC))
    ticket = gw.positions_get()[0].broker_position_id
    conn.execute(
        "INSERT INTO positions (broker_position_id, canonical_symbol, direction, volume, entry_price, "
        "initial_monetary_risk, strategy_key, status, opened_at_utc) "
        "VALUES (?, 'XAUUSD', 'BUY', ?, 2000.0, 20.0, 'momentum_continuation', 'OPEN', 1000)",
        (ticket, volume),
    )
    conn.commit()
    return conn, gw, ticket


def _close(gw, ticket, conn):
    return close_position_safely(gw, broker_position_id=ticket, expected_direction="BUY", expected_volume=0.05,
                                 broker_symbol="XAUUSDm", magic=MAGIC, comment="ASN exit",
                                 clock=lambda: float(NOW), conn=conn, reconciliation_chain_key=f"close:{ticket}")


def _timeout(gw, request):
    raise TimeoutError("order_send acknowledgement lost")


def _close_then_timeout(gw, request):
    """The broker DID close the position (a real, timestamped deal), but the
    acknowledgement never arrived."""
    pos = gw._open_positions.pop(str(request.position_ticket))
    gw._historical_deals.append(HistoricalDeal(
        ticket=9001, order=9000, time=NOW, type=1, entry=1, magic=MAGIC, position_id=int(pos.broker_position_id),
        volume=pos.volume, price=1999.0, commission=-0.35, swap=0.0, profit=-5.0, fee=0.0, symbol="XAUUSDm",
        comment="ASN exit", external_id="",
    ))
    raise TimeoutError("order_send acknowledgement lost")


def _shrink(gw, request, *, volume, with_deal):
    key = str(request.position_ticket)
    pos = gw._open_positions[key]
    gw._open_positions[key] = pos.__class__(**{**pos.__dict__, "volume": volume})
    if with_deal:
        gw._historical_deals.append(HistoricalDeal(
            ticket=9101, order=9100, time=NOW, type=1, entry=1, magic=MAGIC, position_id=int(key),
            volume=round(pos.volume - volume, 8), price=1999.0, commission=0.0, swap=0.0, profit=-2.0, fee=0.0,
            symbol="XAUUSDm", comment="", external_id="",
        ))
    raise TimeoutError("lost")


def _row(conn, ticket):
    return conn.execute("SELECT * FROM close_requests WHERE broker_position_id = ? ORDER BY id DESC LIMIT 1",
                        (ticket,)).fetchone()


def _closes_sent(gw):
    return [r for r in gw.order_send_calls if r.position_ticket is not None]


def _position(conn, ticket):
    return conn.execute("SELECT * FROM positions WHERE broker_position_id = ?", (ticket,)).fetchone()


# -- A: send timeout, broker position still open ---------------------------

def test_a_close_timeout_with_position_still_open_resolves_still_open_after_settle():
    conn, gw, ticket = _setup()
    gw.send_action = _timeout

    outcome = _close(gw, ticket, conn)
    assert outcome.status == UNKNOWN
    row = _row(conn, ticket)
    assert row["status"] == UNRESOLVED and row["send_outcome"] == UNKNOWN
    assert row["requested_volume"] == pytest.approx(0.05) and row["position_direction"] == "BUY"
    assert row["magic"] == MAGIC and row["comment"] == "ASN exit" and row["requested_at_utc"] == NOW
    assert row["position_id"] == _position(conn, ticket)["id"]
    assert has_unresolved_close(conn) and has_dangerous_unresolved_unknown(conn)

    # No blind resend while the first close is unproven.
    assert _close(gw, ticket, conn).status == CLOSE_UNRESOLVED
    assert len(_closes_sent(gw)) == 1

    [early] = resolve_unresolved_closes(conn, gw, now_utc=NOW + 2)
    assert early.status == UNRESOLVED and "settle" in early.detail

    [late] = resolve_unresolved_closes(conn, gw, now_utc=NOW + 30)
    assert late.status == RESOLVED_STILL_OPEN
    assert not has_unresolved_close(conn) and not has_dangerous_unresolved_unknown(conn)
    assert _position(conn, ticket)["status"] == "OPEN"


# -- B: send timeout, broker actually closed the position ------------------

def test_b_close_timeout_when_broker_closed_recovers_the_real_close_from_history():
    conn, gw, ticket = _setup()
    gw.send_action = _close_then_timeout

    assert _close(gw, ticket, conn).status == UNKNOWN
    assert _row(conn, ticket)["status"] == UNRESOLVED  # the send itself proved nothing

    [resolution] = resolve_unresolved_closes(conn, gw, now_utc=NOW + 1)
    assert resolution.status == RESOLVED_CLOSED
    assert _position(conn, ticket)["status"] != "OPEN"  # from the real broker deal, never guessed
    assert not has_unresolved_close(conn) and not has_dangerous_unresolved_unknown(conn)
    assert len(_closes_sent(gw)) == 1


# -- C: ambiguous send and broker truth unavailable ------------------------

def test_c_ambiguous_close_with_broker_truth_unavailable_persists_and_never_resends():
    conn, gw, ticket = _setup()

    def ambiguous_then_truth_down(gw, request):
        gw.truth_down = True
        return OrderSendResult(retcode=10012, comment="timeout", broker_order_id=None, broker_deal_id=None,
                               broker_position_id=None, volume_filled=0.0, price_filled=None, raw={})
    gw.send_action = ambiguous_then_truth_down

    outcome = _close(gw, ticket, conn)  # the broker-truth failure is never raised away
    assert outcome.status == UNKNOWN
    assert outcome.broker_truth_error and "Mt5QueryError" in outcome.broker_truth_error
    row = _row(conn, ticket)
    assert row["status"] == UNRESOLVED and row["retcode"] == 10012
    assert "broker truth unavailable" in row["send_detail"]

    for i in range(3):
        with pytest.raises(Mt5QueryError):
            resolve_unresolved_closes(conn, gw, now_utc=NOW + 60 * (i + 1))
    row = _row(conn, ticket)
    assert row["status"] == UNRESOLVED and row["attempt_count"] == 3
    assert "broker truth unavailable" in row["last_attempt_detail"]
    assert row["last_attempt_at_utc"] == NOW + 180
    assert has_dangerous_unresolved_unknown(conn)

    assert _close(gw, ticket, conn).status == CLOSE_UNRESOLVED
    assert len(_closes_sent(gw)) == 1

    gw.truth_down = False  # truth returns and proves the position still open
    [resolution] = resolve_unresolved_closes(conn, gw, now_utc=NOW + 240)
    assert resolution.status == RESOLVED_STILL_OPEN


# -- D: process restart during an unresolved close -------------------------

def test_d_restart_during_an_unresolved_close_stays_fail_closed(tmp_path):
    db = tmp_path / "restart.sqlite3"
    conn, gw, ticket = _setup(db)

    class Crash(BaseException):
        pass

    def crash(gw, request):
        raise Crash("process killed between the write-ahead row and the acknowledgement")
    gw.send_action = crash
    with pytest.raises(Crash):
        _close(gw, ticket, conn)
    conn.close()

    reopened = connect(db)
    row = _row(reopened, ticket)
    assert row["status"] == UNRESOLVED and row["send_outcome"] == "SENDING"
    assert has_unresolved_close(reopened)

    gw.truth_down = True
    with pytest.raises(Mt5QueryError):
        resolve_unresolved_closes(reopened, gw, now_utc=NOW + 60)
    assert has_unresolved_close(reopened) and has_dangerous_unresolved_unknown(reopened)
    gw.send_action = None
    assert _close(gw, ticket, reopened).status == CLOSE_UNRESOLVED
    assert len(_closes_sent(gw)) == 1

    gw.truth_down = False
    [resolution] = resolve_unresolved_closes(reopened, gw, now_utc=NOW + 120)
    assert resolution.status == RESOLVED_STILL_OPEN
    assert not has_dangerous_unresolved_unknown(reopened)


# -- partial close and proven outcomes -------------------------------------

def test_partial_close_is_resolved_only_when_deals_explain_the_volume():
    conn, gw, ticket = _setup()
    gw.send_action = lambda gw, r: _shrink(gw, r, volume=0.03, with_deal=True)
    assert _close(gw, ticket, conn).status == UNKNOWN

    [resolution] = resolve_unresolved_closes(conn, gw, now_utc=NOW + 1)
    assert resolution.status == RESOLVED_PARTIALLY_CLOSED
    pos = _position(conn, ticket)
    assert pos["volume"] == pytest.approx(0.03)
    assert pos["initial_monetary_risk"] == pytest.approx(20.0 * 0.03 / 0.05)


def test_volume_change_without_explaining_deals_stays_unresolved():
    conn, gw, ticket = _setup()
    gw.send_action = lambda gw, r: _shrink(gw, r, volume=0.03, with_deal=False)
    _close(gw, ticket, conn)

    [resolution] = resolve_unresolved_closes(conn, gw, now_utc=NOW + 60)
    assert resolution.status == UNRESOLVED and "conflicting evidence" in resolution.detail
    assert _position(conn, ticket)["volume"] == pytest.approx(0.05)  # never guessed


def _done(gw, request):
    """A broker-confirmed close whose real closing deal is in history."""
    try:
        _close_then_timeout(gw, request)
    except TimeoutError:
        pass
    return OrderSendResult(retcode=10009, comment="done", broker_order_id="9000", broker_deal_id="9001",
                           broker_position_id=None, volume_filled=request.volume, price_filled=1999.0, raw={})


def test_a_done_close_without_history_evidence_stays_unresolved():
    # FakeGateway's own close deal carries time=0, before the position opened:
    # reconciliation cannot recover it, so a DONE retcode alone proves nothing
    # about local state -- the request stays UNRESOLVED (fail closed).
    conn, gw, ticket = _setup()
    assert _close(gw, ticket, conn).status == "FULLY_CLOSED"
    assert _row(conn, ticket)["status"] == UNRESOLVED
    assert has_dangerous_unresolved_unknown(conn)


def test_a_proven_full_close_and_a_definitive_rejection_resolve_immediately():
    conn, gw, ticket = _setup()
    gw.send_action = _done
    assert _close(gw, ticket, conn).status == "FULLY_CLOSED"
    assert _row(conn, ticket)["status"] == RESOLVED_CLOSED
    assert not has_unresolved_close(conn) and not has_dangerous_unresolved_unknown(conn)

    conn2, gw2, ticket2 = _setup()
    gw2.send_action = lambda gw, r: OrderSendResult(
        retcode=10019, comment="no money", broker_order_id=None, broker_deal_id=None, broker_position_id=None,
        volume_filled=0.0, price_filled=None, raw={})
    assert _close(gw2, ticket2, conn2).status == "REJECTED"
    assert _row(conn2, ticket2)["status"] == RESOLVED_NOT_EXECUTED
    assert not has_dangerous_unresolved_unknown(conn2)


def test_only_one_unresolved_close_per_position_is_ever_recorded():
    conn, gw, ticket = _setup()
    kwargs = dict(broker_position_id=ticket, broker_symbol="XAUUSDm", position_direction="BUY",
                  requested_volume=0.05, magic=MAGIC, comment="x")
    record_close_intent(conn, **kwargs, now_utc=NOW)
    with pytest.raises(CloseRequestConflict):
        record_close_intent(conn, **kwargs, now_utc=NOW + 1)
    with pytest.raises(sqlite3.IntegrityError):  # the database itself refuses a second unresolved row
        conn.execute("INSERT INTO close_requests (broker_position_id, broker_symbol, position_direction, "
                     "requested_volume, magic, comment, requested_at_utc, send_outcome, status) "
                     "VALUES (?, 'XAUUSDm', 'BUY', 0.05, 1, 'x', 1, 'SENDING', 'UNRESOLVED')", (ticket,))
