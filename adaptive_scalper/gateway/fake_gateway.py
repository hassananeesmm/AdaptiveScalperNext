"""In-memory Gateway implementation for deterministic tests.

Implements the same `Gateway` protocol as `Mt5Gateway` so demo-gate and
symbol-resolver logic can be tested without a real MT5 terminal, and so
the exact same test suite structure could later be pointed at the real
gateway for a live (skip-if-unavailable) smoke test.
"""

from __future__ import annotations

from adaptive_scalper.gateway.types import AccountSnapshot, Bar, SymbolSpec, TerminalSnapshot, Tick


class FakeGateway:
    def __init__(
        self,
        account: AccountSnapshot | None = None,
        terminal: TerminalSnapshot | None = None,
        symbols: list[SymbolSpec] | None = None,
        ticks: dict[str, Tick] | None = None,
        bars: dict[str, list[Bar]] | None = None,
    ) -> None:
        self._account = account
        self._terminal = terminal
        self._symbols = symbols or []
        self._ticks = ticks or {}
        self._bars = bars or {}
        self._initialized = False
        self._last_error = (1, "Success")

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

    def last_error(self) -> tuple[int, str]:
        return self._last_error

    # -- test helpers, not part of the Gateway protocol --

    def set_account(self, account: AccountSnapshot | None) -> None:
        self._account = account

    def set_terminal(self, terminal: TerminalSnapshot | None) -> None:
        self._terminal = terminal
