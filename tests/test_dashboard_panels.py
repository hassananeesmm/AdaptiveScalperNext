"""Observer-only dashboard (completion directive Phase 9): read-only DB,
no MT5, no mutating endpoint, every panel honest about missing data, one
panel's failure isolated, live WebSocket feed."""

from __future__ import annotations

import ast
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from adaptive_scalper.core.kill_switch import bootstrap as bootstrap_kill_switch
from adaptive_scalper.core.operator_authority import OperatorAuthority
from adaptive_scalper.dashboard import panels as panels_module
from adaptive_scalper.dashboard.app import create_app
from adaptive_scalper.dashboard.page import PAGE_HTML
from adaptive_scalper.dashboard.panels import PANELS, compute_all
from adaptive_scalper.persistence.database import connect, connect_readonly, migrate
from runtime_helpers import STEP, T0, FakeClock, build_engine, step

DASHBOARD = Path(__file__).resolve().parents[1] / "adaptive_scalper" / "dashboard"


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "dash.sqlite3"
    conn = connect(path)
    migrate(conn)
    conn.close()
    return path


def test_the_read_only_connection_rejects_writes(db):
    conn = connect_readonly(db)
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("INSERT INTO runtime_state (key, value_json, updated_at_utc) VALUES ('x', '{}', 0)")


def test_a_missing_database_is_reported_and_never_created(tmp_path):
    path = tmp_path / "absent.sqlite3"
    client = TestClient(create_app(path))
    assert client.get("/api/panels").json()["status"] == "UNAVAILABLE"
    assert client.get("/api/health").json()["state"] == "UNAVAILABLE"
    assert not path.exists()


def test_every_panel_renders_honestly_on_an_empty_database(db):
    body = TestClient(create_app(db)).get("/api/panels").json()
    assert set(body["panels"]) == set(PANELS)
    for name, panel in body["panels"].items():
        assert panel["status"] in ("OK", "NO_DATA"), (name, panel)
    overview = body["panels"]["overview"]
    assert overview["real_money_execution"] == "DISABLED"
    assert overview["runtime"]["status"] == "NOT_STARTED"
    assert overview["kill_switch"]["blocks_new_entries"] is True
    assert body["panels"]["components"]["status"] == "NO_DATA"


def test_panels_reflect_a_running_paper_runtime(tmp_path):
    clock = FakeClock(T0 + 60 * STEP + 10)
    engine, conn, _ = build_engine(tmp_path, mode="PAPER", clock=clock)
    bootstrap_kill_switch(conn, OperatorAuthority("test-operator"), reason="test setup")
    engine.startup()
    step(engine, clock, seconds=STEP * 25, tick=4)
    observer = connect_readonly(engine.config.database.path)
    data = compute_all(observer, now=int(clock()))
    p = data["panels"]
    assert p["overview"]["runtime"]["status"] == "RUNNING" and p["overview"]["runtime"]["mode"] == "PAPER"
    assert p["components"]["status"] == "OK" and "okf" in p["components"]["components"]
    assert set(p["symbols"]["symbols"]) == {"XAUUSD", "GBPJPY", "BTCUSD"}
    assert p["symbols"]["symbols"]["XAUUSD"]["spec_captured_at_utc"] is not None
    assert p["decisions"]["recent"] and p["positions"]["paper_sessions"]
    assert p["memory"]["status"] == "OK" and p["knowledge"]["okf_version"] == "0.2"
    later = compute_all(observer, now=int(clock()) + 3600)
    assert later["panels"]["overview"]["runtime"]["status"] == "STALE"


def test_one_failing_panel_never_takes_down_the_others(db, monkeypatch):
    def broken(conn, now):
        raise RuntimeError("boom")

    monkeypatch.setitem(panels_module.PANELS, "news", broken)
    body = TestClient(create_app(db)).get("/api/panels").json()
    assert body["panels"]["news"]["status"] == "UNAVAILABLE" and "boom" in body["panels"]["news"]["detail"]
    assert body["panels"]["overview"]["status"] == "OK"


def test_single_panel_and_unknown_panel(db):
    client = TestClient(create_app(db))
    assert client.get("/api/panels/orders").json()["status"] == "OK"
    assert client.get("/api/panels/nope").status_code == 404


def test_the_websocket_pushes_all_panels(db):
    with TestClient(create_app(db, push_seconds=0.05)).websocket_connect("/ws") as ws:
        first = ws.receive_json()
        second = ws.receive_json()
    assert set(first["panels"]) == set(PANELS) and "panels" in second


def test_the_page_is_served_and_never_uses_inner_html():
    assert "REAL-MONEY EXECUTION: DISABLED" in PAGE_HTML
    assert "innerHTML" not in PAGE_HTML and "outerHTML" not in PAGE_HTML and "eval(" not in PAGE_HTML
    assert "http://" not in PAGE_HTML.replace('"ws://"', "") and "https://" not in PAGE_HTML  # no external assets


def test_no_endpoint_can_change_anything(db):
    app = create_app(db)
    for route in app.routes:
        methods = getattr(route, "methods", None) or set()
        assert methods <= {"GET", "HEAD"}, (route.path, methods)


def test_the_dashboard_never_imports_mt5_execution_or_operator_authority():
    forbidden = ("adaptive_scalper.gateway", "adaptive_scalper.execution", "adaptive_scalper.core.operator_authority",
                 "MetaTrader5", "adaptive_scalper.runtime.engine")
    for path in DASHBOARD.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            elif isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            for name in names:
                if path.name == "health.py" and name == "adaptive_scalper.gateway.protocol":
                    continue  # type-only import for compute_health's optional gateway (unused by the app)
                assert not name.startswith(forbidden), (path.name, name)
        calls = {n.func.attr for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
        assert not calls & {"engage", "clear", "bootstrap", "order_send", "order_check", "commit"}, path.name
