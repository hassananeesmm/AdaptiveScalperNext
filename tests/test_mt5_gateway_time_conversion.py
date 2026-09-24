"""`Mt5Gateway` converts every MT5 server-clock time to UTC at the boundary,
and range inputs from UTC to the server clock (BUG_BACKLOG #14). Driven by
a fake MetaTrader5 module shaped like the real SDK's return values."""

from __future__ import annotations

import inspect
from datetime import datetime, timezone
from types import SimpleNamespace

from adaptive_scalper.gateway.factory import create_live_gateway
from adaptive_scalper.gateway.mt5_gateway import Mt5Gateway
from adaptive_scalper.gateway.server_time import RULE_UTC, RULE_UTC2_US_DST

UTC_NOW = int(datetime(2026, 9, 24, 20, 0, tzinfo=timezone.utc).timestamp())
SERVER_NOW = UTC_NOW + 3 * 3600  # measured on the laptop: server = UTC+3 in US DST


class FakeMt5:
    COPY_TICKS_ALL = -1
    TIMEFRAME_M5 = 5

    def __init__(self):
        self.calls = []

    def symbol_info_tick(self, name):
        return SimpleNamespace(time=SERVER_NOW, bid=1.0, ask=1.1, last=0.0, volume=0.0,
                               time_msc=SERVER_NOW * 1000 + 250)

    def _bars(self):
        return [{"time": SERVER_NOW - 300, "open": 1.0, "high": 1.2, "low": 0.9, "close": 1.1,
                 "tick_volume": 10, "spread": 2, "real_volume": 0}]

    def copy_rates_from_pos(self, name, timeframe, start, count):
        return self._bars()

    def copy_rates_range(self, name, timeframe, date_from, date_to):
        self.calls.append(("rates", date_from, date_to))
        return self._bars()

    def copy_ticks_range(self, name, date_from, date_to, flags):
        self.calls.append(("ticks", date_from, date_to))
        return [{"time": SERVER_NOW, "bid": 1.0, "ask": 1.1, "last": 0.0, "volume": 0.0,
                 "time_msc": SERVER_NOW * 1000 + 7}]

    def history_deals_get(self, date_from, date_to):
        self.calls.append(("deals", date_from, date_to))
        return [SimpleNamespace(ticket=1, order=2, time=SERVER_NOW, type=0, entry=0, magic=0, position_id=3,
                                volume=0.01, price=1.0, commission=0.0, swap=0.0, profit=0.0, fee=0.0,
                                symbol="XAUUSD", comment="", external_id="")]

    def history_orders_get(self, date_from, date_to):
        self.calls.append(("orders", date_from, date_to))
        return [SimpleNamespace(ticket=2, time_setup=SERVER_NOW - 60, time_done=SERVER_NOW, type=0, state=4,
                                magic=0, position_id=3, volume_initial=0.01, volume_current=0.0, price_open=1.0,
                                sl=0.0, tp=0.0, price_current=1.0, symbol="XAUUSD", comment="", external_id="")]


def gateway(rule=RULE_UTC2_US_DST):
    gw = Mt5Gateway(rule)
    gw._mt5 = FakeMt5()
    return gw


def test_quotes_are_converted_to_utc():
    tick = gateway().symbol_info_tick("XAUUSD")
    assert tick.time == UTC_NOW and tick.time_msc == UTC_NOW * 1000 + 250


def test_bars_are_converted_to_utc():
    gw = gateway()
    assert gw.copy_rates_from_pos("XAUUSD", 5, 0, 1)[0].time == UTC_NOW - 300
    assert gw.copy_rates_range("XAUUSD", "M5", UTC_NOW - 3600, UTC_NOW)[0].time == UTC_NOW - 300


def test_range_inputs_are_sent_in_server_time():
    gw = gateway()
    gw.copy_rates_range("XAUUSD", "M5", UTC_NOW - 3600, UTC_NOW)
    gw.copy_ticks_range("XAUUSD", UTC_NOW - 60, UTC_NOW)
    gw.history_deals_get(UTC_NOW - 60, UTC_NOW)
    gw.history_orders_get(UTC_NOW - 60, UTC_NOW)
    assert len(gw._mt5.calls) == 4
    for _, date_from, date_to in gw._mt5.calls:
        assert int(date_to.timestamp()) == SERVER_NOW
        assert int(date_from.timestamp()) in (SERVER_NOW - 3600, SERVER_NOW - 60)


def test_ticks_and_broker_history_are_converted_to_utc():
    gw = gateway()
    tick = gw.copy_ticks_range("XAUUSD", UTC_NOW - 60, UTC_NOW)[0]
    assert tick.time == UTC_NOW and tick.time_msc == UTC_NOW * 1000 + 7
    assert gw.history_deals_get(0, UTC_NOW)[0].time == UTC_NOW
    order = gw.history_orders_get(0, UTC_NOW)[0]
    assert (order.time_setup, order.time_done) == (UTC_NOW - 60, UTC_NOW)


def test_rule_utc_passes_times_through():
    assert gateway(RULE_UTC).symbol_info_tick("XAUUSD").time == SERVER_NOW


def test_the_factory_requires_an_explicit_rule():
    param = inspect.signature(create_live_gateway).parameters["server_time_rule"]
    assert param.default is inspect.Parameter.empty
