"""Broker-independent types.

Per MASTER_BUILD_DIRECTIVE.md section 110: business logic must consume
these internal types, never the raw `MetaTrader5` module's named tuples
directly, so the rest of the system stays testable against
FakeGateway/mocks and isolated from a specific broker API's shape.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, IntEnum


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
    # Raw MT5 ENUM_SYMBOL_TRADING_MODE_FILLING bitmask (SYMBOL_FILLING_FOK=1,
    # SYMBOL_FILLING_IOC=2), deliberately undecoded here (see
    # HistoricalOrder's docstring for why this layer doesn't interpret raw
    # broker codes). Default of 3 (both bits set) is a permissive stand-in
    # for tests that don't care about this dimension; Mt5Gateway always
    # sets the broker's true value. See gateway/broker_constraints.py.
    filling_mode: int = 3
    # MT5's trade_stops_level/trade_freeze_level (both in POINTS, not
    # price) -- the broker's minimum distance a SL/TP may sit from the
    # current price, and the distance inside which an existing order/
    # position may not be modified at all. Defaults of 0 are a permissive
    # stand-in for tests that don't care about this dimension; Mt5Gateway
    # always sets the broker's true value. Used by
    # execution/stop_modification.py to refuse a stop that would be
    # rejected by the broker rather than discovering that only via a
    # failed order_check/order_send.
    trade_stops_level: int = 0
    trade_freeze_level: int = 0


@dataclass(frozen=True)
class Tick:
    time: int  # epoch seconds, UTC (Mt5Gateway converts the broker server clock)
    bid: float
    ask: float
    last: float
    volume: float
    # Millisecond-resolution timestamp (MT5's `time_msc`). Defaults to 0 for
    # call sites that only ever need one "current" tick (symbol_info_tick),
    # where second-resolution uniqueness is irrelevant. Historical tick
    # storage (adaptive_scalper.history) relies on this for row uniqueness,
    # since two genuinely distinct ticks commonly share the same second.
    time_msc: int = 0


@dataclass(frozen=True)
class Bar:
    time: int  # epoch seconds, bar open time, UTC (converted from the server clock)
    open: float
    high: float
    low: float
    close: float
    tick_volume: int
    spread: int
    real_volume: int


@dataclass(frozen=True)
class HistoricalOrder:
    """One row of the connected account's broker order history
    (MetaTrader5.history_orders_get). `type`/`state` are the raw MT5
    ENUM_ORDER_TYPE / ENUM_ORDER_STATE integer codes, deliberately not
    decoded here — this is a thin import layer (directive section 51-52),
    not a research/analysis layer; whoever eventually consumes this data
    for cost/slippage research owns interpreting those codes.
    """

    ticket: int
    time_setup: int   # epoch seconds
    time_done: int | None
    type: int
    state: int
    magic: int
    position_id: int
    volume_initial: float
    volume_current: float
    price_open: float
    sl: float
    tp: float
    price_current: float
    symbol: str
    comment: str
    external_id: str


@dataclass(frozen=True)
class HistoricalDeal:
    """One row of the connected account's broker deal history
    (MetaTrader5.history_deals_get). `type`/`entry` are raw MT5
    ENUM_DEAL_TYPE / ENUM_DEAL_ENTRY integer codes — see HistoricalOrder's
    docstring for why these aren't decoded at this layer."""

    ticket: int
    order: int
    time: int   # epoch seconds
    type: int
    entry: int
    magic: int
    position_id: int
    volume: float
    price: float
    commission: float
    swap: float
    profit: float
    fee: float
    symbol: str
    comment: str
    external_id: str
    # MT5 ENUM_DEAL_REASON (CLIENT/MOBILE/WEB/EXPERT/SL/TP/SO/...), raw code.
    # None when the source did not provide it -- never guessed.
    reason: int | None = None


class OrderAction(str, Enum):
    """Restricted to the subset of MT5's ENUM_TRADE_REQUEST_ACTIONS this
    project ever issues. DEAL = immediate market execution. SLTP =
    modify an existing position's stop loss/take profit (never widening
    protection backward — directive section 23 — that rule is enforced
    by the caller, not this type). REMOVE = cancel a still-pending
    order. CLOSE_BY / PENDING (placing a new pending order) are not
    supported — this project only ever trades at market."""

    DEAL = "DEAL"
    SLTP = "SLTP"
    REMOVE = "REMOVE"


@dataclass(frozen=True)
class OrderRequest:
    """Internal, broker-independent order request. `Mt5Gateway` alone
    translates this into MetaTrader5's raw request dict — nothing
    outside `mt5_gateway.py` ever constructs MT5's own request shape."""

    action: OrderAction
    symbol: str                          # broker symbol name
    direction: str                       # "BUY"/"SELL" — required for DEAL, ignored for SLTP/REMOVE
    volume: float
    price: float | None = None           # None lets the broker fill a DEAL at current market price
    stop_loss: float | None = None
    take_profit: float | None = None
    deviation_points: int = 20           # max acceptable slippage, in points, for a DEAL
    magic: int = 0
    comment: str = ""                    # should embed the client_request_id for idempotency-by-comment
    position_ticket: int | None = None   # SLTP: which position to modify. DEAL: when set, this is a CLOSE
                                          # of that exact position (never an ordinary new-entry DEAL) —
                                          # see execution/close.py (execution-safety review finding #2).
    order_ticket: int | None = None      # required for REMOVE (which pending order to cancel)
    # Broker-supported fill policy for a DEAL, resolved by the caller from
    # the symbol's advertised filling_mode bitmask (gateway/broker_constraints
    # .derive_filling_type) — never blindly assumed. None falls back to IOC
    # only for callers (tests, ad-hoc scripts) that haven't gone through
    # that resolution; execution/service.py, the sole real NEW-ENTRY caller,
    # always resolves and sets this explicitly (finding #7).
    filling_type: str | None = None


@dataclass(frozen=True)
class OrderSendResult:
    retcode: int
    comment: str
    broker_order_id: str | None
    broker_deal_id: str | None
    broker_position_id: str | None
    volume_filled: float
    price_filled: float | None
    raw: dict   # full raw broker response fields, for audit — never parsed beyond the typed fields above


@dataclass(frozen=True)
class OrderCheckResult:
    retcode: int
    comment: str
    margin_required: float | None


@dataclass(frozen=True)
class PositionSnapshot:
    broker_position_id: str
    symbol: str
    direction: str
    volume: float
    price_open: float
    stop_loss: float
    take_profit: float
    profit: float
    magic: int
    comment: str


@dataclass(frozen=True)
class PendingOrderSnapshot:
    broker_order_id: str
    symbol: str
    direction: str
    volume: float
    price: float
    magic: int
    comment: str
