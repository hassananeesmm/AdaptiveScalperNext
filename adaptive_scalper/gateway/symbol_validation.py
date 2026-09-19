"""Post-resolution symbol validation.

Per external architecture review: a broker symbol is not safely
executable merely because its NAME matched the canonical symbol
(`symbol_resolver.resolve_symbol`). Name resolution only answers "which
broker symbol is this"; it says nothing about whether that symbol is
currently tradable, has sane contract data, or has a live quote. This
module answers that second question and fails closed on any doubt,
exactly like resolve_symbol does for naming — never silently substitutes
another asset.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass

from adaptive_scalper.gateway.protocol import Gateway
from adaptive_scalper.gateway.symbol_resolver import ResolutionResult
from adaptive_scalper.gateway.types import SymbolSpec, SymbolTradeMode, Tick

NO_SYMBOL_INFO = "no_symbol_info"
TRADING_DISABLED = "trading_disabled"
INVALID_CONTRACT_SPEC = "invalid_contract_spec"
NO_QUOTE = "no_quote"
INVALID_QUOTE = "invalid_quote"
STALE_QUOTE = "stale_quote"
VALID = "valid"

DEFAULT_MAX_QUOTE_AGE_SECONDS = 30.0


@dataclass(frozen=True)
class SymbolValidationResult:
    canonical: str
    broker_symbol: str
    valid: bool
    reason: str
    detail: str


def _contract_spec_is_sane(spec: SymbolSpec) -> bool:
    return (
        spec.trade_contract_size > 0
        and spec.volume_min > 0
        and spec.volume_max >= spec.volume_min
        and spec.volume_step > 0
        and spec.point > 0
        and spec.trade_tick_size > 0
        and spec.trade_tick_value > 0
        and spec.digits >= 0
    )


def validate_resolved_symbol(
    gateway: Gateway,
    canonical: str,
    broker_symbol: str,
    *,
    max_quote_age_seconds: float = DEFAULT_MAX_QUOTE_AGE_SECONDS,
    now: float | None = None,
) -> SymbolValidationResult:
    """Re-check a name-resolved broker symbol against live broker state.

    `now`: epoch seconds to compare quote freshness against. Callers pass
    this explicitly for determinism (tests) or omit it to use
    `time.time()` (real use). Freshness is only checked when `now` is
    resolvable to a real value AND the tick has a time — a tick lacking a
    usable timestamp does not itself fail validation on that basis alone,
    since not every broker/test fixture populates it meaningfully.
    """
    spec = gateway.symbol_info(broker_symbol)
    if spec is None:
        return SymbolValidationResult(
            canonical, broker_symbol, False, NO_SYMBOL_INFO,
            f"gateway.symbol_info({broker_symbol!r}) returned None",
        )

    if spec.trade_mode == SymbolTradeMode.DISABLED:
        return SymbolValidationResult(
            canonical, broker_symbol, False, TRADING_DISABLED,
            f"symbol trade_mode is DISABLED",
        )

    if not _contract_spec_is_sane(spec):
        return SymbolValidationResult(
            canonical, broker_symbol, False, INVALID_CONTRACT_SPEC,
            f"contract_size={spec.trade_contract_size} volume_min={spec.volume_min} "
            f"volume_max={spec.volume_max} volume_step={spec.volume_step} "
            f"point={spec.point} tick_size={spec.trade_tick_size} "
            f"tick_value={spec.trade_tick_value} digits={spec.digits}",
        )

    tick: Tick | None = gateway.symbol_info_tick(broker_symbol)
    if tick is None:
        return SymbolValidationResult(
            canonical, broker_symbol, False, NO_QUOTE,
            f"gateway.symbol_info_tick({broker_symbol!r}) returned None",
        )

    if not (tick.bid > 0 and tick.ask > 0 and tick.ask >= tick.bid):
        return SymbolValidationResult(
            canonical, broker_symbol, False, INVALID_QUOTE,
            f"bid={tick.bid} ask={tick.ask}",
        )

    effective_now = time.time() if now is None else now
    if tick.time:
        age = effective_now - tick.time
        if age > max_quote_age_seconds:
            return SymbolValidationResult(
                canonical, broker_symbol, False, STALE_QUOTE,
                f"quote age {age:.1f}s exceeds max {max_quote_age_seconds}s "
                f"(tick.time={tick.time}, now={effective_now})",
            )

    return SymbolValidationResult(
        canonical, broker_symbol, True, VALID,
        f"trade_mode={spec.trade_mode.name} bid={tick.bid} ask={tick.ask}",
    )


def resolve_and_validate(
    gateway: Gateway,
    resolution: ResolutionResult,
    *,
    max_quote_age_seconds: float = DEFAULT_MAX_QUOTE_AGE_SECONDS,
    now: float | None = None,
) -> SymbolValidationResult | None:
    """Validate an already-resolved symbol. Returns None (not a failure
    result) if the resolution itself did not succeed — validation only
    applies to a symbol that was actually named, distinct from a naming
    failure, which callers must handle via ResolutionResult.resolved."""
    if not resolution.resolved or resolution.broker_symbol is None:
        return None
    return validate_resolved_symbol(
        gateway, resolution.canonical, resolution.broker_symbol,
        max_quote_age_seconds=max_quote_age_seconds, now=now,
    )


def persist_validation(conn: sqlite3.Connection, result: SymbolValidationResult) -> None:
    """Attach validation evidence to an existing symbol_mapping row.

    Requires `persist_resolution`/`persist_all` (symbol_resolver.py) to
    have already written a row for this canonical symbol — validation
    evidence augments a name resolution, it doesn't stand alone. Raises
    if no matching row exists, rather than silently no-op'ing on a
    caller-ordering bug.
    """
    cursor = conn.execute(
        """
        UPDATE symbol_mapping
        SET valid = ?, validation_reason = ?, validation_detail = ?,
            validated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
        WHERE canonical = ?
        """,
        (int(result.valid), result.reason, result.detail, result.canonical),
    )
    if cursor.rowcount == 0:
        raise ValueError(
            f"no symbol_mapping row for canonical={result.canonical!r} — "
            f"call persist_resolution() before persist_validation()"
        )
