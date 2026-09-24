"""Live smoke test for Mt5Gateway against a real MT5 terminal.

Unlike every other test in this suite, this one is NOT portable — it
requires an actual MT5 terminal installed, running, and logged in on the
machine pytest runs on. It is opt-in: it runs only with ASN_LIVE_MT5=1
(set by scripts/windows_verify.ps1 for its live step), because
`initialize()` launches the installed terminal. Otherwise, or when no
terminal is reachable, it skips, so it never breaks CI or another
developer's machine (directive
section 119: report actual results, never fabricate them — skipping
honestly is the correct behavior here, not a workaround).

Read-only only. No order_send/order_check call exists anywhere in this
file.
"""

from __future__ import annotations

import os

import pytest

from adaptive_scalper.config.constants import ALLOWED_CANONICAL_SYMBOLS
from adaptive_scalper.gateway.demo_gate import verify_demo_before_order
from adaptive_scalper.gateway.mt5_gateway import Mt5Gateway, Mt5NotAvailableError
from adaptive_scalper.gateway.symbol_resolver import resolve_all
from adaptive_scalper.gateway.types import AccountSnapshot, TerminalSnapshot, TradeMode


def _mt5_available() -> bool:
    # Opt-in: `initialize()` launches the installed terminal, so collecting
    # this file must never do that implicitly during an ordinary `pytest`.
    if os.environ.get("ASN_LIVE_MT5") != "1":
        return False
    try:
        gw = Mt5Gateway()
        ok = gw.initialize()
        gw.shutdown()
        return ok
    except Mt5NotAvailableError:
        return False


pytestmark = pytest.mark.skipif(
    not _mt5_available(), reason="live MT5 tests are opt-in (ASN_LIVE_MT5=1) and need a reachable terminal"
)


@pytest.fixture()
def gateway():
    gw = Mt5Gateway()
    assert gw.initialize() is True
    yield gw
    gw.shutdown()


def test_account_info_returns_a_well_typed_snapshot(gateway):
    account = gateway.account_info()
    assert isinstance(account, AccountSnapshot)
    assert account.login > 0
    assert account.currency


def test_terminal_info_returns_a_well_typed_snapshot(gateway):
    terminal = gateway.terminal_info()
    assert isinstance(terminal, TerminalSnapshot)
    assert terminal.connected is True


def test_live_account_on_this_dev_machine_is_demo(gateway):
    # Documented, machine-specific fact (PROJECT_STATUS.md "Live MT5
    # environment"): this account is confirmed DEMO. If this ever fails,
    # STOP all order-related development until corrected — CLAUDE.md
    # rule 4 prohibits real-money trading absolutely.
    account = gateway.account_info()
    assert account.trade_mode == TradeMode.DEMO, (
        "connected MT5 account is NOT DEMO — do not proceed with any "
        "order-related development until this is corrected"
    )


def test_demo_gate_allows_new_entry_against_the_live_connection(gateway):
    result = verify_demo_before_order(gateway)
    assert result.allowed is True, result.detail


def test_symbols_get_returns_a_nonempty_broker_symbol_list(gateway):
    symbols = gateway.symbols_get()
    assert len(symbols) > 0


def test_resolve_all_canonical_symbols_against_the_live_broker_list(gateway):
    symbols = gateway.symbols_get()
    results = resolve_all(symbols)
    assert set(results) == ALLOWED_CANONICAL_SYMBOLS
    unresolved = {c: r.reason for c, r in results.items() if not r.resolved}
    # Report honestly rather than force success: broker naming varies per
    # server, and section 6 requires failing closed, not guessing, when a
    # symbol doesn't resolve cleanly.
    if unresolved:
        pytest.fail(
            f"symbol resolution incomplete against this broker's live symbol "
            f"list: {unresolved} — resolved: "
            f"{ {c: r.broker_symbol for c, r in results.items() if r.resolved} }"
        )


def test_symbol_info_tick_for_a_resolved_canonical_symbol(gateway):
    symbols = gateway.symbols_get()
    results = resolve_all(symbols)
    resolved = [r for r in results.values() if r.resolved]
    if not resolved:
        pytest.skip("no canonical symbol resolved against this broker's symbol list")
    tick = gateway.symbol_info_tick(resolved[0].broker_symbol)
    assert tick is not None
    assert tick.bid > 0
    assert tick.ask >= tick.bid
