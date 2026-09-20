"""The gateway interface every broker backend (real MT5, fake/test) implements.

`order_send`/`order_check`/`positions_get`/`orders_get` were deliberately
withheld until the order state machine, idempotency, and reconciliation
(directive sections 29-31, `adaptive_scalper/execution/`) existed to
receive their results safely — adding them earlier would have been
exactly the kind of disconnected "showpiece" capability section 118
forbids. That layer now exists (see PROJECT_STATUS.md), so these methods
are added here. Nothing in this codebase calls `order_send` yet outside
tests against `FakeGateway` — see CLAUDE.md/MASTER_BUILD_DIRECTIVE.md for
the PAPER/QA/controlled-DEMO validation gates that must pass first.
"""

from __future__ import annotations

from typing import Protocol

from adaptive_scalper.gateway.types import (
    AccountSnapshot,
    Bar,
    HistoricalDeal,
    HistoricalOrder,
    OrderCheckResult,
    OrderRequest,
    OrderSendResult,
    PendingOrderSnapshot,
    PositionSnapshot,
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

    def positions_get(self) -> list[PositionSnapshot]:
        """Every currently OPEN position on the connected account, across
        all symbols. This is broker-authoritative live state — directive
        section 31's reconciliation source of truth."""
        ...

    def orders_get(self) -> list[PendingOrderSnapshot]:
        """Every currently PENDING (not-yet-filled, still-resting) order
        on the connected account."""
        ...

    def order_check(self, request: OrderRequest) -> OrderCheckResult:
        """Dry-run validation of a request against current broker/account
        state (margin, contract constraints) WITHOUT submitting it."""
        ...

    def order_send(self, request: OrderRequest) -> OrderSendResult:
        """Submit a request. Broker acknowledgement of this call is NOT
        a fill — callers must interpret the result through
        `adaptive_scalper.execution.state_machine`, never assume
        `retcode` success means `FILLED`."""
        ...

    def last_error(self) -> tuple[int, str]: ...
