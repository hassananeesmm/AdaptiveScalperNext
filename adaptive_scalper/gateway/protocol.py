"""The gateway interface every broker backend (real MT5, fake/test) implements.

Order submission (order_send/order_check) is deliberately NOT part of this
protocol yet. It is added only once the order state machine, idempotency,
and reconciliation (directive sections 29-31) exist to receive its result
safely — adding it earlier would be exactly the kind of disconnected
"showpiece" capability section 118 forbids.
"""

from __future__ import annotations

from typing import Protocol

from adaptive_scalper.gateway.types import AccountSnapshot, Bar, SymbolSpec, TerminalSnapshot, Tick


class Gateway(Protocol):
    def initialize(self) -> bool: ...

    def shutdown(self) -> None: ...

    def account_info(self) -> AccountSnapshot | None: ...

    def terminal_info(self) -> TerminalSnapshot | None: ...

    def symbols_get(self) -> list[SymbolSpec]: ...

    def symbol_info(self, name: str) -> SymbolSpec | None: ...

    def symbol_info_tick(self, name: str) -> Tick | None: ...

    def copy_rates_from_pos(self, name: str, timeframe: int, start_pos: int, count: int) -> list[Bar]: ...

    def last_error(self) -> tuple[int, str]: ...
