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

import logging
import os
from datetime import datetime, timezone

from adaptive_scalper.gateway.server_time import (
    RULE_UTC,
    is_skipped_server_time,
    server_ms_to_utc_ms,
    server_to_utc,
    utc_to_server,
    validate_rule,
)
from adaptive_scalper.gateway.types import (
    AccountSnapshot,
    Bar,
    HistoricalDeal,
    HistoricalOrder,
    OrderAction,
    OrderCheckResult,
    OrderRequest,
    OrderSendResult,
    PendingOrderSnapshot,
    PositionSnapshot,
    SymbolSpec,
    SymbolTradeMode,
    TerminalSnapshot,
    Tick,
    TradeMode,
)

logger = logging.getLogger(__name__)

# Canonical resolution string -> MetaTrader5.TIMEFRAME_* attribute name.
# Built lazily against the real imported module (never hardcoded integer
# values) so this stays correct even if the SDK's internal encoding changes.
_RESOLUTION_TO_MT5_ATTR = {
    "M1": "TIMEFRAME_M1",
    "M2": "TIMEFRAME_M2",
    "M3": "TIMEFRAME_M3",
    "M5": "TIMEFRAME_M5",
    "M15": "TIMEFRAME_M15",
}


class Mt5NotAvailableError(RuntimeError):
    """Raised when the MetaTrader5 package cannot be imported."""


def _import_mt5():
    if os.environ.get("ASN_DISABLE_MT5") == "1":
        raise Mt5NotAvailableError(
            "MetaTrader5 access is disabled by ASN_DISABLE_MT5=1 (offline/smoke-test safety guard)"
        )
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
        trade_mode=SymbolTradeMode(raw.trade_mode),
        filling_mode=raw.filling_mode,
        trade_stops_level=raw.trade_stops_level,
        trade_freeze_level=raw.trade_freeze_level,
    )


def _tick(raw, rule: str) -> Tick:
    return Tick(
        time=server_to_utc(rule, raw.time),
        bid=raw.bid,
        ask=raw.ask,
        last=raw.last,
        volume=raw.volume,
        time_msc=server_ms_to_utc_ms(rule, int(getattr(raw, "time_msc", raw.time * 1000))),
    )


def _tick_row(raw, rule: str) -> Tick:
    # copy_ticks_range returns a numpy structured array, like
    # copy_rates_from_pos/copy_rates_range — field access via __getitem__,
    # not attribute access (see _bar's comment).
    return Tick(
        time=server_to_utc(rule, int(raw["time"])),
        bid=float(raw["bid"]),
        ask=float(raw["ask"]),
        last=float(raw["last"]),
        volume=float(raw["volume"]),
        time_msc=server_ms_to_utc_ms(rule, int(raw["time_msc"])),
    )


def _historical_order(raw, rule: str) -> HistoricalOrder:
    # history_orders_get returns namedtuple-like TradeOrder objects with
    # normal attribute access (unlike copy_rates_*/copy_ticks_*, which
    # return numpy structured arrays) — no __getitem__ needed here.
    return HistoricalOrder(
        ticket=raw.ticket,
        time_setup=server_to_utc(rule, raw.time_setup),
        time_done=server_to_utc(rule, raw.time_done) if raw.time_done else None,
        type=raw.type,
        state=raw.state,
        magic=raw.magic,
        position_id=raw.position_id,
        volume_initial=raw.volume_initial,
        volume_current=raw.volume_current,
        price_open=raw.price_open,
        sl=raw.sl,
        tp=raw.tp,
        price_current=raw.price_current,
        symbol=raw.symbol,
        comment=raw.comment,
        external_id=raw.external_id,
    )


