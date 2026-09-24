"""One non-executing live DEMO `order_check()` (completion directive
Phase 16 / LOCAL_MT5_HANDOFF step L).

Freshly verifies, in order, and stops at the first failure: DEMO account
+ connected terminal + terminal/account trading permission
(`verify_demo_before_order`), canonical symbol resolution and instrument
identity (`validate_resolved_symbol`), direction permission, a fresh
quote, a risk-safe hypothetical volume (the broker minimum, and only if
the risk governor would allow at least that at the probe's stop distance),
and a broker-supported filling type. Only then does it call
`gateway.order_check()` with a request that is never sent, and records the
retcode, comment, margin, broker, terminal build, symbol and time in
`order_check_probes`.

This module has no path to `order_send` (audited): a probe is evidence for
the success-retcode convention (BUG_BACKLOG #5), not a trade.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import asdict, dataclass
from typing import Callable

from adaptive_scalper.gateway.broker_constraints import derive_filling_type
from adaptive_scalper.gateway.demo_gate import verify_demo_before_order
from adaptive_scalper.gateway.protocol import Gateway
from adaptive_scalper.gateway.symbol_resolver import resolve_all
from adaptive_scalper.gateway.symbol_validation import (
    validate_direction_for_new_exposure,
    validate_execution_quote,
    validate_resolved_symbol,
)
from adaptive_scalper.gateway.types import OrderAction, OrderRequest
from adaptive_scalper.risk.governor import calculate_safe_volume

CHECKED = "CHECKED"
BLOCKED = "BLOCKED"
ERROR = "ERROR"
PROBE_COMMENT = "ASN-ORDER-CHECK-PROBE"
STOP_DISTANCE_SPREADS = 10


@dataclass(frozen=True)
class ProbeResult:
    status: str
    stage: str
    detail: str
    canonical_symbol: str
    direction: str
    broker_symbol: str | None = None
    volume: float | None = None
    retcode: int | None = None
    comment: str | None = None
    margin_required: float | None = None
    broker_company: str | None = None
    broker_server: str | None = None
    terminal_build: int | None = None
    checked_at_utc: int | None = None


def _record(conn: sqlite3.Connection, result: ProbeResult) -> ProbeResult:
    conn.execute(
        "INSERT INTO order_check_probes (checked_at_utc, canonical_symbol, broker_symbol, direction, volume, status, "
        "stage, retcode, comment, margin_required, broker_company, broker_server, terminal_build, detail) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (result.checked_at_utc, result.canonical_symbol, result.broker_symbol, result.direction, result.volume,
         result.status, result.stage, result.retcode, result.comment, result.margin_required, result.broker_company,
         result.broker_server, result.terminal_build, result.detail),
    )
    conn.commit()
    return result


def run_order_check_probe(
    conn: sqlite3.Connection, gateway: Gateway, *, canonical_symbol: str, direction: str, risk_per_trade_pct: float,
    magic: int, clock: Callable[[], float] = time.time,
) -> ProbeResult:
    now = int(clock())
    base = {"canonical_symbol": canonical_symbol, "direction": direction, "checked_at_utc": now}

    def stop(stage: str, detail: str, status: str = BLOCKED, **extra) -> ProbeResult:
        return _record(conn, ProbeResult(status=status, stage=stage, detail=detail, **{**base, **extra}))

    demo = verify_demo_before_order(gateway)
    if not demo.allowed:
        return stop("DEMO_AND_PERMISSIONS", f"{demo.block_reason}: {demo.detail}")
    account, terminal = gateway.account_info(), gateway.terminal_info()
    base.update(broker_company=account.company, broker_server=account.server, terminal_build=terminal.build)

    resolution = resolve_all(gateway.symbols_get()).get(canonical_symbol)
    if resolution is None or not resolution.resolved:
        return stop("SYMBOL_RESOLUTION", f"{canonical_symbol} does not resolve on this broker")
    broker = resolution.broker_symbol
    base["broker_symbol"] = broker
    validation = validate_resolved_symbol(gateway, canonical_symbol, broker, now=clock())
    if not validation.valid:
        return stop("SYMBOL_VALIDATION", f"{validation.reason}: {validation.detail}")

    spec = gateway.symbol_info(broker)
    permitted = validate_direction_for_new_exposure(spec.trade_mode, direction)
    if not permitted.valid:
        return stop("DIRECTION", permitted.detail)
    tick = gateway.symbol_info_tick(broker)
    quote = validate_execution_quote(tick, now=clock())
    if not quote.valid:
        return stop("QUOTE", f"{quote.reason}: {quote.detail}")

    stop_distance = max(STOP_DISTANCE_SPREADS * (tick.ask - tick.bid), 2 * spec.trade_stops_level * spec.point,
                        spec.trade_tick_size)
    sizing = calculate_safe_volume(equity=account.equity, risk_per_trade_pct=risk_per_trade_pct,
                                   stop_distance_price=stop_distance, symbol_spec=spec)
    if not sizing.approved:
        return stop("RISK_SAFE_VOLUME", f"risk governor refuses even the broker minimum: {sizing.reason}")
    volume = spec.volume_min
    base["volume"] = volume
    filling = derive_filling_type(spec.filling_mode)
    if filling is None:
        return stop("BROKER_CONSTRAINTS", f"no supported filling type in filling_mode={spec.filling_mode}")

    price = tick.ask if direction == "BUY" else tick.bid
    sign = 1.0 if direction == "BUY" else -1.0
    request = OrderRequest(
        action=OrderAction.DEAL, symbol=broker, direction=direction, volume=volume,
        stop_loss=round(price - sign * stop_distance, spec.digits),
        take_profit=round(price + sign * 2 * stop_distance, spec.digits),
        magic=magic, comment=PROBE_COMMENT, filling_type=filling,
    )
    try:
        check = gateway.order_check(request)  # checked, never sent
    except Exception as exc:
        return stop("ORDER_CHECK", f"order_check raised {type(exc).__name__}: {exc}", status=ERROR)
    if check is None:
        return stop("ORDER_CHECK", "order_check returned None", status=ERROR)
    margin_note = ""
    if check.margin_required is not None and check.margin_required > account.margin_free:
        margin_note = f"; margin_required {check.margin_required} exceeds free margin"
    return stop("ORDER_CHECK", f"order_check returned retcode {check.retcode}{margin_note}; request NOT sent",
                status=CHECKED, retcode=check.retcode, comment=check.comment, margin_required=check.margin_required)


def result_dict(result: ProbeResult) -> dict:
    return asdict(result)
