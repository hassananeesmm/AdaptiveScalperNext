"""Live dashboard evidence, freshness and responsive layout regressions.

Runs entirely against SQLite and the deterministic fake MT5 gateway;
none of these tests send a broker order or require a real terminal.
"""

from __future__ import annotations

import sqlite3

from adaptive_scalper.dashboard.page import PAGE_HTML
from adaptive_scalper.dashboard.panels import compute_all, compute_panel
from adaptive_scalper.persistence import connect, migrate
from adaptive_scalper.runtime.state import get_state, put_state
from runtime_helpers import STEP, T0, FakeClock, build_engine


def _database(tmp_path):
    conn = connect(tmp_path / "dashboard_live.sqlite3")
    migrate(conn)
    return conn


def test_market_panel_never_claims_liveness_without_a_runtime_sample(tmp_path):
    conn = _database(tmp_path)
    result = compute_panel(conn, "market", now=1_700_000_000)
    assert result["status"] == "NO_DATA"
    assert result["account"] if "account" in result else True
    conn.close()


def test_market_panel_exposes_only_fresh_demonstrably_sampled_quotes(tmp_path):
    conn = _database(tmp_path)
    now = 1_700_000_000
    put_state(conn, "live_telemetry", {
        "sampled_at_utc": now, "account": {
            "trade_mode": "DEMO", "balance": 1000, "equity": 1001,
            "margin_free": 999, "currency": "USD", "company": "Demo Broker"
        },
        "terminal": {"connected": True, "trade_allowed": False, "build": 5000},
        "quotes": {
            "XAUUSD": {"broker_symbol": "XAUUSDm", "time_utc": now,
                       "bid": 2600.01, "ask": 2600.21, "spread_points": 20.0},
        }, "positions": [], "errors": []
    }, now_utc=now)
    fresh = compute_panel(conn, "market", now=now + 3)
    assert fresh["status"] == "OK"
    assert fresh["quotes"]["XAUUSD"]["status"] == "LIVE"
    assert fresh["quotes"]["GBPJPY"]["status"] == "UNAVAILABLE"
    assert fresh["account"]["trade_mode"] == "DEMO"
    stale = compute_panel(conn, "market", now=now + 30)
    assert stale["status"] == "STALE"
    assert stale["quotes"]["XAUUSD"]["status"] == "STALE"
    assert "login" not in str(fresh["account"]).lower()
    conn.close()


def test_market_panel_never_turns_disconnected_terminal_into_live_quotes(tmp_path):
    conn = _database(tmp_path)
    now = 1_700_000_000
    put_state(conn, "live_telemetry", {
        "sampled_at_utc": now, "account": {"trade_mode": "DEMO"},
        "terminal": {"connected": False},
        "quotes": {"XAUUSD": {"bid": 2500, "ask": 2501, "time_utc": now}},
        "positions": None, "errors": ["terminal disconnected"]
    }, now_utc=now)
    panel = compute_panel(conn, "market", now=now)
    assert panel["status"] == "STALE"
    assert panel["quotes"]["XAUUSD"]["status"] == "STALE"
    conn.close()


def test_demo_deals_and_paper_results_are_never_pooled(tmp_path):
    conn = _database(tmp_path)
    now = 1_700_000_000
    conn.execute(
        "INSERT INTO deals (broker_deal_id, price, volume, commission, swap, profit, "
        "occurred_at_utc, fee) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("demo-deal-1", 2000.0, 0.01, -1.0, 0.0, 10.0, now, -0.5),
    )
    conn.execute(
        "INSERT INTO paper_session_state "
        "(session_key, canonical_symbol, resolution, equity, "
        "created_at_utc, updated_at_utc) VALUES (?, ?, ?, ?, ?, ?)",
        ("PAPER:XAUUSD:M5:test", "XAUUSD", "M5", 10025.0, now, now),
    )
    conn.execute(
        "INSERT INTO paper_trades "
        "(session_key, canonical_symbol, strategy_key, direction, entry_time_utc, "
        "entry_price, volume, initial_monetary_risk, entry_regime, exit_time_utc, "
        "exit_price, exit_reason, realized_pnl, total_cost, origin, recorded_at_utc) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("PAPER:XAUUSD:M5:test", "XAUUSD", "range_breakout", "BUY", now - 3600,
         2000.0, 0.01, 2.5, "TRENDING", now - 1800, 2002.0,
         "TARGET", 25.0, 1.0, "PAPER_LIVE_DATA", now),
    )
    panel = compute_panel(conn, "performance", now=now + 1)
    assert panel["status"] == "OK"
    assert panel["demo"]["observed_deals"] == 1
    assert panel["demo"]["net"] == 8.5
    assert panel["paper"]["by_symbol"][0]["net_pnl"] == 25.0
    assert panel["paper"]["recent_closed_trades"][0]["origin"] == "PAPER_LIVE_DATA"
    assert set(compute_all(conn, now=now + 1)["panels"]) >= {"market", "performance"}
    conn.close()


def test_runtime_publishes_live_telemetry_without_private_account_login(tmp_path):
    clock = FakeClock(T0 + 60 * STEP + 10)
    engine, conn, gateway = build_engine(tmp_path, mode="PAPER", clock=clock)
    engine.startup()
    engine._publish_telemetry()
    sample = get_state(conn, "live_telemetry")
    assert sample["sampled_at_utc"] == int(clock())
    assert sample["account"]["trade_mode"] == "DEMO"
    assert "login" not in sample["account"]
    assert sample["terminal"]["connected"] is True
    assert set(sample["quotes"]) == {"XAUUSD", "GBPJPY", "BTCUSD"}
    assert sample["positions"] is None  # PAPER is not broker exposure.
    gateway.shutdown()
    conn.close()


def test_page_has_windows_breakpoints_navigation_and_no_mutating_controls():
    assert "@media(max-width:1080px)" in PAGE_HTML
    assert "@media(max-width:740px)" in PAGE_HTML
    assert "grid-template-columns:minmax(0,1fr)" in PAGE_HTML
    assert "prefers-reduced-motion" in PAGE_HTML
    assert 'id="nav"' in PAGE_HTML and 'id="search"' in PAGE_HTML
    assert "AdaptiveScalperNext" in PAGE_HTML
    assert "REAL-MONEY EXECUTION: DISABLED" in PAGE_HTML
    assert "innerHTML" not in PAGE_HTML and "outerHTML" not in PAGE_HTML
    assert "eval(" not in PAGE_HTML
    assert "http://" not in PAGE_HTML.replace('"ws://"', "")
    assert "https://" not in PAGE_HTML
    assert "order_send" not in PAGE_HTML
    assert "kill-switch clear" not in PAGE_HTML
