"""Tests for post-resolution symbol validation (directive section 6,
strengthened per external architecture review): a name match alone is
not sufficient — trade mode, contract spec sanity, and a live quote must
also check out, and any doubt fails closed."""

import pytest

from adaptive_scalper.gateway.fake_gateway import FakeGateway
from adaptive_scalper.gateway.symbol_resolver import ResolutionResult, persist_resolution
from adaptive_scalper.gateway.symbol_validation import (
    ASSET_IDENTITY_MISMATCH,
    DIRECTION_ALLOWED,
    DIRECTION_CLOSEONLY,
    DIRECTION_DISABLED,
    DIRECTION_NOT_ALLOWED,
    EXECUTION_FUTURE_TIMESTAMP,
    EXECUTION_INVALID_QUOTE,
    EXECUTION_INVALID_TIMESTAMP,
    EXECUTION_NO_QUOTE,
    EXECUTION_STALE_QUOTE,
    EXECUTION_VALID,
    INVALID_CONTRACT_SPEC,
    INVALID_QUOTE,
    NO_QUOTE,
    NO_SYMBOL_INFO,
    STALE_QUOTE,
    TRADING_DISABLED,
    VALID,
    persist_validation,
    resolve_and_validate,
    validate_direction_for_new_exposure,
    validate_execution_quote,
    validate_resolved_symbol,
)
from adaptive_scalper.gateway.types import SymbolSpec, SymbolTradeMode, Tick
from adaptive_scalper.persistence import connect, migrate


def _symbol(name: str = "XAUUSD", **overrides) -> SymbolSpec:
    defaults = dict(
        name=name, description="Gold vs US Dollar", currency_base="XAU", currency_profit="USD",
        currency_margin="XAU", digits=2, point=0.01, trade_contract_size=100.0,
        volume_min=0.01, volume_max=100.0, volume_step=0.01, trade_tick_size=0.01,
        trade_tick_value=1.0, spread=10, visible=True, trade_mode=SymbolTradeMode.FULL,
    )
    defaults.update(overrides)
    return SymbolSpec(**defaults)


def _tick(**overrides) -> Tick:
    defaults = dict(time=1_700_000_000, bid=2000.00, ask=2000.20, last=2000.10, volume=1.0)
    defaults.update(overrides)
    return Tick(**defaults)


@pytest.fixture()
def db(tmp_path):
    conn = connect(tmp_path / "test.sqlite3")
    migrate(conn)
    yield conn
    conn.close()


# --------------------------------------------------------------------------
# Happy path
# --------------------------------------------------------------------------

def test_valid_symbol_with_fresh_quote_passes():
    gw = FakeGateway(symbols=[_symbol()], ticks={"XAUUSD": _tick(time=1000)})
    result = validate_resolved_symbol(gw, "XAUUSD", "XAUUSD", now=1005)
    assert result.valid is True
    assert result.reason == VALID


# --------------------------------------------------------------------------
# Fails closed: symbol_info missing
# --------------------------------------------------------------------------

def test_missing_symbol_info_fails_closed():
    gw = FakeGateway(symbols=[], ticks={})
    result = validate_resolved_symbol(gw, "XAUUSD", "XAUUSD")
    assert result.valid is False
    assert result.reason == NO_SYMBOL_INFO


# --------------------------------------------------------------------------
# Fails closed: trading disabled
# --------------------------------------------------------------------------

def test_disabled_trade_mode_fails_closed():
    gw = FakeGateway(symbols=[_symbol(trade_mode=SymbolTradeMode.DISABLED)],
                      ticks={"XAUUSD": _tick()})
    result = validate_resolved_symbol(gw, "XAUUSD", "XAUUSD")
    assert result.valid is False
    assert result.reason == TRADING_DISABLED


