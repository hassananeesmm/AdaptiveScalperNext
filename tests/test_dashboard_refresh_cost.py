"""Dashboard refresh cost and snapshot consistency (Windows validation).

On the laptop's 218 MB database the overview panel ran a full
`PRAGMA integrity_check` on every refresh: 6.1 s per update, so the 2-s
WebSocket feed fell back to polling and every age was computed against a
timestamp taken 6 s before the data was read (heartbeat age -6 s).
"""

from __future__ import annotations

import re
import threading
import time

import adaptive_scalper.dashboard.health as health_module
import adaptive_scalper.dashboard.panels as panels
from adaptive_scalper.dashboard.health import INTEGRITY_PENDING, IntegrityMonitor, compute_health
from adaptive_scalper.persistence.database import connect, connect_readonly, migrate
from adaptive_scalper.runtime.state import put_state


def _db(tmp_path):
    path = tmp_path / "dash.sqlite3"
    conn = connect(path)
    migrate(conn)
    return path, conn


def test_a_slow_integrity_check_never_blocks_a_refresh_and_is_never_reported_ok(tmp_path, monkeypatch):
    path, _ = _db(tmp_path)
    release = threading.Event()
    real_check = health_module.integrity_check

    def slow_check(conn):
        release.wait(10)
        return real_check(conn)

    monkeypatch.setattr(health_module, "integrity_check", slow_check)
    monitor = IntegrityMonitor(ttl_seconds=600)
    started = time.monotonic()
    assert monitor.status(path, wait_seconds=0.1) == (INTEGRITY_PENDING, None)
    assert monitor.status(path, wait_seconds=0.1)[0] == INTEGRITY_PENDING   # one check in flight, not two
    assert time.monotonic() - started < 2
    release.set()
    deadline = time.monotonic() + 5
    while monitor.status(path, wait_seconds=0.1)[0] == INTEGRITY_PENDING and time.monotonic() < deadline:
        time.sleep(0.05)
    result, checked_at = monitor.status(path)
    assert result == "ok" and checked_at is not None


def test_a_pending_integrity_check_is_neither_ok_nor_critical(tmp_path):
    _, conn = _db(tmp_path)
    report = compute_health(conn, integrity=INTEGRITY_PENDING)
    assert report.database_integrity == INTEGRITY_PENDING
    assert report.state.value == "TRADING_BLOCKED"          # the kill switch, not the pending check
    assert any("pending" in r for r in report.reasons)
    assert compute_health(conn, integrity="*** corrupted page").state.value == "CRITICAL"


def test_the_overview_panel_no_longer_runs_integrity_check_per_refresh(tmp_path, monkeypatch):
    path, _ = _db(tmp_path)
    calls = []
    monkeypatch.setattr(health_module, "integrity_check", lambda conn: calls.append(1) or "ok")
    monkeypatch.setattr(panels, "DASHBOARD_INTEGRITY", IntegrityMonitor(ttl_seconds=600))
    ro = connect_readonly(path)
    for _ in range(5):
        body = panels.compute_panel(ro, "overview")
    assert len(calls) == 1
    assert body["database_integrity"]["result"] == "ok"


def test_no_panel_age_is_negative_when_the_runtime_writes_mid_refresh(tmp_path, monkeypatch):
    path, writer = _db(tmp_path)
    put_state(writer, "engine", {"state": "RUNNING", "mode": "PAPER"}, now_utc=int(time.time()) - 30)

    def runtime_writes_during_refresh(conn, now):
        # the runtime keeps writing while the dashboard computes; its clock is ahead of the old `now`
        put_state(writer, "engine", {"state": "RUNNING", "mode": "PAPER"}, now_utc=int(time.time()) + 5)
        return {}

    order = {"first": runtime_writes_during_refresh, **panels.PANELS}
    monkeypatch.setattr(panels, "PANELS", order)
    ro = connect_readonly(path)
    body = panels.compute_all(ro)
    runtime = body["panels"]["overview"]["runtime"]
    # The pinned snapshot still shows the 30-s-old heartbeat, not the row written
    # "in the future" mid-refresh (which previously produced a negative age).
    age = int(re.search(r"no heartbeat for (-?\d+)s", runtime["detail"]).group(1))
    assert runtime["status"] == "STALE" and 30 <= age <= 32
