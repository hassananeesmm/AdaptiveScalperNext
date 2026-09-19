"""Tests for broker account order/deal history import (directive
sections 51-52): idempotent import, dedup by (login, server, ticket),
never falsely attributing an imported trade to a strategy."""

from __future__ import annotations

import pytest

from adaptive_scalper.gateway.fake_gateway import FakeGateway
from adaptive_scalper.gateway.types import AccountSnapshot, HistoricalDeal, HistoricalOrder, TradeMode
from adaptive_scalper.history import account_history
from adaptive_scalper.persistence import connect, migrate


@pytest.fixture()
def db(tmp_path):
    conn = connect(tmp_path / "test.sqlite3")
    migrate(conn)
    yield conn
    conn.close()


def _account(**overrides) -> AccountSnapshot:
    defaults = dict(
        login=90000001, trade_mode=TradeMode.DEMO, balance=10000.0, equity=10000.0,
        margin_free=10000.0, currency="USD", server="Broker-Demo-Server", company="Test Broker",
        trade_allowed=True, trade_expert=True,
    )
    defaults.update(overrides)
    return AccountSnapshot(**defaults)


def _order(ticket: int, time_setup: int = 1_700_000_000, **overrides) -> HistoricalOrder:
    defaults = dict(
        ticket=ticket, time_setup=time_setup, time_done=time_setup + 1, type=0, state=4,
        magic=0, position_id=ticket, volume_initial=0.1, volume_current=0.0, price_open=2000.0,
        sl=1990.0, tp=2010.0, price_current=2005.0, symbol="XAUUSD", comment="", external_id="",
    )
    defaults.update(overrides)
    return HistoricalOrder(**defaults)


def _deal(ticket: int, time: int = 1_700_000_000, **overrides) -> HistoricalDeal:
    defaults = dict(
        ticket=ticket, order=ticket, time=time, type=0, entry=0, magic=0, position_id=ticket,
        volume=0.1, price=2000.0, commission=-0.5, swap=0.0, profit=5.0, fee=0.0,
        symbol="XAUUSD", comment="", external_id="",
    )
    defaults.update(overrides)
    return HistoricalDeal(**defaults)


def test_import_orders_is_idempotent(db):
    account = _account()
    orders = [_order(1), _order(2)]
    first = account_history.import_orders(db, account, orders)
    second = account_history.import_orders(db, account, orders)
    assert first == 2
    assert second == 0
    count = db.execute("SELECT COUNT(*) FROM broker_account_orders").fetchone()[0]
    assert count == 2


def test_import_deals_is_idempotent(db):
    account = _account()
    deals = [_deal(1), _deal(2)]
    first = account_history.import_deals(db, account, deals)
    second = account_history.import_deals(db, account, deals)
    assert first == 2
    assert second == 0


def test_import_defaults_to_unknown_strategy_attribution(db):
    account = _account()
    account_history.import_orders(db, account, [_order(1)])
    row = db.execute("SELECT strategy_attribution, origin FROM broker_account_orders").fetchone()
    assert row["strategy_attribution"] == "UNKNOWN"
    assert row["origin"] == "BROKER_ACCOUNT_HISTORY"


def test_import_account_history_via_gateway(db):
    account = _account()
    start, end = 1_700_000_000, 1_700_100_000
    gw = FakeGateway(
        historical_orders=[_order(1, time_setup=start + 10)],
        historical_deals=[_deal(1, time=start + 10)],
    )
    inserted_orders, inserted_deals = account_history.import_account_history(db, gw, account, start, end)
    assert (inserted_orders, inserted_deals) == (1, 1)

    # Re-running over an overlapping range imports zero new rows.
    inserted_orders2, inserted_deals2 = account_history.import_account_history(db, gw, account, start, end)
    assert (inserted_orders2, inserted_deals2) == (0, 0)


def test_different_accounts_do_not_collide_on_ticket(db):
    account_a = _account(login=1, server="ServerA")
    account_b = _account(login=2, server="ServerB")
    account_history.import_orders(db, account_a, [_order(1)])
    account_history.import_orders(db, account_b, [_order(1)])  # same ticket, different account
    count = db.execute("SELECT COUNT(*) FROM broker_account_orders").fetchone()[0]
    assert count == 2


def test_coverage_reports_earliest_latest_count(db):
    account = _account()
    account_history.import_orders(db, account, [_order(1, time_setup=1000), _order(2, time_setup=2000)])
    account_history.import_deals(db, account, [_deal(1, time=1500)])
    cov = account_history.coverage(db, account.login, account.server)
    assert cov["orders"] == {"earliest_utc": 1000, "latest_utc": 2000, "count": 2}
    assert cov["deals"] == {"earliest_utc": 1500, "latest_utc": 1500, "count": 1}


def test_coverage_is_zero_for_unknown_account(db):
    cov = account_history.coverage(db, 999, "NoSuchServer")
    assert cov["orders"]["count"] == 0
    assert cov["deals"]["count"] == 0