@pytest.mark.parametrize("mode", [
    SymbolTradeMode.FULL, SymbolTradeMode.LONGONLY, SymbolTradeMode.SHORTONLY,
    SymbolTradeMode.CLOSEONLY,
])
def test_all_non_disabled_trade_modes_pass_the_trade_mode_check(mode):
    # CLOSEONLY still passes symbol-level validation here — the
    # distinction between "can open new exposure" vs "can only close" is
    # a DIRECTION check made by the eventual order-validation layer using
    # SymbolTradeMode.allows_new_long/allows_new_short, not this function.
    gw = FakeGateway(symbols=[_symbol(trade_mode=mode)], ticks={"XAUUSD": _tick(time=1000)})
    result = validate_resolved_symbol(gw, "XAUUSD", "XAUUSD", now=1005)
    assert result.valid is True


# --------------------------------------------------------------------------
# Fails closed: invalid contract spec
# --------------------------------------------------------------------------

@pytest.mark.parametrize("field,bad_value", [
    ("trade_contract_size", 0.0),
    ("volume_min", 0.0),
    ("volume_step", 0.0),
    ("point", 0.0),
    ("trade_tick_size", 0.0),
    ("trade_tick_value", 0.0),
])
def test_zero_or_invalid_contract_fields_fail_closed(field, bad_value):
    gw = FakeGateway(symbols=[_symbol(**{field: bad_value})], ticks={"XAUUSD": _tick()})
    result = validate_resolved_symbol(gw, "XAUUSD", "XAUUSD")
    assert result.valid is False
    assert result.reason == INVALID_CONTRACT_SPEC


def test_volume_max_below_volume_min_fails_closed():
    gw = FakeGateway(symbols=[_symbol(volume_min=1.0, volume_max=0.5)], ticks={"XAUUSD": _tick()})
    result = validate_resolved_symbol(gw, "XAUUSD", "XAUUSD")
    assert result.valid is False
    assert result.reason == INVALID_CONTRACT_SPEC


# --------------------------------------------------------------------------
# Fails closed: quote problems
# --------------------------------------------------------------------------

def test_missing_quote_fails_closed():
    gw = FakeGateway(symbols=[_symbol()], ticks={})
    result = validate_resolved_symbol(gw, "XAUUSD", "XAUUSD")
    assert result.valid is False
    assert result.reason == NO_QUOTE


def test_zero_bid_fails_closed():
    gw = FakeGateway(symbols=[_symbol()], ticks={"XAUUSD": _tick(bid=0.0)})
    result = validate_resolved_symbol(gw, "XAUUSD", "XAUUSD")
    assert result.valid is False
    assert result.reason == INVALID_QUOTE


def test_ask_below_bid_fails_closed():
    gw = FakeGateway(symbols=[_symbol()], ticks={"XAUUSD": _tick(bid=2000.0, ask=1999.0)})
    result = validate_resolved_symbol(gw, "XAUUSD", "XAUUSD")
    assert result.valid is False
    assert result.reason == INVALID_QUOTE


def test_stale_quote_fails_closed():
    gw = FakeGateway(symbols=[_symbol()], ticks={"XAUUSD": _tick(time=1000)})
    result = validate_resolved_symbol(gw, "XAUUSD", "XAUUSD", now=1100, max_quote_age_seconds=30)
    assert result.valid is False
    assert result.reason == STALE_QUOTE


def test_quote_within_max_age_passes():
    gw = FakeGateway(symbols=[_symbol()], ticks={"XAUUSD": _tick(time=1000)})
    result = validate_resolved_symbol(gw, "XAUUSD", "XAUUSD", now=1010, max_quote_age_seconds=30)
    assert result.valid is True


# --------------------------------------------------------------------------
# resolve_and_validate composition
# --------------------------------------------------------------------------

def test_resolve_and_validate_returns_none_for_a_failed_resolution():
    gw = FakeGateway(symbols=[], ticks={})
    failed = ResolutionResult(canonical="XAUUSD", broker_symbol=None, resolved=False, reason="no_match")
    assert resolve_and_validate(gw, failed) is None


