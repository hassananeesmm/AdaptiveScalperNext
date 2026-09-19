"""Tests for the Phase 1 dashboard health computation and its /api/health
endpoint (directive section 107/83). Deterministic only — FakeGateway,
tmp SQLite. No live MT5/network dependency."""

import pytest
from fastapi.testclient import TestClient

from adaptive_scalper.core import kill_switch
from adaptive_scalper.core.operator_authority import OperatorAuthority
from adaptive_scalper.dashboard.app import create_app
from adaptive_scalper.dashboard.health import HealthState, compute_health
from adaptive_scalper.gateway.fake_gateway import FakeGateway
from adaptive_scalper.gateway.types import TerminalSnapshot
from adaptive_scalper.persistence import connect, migrate


@pytest.fixture()
def db(tmp_path):
    conn = connect(tmp_path / "test.sqlite3")
    migrate(conn)
    yield conn
    conn.close()


def _terminal(connected: bool) -> TerminalSnapshot:
    return TerminalSnapshot(
        connected=connected, trade_allowed=True, build=4000,
        name="MetaTrader 5", company="Broker Ltd", path=r"C:\MT5",
    )


# --------------------------------------------------------------------------
# compute_health
# --------------------------------------------------------------------------

def test_fresh_uninitialized_db_is_trading_blocked_not_healthy(db):
    # A never-bootstrapped kill switch fails closed — this must NOT
    # report HEALTHY, since the kill switch itself blocks new entries.
    report = compute_health(db)
    assert report.state == HealthState.TRADING_BLOCKED
    assert report.kill_switch_status == "UNINITIALIZED"


def test_healthy_once_bootstrapped_and_no_gateway_supplied(db):
    kill_switch.bootstrap(db, OperatorAuthority("operator"))
    report = compute_health(db)
    assert report.state == HealthState.HEALTHY
    assert report.mt5_connected is None  # not checked — no gateway supplied


def test_healthy_with_a_connected_gateway(db):
    kill_switch.bootstrap(db, OperatorAuthority("operator"))
    gw = FakeGateway(terminal=_terminal(connected=True))
    report = compute_health(db, gw)
    assert report.state == HealthState.HEALTHY
    assert report.mt5_connected is True


def test_new_entries_blocked_when_gateway_disconnected(db):
    kill_switch.bootstrap(db, OperatorAuthority("operator"))
    gw = FakeGateway(terminal=_terminal(connected=False))
    report = compute_health(db, gw)
    assert report.state == HealthState.NEW_ENTRIES_BLOCKED
    assert report.mt5_connected is False


def test_trading_blocked_when_kill_switch_engaged(db):
    kill_switch.bootstrap(db, OperatorAuthority("operator"))
    kill_switch.engage(db, reason="daily loss limit breached", actor="risk_governor")
    report = compute_health(db)
    assert report.state == HealthState.TRADING_BLOCKED
    assert "daily loss limit breached" in (report.reasons[0] if report.reasons else "")


def test_kill_switch_takes_precedence_over_gateway_disconnection(db):
    kill_switch.engage(db, reason="x", actor="engine")
    gw = FakeGateway(terminal=_terminal(connected=False))
    report = compute_health(db, gw)
    assert report.state == HealthState.TRADING_BLOCKED  # not NEW_ENTRIES_BLOCKED


def test_gateway_exception_does_not_crash_health_and_reports_disconnected(db):
    class ExplodingGateway:
        def terminal_info(self):
            raise RuntimeError("MT5 terminal call failed")

    kill_switch.bootstrap(db, OperatorAuthority("operator"))
    report = compute_health(db, ExplodingGateway())
    assert report.state == HealthState.NEW_ENTRIES_BLOCKED
    assert report.mt5_connected is False
    assert any("RuntimeError" in r for r in report.reasons)


# --------------------------------------------------------------------------
# /api/health endpoint
# --------------------------------------------------------------------------

def test_health_endpoint_returns_expected_shape(tmp_path):
    db_path = tmp_path / "endpoint.sqlite3"
    conn = connect(db_path)
    migrate(conn)
    kill_switch.bootstrap(conn, OperatorAuthority("operator"))
    conn.close()

    app = create_app(db_path)
    client = TestClient(app)
    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["state"] == "HEALTHY"
    assert body["database_integrity"] == "ok"
    assert body["kill_switch_status"] == "DISENGAGED"
    assert body["kill_switch_blocks_new_entries"] is False
    assert body["mt5_connected"] is None


def test_health_endpoint_reflects_engaged_kill_switch(tmp_path):
    db_path = tmp_path / "endpoint_engaged.sqlite3"
    conn = connect(db_path)
    migrate(conn)
    kill_switch.engage(conn, reason="critical reconciliation failure", actor="engine")
    conn.close()

    app = create_app(db_path)
    client = TestClient(app)
    resp = client.get("/api/health")
    body = resp.json()
    assert body["state"] == "TRADING_BLOCKED"
    assert body["kill_switch_blocks_new_entries"] is True
