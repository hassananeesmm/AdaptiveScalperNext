"""Tests for post-resolution symbol validation (directive section 6,
strengthened per external architecture review): a name match alone is
not sufficient — trade mode, contract spec sanity, and a live quote must
also check out, and any doubt fails closed."""

import pytest

from adaptive_scalper.gateway.fake_gateway import FakeGateway
from adaptive_scalper.gateway.symbol_resolver import ResolutionResult, persist_resolution
from adaptive_scalper.gateway.symbol_validation import (
    INVALID_CONTRACT_SPEC,
    INVALID_QUOTE,
    NO_QUOTE,
    NO_SYMBOL_INFO,
    STALE_QUOTE,
    TRADING_DISABLED,
    VALID,
    persist_validation,
    resolve_and_validate,
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