def test_resolve_and_validate_validates_a_successful_resolution():
    gw = FakeGateway(symbols=[_symbol()], ticks={"XAUUSD": _tick(time=1000)})
    resolved = ResolutionResult(canonical="XAUUSD", broker_symbol="XAUUSD", resolved=True, reason="exact_match")
    result = resolve_and_validate(gw, resolved, now=1005)
    assert result is not None
    assert result.valid is True


# --------------------------------------------------------------------------
# Persistence
# --------------------------------------------------------------------------

def test_persist_validation_attaches_evidence_to_existing_mapping_row(db):
    resolution = ResolutionResult(canonical="XAUUSD", broker_symbol="XAUUSD", resolved=True, reason="exact_match")
    persist_resolution(db, resolution)

    gw = FakeGateway(symbols=[_symbol()], ticks={"XAUUSD": _tick(time=1000)})
    result = validate_resolved_symbol(gw, "XAUUSD", "XAUUSD", now=1005)
    persist_validation(db, result)

    row = db.execute("SELECT * FROM symbol_mapping WHERE canonical = 'XAUUSD'").fetchone()
    assert row["valid"] == 1
    assert row["validation_reason"] == VALID
    assert row["validated_at"] is not None


def test_persist_validation_records_a_failed_validation_too(db):
    persist_resolution(db, ResolutionResult(
        canonical="XAUUSD", broker_symbol="XAUUSD", resolved=True, reason="exact_match",
    ))
    gw = FakeGateway(symbols=[_symbol(trade_mode=SymbolTradeMode.DISABLED)], ticks={"XAUUSD": _tick()})
    result = validate_resolved_symbol(gw, "XAUUSD", "XAUUSD")
    persist_validation(db, result)

    row = db.execute("SELECT * FROM symbol_mapping WHERE canonical = 'XAUUSD'").fetchone()
    assert row["valid"] == 0
    assert row["validation_reason"] == TRADING_DISABLED


def test_persist_validation_without_a_prior_resolution_raises(db):
    gw = FakeGateway(symbols=[_symbol()], ticks={"XAUUSD": _tick()})
    result = validate_resolved_symbol(gw, "XAUUSD", "XAUUSD")
    with pytest.raises(ValueError):
        persist_validation(db, result)


# --------------------------------------------------------------------------
# Canonical asset identity (external review #1): name match alone is not
# proof of identity — currency_base/currency_profit must also match.
# --------------------------------------------------------------------------

def test_matching_currency_pair_passes():
    gw = FakeGateway(symbols=[_symbol(currency_base="XAU", currency_profit="USD")],
                      ticks={"XAUUSD": _tick(time=1000)})
    result = validate_resolved_symbol(gw, "XAUUSD", "XAUUSD", now=1005)
    assert result.valid is True


def test_mismatched_currency_base_fails_closed():
    # e.g. a confusingly-named broker symbol whose actual underlying isn't gold.
    gw = FakeGateway(symbols=[_symbol(currency_base="XAG", currency_profit="USD")],
                      ticks={"XAUUSD": _tick()})
    result = validate_resolved_symbol(gw, "XAUUSD", "XAUUSD")
    assert result.valid is False
    assert result.reason == ASSET_IDENTITY_MISMATCH


def test_mismatched_currency_profit_fails_closed():
    gw = FakeGateway(symbols=[_symbol(currency_base="XAU", currency_profit="EUR")],
                      ticks={"XAUUSD": _tick()})
    result = validate_resolved_symbol(gw, "XAUUSD", "XAUUSD")
    assert result.valid is False
    assert result.reason == ASSET_IDENTITY_MISMATCH


def test_empty_currency_metadata_fails_closed_rather_than_matching():
    gw = FakeGateway(symbols=[_symbol(currency_base="", currency_profit="")], ticks={"XAUUSD": _tick()})
    result = validate_resolved_symbol(gw, "XAUUSD", "XAUUSD")
    assert result.valid is False
    assert result.reason == ASSET_IDENTITY_MISMATCH


