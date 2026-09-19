"""In-memory Gateway implementation for deterministic tests.

Implements the same `Gateway` protocol as `Mt5Gateway` so demo-gate and
symbol-resolver logic can be tested without a real MT5 terminal, and so
the exact same test suite structure could later be pointed at the real
gateway for a live (skip-if-unavailable) smoke test.
"""

from __future__ import annotations

from adaptive_scalper.gateway.types import (
    AccountSnapshot,
    Bar,
    HistoricalDeal,
    HistoricalOrder,
    SymbolSpec,
    TerminalSnapshot,
    Tick,
)


class FakeGateway:
    def __init__(
        self,
        account: AccountSnapshot | None = None,
        terminal: TerminalSnapshot | None = None,
        symbols: list[SymbolSpec] | None = None,
        ticks: dict[str, Tick] | None = None,
        bars: dict[str, list[Bar]] | None = None,
        bars_by_resolution: dict[str, dict[str, list[Bar]]] | None = None,
        tick_history: dict[str, list[Tick]] | None = None,
        historical_orders: list[HistoricalOrder] | None = None,
        historical_deals: list[HistoricalDeal] | None = None,
    ) -> None:
        self._account = account
        self._terminal = terminal
        self._symbols = symbols or []
        self._ticks = ticks or {}
        self._bars = bars or {}
        # name -> resolution -> bars sorted by .time, for copy_rates_range.
        self._bars_by_resolution = bars_by_resolution or {}
        # name -> ticks sorted by .time_msc, for copy_ticks_range.
        self._tick_history = tick_history or {}
        self._historical_orders = historical_orders or []
        self._historical_deals = historical_deals or []
        self._initialized = False
        self._last_error = (1, "Success")
        # Test-observable record of every copy_rates_range/copy_ticks_range
        # call, in order, so resumability tests can assert exactly which
        # ranges were (re-)requested rather than only the final DB state.
        self.rates_range_calls: list[tuple[str, str, int, int]] = []
        self.ticks_range_calls: list[tuple[str, int, int]] = []

    def initialize(self) -> bool:
        self._initialized = True
        return True

    def shutdown(self) -> None:
        self._initialized = False

    def account_info(self) -> AccountSnapshot | None:
        return self._account

    def terminal_info(self) -> TerminalSnapshot | None:
        return self._terminal

    def symbols_get(self) -> list[SymbolSpec]:
        return list(self._symbols)

    def symbol_info(self, name: str) -> SymbolSpec | None:
        for s in self._symbols:
            if s.name == name:
                return s
        return None

    def symbol_info_tick(self, name: str) -> Tick | None:
        return self._ticks.get(name)

    def copy_rates_from_pos(self, name: str, timeframe: int, start_pos: int, count: int) -> list[Bar]:
        bars = self._bars.get(name, [])
        return bars[start_pos:start_pos + count]

    def copy_rates_range(
        self, name: str, resolution: str, date_from_utc: int, date_to_utc: int
    ) -> list[Bar]:
        self.rates_range_calls.append((name, resolution, date_from_utc, date_to_utc))
        bars = self._bars_by_resolution.get(name, {}).get(resolution, [])
        return [b for b in bars if date_from_utc <= b.time <= date_to_utc]

    def copy_ticks_range(self, name: str, date_from_utc: int, date_to_utc: int) -> list[Tick]:
        self.ticks_range_calls.append((name, date_from_utc, date_to_utc))
        ticks = self._tick_history.get(name, [])
        return [t for t in ticks if date_from_utc <= t.time <= date_to_utc]

    def history_orders_get(self, date_from_utc: int, date_to_utc: int) -> list[HistoricalOrder]:
        return [o for o in self._historical_orders if date_from_utc <= o.time_setup <= date_to_utc]

    def history_deals_get(self, date_from_utc: int, date_to_utc: int) -> list[HistoricalDeal]:
        return [d for d in self._historical_deals if date_from_utc <= d.time <= date_to_utc]

    def last_error(self) -> tuple[int, str]:
        return self._last_error

    # -- test helpers, not part of the Gateway protocol --

    def set_account(self, account: AccountSnapshot | None) -> None:
        self._account = account

    def set_terminal(self, terminal: TerminalSnapshot | None) -> None:
        self._terminal = terminal
