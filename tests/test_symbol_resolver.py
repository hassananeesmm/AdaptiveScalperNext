"""Tests for broker symbol resolution (directive section 6) and its
persistence in the symbol_mapping table."""

import pytest

from adaptive_scalper.gateway.symbol_resolver import (
    ALIAS_MATCH,
    AMBIGUOUS,
    EXACT_MATCH,
    NO_MATCH,
    load_persisted_mapping,
    persist_all,
    persist_resolution,
    resolve_all,
    resolve_symbol,
)
from adaptive_scalper.gateway.types import SymbolSpec
from adaptive_scalper.persistence import connect, migrate


def _symbol(name: str, **overrides) -> SymbolSpec:
    defaults = dict(
        name=name, description="", currency_base="USD", currency_profit="USD",
        currency_margin="USD", digits=2, point=0.01, trade_contract_size=100.0,
        volume_min=0.01, volume_max=100.0, volume_step=0.01, trade_tick_size=0.01,
        trade_tick_value=1.0, spread=10, visible=True, trade_allowed=True,
    )
    defaults.update(overrides)
    return SymbolSpec(**defaults)


@pytest.fixture()
def db(tmp_path):
    conn = connect(tmp_path / "test.sqlite3")
    migrate(conn)
    yield conn
    conn.close()


# --------------------------------------------------------------------------
# Exact match
# --------------------------------------------------------------------------

def test_exact_match():
    symbols = [_symbol("XAUUSD"), _symbol("EURUSD")]
    result = resolve_symbol("XAUUSD", symbols)
    assert result.resolved is True
    assert result.broker_symbol == "XAUUSD"
    assert result.reason == EXACT_MATCH


# --------------------------------------------------------------------------
# Prefix / suffix alias
# --------------------------------------------------------------------------

@pytest.mark.parametrize("broker_name", [
    "XAUUSD.a", "XAUUSDm", "#XAUUSD", "XAUUSD_i", "xauusd.raw",
])
def test_alias_variants_resolve(broker_name):
    symbols = [_symbol(broker_name)]
    result = resolve_symbol("XAUUSD", symbols)
    assert result.resolved is True
    assert result.broker_symbol == broker_name
    assert result.reason == ALIAS_MATCH


def test_unrelated_symbol_starting_with_same_letters_does_not_match():
    # "XAUUSDMICRO" is a plausible unrelated micro-lot instrument; the
    # suffix cap must reject it rather than alias-match it away.
    symbols = [_symbol("XAUUSDMICROLOTSVARIANT")]
    result = resolve_symbol("XAUUSD", symbols)
    assert result.resolved is False
    assert result.reason == NO_MATCH


# --------------------------------------------------------------------------
# Missing broker symbol
# --------------------------------------------------------------------------

def test_no_match_fails_closed():
    symbols = [_symbol("EURUSD"), _symbol("GBPUSD")]
    result = resolve_symbol("XAUUSD", symbols)
    assert result.resolved is False
    assert result.reason == NO_MATCH
    assert result.broker_symbol is None


# --------------------------------------------------------------------------
# Ambiguous specification
# --------------------------------------------------------------------------

def test_multiple_plausible_aliases_is_ambiguous_and_fails_closed():
    symbols = [_symbol("XAUUSD.a"), _symbol("XAUUSD.b")]
    result = resolve_symbol("XAUUSD", symbols)
    assert result.resolved is False
    assert result.reason == AMBIGUOUS
    assert result.broker_symbol is None
    assert set(result.candidates) == {"XAUUSD.a", "XAUUSD.b"}


# --------------------------------------------------------------------------
# Canonical allow-list firewall
# --------------------------------------------------------------------------

def test_rejects_resolving_a_symbol_outside_the_canonical_allowlist():
    with pytest.raises(ValueError):
        resolve_symbol("EURUSD", [_symbol("EURUSD")])


def test_resolve_all_covers_exactly_the_three_canonical_symbols():
    symbols = [_symbol("XAUUSD"), _symbol("GBPJPY"), _symbol("BTCUSD")]
    results = resolve_all(symbols)
    assert set(results.keys()) == {"XAUUSD", "GBPJPY", "BTCUSD"}
    assert all(r.resolved for r in results.values())


def test_resolve_all_reports_partial_failure_honestly():
    symbols = [_symbol("XAUUSD"), _symbol("GBPJPY")]  # BTCUSD missing
    results = resolve_all(symbols)
    assert results["XAUUSD"].resolved is True
    assert results["GBPJPY"].resolved is True
    assert results["BTCUSD"].resolved is False
    assert results["BTCUSD"].reason == NO_MATCH


# --------------------------------------------------------------------------
# Persistence
# --------------------------------------------------------------------------

def test_persist_and_load_a_resolved_mapping(db):
    symbols = [_symbol("XAUUSD")]
    result = resolve_symbol("XAUUSD", symbols)
    persist_resolution(db, result)

    loaded = load_persisted_mapping(db, "XAUUSD")
    assert loaded is not None
    assert loaded.broker_symbol == "XAUUSD"
    assert loaded.resolved is True
    assert loaded.reason == EXACT_MATCH


def test_persist_records_failed_resolution_too(db):
    result = resolve_symbol("BTCUSD", [_symbol("XAUUSD")])  # BTCUSD absent
    persist_resolution(db, result)

    loaded = load_persisted_mapping(db, "BTCUSD")
    assert loaded is not None
    assert loaded.resolved is False
    assert loaded.broker_symbol is None
    assert loaded.reason == NO_MATCH


def test_persist_all_and_reresolution_overwrites_not_duplicates(db):
    symbols = [_symbol("XAUUSD"), _symbol("GBPJPY"), _symbol("BTCUSD")]
    persist_all(db, resolve_all(symbols))

    # Re-resolve after e.g. a broker symbol rename and persist again.
    symbols2 = [_symbol("XAUUSD.a"), _symbol("GBPJPY"), _symbol("BTCUSD")]
    persist_all(db, resolve_all(symbols2))

    rows = db.execute("SELECT COUNT(*) AS n FROM symbol_mapping").fetchone()
    assert rows["n"] == 3  # not 6 — overwritten, not accumulated

    loaded = load_persisted_mapping(db, "XAUUSD")
    assert loaded.broker_symbol == "XAUUSD.a"
    assert loaded.reason == ALIAS_MATCH


def test_load_persisted_mapping_returns_none_when_never_resolved(db):
    assert load_persisted_mapping(db, "XAUUSD") is None