def test_mismatched_description_fails_closed_even_with_correct_currencies():
    gw = FakeGateway(symbols=[_symbol(currency_base="XAU", currency_profit="USD",
                                       description="Some Unrelated Instrument")],
                      ticks={"XAUUSD": _tick()})
    result = validate_resolved_symbol(gw, "XAUUSD", "XAUUSD")
    assert result.valid is False
    assert result.reason == ASSET_IDENTITY_MISMATCH


def test_currency_match_is_case_insensitive():
    gw = FakeGateway(symbols=[_symbol(currency_base="xau", currency_profit="usd")],
                      ticks={"XAUUSD": _tick(time=1000)})
    result = validate_resolved_symbol(gw, "XAUUSD", "XAUUSD", now=1005)
    assert result.valid is True


def test_btcusd_identity_does_not_require_btc_as_currency_base():
    # Live-verified real broker behavior (IC Markets Global): BTCUSD
    # reports currency_base=currency_profit="USD", not "BTC" — the base
    # currency check is deliberately skipped for BTCUSD; identity instead
    # rests on profit_currency=USD + a matching description.
    gw = FakeGateway(symbols=[_symbol(
        name="BTCUSD", currency_base="USD", currency_profit="USD",
        currency_margin="USD", description="Bitcoin (USD)",
    )], ticks={"BTCUSD": _tick(time=1000)})
    result = validate_resolved_symbol(gw, "BTCUSD", "BTCUSD", now=1005)
    assert result.valid is True


def test_btcusd_still_fails_closed_on_wrong_profit_currency_or_description():
    gw_wrong_profit = FakeGateway(symbols=[_symbol(
        name="BTCUSD", currency_base="USD", currency_profit="EUR", description="Bitcoin (EUR)",
    )], ticks={"BTCUSD": _tick()})
    result = validate_resolved_symbol(gw_wrong_profit, "BTCUSD", "BTCUSD")
    assert result.valid is False
    assert result.reason == ASSET_IDENTITY_MISMATCH

    gw_wrong_description = FakeGateway(symbols=[_symbol(
        name="BTCUSD", currency_base="USD", currency_profit="USD", description="Some Other CFD",
    )], ticks={"BTCUSD": _tick()})
    result2 = validate_resolved_symbol(gw_wrong_description, "BTCUSD", "BTCUSD")
    assert result2.valid is False
    assert result2.reason == ASSET_IDENTITY_MISMATCH


def test_gbpjpy_and_btcusd_expected_identity_are_correct():
    from adaptive_scalper.gateway.symbol_validation import EXPECTED_IDENTITY
    assert EXPECTED_IDENTITY["GBPJPY"].base_currency == "GBP"
    assert EXPECTED_IDENTITY["GBPJPY"].profit_currency == "JPY"
    assert EXPECTED_IDENTITY["BTCUSD"].base_currency is None
    assert EXPECTED_IDENTITY["BTCUSD"].profit_currency == "USD"


# --------------------------------------------------------------------------
# Execution-grade quote freshness (external review #2): stricter and
# separate from validate_resolved_symbol's lenient bootstrap-time check.
# --------------------------------------------------------------------------

def test_execution_quote_missing_tick_blocks():
    check = validate_execution_quote(None, now=1000)
    assert check.valid is False
    assert check.reason == EXECUTION_NO_QUOTE


def test_execution_quote_zero_timestamp_blocks():
    # Unlike validate_resolved_symbol, a zero/missing timestamp here BLOCKS
    # rather than being silently skipped.
    check = validate_execution_quote(_tick(time=0), now=1000)
    assert check.valid is False
    assert check.reason == EXECUTION_INVALID_TIMESTAMP


