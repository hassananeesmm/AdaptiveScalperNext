"""Broker-independent types.

Per MASTER_BUILD_DIRECTIVE.md section 110: business logic must consume
these internal types, never the raw `MetaTrader5` module's named tuples
directly, so the rest of the system stays testable against
FakeGateway/mocks and isolated from a specific broker API's shape.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum


class TradeMode(IntEnum):
    """Mirrors MT5's ENUM_ACCOUNT_TRADE_MODE. DEMO is the only value this
    project may ever act on (CLAUDE.md rule 3/4)."""

    DEMO = 0
    CONTEST = 1
    REAL = 2


class SymbolTradeMode(IntEnum):
    """Mirrors MT5's ENUM_SYMBOL_TRADE_MODE (per-symbol trading
    permission — distinct from the account-level TradeMode above).

    Per external architecture review: this must be preserved as its full
    5-state enum, not flattened into one boolean. A CLOSE_ONLY symbol, for
    example, may allow reducing an existing position but must never allow
    new exposure — a bool can't express that distinction, so callers that
    only check "is trading allowed" would incorrectly treat CLOSE_ONLY the
    same as FULL.
    """

    DISABLED = 0    # no trading at all
    LONGONLY = 1    # only BUY orders/positions
    SHORTONLY = 2   # only SELL orders/positions
    CLOSEONLY = 3   # only closing existing positions; no new exposure
    FULL = 4        # unrestricted

    @property
    def allows_new_long(self) -> bool:
        return self in (SymbolTradeMode.LONGONLY, SymbolTradeMode.FULL)

    @property
    def allows_new_short(self) -> bool:
        return self in (SymbolTradeMode.SHORTONLY, SymbolTradeMode.FULL)

    @property
    def allows_any_new_exposure(self) -> bool:
        return self.allows_new_long or self.allows_new_short

    @property
    def allows_close(self) -> bool:
        # Every non-DISABLED mode allows closing existing exposure —
        # risk reduction is never blocked by a directional restriction.
        return self != SymbolTradeMode.DISABLED


@dataclass(frozen=True)
class AccountSnapshot:
    login: int
    trade_mode: TradeMode
    balance: float
    equity: float
    margin_free: float
    currency: str
    server: str
    company: str
    trade_allowed: bool   # account-level "Algo trading" permission (AccountInfo.trade_allowed)
    trade_expert: bool    # account-level "Expert trading" permission (AccountInfo.trade_expert)


@dataclass(frozen=True)
class TerminalSnapshot:
    connected: bool
    trade_allowed: bool   # terminal-level "Algo Trading" button state
    build: int
    name: str
    company: str
    path: str


@dataclass(frozen=True)
class SymbolSpec:
    name: str
    description: str
    currency_base: str
    currency_profit: str
    currency_margin: str
    digits: int
    point: float
    trade_contract_size: float
    volume_min: float
    volume_max: float
    volume_step: float
    trade_tick_size: float
    trade_tick_value: float
    spread: int
    visible: bool
    trade_mode: SymbolTradeMode


@dataclass(frozen=True)
class Tick:
    time: int  # epoch seconds, broker/server time
    bid: float
    ask: float
    last: float
    volume: float


@dataclass(frozen=True)
class Bar:
    time: int  # epoch seconds, bar open time, broker/server time
    open: float
    high: float
    low: float
    close: float
    tick_volume: int
    spread: int
    real_volume: int
