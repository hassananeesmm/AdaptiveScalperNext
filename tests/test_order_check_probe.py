"""The live DEMO order_check probe (LOCAL_MT5_HANDOFF step L), against the
deterministic chaos gateway. The real terminal's retcode is a Windows-local
item; here we prove the probe verifies everything first, stops at the
first failure, records evidence, and never sends."""

from __future__ import annotations

import pytest

from adaptive_scalper.execution.order_check_probe import BLOCKED, CHECKED, ERROR, run_order_check_probe
from adaptive_scalper.gateway.types import OrderCheckResult, SymbolTradeMode, TradeMode
from adaptive_scalper.persistence import connect, migrate
from chaos_harness import NOW, chaos_gateway, demo_account, demo_terminal, fresh_tick, gold_spec, raise_, returns


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "probe.sqlite3")
    migrate(c)
    return c


def _probe(conn, gw, **kw):
    return run_order_check_probe(conn, gw, canonical_symbol="XAUUSD", direction=kw.pop("direction", "BUY"),
                                 risk_per_trade_pct=0.25, magic=240924, clock=lambda: NOW, **kw)


def _rows(conn):
    return conn.execute("SELECT * FROM order_check_probes").fetchall()


def test_a_clean_probe_checks_records_and_never_sends(conn):
    gw = chaos_gateway()
    gw.on("order_check", returns(OrderCheckResult(retcode=0, comment="Done", margin_required=42.0)))
    result = _probe(conn, gw)
    assert result.status == CHECKED and result.retcode == 0 and result.margin_required == 42.0
    assert result.volume == gold_spec().volume_min and result.terminal_build == 1000
    assert gw.calls["order_check"] == 1 and gw.calls["order_send"] == 0 and not gw.order_send_calls
    (row,) = _rows(conn)
    assert row["status"] == CHECKED and row["retcode"] == 0 and row["broker_company"] == "Broker"
    assert "NOT sent" in row["detail"]


@pytest.mark.parametrize("gateway_kwargs,stage", [
    ({"account": demo_account(trade_mode=TradeMode.REAL)}, "DEMO_AND_PERMISSIONS"),
    ({"terminal": demo_terminal(trade_allowed=False)}, "DEMO_AND_PERMISSIONS"),
    ({"terminal": demo_terminal(connected=False)}, "DEMO_AND_PERMISSIONS"),
    ({"symbols": []}, "SYMBOL_RESOLUTION"),
    ({"symbols": [gold_spec(trade_mode=SymbolTradeMode.CLOSEONLY)]}, "DIRECTION"),
    ({"ticks": {gold_spec().name: fresh_tick(time=NOW - 3600)}}, "SYMBOL_VALIDATION"),  # validation includes quote age
    ({"symbols": [gold_spec(filling_mode=0)]}, "BROKER_CONSTRAINTS"),
])
def test_every_failed_precondition_blocks_before_order_check(conn, gateway_kwargs, stage):
    gw = chaos_gateway(**gateway_kwargs)
    result = _probe(conn, gw)
    assert result.status == BLOCKED and result.stage == stage, result
    assert gw.calls["order_check"] == 0 and gw.calls["order_send"] == 0
    assert _rows(conn)[0]["stage"] == stage


def test_a_risk_unsafe_minimum_volume_blocks(conn):
    gw = chaos_gateway(account=demo_account(equity=5.0, balance=5.0))
    result = _probe(conn, gw)
    assert result.status == BLOCKED and result.stage == "RISK_SAFE_VOLUME"
    assert gw.calls["order_check"] == 0


def test_an_order_check_exception_is_recorded_as_error(conn):
    gw = chaos_gateway()
    gw.on("order_check", raise_(TimeoutError("terminal busy")))
    result = _probe(conn, gw)
    assert result.status == ERROR and "TimeoutError" in result.detail
    assert gw.calls["order_send"] == 0


def test_a_non_success_retcode_is_still_evidence(conn):
    gw = chaos_gateway()
    gw.on("order_check", returns(OrderCheckResult(retcode=10019, comment="No money", margin_required=None)))
    result = _probe(conn, gw, direction="SELL")
    assert result.status == CHECKED and result.retcode == 10019 and result.comment == "No money"


def test_the_probe_module_has_no_send_path():
    import ast
    from pathlib import Path

    tree = ast.parse(Path("adaptive_scalper/execution/order_check_probe.py").read_text())
    attrs = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    assert "order_send" not in attrs and "submit_new_entry" not in names
