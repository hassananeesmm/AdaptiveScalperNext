"""Tests for adaptive_scalper.cli.

Commands that don't touch MT5 (status/health/kill-switch/doctor's
config+DB checks) are tested deterministically against a tmp config +
tmp SQLite DB. `symbols` and doctor's MT5 reachability line depend on a
real MT5 terminal and are exercised by test_mt5_gateway_live.py's
skip-if-unavailable pattern instead of duplicated here.
"""

import json

import pytest

from adaptive_scalper.cli import main
from adaptive_scalper.core import kill_switch
from adaptive_scalper.core.operator_authority import OperatorAuthority
from adaptive_scalper.persistence import connect, migrate


@pytest.fixture()
def config_path(tmp_path):
    db_path = tmp_path / "cli_test.sqlite3"
    cfg_path = tmp_path / "test_config.toml"
    cfg_path.write_text(
        f"""
mode = "PAPER"

[market]
symbols = ["XAUUSD", "GBPJPY", "BTCUSD"]

[strategies]
retired = ["failed_breakout_fade", "support_resistance_reaction"]

[database]
path = "{db_path.as_posix()}"
""",
        encoding="utf-8",
    )
    return str(cfg_path), db_path


def _run(config_path: str, *args: str) -> int:
    return main(["--config", config_path, *args])


def test_doctor_reports_ok_for_valid_config_and_db(config_path, capsys):
    cfg_path, _ = config_path
    code = _run(cfg_path, "doctor")
    out = capsys.readouterr().out
    assert "config: OK" in out
    assert "database: integrity=ok" in out
    # Exit code depends only on config/DB health here — MT5 reachability
    # is logged but never makes doctor fail (see cmd_doctor).
    assert code == 0


def test_status_reports_mode_and_symbols(config_path, capsys):
    cfg_path, _ = config_path
    code = _run(cfg_path, "status")
    body = json.loads(capsys.readouterr().out)
    assert code == 0
    assert body["mode"] == "PAPER"
    assert set(body["symbols"]) == {"XAUUSD", "GBPJPY", "BTCUSD"}
    assert body["kill_switch_status"] == "UNINITIALIZED"


def test_health_reflects_fresh_uninitialized_kill_switch(config_path, capsys):
    cfg_path, _ = config_path
    code = _run(cfg_path, "health")
    body = json.loads(capsys.readouterr().out)
    assert body["state"] == "TRADING_BLOCKED"  # fail-closed: UNINITIALIZED blocks
    assert code == 1  # non-HEALTHY exits non-zero


def test_health_ok_after_bootstrap(config_path, capsys):
    cfg_path, db_path = config_path
    conn = connect(db_path)
    migrate(conn)
    kill_switch.bootstrap(conn, OperatorAuthority("operator"))
    conn.close()

    code = _run(cfg_path, "health")
    body = json.loads(capsys.readouterr().out)
    assert body["state"] == "HEALTHY"
    assert code == 0


def test_kill_switch_status_initially_uninitialized(config_path, capsys):
    cfg_path, _ = config_path
    _run(cfg_path, "kill-switch", "status")
    body = json.loads(capsys.readouterr().out)
    assert body["status"] == "UNINITIALIZED"
    assert body["blocks_new_entries"] is True


def test_kill_switch_engage_then_status(config_path, capsys):
    cfg_path, _ = config_path
    _run(cfg_path, "kill-switch", "engage", "--reason", "daily loss limit", "--actor", "risk_governor")
    capsys.readouterr()
    _run(cfg_path, "kill-switch", "status")
    body = json.loads(capsys.readouterr().out)
    assert body["status"] == "ENGAGED"
    assert body["reason"] == "daily loss limit"


def test_kill_switch_clear_requires_operator_id(config_path, capsys):
    cfg_path, _ = config_path
    _run(cfg_path, "kill-switch", "engage", "--reason", "x", "--actor", "engine")
    capsys.readouterr()
    _run(cfg_path, "kill-switch", "clear", "--reason", "resolved", "--operator-id", "jane")
    out = capsys.readouterr().out
    assert "CLEARED" in out

    _run(cfg_path, "kill-switch", "status")
    body = json.loads(capsys.readouterr().out)
    assert body["status"] == "DISENGAGED"
    assert body["changed_by"] == "jane"


def test_history_status_reports_not_started_before_any_bootstrap(config_path, capsys):
    cfg_path, _ = config_path
    code = _run(cfg_path, "history", "status")
    body = json.loads(capsys.readouterr().out)
    assert code == 0
    assert set(body.keys()) == {"XAUUSD", "GBPJPY", "BTCUSD"}
    xau = body["XAUUSD"]
    assert xau["bars"]["M1"]["job_status"] == "NOT_STARTED"
    assert xau["bars"]["M1"]["bar_count"] == 0
    assert xau["ticks"]["job_status"] == "NOT_STARTED"


def test_history_status_reflects_completed_bootstrap(config_path, capsys):
    from adaptive_scalper.gateway.types import Bar
    from adaptive_scalper.history.bootstrap import bootstrap_bars

    cfg_path, db_path = config_path
    conn = connect(db_path)
    migrate(conn)
    from adaptive_scalper.gateway.fake_gateway import FakeGateway

    start = 1_700_000_000
    bars = [
        Bar(time=start + i * 60, open=1, high=1, low=1, close=1, tick_volume=1, spread=1, real_volume=0)
        for i in range(5)
    ]
    gw = FakeGateway(bars_by_resolution={"XAUUSDm": {"M1": bars}})
    bootstrap_bars(conn, gw, "XAUUSD", "XAUUSDm", "M1", start, bars[-1].time)
    conn.close()

    code = _run(cfg_path, "history", "status")
    body = json.loads(capsys.readouterr().out)
    assert code == 0
    assert body["XAUUSD"]["bars"]["M1"]["job_status"] == "COMPLETE"
    assert body["XAUUSD"]["bars"]["M1"]["bar_count"] == 5


def test_config_error_reported_and_nonzero_exit(tmp_path, capsys):
    bad_cfg = tmp_path / "bad.toml"
    bad_cfg.write_text('mode = "PAPER"\n[market]\nsymbols = ["EURUSD"]\n', encoding="utf-8")
    code = main(["--config", str(bad_cfg), "status"])
    assert code == 1
    assert "FAIL" in capsys.readouterr().err