def _historical_deal(raw, rule: str) -> HistoricalDeal:
    return HistoricalDeal(
        ticket=raw.ticket,
        order=raw.order,
        time=server_to_utc(rule, raw.time),
        type=raw.type,
        entry=raw.entry,
        magic=raw.magic,
        position_id=raw.position_id,
        volume=raw.volume,
        price=raw.price,
        commission=raw.commission,
        swap=raw.swap,
        profit=raw.profit,
        fee=raw.fee,
        symbol=raw.symbol,
        comment=raw.comment,
        external_id=raw.external_id,
    )


def _bar(raw, rule: str) -> Bar:
    # copy_rates_from_pos returns a numpy structured array; each row is a
    # numpy.void accessed by field name via __getitem__, not attribute access.
    return Bar(
        time=server_to_utc(rule, int(raw["time"])),
        open=float(raw["open"]),
        high=float(raw["high"]),
        low=float(raw["low"]),
        close=float(raw["close"]),
        tick_volume=int(raw["tick_volume"]),
        spread=int(raw["spread"]),
        real_volume=int(raw["real_volume"]),
    )


def _direction_from_mt5_position_type(raw_type: int) -> str:
    # MT5 POSITION_TYPE_BUY=0, POSITION_TYPE_SELL=1.
    return "BUY" if raw_type == 0 else "SELL"


def _direction_from_mt5_order_type(raw_type: int) -> str:
    # MT5 ORDER_TYPE_BUY*=even values, ORDER_TYPE_SELL*=odd values for
    # the market/limit/stop family this project ever places (0-5); this
    # project only ever places plain market orders (0/1) in practice,
    # but pending orders_get() can still legitimately report limit/stop
    # types placed manually or by a future feature, so this stays general
    # for the direction bit specifically.
    return "BUY" if raw_type % 2 == 0 else "SELL"


def _position_snapshot(raw) -> PositionSnapshot:
    return PositionSnapshot(
        broker_position_id=str(raw.ticket),
        symbol=raw.symbol,
        direction=_direction_from_mt5_position_type(raw.type),
        volume=raw.volume,
        price_open=raw.price_open,
        stop_loss=raw.sl,
        take_profit=raw.tp,
        profit=raw.profit,
        magic=raw.magic,
        comment=raw.comment,
    )


def _pending_order_snapshot(raw) -> PendingOrderSnapshot:
    return PendingOrderSnapshot(
        broker_order_id=str(raw.ticket),
        symbol=raw.symbol,
        direction=_direction_from_mt5_order_type(raw.type),
        volume=raw.volume_current,
        price=raw.price_open,
        magic=raw.magic,
        comment=raw.comment,
    )


_ORDER_ACTION_TO_MT5_ATTR = {
    OrderAction.DEAL: "TRADE_ACTION_DEAL",
    OrderAction.SLTP: "TRADE_ACTION_SLTP",
    OrderAction.REMOVE: "TRADE_ACTION_REMOVE",
}

_FILLING_TYPE_TO_MT5_ATTR = {
    "IOC": "ORDER_FILLING_IOC",
    "FOK": "ORDER_FILLING_FOK",
}


