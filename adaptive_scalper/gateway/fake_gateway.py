"""In-memory Gateway implementation for deterministic tests.

Implements the same `Gateway` protocol as `Mt5Gateway` so demo-gate and
symbol-resolver logic can be tested without a real MT5 terminal, and so
the exact same test suite structure could later be pointed at the real
gateway for a live (skip-if-unavailable) smoke test.
"""

from __future__ import annotations

import dataclasses

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
    TerminalSnapshot,
    Tick,
)

# Mirrors MT5's TRADE_RETCODE_DONE / a generic "not found" failure code —
# used only by FakeGateway's own simulation, never asserted as the real
# MT5 value elsewhere (Mt5Gateway always reports the SDK's own raw retcode).
FAKE_RETCODE_DONE = 10009
FAKE_RETCODE_NOT_FOUND = 10013


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
        order_send_responses: list[OrderSendResult] | None = None,
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

        # Order/position simulation state. broker_position_id/broker_order_id
        # (both strings, matching Mt5Gateway's convention) -> snapshot.
        self._open_positions: dict[str, PositionSnapshot] = {}
        self._pending_orders: dict[str, PendingOrderSnapshot] = {}
        self._next_ticket = 1000
        # If set, order_send() returns these IN ORDER instead of running
        # the default auto-fill simulation — lets a test dictate exact
        # broker responses (rejects, UNKNOWN-simulating None-like retcodes,
        # partial fills) rather than only the default "always fills" path.
        self._order_send_responses = list(order_send_responses) if order_send_responses is not None else None
        self.order_send_calls: list[OrderRequest] = []

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

    def positions_get(self) -> list[PositionSnapshot]:
        return list(self._open_positions.values())

    def orders_get(self) -> list[PendingOrderSnapshot]:
        return list(self._pending_orders.values())

    def order_check(self, request: OrderRequest) -> OrderCheckResult:
        return OrderCheckResult(retcode=FAKE_RETCODE_DONE, comment="fake: check ok", margin_required=0.0)

    def order_send(self, request: OrderRequest) -> OrderSendResult:
        self.order_send_calls.append(request)

        if self._order_send_responses is not None:
            if not self._order_send_responses:
                raise AssertionError("FakeGateway.order_send() called more times than queued order_send_responses")
            return self._order_send_responses.pop(0)

        return self._default_order_send(request)

    def _next_distinct_ticket(self) -> int:
        ticket = self._next_ticket
        self._next_ticket += 1
        return ticket

    def _fill_price_for(self, request: OrderRequest) -> float:
        if request.price is not None:
            return request.price
        tick = self._ticks.get(request.symbol)
        if tick is None:
            return 0.0
        return tick.ask if request.direction == "BUY" else tick.bid

    def _default_order_send(self, request: OrderRequest) -> OrderSendResult:
        """Simple, deterministic auto-fill simulation: a DEAL without a
        `position_ticket` opens a NEW position; a DEAL WITH one CLOSES
        that exact position (execution-safety review finding #2); SLTP
        modifies a matching open position; REMOVE cancels a matching
        pending order. Good enough for tests exercising the caller's
        handling of a successful path — tests needing a reject/partial/
        UNKNOWN outcome should pass `order_send_responses` instead.

        Deliberately uses THREE DISTINCT tickets (order/deal/position) for
        every fill, and always reports `broker_position_id=None` in the
        `OrderSendResult` — exactly mirroring real MT5's `MqlTradeResult`,
        which carries no position ticket at all. This is a direct fix for
        a prior defect where this simulator taught callers/tests that
        order ticket == position ticket (execution-safety review finding
        #1); the true position ticket is only discoverable afterward via
        `history_deals_get()`/`history_orders_get()`, exactly like the
        real gateway.
        """
        if request.action == OrderAction.DEAL and request.position_ticket is not None:
            return self._simulate_close_deal(request)

        if request.action == OrderAction.DEAL:
            return self._simulate_open_deal(request)

        if request.action == OrderAction.SLTP:
            key = str(request.position_ticket)
            pos = self._open_positions.get(key)
            if pos is None:
                return OrderSendResult(
                    retcode=FAKE_RETCODE_NOT_FOUND, comment="fake: no such position", broker_order_id=None,
                    broker_deal_id=None, broker_position_id=None, volume_filled=0.0, price_filled=None, raw={},
                )
            self._open_positions[key] = dataclasses.replace(
                pos,
                stop_loss=request.stop_loss if request.stop_loss is not None else pos.stop_loss,
                take_profit=request.take_profit if request.take_profit is not None else pos.take_profit,
            )
            return OrderSendResult(
                retcode=FAKE_RETCODE_DONE, comment="fake: sltp updated", broker_order_id=key,
                broker_deal_id=None, broker_position_id=key, volume_filled=0.0, price_filled=None, raw={},
            )

        if request.action == OrderAction.REMOVE:
            key = str(request.order_ticket)
            removed = self._pending_orders.pop(key, None)
            retcode = FAKE_RETCODE_DONE if removed is not None else FAKE_RETCODE_NOT_FOUND
            return OrderSendResult(
                retcode=retcode, comment="fake: removed" if removed else "fake: no such order",
                broker_order_id=key, broker_deal_id=None, broker_position_id=None,
                volume_filled=0.0, price_filled=None, raw={},
            )

        raise ValueError(f"unhandled OrderAction: {request.action!r}")

    def _simulate_open_deal(self, request: OrderRequest) -> OrderSendResult:
        order_ticket = self._next_distinct_ticket()
        deal_ticket = self._next_distinct_ticket()
        position_ticket = self._next_distinct_ticket()
        fill_price = self._fill_price_for(request)
        position_key = str(position_ticket)
        self._open_positions[position_key] = PositionSnapshot(
            broker_position_id=position_key, symbol=request.symbol, direction=request.direction,
            volume=request.volume, price_open=fill_price, stop_loss=request.stop_loss or 0.0,
            take_profit=request.take_profit or 0.0, profit=0.0, magic=request.magic, comment=request.comment,
        )
        self._historical_deals.append(HistoricalDeal(
            ticket=deal_ticket, order=order_ticket, time=0, type=0 if request.direction == "BUY" else 1,
            entry=0, magic=request.magic, position_id=position_ticket, volume=request.volume,
            price=fill_price, commission=0.0, swap=0.0, profit=0.0, fee=0.0, symbol=request.symbol,
            comment=request.comment, external_id="",
        ))
        return OrderSendResult(
            retcode=FAKE_RETCODE_DONE, comment="fake: filled", broker_order_id=str(order_ticket),
            broker_deal_id=str(deal_ticket),
            # Deliberately None: real MqlTradeResult carries no position
            # ticket (finding #1). Resolve via history_deals_get() /
            # execution.position_resolution, exactly as with the real
            # gateway.
            broker_position_id=None,
            volume_filled=request.volume, price_filled=fill_price, raw={},
        )

    def _simulate_close_deal(self, request: OrderRequest) -> OrderSendResult:
        position_key = str(request.position_ticket)
        pos = self._open_positions.get(position_key)
        if pos is None:
            return OrderSendResult(
                retcode=FAKE_RETCODE_NOT_FOUND, comment="fake: no such position to close",
                broker_order_id=None, broker_deal_id=None, broker_position_id=None,
                volume_filled=0.0, price_filled=None, raw={},
            )
        order_ticket = self._next_distinct_ticket()
        deal_ticket = self._next_distinct_ticket()
        fill_price = self._fill_price_for(request)
        del self._open_positions[position_key]
        self._historical_deals.append(HistoricalDeal(
            ticket=deal_ticket, order=order_ticket, time=0, type=0 if request.direction == "BUY" else 1,
            entry=1, magic=request.magic, position_id=int(position_key), volume=request.volume,
            price=fill_price, commission=0.0, swap=0.0, profit=0.0, fee=0.0, symbol=request.symbol,
            comment=request.comment, external_id="",
        ))
        return OrderSendResult(
            retcode=FAKE_RETCODE_DONE, comment="fake: closed", broker_order_id=str(order_ticket),
            broker_deal_id=str(deal_ticket), broker_position_id=None,
            volume_filled=request.volume, price_filled=fill_price, raw={},
        )

    def last_error(self) -> tuple[int, str]:
        return self._last_error

    # -- test helpers, not part of the Gateway protocol --

    def set_account(self, account: AccountSnapshot | None) -> None:
        self._account = account

    def set_terminal(self, terminal: TerminalSnapshot | None) -> None:
        self._terminal = terminal

    def inject_open_position(self, position: PositionSnapshot) -> None:
        """Directly set up broker-side open-position state for a test —
        e.g. to simulate a manually-opened or pre-existing position for
        reconciliation tests, without going through order_send()."""
        self._open_positions[position.broker_position_id] = position

    def inject_pending_order(self, order: PendingOrderSnapshot) -> None:
        self._pending_orders[order.broker_order_id] = order

    def remove_open_position(self, broker_position_id: str) -> None:
        """Simulate the broker closing a position outside this module's
        knowledge (SL/TP/manual) — for reconciliation tests."""
        self._open_positions.pop(broker_position_id, None)
