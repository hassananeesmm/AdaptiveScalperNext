"""Deterministic broker fault injection for chaos tests.

`ChaosGateway` is a `FakeGateway` whose every Gateway method goes through
a per-method, per-call-number FAULT PLAN:

    gw = ChaosGateway(...)
    gw.on("order_send", default_then_raise(TimeoutError("ack lost")))   # every call
    gw.on("account_info", returns(REAL_ACCOUNT), call=3)                # 3rd call only

No randomness, no threads, no sleeping: the same plan always produces the
same sequence of broker behaviour. `SimulatedCrash` derives from
BaseException so it passes through `except Exception` handlers exactly like
a real process death would, and a test then "restarts" by running startup
recovery against the same database.
"""

from __future__ import annotations

import dataclasses
from collections import Counter, defaultdict
from typing import Callable

from adaptive_scalper.gateway.fake_gateway import FakeGateway
from adaptive_scalper.gateway.types import (
    AccountSnapshot,
    SymbolSpec,
    SymbolTradeMode,
    TerminalSnapshot,
    Tick,
    TradeMode,
)

NOW = 5000
BROKER_SYMBOL = "XAUUSDm"


class SimulatedCrash(BaseException):
    """The process died here. Not an Exception: nothing may swallow it."""


Action = Callable[..., object]


def raise_(exc: BaseException) -> Action:
    def action(gw, default, *args):
        raise exc
    return action


def returns(value) -> Action:
    def action(gw, default, *args):
        return value
    return action


def mutate_then_default(fn: Callable[["ChaosGateway"], None]) -> Action:
    def action(gw, default, *args):
        fn(gw)
        return default(*args)
    return action


def default_then_raise(exc: BaseException) -> Action:
    """The broker performed the operation; the acknowledgement never came back."""
    def action(gw, default, *args):
        default(*args)
        raise exc
    return action


def default_then_return(value) -> Action:
    def action(gw, default, *args):
        default(*args)
        return value
    return action


class ChaosGateway(FakeGateway):
    def __init__(self, *args, clock: Callable[[], int] = lambda: NOW, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.clock = clock
        self.calls: Counter = Counter()
        self._plan: dict[str, list[tuple[int | None, Action]]] = defaultdict(list)

    def on(self, method: str, action: Action, *, call: int | None = None) -> "ChaosGateway":
        self._plan[method].append((call, action))
        return self

    def _dispatch(self, method: str, default, *args):
        self.calls[method] += 1
        n = self.calls[method]
        for call, action in self._plan.get(method, ()):
            if call is None or call == n:
                return action(self, default, *args)
        return default(*args)

    # -- Gateway protocol, every method routed through the plan --
    def account_info(self):
        return self._dispatch("account_info", super().account_info)

    def terminal_info(self):
        return self._dispatch("terminal_info", super().terminal_info)

    def symbol_info(self, name):
        return self._dispatch("symbol_info", super().symbol_info, name)

    def symbol_info_tick(self, name):
        return self._dispatch("symbol_info_tick", super().symbol_info_tick, name)

    def positions_get(self):
        return self._dispatch("positions_get", super().positions_get)

    def orders_get(self):
        return self._dispatch("orders_get", super().orders_get)

    def history_orders_get(self, date_from_utc, date_to_utc):
        return self._dispatch("history_orders_get", super().history_orders_get, date_from_utc, date_to_utc)

    def history_deals_get(self, date_from_utc, date_to_utc):
        return self._dispatch("history_deals_get", super().history_deals_get, date_from_utc, date_to_utc)

    def order_check(self, request):
        return self._dispatch("order_check", super().order_check, request)

    def order_send(self, request):
        return self._dispatch("order_send", self._timestamped_send, request)

    def _timestamped_send(self, request):
        before = len(self._historical_deals)
        result = FakeGateway.order_send(self, request)
        now = self.clock()
        for i in range(before, len(self._historical_deals)):
            self._historical_deals[i] = dataclasses.replace(self._historical_deals[i], time=now)
        return result

    # -- test helpers --
    def set_symbol(self, spec: SymbolSpec) -> None:
        self._symbols = [spec if s.name == spec.name else s for s in self._symbols]

    def set_tick(self, name: str, tick: Tick) -> None:
        self._ticks[name] = tick


def demo_account(**overrides) -> AccountSnapshot:
    defaults = dict(
        login=123, trade_mode=TradeMode.DEMO, balance=10000.0, equity=10000.0, margin_free=10000.0,
        currency="USD", server="Broker-Demo", company="Broker", trade_allowed=True, trade_expert=True,
    )
    defaults.update(overrides)
    return AccountSnapshot(**defaults)


def demo_terminal(**overrides) -> TerminalSnapshot:
    defaults = dict(connected=True, trade_allowed=True, build=1000, name="MT5", company="MetaQuotes", path="")
    defaults.update(overrides)
    return TerminalSnapshot(**defaults)


def gold_spec(**overrides) -> SymbolSpec:
    defaults = dict(
        name=BROKER_SYMBOL, description="Gold", currency_base="XAU", currency_profit="USD", currency_margin="USD",
        digits=2, point=0.01, trade_contract_size=100.0, volume_min=0.01, volume_max=50.0, volume_step=0.01,
        trade_tick_size=0.01, trade_tick_value=1.0, spread=20, visible=True,
        trade_mode=SymbolTradeMode.FULL, filling_mode=3,
    )
    defaults.update(overrides)
    return SymbolSpec(**defaults)


def fresh_tick(**overrides) -> Tick:
    defaults = dict(time=NOW, bid=1999.0, ask=2001.0, last=2000.0, volume=1.0)
    defaults.update(overrides)
    return Tick(**defaults)


def chaos_gateway(**overrides) -> ChaosGateway:
    defaults = dict(
        account=demo_account(), terminal=demo_terminal(), symbols=[gold_spec()],
        ticks={BROKER_SYMBOL: fresh_tick()},
    )
    defaults.update(overrides)
    return ChaosGateway(**defaults)
