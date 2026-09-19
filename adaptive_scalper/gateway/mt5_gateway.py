"""Real MetaTrader5-backed Gateway implementation.

This is the ONLY module in the project allowed to `import MetaTrader5`
(directive section 110) — everything else consumes the broker-independent
types in `adaptive_scalper.gateway.types` via the `Gateway` protocol.

The import is deferred into `__init__`/methods rather than done at module
level so the rest of the codebase (and its tests) can be imported and run
on a machine without the MetaTrader5 package installed (non-Windows dev,
CI) without ever touching this class.
"""

from __future__ import annotations

from adaptive_scalper.gateway.types import (
    AccountSnapshot,
    Bar,
    SymbolSpec,
    TerminalSnapshot,
    Tick,
    TradeMode,
)


class Mt5NotAvailableError(RuntimeError):
    """Raised when the MetaTrader5 package cannot be imported."""


def _import_mt5():
    try:
        import MetaTrader5 as mt5  # type: ignore
    except ImportError as exc:
        raise Mt5NotAvailableError(
            "the MetaTrader5 package is not installed/importable; "
            "Mt5Gateway requires it (Windows + MetaTrader5 pip package)"
        ) from exc
    return mt5


def _account_snapshot(raw) -> AccountSnapshot:
    return AccountSnapshot(
        login=raw.login,
        trade_mode=TradeMode(raw.trade_mode),
        balance=raw.balance,
        equity=raw.equity,
        margin_free=raw.margin_free,
        currency=raw.currency,
        server=raw.server,
        company=raw.company,
        trade_allowed=bool(raw.trade_allowed),
        trade_expert=bool(raw.trade_expert),
    )


def _terminal_snapshot(raw) -> TerminalSnapshot:
    return TerminalSnapshot(
        connected=bool(raw.connected),
        trade_allowed=bool(raw.trade_allowed),
        build=raw.build,
        name=raw.name,
        company=raw.company,
        path=raw.path,
    )


def _symbol_spec(raw) -> SymbolSpec:
    return SymbolSpec(
        name=raw.name,
        description=raw.description,
        currency_base=raw.currency_base,
        currency_profit=raw.currency_profit,
        currency_margin=raw.currency_margin,
        digits=raw.digits,
        point=raw.point,
        trade_contract_size=raw.trade_contract_size,
        volume_min=raw.volume_min,
        volume_max=raw.volume_max,
        volume_step=raw.volume_step,
        trade_tick_size=raw.trade_tick_size,
        trade_tick_value=raw.trade_tick_value,
        spread=raw.spread,
        visible=bool(raw.visible),
        trade_allowed=raw.trade_mode != 0,  # SYMBOL_TRADE_MODE_DISABLED == 0
    )


def _tick(raw) -> Tick:
    return Tick(time=raw.time, bid=raw.bid, ask=raw.ask, last=raw.last, volume=raw.volume)


def _bar(raw) -> Bar:
    # copy_rates_from_pos returns a numpy structured array; each row is a
    # numpy.void accessed by field name via __getitem__, not attribute access.
    return Bar(
        time=int(raw["time"]),
        open=float(raw["open"]),
        high=float(raw["high"]),
        low=float(raw["low"]),
        close=float(raw["close"]),
        tick_volume=int(raw["tick_volume"]),
        spread=int(raw["spread"]),
        real_volume=int(raw["real_volume"]),
    )


class Mt5Gateway:
    """Thin translation layer over the `MetaTrader5` package. No business
    logic lives here — only calling the SDK and converting its output to
    our internal types."""

    def __init__(self) -> None:
        self._mt5 = None

    def initialize(self) -> bool:
        self._mt5 = _import_mt5()
        return bool(self._mt5.initialize())

    def shutdown(self) -> None:
        if self._mt5 is not None:
            self._mt5.shutdown()

    def account_info(self) -> AccountSnapshot | None:
        raw = self._mt5.account_info()
        return _account_snapshot(raw) if raw is not None else None

    def terminal_info(self) -> TerminalSnapshot | None:
        raw = self._mt5.terminal_info()
        return _terminal_snapshot(raw) if raw is not None else None

    def symbols_get(self) -> list[SymbolSpec]:
        raw = self._mt5.symbols_get()
        return [_symbol_spec(s) for s in raw] if raw is not None else []

    def symbol_info(self, name: str) -> SymbolSpec | None:
        raw = self._mt5.symbol_info(name)
        return _symbol_spec(raw) if raw is not None else None

    def symbol_info_tick(self, name: str) -> Tick | None:
        raw = self._mt5.symbol_info_tick(name)
        return _tick(raw) if raw is not None else None

    def copy_rates_from_pos(self, name: str, timeframe: int, start_pos: int, count: int) -> list[Bar]:
        raw = self._mt5.copy_rates_from_pos(name, timeframe, start_pos, count)
        if raw is None:
            return []
        return [_bar(row) for row in raw]

    def last_error(self) -> tuple[int, str]:
        return tuple(self._mt5.last_error())
