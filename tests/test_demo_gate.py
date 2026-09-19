"""Tests for the DEMO hard interlock (directive section 4).

Uses FakeGateway exclusively — this must be deterministic and portable,
unlike tests/test_mt5_gateway_live.py which talks to a real terminal.
"""

import pytest

from adaptive_scalper.gateway.demo_gate import (
    BLOCK_ACCOUNT_NOT_DEMO,
    BLOCK_BROKER_TRADING_DISABLED,
    BLOCK_MT5_DISCONNECTED,
    BLOCK_TERMINAL_TRADING_DISABLED,
    verify_demo_before_order,
)
from adaptive_scalper.gateway.fake_gateway import FakeGateway
from adaptive_scalper.gateway.types import AccountSnapshot, TerminalSnapshot, TradeMode


def _account(**overrides) -> AccountSnapshot:
    defaults = dict(
        login=12345, trade_mode=TradeMode.DEMO, balance=10000.0, equity=10000.0,
        margin_free=10000.0, currency="USD", server="Broker-Demo", company="Broker Ltd",
        trade_allowed=True, trade_expert=True,
    )
    defaults.update(overrides)
    return AccountSnapshot(**defaults)


def _terminal(**overrides) -> TerminalSnapshot:
    defaults = dict(
        connected=True, trade_allowed=True, build=4000, name="MetaTrader 5",
        company="Broker Ltd", path=r"C:\MT5",
    )
    defaults.update(overrides)
    return TerminalSnapshot(**defaults)


def test_allows_when_everything_confirms_demo():
    gw = FakeGateway(account=_account(), terminal=_terminal())
    result = verify_demo_before_order(gw)
    assert result.allowed is True
    assert result.block_reason is None


def test_blocks_when_terminal_info_missing():
    gw = FakeGateway(account=_account(), terminal=None)
    result = verify_demo_before_order(gw)
    assert result.allowed is False
    assert result.block_reason == BLOCK_MT5_DISCONNECTED


def test_blocks_when_terminal_not_connected():
    gw = FakeGateway(account=_account(), terminal=_terminal(connected=False))
    result = verify_demo_before_order(gw)
    assert result.allowed is False
    assert result.block_reason == BLOCK_MT5_DISCONNECTED


def test_blocks_when_account_info_missing():
    gw = FakeGateway(account=None, terminal=_terminal())
    result = verify_demo_before_order(gw)
    assert result.allowed is False
    assert result.block_reason == BLOCK_MT5_DISCONNECTED


def test_blocks_when_terminal_algo_trading_disabled():
    gw = FakeGateway(account=_account(), terminal=_terminal(trade_allowed=False))
    result = verify_demo_before_order(gw)
    assert result.allowed is False
    assert result.block_reason == BLOCK_TERMINAL_TRADING_DISABLED


def test_blocks_when_account_trade_not_allowed():
    gw = FakeGateway(account=_account(trade_allowed=False), terminal=_terminal())
    result = verify_demo_before_order(gw)
    assert result.allowed is False
    assert result.block_reason == BLOCK_BROKER_TRADING_DISABLED


def test_blocks_when_account_expert_trading_disabled():
    gw = FakeGateway(account=_account(trade_expert=False), terminal=_terminal())
    result = verify_demo_before_order(gw)
    assert result.allowed is False
    assert result.block_reason == BLOCK_BROKER_TRADING_DISABLED


@pytest.mark.parametrize("mode", [TradeMode.CONTEST, TradeMode.REAL])
def test_blocks_when_account_is_not_demo(mode):
    gw = FakeGateway(account=_account(trade_mode=mode), terminal=_terminal())
    result = verify_demo_before_order(gw)
    assert result.allowed is False
    assert result.block_reason == BLOCK_ACCOUNT_NOT_DEMO


def test_never_infers_demo_from_absence_of_information():
    # Everything None/missing must fail closed, not default-allow.
    gw = FakeGateway(account=None, terminal=None)
    result = verify_demo_before_order(gw)
    assert result.allowed is False


def test_account_switch_from_demo_to_real_is_caught_on_next_call():
    gw = FakeGateway(account=_account(), terminal=_terminal())
    first = verify_demo_before_order(gw)
    assert first.allowed is True

    # Simulate MT5 switching the connected account to a real one mid-session.
    gw.set_account(_account(trade_mode=TradeMode.REAL))
    second = verify_demo_before_order(gw)
    assert second.allowed is False
    assert second.block_reason == BLOCK_ACCOUNT_NOT_DEMO


def test_result_detail_includes_login_and_server_on_success():
    # Placeholder login/server — not any real account. See BUG_BACKLOG.md /
    # WORKLOG.md: an earlier draft of this fixture used the real login
    # number observed from this machine's live MT5 terminal, caught before
    # commit.
    gw = FakeGateway(account=_account(login=90000001, server="Broker-Demo-Server"), terminal=_terminal())
    result = verify_demo_before_order(gw)
    assert "90000001" in result.detail
    assert "Broker-Demo-Server" in result.detail
