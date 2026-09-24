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

from adaptive_scalper.config.constants import ALLOWED_CANONICAL_SYMBOLS
from adaptive_scalper.gateway.protocol import Gateway
from adaptive_scalper.gateway.symbol_resolver import ResolutionResult
from adaptive_scalper.gateway.types import SymbolSpec, SymbolTradeMode, Tick

NO_SYMBOL_INFO = "no_symbol_info"
TRADING_DISABLED = "trading_disabled"
INVALID_CONTRACT_SPEC = "invalid_contract_spec"
ASSET_IDENTITY_MISMATCH = "asset_identity_mismatch"
NO_QUOTE = "no_quote"
INVALID_QUOTE = "invalid_quote"
STALE_QUOTE = "stale_quote"
VALID = "valid"

DEFAULT_MAX_QUOTE_AGE_SECONDS = 30.0


@dataclass(frozen=True)
class _IdentitySpec:
    """What "this broker symbol is genuinely the canonical instrument"
    means for one canonical symbol, given real broker metadata isn't
    uniform across instrument classes (see the module-level comment
    below `EXPECTED_IDENTITY` for why `base_currency` is `None` for
    BTCUSD)."""

    profit_currency: str            # settlement/quote currency — reliable across brokers, always checked
    description_keywords: tuple[str, ...]  # at least one must appear (case-insensitive) in spec.description
    base_currency: str | None = None       # exact match required ONLY when not None


# Per external review: name resolution (symbol_resolver.py) is NOT
# sufficient proof of asset identity on its own — it caught XAUUSDT
# aliasing to XAUUSD once already (see symbol_resolver.py's regression
# test) via a name-pattern fix, but a name-only defense can in principle
# be fooled by an unusual broker naming scheme. This is a SECOND,
# independent layer using broker-reported metadata.
#
# `base_currency` is deliberately checked ONLY for true FX/metal pairs
# (XAUUSD, GBPJPY), where MT5's currency_base field reliably names the
# base asset. It is NOT checked for BTCUSD: live-verified against this
# project's real IC Markets DEMO terminal, BTCUSD reports
# currency_base=currency_profit=currency_margin="USD" — the broker
# settles/margins the CFD entirely in USD and doesn't use currency_base
# to name the crypto asset at all. Requiring base_currency="BTC" there
# would fail closed on the genuine, correctly-name-resolved instrument
# every single time on this (and plausibly other) brokers, which is not
# "conservative handling of broker-specific metadata" — it defeats the
# exact three-symbol universe the whole project exists to trade. Identity
# for BTCUSD instead rests on profit_currency=USD PLUS the broker's own
# description containing "bitcoin"/"btc" — two independent signals that
# are unlikely to both hold for a genuinely different instrument.
EXPECTED_IDENTITY: dict[str, _IdentitySpec] = {
    "XAUUSD": _IdentitySpec(profit_currency="USD", base_currency="XAU", description_keywords=("gold",)),
    "GBPJPY": _IdentitySpec(profit_currency="JPY", base_currency="GBP", description_keywords=("pound", "yen")),
    "BTCUSD": _IdentitySpec(profit_currency="USD", base_currency=None, description_keywords=("bitcoin", "btc")),
}

# If the canonical allow-list ever gains/loses a symbol without this map
# being updated to match, that is exactly the kind of silent drift this
# whole module exists to prevent — fail loudly at import time, not by
# quietly skipping the identity check for whatever symbol was missed.
if frozenset(EXPECTED_IDENTITY) != ALLOWED_CANONICAL_SYMBOLS:  # explicit: survives `python -O`
    raise RuntimeError("EXPECTED_IDENTITY must have exactly one entry per ALLOWED_CANONICAL_SYMBOLS")


