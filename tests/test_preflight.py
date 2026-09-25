from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from adaptive_scalper.cli import main
from adaptive_scalper.persistence import connect, migrate
from adaptive_scalper.preflight import run_preflight


def _config(tmp_path: Path) -> tuple[str, Path]:
    db = tmp_path / "preflight.sqlite3"
    cfg = tmp_path / "preflight.toml"
    cfg.write_text(
        'mode = "PAPER"\n'
        '[market]\nsymbols = ["XAUUSD", "GBPJPY", "BTCUSD"]\n'
        '[strategies]\nretired = ["failed_breakout_fade", "support_resistance_reaction"]\n'
        f'[database]\npath = "{db.as_posix()}"\n',
        encoding="utf-8",
    )
    conn = connect(db)
    migrate(conn)
    conn.close()
    return str(cfg), db


def test_preflight_is_non_mutating_when_mt5_is_unavailable(tmp_path):
    cfg, db = _config(tmp_path)
    before = db.read_bytes()
    report = run_preflight(cfg, dashboard_url="http://127.0.0.1:1", now_utc=1_800_000_000)
    after = db.read_bytes()
    assert report["result"] == "BLOCKED"
    assert report["non_mutating"] is True
    assert report["real_money_execution"] == "DISABLED"
    assert any("MT5 probe failed" in reason for reason in report["paper_blockers"])
    assert before == after


def test_preflight_never_calls_migration_or_writes(tmp_path, monkeypatch):
    cfg, db = _config(tmp_path)

    def forbidden_connect(*args, **kwargs):
        raise AssertionError("writable database connection used")

    monkeypatch.setattr("adaptive_scalper.persistence.database.connect", forbidden_connect)
    report = run_preflight(cfg, dashboard_url="http://127.0.0.1:1", now_utc=1_800_000_000)
    assert report["non_mutating"] is True
    with sqlite3.connect(db) as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_preflight_command_is_json_and_blocked_without_live_mt5(tmp_path, capsys):
    cfg, _ = _config(tmp_path)
    code = main(["--config", cfg, "preflight", "--dashboard-url", "http://127.0.0.1:1"])
    body = json.loads(capsys.readouterr().out)
    assert code == 1
    assert body["result"] == "BLOCKED"
    assert body["checks"]