def _build_mt5_request(mt5mod, req: OrderRequest) -> dict:
    """Translate our broker-independent `OrderRequest` into MetaTrader5's
    raw request dict. This is the ONLY place in the codebase that
    constructs MT5's own request shape."""
    request: dict = {
        "action": getattr(mt5mod, _ORDER_ACTION_TO_MT5_ATTR[req.action]),
        "symbol": req.symbol,
        "magic": req.magic,
        "comment": req.comment,
    }

    if req.action == OrderAction.DEAL:
        if req.direction not in ("BUY", "SELL"):
            raise ValueError(f"OrderRequest.direction must be 'BUY' or 'SELL' for a DEAL, got {req.direction!r}")
        request["type"] = mt5mod.ORDER_TYPE_BUY if req.direction == "BUY" else mt5mod.ORDER_TYPE_SELL
        request["volume"] = req.volume
        request["type_time"] = mt5mod.ORDER_TIME_GTC
        # execution-safety review finding #7: IOC is used here ONLY as the
        # literal request default for callers that never resolved a
        # broker-supported filling type. execution/service.py — the sole
        # real NEW-ENTRY caller — always resolves this via
        # gateway.broker_constraints.derive_filling_type() first and sets
        # OrderRequest.filling_type explicitly; it never relies on this
        # fallback.
        filling_type = req.filling_type or "IOC"
        request["type_filling"] = getattr(mt5mod, _FILLING_TYPE_TO_MT5_ATTR[filling_type])
        request["deviation"] = req.deviation_points
        if req.price is not None:
            request["price"] = req.price
        if req.stop_loss is not None:
            request["sl"] = req.stop_loss
        if req.take_profit is not None:
            request["tp"] = req.take_profit
        if req.position_ticket is not None:
            # execution-safety review finding #2: a DEAL with a
            # position_ticket set is a CLOSE of that exact broker
            # position, not an ordinary new-entry market order — MT5
            # requires the `position` field to target it precisely
            # (critical on hedging accounts, where symbol+direction alone
            # cannot disambiguate which position to reduce).
            request["position"] = req.position_ticket

    elif req.action == OrderAction.SLTP:
        if req.position_ticket is None:
            raise ValueError("OrderRequest.position_ticket is required for an SLTP action")
        request["position"] = req.position_ticket
        if req.stop_loss is not None:
            request["sl"] = req.stop_loss
        if req.take_profit is not None:
            request["tp"] = req.take_profit

    elif req.action == OrderAction.REMOVE:
        if req.order_ticket is None:
            raise ValueError("OrderRequest.order_ticket is required for a REMOVE action")
        request["order"] = req.order_ticket

    return request


def _order_send_result(raw, retcode_on_none: tuple[int, str] | None = None) -> OrderSendResult:
    if raw is None:
        code, msg = retcode_on_none or (-1, "order_send returned None")
        return OrderSendResult(
            retcode=code, comment=msg, broker_order_id=None, broker_deal_id=None,
            broker_position_id=None, volume_filled=0.0, price_filled=None, raw={},
        )
    return OrderSendResult(
        retcode=raw.retcode,
        comment=raw.comment,
        broker_order_id=str(raw.order) if raw.order else None,
        broker_deal_id=str(raw.deal) if raw.deal else None,
        # execution-safety review finding #1: MqlTradeResult carries NO
        # authoritative position ticket. Treating `raw.order` (the ORDER
        # ticket) as the position ticket is unsafe and was a prior defect
        # here — MT5 does not guarantee they are the same value. The true
        # position ticket must be established from broker truth
        # afterward, via `execution.position_resolution.resolve_opened_
        # position_id()` (deal.position_id / order.position_id from
        # history_deals_get/history_orders_get), never invented here.
        broker_position_id=None,
        volume_filled=raw.volume,
        price_filled=raw.price if raw.price else None,
        raw={
            "retcode": raw.retcode, "deal": raw.deal, "order": raw.order,
            "volume": raw.volume, "price": raw.price, "comment": raw.comment,
        },
    )