def _identity_matches(spec: SymbolSpec, identity: _IdentitySpec) -> bool:
    actual_profit = (spec.currency_profit or "").strip().upper()
    if actual_profit != identity.profit_currency.upper():
        return False

    if identity.base_currency is not None:
        actual_base = (spec.currency_base or "").strip().upper()
        if actual_base != identity.base_currency.upper():
            return False

    description = (spec.description or "").lower()
    if not any(keyword in description for keyword in identity.description_keywords):
        return False

    return True


def identity_matches_canonical(spec: SymbolSpec, canonical: str) -> bool:
    """Public entry point for `_identity_matches()` against an ALREADY-
    FETCHED `SymbolSpec` — for callers (e.g. `execution/service.py`'s
    critical-state check) that need to re-verify identity against fresh
    broker metadata they fetched themselves, without `validate_resolved_symbol()`'s
    own redundant internal `gateway.symbol_info()`/`symbol_info_tick()`
    re-fetch. `canonical` must be one of `EXPECTED_IDENTITY`'s keys
    (`ALLOWED_CANONICAL_SYMBOLS`) — a `KeyError` on anything else is
    correct fail-closed behavior, not something to catch and paper over."""
    return _identity_matches(spec, EXPECTED_IDENTITY[canonical])


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
            "symbol trade_mode is DISABLED",
        )

    if not _contract_spec_is_sane(spec):
        return SymbolValidationResult(
            canonical, broker_symbol, False, INVALID_CONTRACT_SPEC,
            f"contract_size={spec.trade_contract_size} volume_min={spec.volume_min} "
            f"volume_max={spec.volume_max} volume_step={spec.volume_step} "
            f"point={spec.point} tick_size={spec.trade_tick_size} "
            f"tick_value={spec.trade_tick_value} digits={spec.digits}",
        )

    identity = EXPECTED_IDENTITY[canonical]
    if not _identity_matches(spec, identity):
        return SymbolValidationResult(
            canonical, broker_symbol, False, ASSET_IDENTITY_MISMATCH,
            f"expected profit_currency={identity.profit_currency} "
            f"base_currency={identity.base_currency or 'N/A (not checked for this symbol)'} "
            f"description containing one of {identity.description_keywords}; "
            f"broker reports currency_base={spec.currency_base!r} currency_profit={spec.currency_profit!r} "
            f"description={spec.description!r}",
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


DEFAULT_MAX_EXECUTION_QUOTE_AGE_SECONDS = 5.0
DEFAULT_MAX_EXECUTION_FUTURE_SKEW_SECONDS = 5.0

EXECUTION_NO_QUOTE = "execution_no_quote"
EXECUTION_INVALID_TIMESTAMP = "execution_invalid_timestamp"
EXECUTION_FUTURE_TIMESTAMP = "execution_future_timestamp"
EXECUTION_STALE_QUOTE = "execution_stale_quote"
EXECUTION_INVALID_QUOTE = "execution_invalid_quote"
EXECUTION_VALID = "execution_valid"


@dataclass(frozen=True)
class ExecutionQuoteCheck:
    valid: bool
    reason: str
    detail: str


def validate_execution_quote(
    tick: Tick | None,
    *,
    max_quote_age_seconds: float = DEFAULT_MAX_EXECUTION_QUOTE_AGE_SECONDS,
    max_future_skew_seconds: float = DEFAULT_MAX_EXECUTION_FUTURE_SKEW_SECONDS,
    now: float | None = None,
) -> ExecutionQuoteCheck:
    """Strict, final pre-order quote check — deliberately SEPARATE from
    (and stricter than) `validate_resolved_symbol`'s tick check above.

    Per external review: that check is intentionally lenient about a
    missing/zero tick timestamp, because it also runs during bootstrap/
    informational validation where a not-yet-populated timestamp
    shouldn't itself block name resolution. Execution must never inherit
    that leniency. Immediately before order_send, this function requires:

    - a quote to exist at all
    - a real, non-zero, plausible timestamp (missing/zero/unusable BLOCKS,
      not "doesn't count against validity")
    - the timestamp not implausibly in the future (clock skew / bad feed)
    - the quote to be fresh within `max_quote_age_seconds` (default 5s —
      much tighter than the ~30s bootstrap default, because this check
      runs once, right before submitting real broker exposure)
    - bid/ask to be sane (bid, ask both positive; ask >= bid)

    Callers fetch the tick fresh (`gateway.symbol_info_tick(...)`)
    immediately before calling this — it takes the tick, not the gateway,
    so it stays a pure, trivially-testable function.
    """
    if tick is None:
        return ExecutionQuoteCheck(False, EXECUTION_NO_QUOTE, "gateway returned no tick")

    if not tick.time:
        return ExecutionQuoteCheck(
            False, EXECUTION_INVALID_TIMESTAMP, f"tick.time={tick.time!r} is missing/zero/unusable"
        )

    effective_now = time.time() if now is None else now
    if tick.time > effective_now + max_future_skew_seconds:
        return ExecutionQuoteCheck(
            False, EXECUTION_FUTURE_TIMESTAMP,
            f"tick.time={tick.time} is more than {max_future_skew_seconds}s ahead of now={effective_now}",
        )

    age = effective_now - tick.time
    if age > max_quote_age_seconds:
        return ExecutionQuoteCheck(
            False, EXECUTION_STALE_QUOTE,
            f"quote age {age:.3f}s exceeds max {max_quote_age_seconds}s (tick.time={tick.time}, now={effective_now})",
        )

    if not (tick.bid > 0 and tick.ask > 0 and tick.ask >= tick.bid):
        return ExecutionQuoteCheck(False, EXECUTION_INVALID_QUOTE, f"bid={tick.bid} ask={tick.ask}")

    return ExecutionQuoteCheck(True, EXECUTION_VALID, f"age={age:.3f}s bid={tick.bid} ask={tick.ask}")


DIRECTION_ALLOWED = "direction_allowed"
DIRECTION_DISABLED = "direction_disabled"
DIRECTION_CLOSEONLY = "direction_closeonly"
DIRECTION_NOT_ALLOWED = "direction_not_allowed"


@dataclass(frozen=True)
class DirectionCheck:
    valid: bool
    reason: str
    detail: str


def validate_direction_for_new_exposure(trade_mode: SymbolTradeMode, direction: str) -> DirectionCheck:
    """For NEW exposure only (per external review):

    DISABLED   -> BLOCK
    CLOSEONLY  -> BLOCK (closing existing risk remains possible elsewhere —
                  this function is never consulted for a close)
    LONGONLY   -> BUY only
    SHORTONLY  -> SELL only
    FULL       -> either direction (still subject to every other gate)

    `direction` must be exactly "BUY" or "SELL" (case-insensitive) —
    anything else raises ValueError rather than silently treating an
    unrecognized value as one direction or the other.
    """
    normalized = direction.upper()
    if normalized not in ("BUY", "SELL"):
        raise ValueError(f"direction must be 'BUY' or 'SELL', got {direction!r}")

    if trade_mode == SymbolTradeMode.DISABLED:
        return DirectionCheck(False, DIRECTION_DISABLED, "symbol trade_mode is DISABLED")
    if trade_mode == SymbolTradeMode.CLOSEONLY:
        return DirectionCheck(False, DIRECTION_CLOSEONLY, "symbol trade_mode is CLOSEONLY — no new exposure")

    allowed = trade_mode.allows_new_long if normalized == "BUY" else trade_mode.allows_new_short
    if not allowed:
        return DirectionCheck(
            False, DIRECTION_NOT_ALLOWED,
            f"trade_mode={trade_mode.name} does not permit new {normalized} exposure",
        )
    return DirectionCheck(True, DIRECTION_ALLOWED, f"trade_mode={trade_mode.name} permits new {normalized} exposure")


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