def test_execution_quote_implausibly_future_timestamp_blocks():
    check = validate_execution_quote(_tick(time=2000), now=1000, max_future_skew_seconds=5)
    assert check.valid is False
    assert check.reason == EXECUTION_FUTURE_TIMESTAMP


def test_execution_quote_within_future_skew_tolerance_does_not_block_on_that_basis():
    check = validate_execution_quote(_tick(time=1002), now=1000, max_future_skew_seconds=5, max_quote_age_seconds=30)
    assert check.reason != EXECUTION_FUTURE_TIMESTAMP


def test_execution_quote_stale_blocks_with_tight_default():
    check = validate_execution_quote(_tick(time=1000), now=1010, max_quote_age_seconds=5)
    assert check.valid is False
    assert check.reason == EXECUTION_STALE_QUOTE


def test_execution_quote_fresh_within_tight_window_passes():
    check = validate_execution_quote(_tick(time=1000), now=1002, max_quote_age_seconds=5)
    assert check.valid is True
    assert check.reason == EXECUTION_VALID


def test_execution_quote_invalid_bid_ask_blocks():
    check = validate_execution_quote(_tick(time=1000, bid=0.0), now=1001)
    assert check.valid is False
    assert check.reason == EXECUTION_INVALID_QUOTE


def test_execution_quote_default_max_age_is_much_tighter_than_bootstrap_default():
    from adaptive_scalper.gateway.symbol_validation import (
        DEFAULT_MAX_EXECUTION_QUOTE_AGE_SECONDS,
        DEFAULT_MAX_QUOTE_AGE_SECONDS,
    )
    assert DEFAULT_MAX_EXECUTION_QUOTE_AGE_SECONDS < DEFAULT_MAX_QUOTE_AGE_SECONDS


# --------------------------------------------------------------------------
# Directional symbol trade mode (external review #3): for NEW exposure,
# DISABLED/CLOSEONLY block, LONGONLY/SHORTONLY restrict direction, FULL
# allows either.
# --------------------------------------------------------------------------

def test_direction_disabled_blocks_both_directions():
    for direction in ("BUY", "SELL"):
        check = validate_direction_for_new_exposure(SymbolTradeMode.DISABLED, direction)
        assert check.valid is False
        assert check.reason == DIRECTION_DISABLED


def test_direction_closeonly_blocks_both_directions_for_new_exposure():
    for direction in ("BUY", "SELL"):
        check = validate_direction_for_new_exposure(SymbolTradeMode.CLOSEONLY, direction)
        assert check.valid is False
        assert check.reason == DIRECTION_CLOSEONLY


def test_direction_longonly_allows_buy_blocks_sell():
    buy = validate_direction_for_new_exposure(SymbolTradeMode.LONGONLY, "BUY")
    sell = validate_direction_for_new_exposure(SymbolTradeMode.LONGONLY, "SELL")
    assert buy.valid is True
    assert buy.reason == DIRECTION_ALLOWED
    assert sell.valid is False
    assert sell.reason == DIRECTION_NOT_ALLOWED


def test_direction_shortonly_allows_sell_blocks_buy():
    sell = validate_direction_for_new_exposure(SymbolTradeMode.SHORTONLY, "SELL")
    buy = validate_direction_for_new_exposure(SymbolTradeMode.SHORTONLY, "BUY")
    assert sell.valid is True
    assert buy.valid is False
    assert buy.reason == DIRECTION_NOT_ALLOWED


def test_direction_full_allows_both():
    for direction in ("BUY", "SELL"):
        check = validate_direction_for_new_exposure(SymbolTradeMode.FULL, direction)
        assert check.valid is True
        assert check.reason == DIRECTION_ALLOWED


def test_direction_is_case_insensitive():
    check = validate_direction_for_new_exposure(SymbolTradeMode.FULL, "buy")
    assert check.valid is True


def test_direction_rejects_unrecognized_value():
    with pytest.raises(ValueError):
        validate_direction_for_new_exposure(SymbolTradeMode.FULL, "CLOSE")
