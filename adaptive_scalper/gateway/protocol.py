"""The gateway interface every broker backend (real MT5, fake/test) implements.

Order submission (order_send/order_check) is deliberately NOT part of this
protocol yet. It is added only once the order state machine, idempotency,
and reconciliation (directive sections 29-31) exist to receive its result
safely — adding it earlier would be exactly the kind of disconnected
"showpiece" capability section 118 forbids.
"""

from __future__ import annotations

from typing import Protocol

from adaptive_scalper.gateway.types import (
    AccountSnapshot,
    Bar,
    HistoricalDeal,
    HistoricalOrder,
    SymbolSpec,
    TerminalSnapshot,
    Tick,
)


class Gateway(Protocol):
    def initialize(self) -> bool: ...

    def shutdown(self) -> None: ...

    def account_info(self) -> AccountSnapshot | None: ...

    def terminal_info(self) -> TerminalSnapshot | None: ...

    def symbols_get(self) -> list[SymbolSpec]: ...

    def symbol_info(self, name: str) -> SymbolSpec | None: ...

    def symbol_info_tick(self, name: str) -> Tick | None: ...

    def copy_rates_from_pos(self, name: str, timeframe: int, start_pos: int, count: int) -> list[Bar]: ...

    def copy_rates_range(
        self, name: str, resolution: str, date_from_utc: int, date_to_utc: int
    ) -> list[Bar]:
        """Bars for `name` at `resolution` (e.g. "M1"), covering the closed
        interval [date_from_utc, date_to_utc] (epoch seconds, UTC).
        `resolution` is a canonical string, not a raw broker timeframe
        constant, so this protocol stays broker-independent — only
        Mt5Gateway knows how to map it to `MetaTrader5.TIMEFRAME_*`."""
        ...

    def copy_ticks_range(self, name: str, date_from_utc: int, date_to_utc: int) -> list[Tick]:
        """Ticks for `name` covering the closed interval
        [date_from_utc, date_to_utc] (epoch seconds, UTC)."""
        ...

    def history_orders_get(self, date_from_utc: int, date_to_utc: int) -> list[HistoricalOrder]:
        """The connected account's order history over the closed interval
        [date_from_utc, date_to_utc] (epoch seconds, UTC), across all
        symbols — this is account history, not per-symbol market data."""
        ...

    def history_deals_get(self, date_from_utc: int, date_to_utc: int) -> list[HistoricalDeal]:
        """The connected account's deal (fill) history over the closed
        interval [date_from_utc, date_to_utc] (epoch seconds, UTC)."""
        ...

    def last_error(self) -> tuple[int, str]: ...
