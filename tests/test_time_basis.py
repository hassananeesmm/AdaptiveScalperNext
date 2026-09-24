"""Stored MT5 rows' time basis and the one-shot conversion (BUG_BACKLOG #14)."""

from __future__ import annotations

import sqlite3

import pytest

from adaptive_scalper.cli import main
from adaptive_scalper.gateway.server_time import RULE_UTC2_US_DST, utc_to_server
from adaptive_scalper.history.time_basis import (
    SERVER_UNCONVERTED,
    UTC,
    TimeBasisError,
    convert_server_time_to_utc,
    get_basis,
    require_utc,
)
from adaptive_scalper.persistence import database
from adaptive_scalper.persistence.database import connect, migrate

U0 = 1_790_000_000  # 2026-09-21 (US DST in effect)


def _server(u: int) -> int:
    return utc_to_server(RULE_UTC2_US_DST, u)


def _legacy_db(path, monkeypatch):
    """Migrated to schema 26, filled with server-time rows by the old
    gateway, then upgraded -- the laptop's real situation."""
    conn = connect(path)
    discover = database._discover_migrations
    with monkeypatch.context() as m:
        m.setattr(database, "_discover_migrations", lambda: [x for x in discover() if x[0] <= 26])
        migrate(conn)
    for i in range(24):
        conn.execute("INSERT INTO bars (canonical_symbol, resolution, ts_utc, open, high, low, close, "
                     "tick_volume, spread, real_volume) VALUES ('XAUUSD', 'M5', ?, 1, 1, 1, 1, 1, 1, 0)",
                     (_server(U0 + 300 * i),))
    conn.execute("INSERT INTO ticks (canonical_symbol, ts_utc, ts_msc, bid, ask, last, volume) "
                 "VALUES ('XAUUSD', ?, ?, 1, 1, 0, 0)", (_server(U0), _server(U0) * 1000 + 9))
    conn.execute("INSERT INTO historical_bar_coverage (canonical_symbol, resolution, earliest_utc, latest_utc, "
                 "bar_count) VALUES ('XAUUSD', 'M5', ?, ?, 24)", (_server(U0), _server(U0 + 300 * 23)))
    migrate(conn)
    return conn


def _convert(conn, db, tmp_path):
    return convert_server_time_to_utc(conn, RULE_UTC2_US_DST, db_path=str(db), backup_dir=tmp_path / "bk",
                                      now_utc=U0 + 86400)


def test_an_empty_database_starts_utc(tmp_path):
    conn = connect(tmp_path / "db.sqlite3")
    migrate(conn)
    assert get_basis(conn)["basis"] == UTC
    require_utc(conn)


def test_a_database_with_pre_27_mt5_rows_is_marked_and_refused(tmp_path, monkeypatch):
    conn = _legacy_db(tmp_path / "db.sqlite3", monkeypatch)
    assert get_basis(conn)["basis"] == SERVER_UNCONVERTED
    with pytest.raises(TimeBasisError, match="convert-server-time"):
        require_utc(conn)


def test_conversion_backs_up_then_converts_every_mt5_time_once(tmp_path, monkeypatch):
    db = tmp_path / "db.sqlite3"
    conn = _legacy_db(db, monkeypatch)
    report = _convert(conn, db, tmp_path)
    assert [r[0] for r in conn.execute("SELECT ts_utc FROM bars ORDER BY ts_utc")] == [U0 + 300 * i for i in range(24)]
    assert tuple(conn.execute("SELECT ts_utc, ts_msc FROM ticks").fetchone()) == (U0, U0 * 1000 + 9)
    assert tuple(conn.execute("SELECT earliest_utc, latest_utc FROM historical_bar_coverage").fetchone()) == (
        U0, U0 + 300 * 23)
    assert report["rows"]["bars"] == 24 and get_basis(conn)["basis"] == UTC
    require_utc(conn)

    backup = sqlite3.connect(report["backup"])
    assert backup.execute("SELECT min(ts_utc) FROM bars").fetchone()[0] == _server(U0)
    backup.close()
    with pytest.raises(TimeBasisError, match="refusing to convert twice"):
        _convert(conn, db, tmp_path)


def test_a_failed_conversion_changes_nothing(tmp_path, monkeypatch):
    db = tmp_path / "db.sqlite3"
    conn = _legacy_db(db, monkeypatch)
    import adaptive_scalper.history.time_basis as tb

    monkeypatch.setattr(tb, "_PLAIN_COLUMNS", tb._PLAIN_COLUMNS + (("no_such_table", ("x",)),))
    with pytest.raises(sqlite3.OperationalError):
        _convert(conn, db, tmp_path)
    assert conn.execute("SELECT min(ts_utc) FROM bars").fetchone()[0] == _server(U0)
    assert get_basis(conn)["basis"] == SERVER_UNCONVERTED


def test_commands_that_use_stored_mt5_rows_refuse_an_unconverted_database(tmp_path, monkeypatch):
    db = tmp_path / "db.sqlite3"
    _legacy_db(db, monkeypatch).close()
    cfg = tmp_path / "config.toml"
    cfg.write_text(f'mode = "PAPER"\n[database]\npath = "{db.as_posix()}"\n[runtime]\n'
                   f'log_dir = "{(tmp_path / "logs").as_posix()}"\n', encoding="utf-8")
    for args in (["backtest", "--symbol", "XAUUSD", "--start", "0", "--end", str(U0)],
                 ["history", "bootstrap"], ["analyse", "--symbol", "XAUUSD", "--source", "db"],
                 ["paper"], ["demo"]):
        assert main(["--config", str(cfg), *args]) == 1, args
    assert main(["--config", str(cfg), "kill-switch", "status"]) == 0  # never blocked by the time basis
    assert main(["--config", str(cfg), "history", "convert-server-time", "--rule", RULE_UTC2_US_DST]) == 0
    assert main(["--config", str(cfg), "history", "convert-server-time"]) == 1  # one shot
