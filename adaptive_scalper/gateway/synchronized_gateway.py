"""Thread-safe serialization boundary over any Gateway implementation.

Per external review: the MetaTrader5 Python module's underlying calls are
synchronous and not documented as safe for concurrent multi-thread use
(BUG_BACKLOG.md's prior entry on this). Multiple independent call sites —
dashboard worker threads today; the entry scanner, position manager,
reconciliation, and history jobs as they're built — must never issue
concurrent raw calls against one MT5 terminal connection.

`SynchronizedGateway` wraps any `Gateway` and serializes every call
through one lock, so callers share a single instance across threads
without each call site needing its own concurrency discipline. It adds
mutual exclusion only — no business logic, no retry, no caching — so it
does not turn the dashboard (or anything else that holds one) into the
trading engine.
"""

from __future__ import annotations

import threading

from adaptive_scalper.gateway.types import (
    AccountSnapshot,
    Bar,
    HistoricalDeal,
    HistoricalOrder,
    SymbolSpec,
    TerminalSnapshot,
    Tick,
)


class SynchronizedGateway:
    """Every method acquires the same `RLock` before delegating to the
    wrapped gateway. RLock (not Lock): defensive against a future Gateway
    implementation whose method internally calls another of its own
    methods, which would deadlock under a plain Lock."""

    def __init__(self, inner) -> None:
        self._inner = inner
        self._lock = threading.RLock()

    def initialize(self) -> bool:
        with self._lock:
            return self._inner.initialize()

    def shutdown(self) -> None:
        with self._lock:
            self._inner.shutdown()

    def account_info(self) -> AccountSnapshot | None:
        with self._lock:
            return self._inner.account_info()

    def terminal_info(self) -> TerminalSnapshot | None:
        with self._lock:
            return self._inner.terminal_info()

    def symbols_get(self) -> list[SymbolSpec]:
        with self._lock:
            return self._inner.symbols_get()

    def symbol_info(self, name: str) -> SymbolSpec | None:
        with self._lock:
            return self._inner.symbol_info(name)

    def symbol_info_tick(self, name: str) -> Tick | None:
        with self._lock:
            return self._inner.symbol_info_tick(name)

    def copy_rates_from_pos(self, name: str, timeframe: int, start_pos: int, count: int) -> list[Bar]:
        with self._lock:
            return self._inner.copy_rates_from_pos(name, timeframe, start_pos, count)

    def copy_rates_range(self, name: str, resolution: str, date_from_utc: int, date_to_utc: int) -> list[Bar]:
        with self._lock:
            return self._inner.copy_rates_range(name, resolution, date_from_utc, date_to_utc)

    def copy_ticks_range(self, name: str, date_from_utc: int, date_to_utc: int) -> list[Tick]:
        with self._lock:
            return self._inner.copy_ticks_range(name, date_from_utc, date_to_utc)

    def history_orders_get(self, date_from_utc: int, date_to_utc: int) -> list[HistoricalOrder]:
        with self._lock:
            return self._inner.history_orders_get(date_from_utc, date_to_utc)

    def history_deals_get(self, date_from_utc: int, date_to_utc: int) -> list[HistoricalDeal]:
        with self._lock:
            return self._inner.history_deals_get(date_from_utc, date_to_utc)

    def last_error(self) -> tuple[int, str]:
        with self._lock:
            return self._inner.last_error()
