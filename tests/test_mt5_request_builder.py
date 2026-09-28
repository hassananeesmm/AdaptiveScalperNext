"""Tests for `Mt5Gateway`'s internal MT5 request-building/result-parsing
helpers — the ONLY place in the codebase that constructs MetaTrader5's
raw request/response shapes. Tested against a fake stand-in module
object exposing the same named constants the real `MetaTrader5` package
does, so these run without the real SDK installed.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from adaptive_scalper.gateway.mt5_gateway import Mt5Gateway, Mt5QueryError, _build_mt5_request, _order_send_result
from adaptive_scalper.gateway.types import OrderAction, OrderRequest


def _fake_mt5_module() -> SimpleNamespace:
    return SimpleNamespace(
        TRADE_ACTION_DEAL=1, TRADE_ACTION_SLTP=2, TRADE_ACTION_REMOVE=3,
        ORDER_TYPE_BUY=10, ORDER_TYPE_SELL=11,
        ORDER_TIME_GTC=20, ORDER_FILLING_IOC=30, ORDER_FILLING_FOK=31,
    )


def test_deal_request_maps_buy_direction():
    req = OrderRequest(action=OrderAction.DEAL, symbol="XAUUSDm", direction="BUY", volume=0.05, price=2000.0)
    mt5_request = _build_mt5_request(_fake_mt5_module(), req)
    assert mt5_request["action"] == 1
    assert mt5_request["type"] == 10
    assert mt5_request["symbol"] == "XAUUSDm"
    assert mt5_request["volume"] == 0.05
    assert mt5_request["price"] == 2000.0


def test_deal_request_maps_sell_direction():
    req = OrderRequest(action=OrderAction.DEAL, symbol="XAUUSDm", direction="SELL", volume=0.05)
    mt5_request = _build_mt5_request(_fake_mt5_module(), req)
    assert mt5_request["type"] == 11


def test_deal_request_omits_price_when_none():
    req = OrderRequest(action=OrderAction.DEAL, symbol="XAUUSDm", direction="BUY", volume=0.05, price=None)
    mt5_request = _build_mt5_request(_fake_mt5_module(), req)
    assert "price" not in mt5_request


def test_deal_request_includes_sl_tp_when_present():
    req = OrderRequest(
        action=OrderAction.DEAL, symbol="XAUUSDm", direction="BUY", volume=0.05,
        stop_loss=1990.0, take_profit=2010.0,
    )
    mt5_request = _build_mt5_request(_fake_mt5_module(), req)
    assert mt5_request["sl"] == 1990.0
    assert mt5_request["tp"] == 2010.0


def test_deal_request_omits_sl_tp_when_none():
    req = OrderRequest(action=OrderAction.DEAL, symbol="XAUUSDm", direction="BUY", volume=0.05)
    mt5_request = _build_mt5_request(_fake_mt5_module(), req)
    assert "sl" not in mt5_request
    assert "tp" not in mt5_request


def test_deal_request_rejects_invalid_direction():
    req = OrderRequest(action=OrderAction.DEAL, symbol="XAUUSDm", direction="HOLD", volume=0.05)
    with pytest.raises(ValueError):
        _build_mt5_request(_fake_mt5_module(), req)


def test_deal_request_carries_deviation_and_magic():
    req = OrderRequest(
        action=OrderAction.DEAL, symbol="XAUUSDm", direction="BUY", volume=0.05,
        deviation_points=15, magic=42, comment="req-abc",
    )
    mt5_request = _build_mt5_request(_fake_mt5_module(), req)
    assert mt5_request["deviation"] == 15
    assert mt5_request["magic"] == 42
    assert mt5_request["comment"] == "req-abc"


def test_sltp_request_requires_position_ticket():
    req = OrderRequest(action=OrderAction.SLTP, symbol="XAUUSDm", direction="BUY", volume=0.0, position_ticket=None)
    with pytest.raises(ValueError):
        _build_mt5_request(_fake_mt5_module(), req)


def test_sltp_request_maps_position_and_sl_tp():
    req = OrderRequest(
        action=OrderAction.SLTP, symbol="XAUUSDm", direction="BUY", volume=0.0,
        position_ticket=12345, stop_loss=1995.0, take_profit=2015.0,
    )
    mt5_request = _build_mt5_request(_fake_mt5_module(), req)
    assert mt5_request["action"] == 2
    assert mt5_request["position"] == 12345
    assert mt5_request["sl"] == 1995.0
    assert mt5_request["tp"] == 2015.0
    assert "type" not in mt5_request  # SLTP doesn't set a direction/type


def test_remove_request_requires_order_ticket():
    req = OrderRequest(action=OrderAction.REMOVE, symbol="XAUUSDm", direction="BUY", volume=0.0, order_ticket=None)
    with pytest.raises(ValueError):
        _build_mt5_request(_fake_mt5_module(), req)


def test_remove_request_maps_order_ticket():
    req = OrderRequest(action=OrderAction.REMOVE, symbol="XAUUSDm", direction="BUY", volume=0.0, order_ticket=999)
    mt5_request = _build_mt5_request(_fake_mt5_module(), req)
    assert mt5_request["action"] == 3
    assert mt5_request["order"] == 999


# --------------------------------------------------------------------------
# DEAL + position_ticket = a CLOSE (execution-safety review finding #2)
# --------------------------------------------------------------------------

def test_deal_request_with_position_ticket_sets_position_field():
    req = OrderRequest(
        action=OrderAction.DEAL, symbol="XAUUSDm", direction="SELL", volume=0.05, position_ticket=54321,
    )
    mt5_request = _build_mt5_request(_fake_mt5_module(), req)
    assert mt5_request["position"] == 54321


def test_deal_request_without_position_ticket_omits_position_field():
    req = OrderRequest(action=OrderAction.DEAL, symbol="XAUUSDm", direction="BUY", volume=0.05)
    mt5_request = _build_mt5_request(_fake_mt5_module(), req)
    assert "position" not in mt5_request


# --------------------------------------------------------------------------
# filling type resolution (execution-safety review finding #7)
# --------------------------------------------------------------------------

def test_deal_request_defaults_to_ioc_when_filling_type_unset():
    req = OrderRequest(action=OrderAction.DEAL, symbol="XAUUSDm", direction="BUY", volume=0.05)
    mt5_request = _build_mt5_request(_fake_mt5_module(), req)
    assert mt5_request["type_filling"] == 30  # ORDER_FILLING_IOC


def test_deal_request_honors_explicit_fok_filling_type():
    req = OrderRequest(action=OrderAction.DEAL, symbol="XAUUSDm", direction="BUY", volume=0.05, filling_type="FOK")
    mt5_request = _build_mt5_request(_fake_mt5_module(), req)
    assert mt5_request["type_filling"] == 31  # ORDER_FILLING_FOK


# --------------------------------------------------------------------------
# _order_send_result
# --------------------------------------------------------------------------

def _raw_result(**overrides):
    defaults = dict(retcode=10009, comment="Request executed", deal=555, order=777, volume=0.05, price=2001.5)
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def test_order_send_result_parses_success():
    result = _order_send_result(_raw_result())
    assert result.retcode == 10009
    assert result.broker_deal_id == "555"
    assert result.broker_order_id == "777"
    assert result.volume_filled == 0.05
    assert result.price_filled == 2001.5


def test_order_send_result_handles_none_from_broker():
    result = _order_send_result(None, retcode_on_none=(-1, "connection lost"))
    assert result.retcode == -1
    assert result.comment == "connection lost"
    assert result.broker_order_id is None
    assert result.broker_deal_id is None


def test_order_send_result_zero_deal_or_order_becomes_none():
    result = _order_send_result(_raw_result(deal=0, order=0))
    assert result.broker_deal_id is None
    assert result.broker_order_id is None


def test_order_send_result_preserves_raw_fields():
    result = _order_send_result(_raw_result())
    assert result.raw["retcode"] == 10009
    assert result.raw["comment"] == "Request executed"


def test_order_send_result_never_invents_a_position_id():
    """Execution-safety review finding #1 regression: MqlTradeResult
    carries no position ticket, and this must NEVER be filled in from
    `raw.order` (or anything else) — it must always be None here."""
    result = _order_send_result(_raw_result(order=777, deal=555))
    assert result.broker_position_id is None
    assert result.broker_order_id == "777"
    assert result.broker_deal_id == "555"


# --------------------------------------------------------------------------
# Broker-truth query failures must never masquerade as "empty"
# --------------------------------------------------------------------------

class _QueryStub:
    TIMEFRAME_M5 = 5
    COPY_TICKS_ALL = 0

    def __init__(self, result):
        self.result = result

    def last_error(self):
        return (-10004, "No IPC connection")

    def symbols_get(self):
        return self.result

    def copy_rates_from_pos(self, *args):
        return self.result

    def copy_rates_range(self, *args):
        return self.result

    def copy_ticks_range(self, *args):
        return self.result

    def history_orders_get(self, *args):
        return self.result

    def history_deals_get(self, *args):
        return self.result

    def positions_get(self):
        return self.result

    def orders_get(self):
        return self.result


def _gateway_with_query_result(result):
    gateway = Mt5Gateway()
    gateway._mt5 = _QueryStub(result)
    return gateway


@pytest.mark.parametrize(
    ("operation", "call"),
    [
        ("symbols_get", lambda gw: gw.symbols_get()),
        ("copy_rates_from_pos", lambda gw: gw.copy_rates_from_pos("XAUUSD", 5, 0, 10)),
        ("copy_rates_range", lambda gw: gw.copy_rates_range("XAUUSD", "M5", 1000, 2000)),
        ("copy_ticks_range", lambda gw: gw.copy_ticks_range("XAUUSD", 1000, 2000)),
        ("history_orders_get", lambda gw: gw.history_orders_get(1000, 2000)),
        ("history_deals_get", lambda gw: gw.history_deals_get(1000, 2000)),
        ("positions_get", lambda gw: gw.positions_get()),
        ("orders_get", lambda gw: gw.orders_get()),
    ],
)
def test_collection_query_none_raises_fail_closed(operation, call):
    gateway = _gateway_with_query_result(None)
    with pytest.raises(Mt5QueryError, match=operation):
        call(gateway)


@pytest.mark.parametrize(
    "call",
    [
        lambda gw: gw.symbols_get(),
        lambda gw: gw.copy_rates_from_pos("XAUUSD", 5, 0, 10),
        lambda gw: gw.copy_rates_range("XAUUSD", "M5", 1000, 2000),
        lambda gw: gw.copy_ticks_range("XAUUSD", 1000, 2000),
        lambda gw: gw.history_orders_get(1000, 2000),
        lambda gw: gw.history_deals_get(1000, 2000),
        lambda gw: gw.positions_get(),
        lambda gw: gw.orders_get(),
    ],
)
def test_collection_query_real_empty_result_remains_empty(call):
    gateway = _gateway_with_query_result(())
    assert call(gateway) == []
