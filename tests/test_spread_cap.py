"""Hard live-spread cap at the DEMO execution boundary (2026-10-06).

`execution.service.submit_new_entry()` refuses to send when the fresh
quote's spread exceeds `costs.<SYMBOL>.max_spread_price`, checked at BOTH
pre-send rounds; a symbol without a configured cap never trades (fail
closed). The probe/preflight side only reports.
"""

from __future__ import annotations

import math

import pytest

from adaptive_scalper.config.loader import AppConfig, load_config
from adaptive_scalper.execution.service import BLOCK_SPREAD, BLOCKED_BROKER_STATE, spread_cap_violation
from adaptive_scalper.gateway.types import Tick

import test_execution_service as svc

db = svc.db  # the execution-service tests' migrated DB + bootstrapped kill switch fixture


def test_within_cap_is_allowed_and_at_cap_is_allowed():
    assert spread_cap_violation(2000.00, 2000.15, 0.20) is None
    assert spread_cap_violation(2000.0, 2000.25, 0.25) is None


def test_above_cap_blocks():
    reason = spread_cap_violation(2000.0, 2000.21, 0.20)
    assert reason is not None and "exceeds max_spread_price 0.2" in reason


@pytest.mark.parametrize("bid,ask", [(math.nan, 2000.0), (2000.0, math.inf), (2000.5, 2000.0)])
def test_unusable_quote_blocks(bid, ask):
    assert spread_cap_violation(bid, ask, 0.20) is not None


def test_missing_cap_blocks_fail_closed():
    assert "fail closed" in spread_cap_violation(2000.0, 2000.01, None)


def test_shipped_config_caps_both_executable_symbols():
    cfg = load_config("config/default.toml")
    assert cfg.cost_for("XAUUSD").max_spread_price == 0.20
    assert cfg.cost_for("BTCUSD").max_spread_price == 15.0
    for symbol in cfg.market.symbols:
        assert cfg.cost_for(symbol).max_spread_price is not None, symbol


@pytest.mark.parametrize("bad", [0.0, -1.0, math.inf])
def test_config_rejects_a_non_positive_or_infinite_cap(bad):
    with pytest.raises(Exception):
        AppConfig(costs={"XAUUSD": {"max_spread_price": bad}})


def test_submit_blocks_before_order_send_when_spread_exceeds_cap(db):
    gw = svc._demo_gateway()  # quote 1999.0 / 2001.0: spread 2.0
    outcome = svc._submit(db, gw, svc._sequence(svc._good_evidence(), svc._good_evidence()), max_spread_price=1.5)
    assert outcome.status == BLOCKED_BROKER_STATE
    assert BLOCK_SPREAD in outcome.detail
    assert gw.order_send_calls == []


def test_submit_blocks_without_a_configured_cap(db):
    gw = svc._demo_gateway()
    outcome = svc._submit(db, gw, svc._sequence(svc._good_evidence(), svc._good_evidence()), max_spread_price=None)
    assert outcome.status == BLOCKED_BROKER_STATE and "fail closed" in outcome.detail
    assert gw.order_send_calls == []


def test_spread_widening_between_rounds_blocks_at_round_two(db):
    gw = svc._demo_gateway()
    quotes = iter([Tick(time=5000, bid=1999.0, ask=2001.0, last=2000.0, volume=1.0),   # round 1: 2.0
                   Tick(time=5000, bid=1998.0, ask=2002.0, last=2000.0, volume=1.0)])  # round 2: 4.0
    last = {}

    def tick(_name):
        last["t"] = next(quotes, last.get("t"))
        return last["t"]
    gw.symbol_info_tick = tick
    outcome = svc._submit(db, gw, svc._sequence(svc._good_evidence(), svc._good_evidence()), max_spread_price=3.0)
    assert outcome.status == BLOCKED_BROKER_STATE and BLOCK_SPREAD in outcome.detail
    assert gw.order_send_calls == []