class Mt5Gateway:
    """Thin translation layer over the `MetaTrader5` package. No business
    logic lives here — only calling the SDK and converting its output to
    our internal types."""

    def __init__(self, server_time_rule: str = RULE_UTC) -> None:
        self._mt5 = None
        # Every MT5 time is the broker's server clock; convert at this
        # boundary so everything past the gateway is real UTC (server_time.py).
        self.server_time_rule = validate_rule(server_time_rule)

    def _convertible(self, rows, name: str, what: str) -> list:
        """Drop rows stamped inside the hour the server clock skips at the
        spring DST change: they have no UTC instant and would duplicate a
        real later timestamp (server_time.is_skipped_server_time)."""
        kept = [r for r in rows if not is_skipped_server_time(self.server_time_rule, int(r["time"]))]
        if len(kept) != len(rows):
            logger.warning("dropped %d %s %s row(s) stamped inside the skipped spring DST server hour",
                           len(rows) - len(kept), name, what)
        return kept

    def _server_dt(self, utc_ts: int) -> datetime:
        return datetime.fromtimestamp(utc_to_server(self.server_time_rule, utc_ts), tz=timezone.utc)

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
        return _tick(raw, self.server_time_rule) if raw is not None else None

    def copy_rates_from_pos(self, name: str, timeframe: int, start_pos: int, count: int) -> list[Bar]:
        raw = self._mt5.copy_rates_from_pos(name, timeframe, start_pos, count)
        if raw is None:
            return []
        return [_bar(row, self.server_time_rule) for row in self._convertible(raw, name, "bar")]

    def copy_rates_range(
        self, name: str, resolution: str, date_from_utc: int, date_to_utc: int
    ) -> list[Bar]:
        attr = _RESOLUTION_TO_MT5_ATTR.get(resolution)
        if attr is None:
            raise ValueError(
                f"unsupported resolution {resolution!r}; supported: "
                f"{sorted(_RESOLUTION_TO_MT5_ATTR)}"
            )
        timeframe = getattr(self._mt5, attr)
        date_from = self._server_dt(date_from_utc)
        date_to = self._server_dt(date_to_utc)
        raw = self._mt5.copy_rates_range(name, timeframe, date_from, date_to)
        if raw is None:
            return []
        return [_bar(row, self.server_time_rule) for row in self._convertible(raw, name, "bar")]

    def copy_ticks_range(self, name: str, date_from_utc: int, date_to_utc: int) -> list[Tick]:
        date_from = self._server_dt(date_from_utc)
        date_to = self._server_dt(date_to_utc)
        raw = self._mt5.copy_ticks_range(name, date_from, date_to, self._mt5.COPY_TICKS_ALL)
        if raw is None:
            return []
        return [_tick_row(row, self.server_time_rule) for row in self._convertible(raw, name, "tick")]

    def history_orders_get(self, date_from_utc: int, date_to_utc: int) -> list[HistoricalOrder]:
        date_from = self._server_dt(date_from_utc)
        date_to = self._server_dt(date_to_utc)
        raw = self._mt5.history_orders_get(date_from, date_to)
        if raw is None:
            return []
        return [_historical_order(row, self.server_time_rule) for row in raw]

    def history_deals_get(self, date_from_utc: int, date_to_utc: int) -> list[HistoricalDeal]:
        date_from = self._server_dt(date_from_utc)
        date_to = self._server_dt(date_to_utc)
        raw = self._mt5.history_deals_get(date_from, date_to)
        if raw is None:
            return []
        return [_historical_deal(row, self.server_time_rule) for row in raw]

    def positions_get(self) -> list[PositionSnapshot]:
        raw = self._mt5.positions_get()
        if raw is None:
            return []
        return [_position_snapshot(p) for p in raw]

    def orders_get(self) -> list[PendingOrderSnapshot]:
        raw = self._mt5.orders_get()
        if raw is None:
            return []
        return [_pending_order_snapshot(o) for o in raw]

    def order_check(self, request: OrderRequest) -> OrderCheckResult:
        mt5_request = _build_mt5_request(self._mt5, request)
        raw = self._mt5.order_check(mt5_request)
        if raw is None:
            code, msg = self.last_error()
            return OrderCheckResult(retcode=code, comment=msg, margin_required=None)
        return OrderCheckResult(
            retcode=raw.retcode, comment=raw.comment, margin_required=getattr(raw, "margin", None),
        )

    def order_send(self, request: OrderRequest) -> OrderSendResult:
        mt5_request = _build_mt5_request(self._mt5, request)
        raw = self._mt5.order_send(mt5_request)
        return _order_send_result(raw, retcode_on_none=self.last_error() if raw is None else None)

    def last_error(self) -> tuple[int, str]:
        return tuple(self._mt5.last_error())
