"""Strategy Lab dashboard panels: registry/source-verified metadata, live
activity, broker-verified attribution, and DEMO/PAPER/BACKTEST performance
evidence. Read-only, SQLite-only, no MT5. Every panel must degrade to
honest empty evidence rather than fabricate a value, and never conflate
PAPER/BACKTEST with DEMO or guess a strategy attribution."""

from __future__ import annotations

from adaptive_scalper.config.constants import RETIRED_STRATEGY_KEYS
from adaptive_scalper.dashboard.page import PAGE_HTML
from adaptive_scalper.dashboard.panels import compute_panel
from adaptive_scalper.journal.events import append_event
from adaptive_scalper.persistence.database import connect, migrate


def _database(tmp_path):
    conn = connect(tmp_path / "strategy_lab.sqlite3")
    migrate(conn)
    return conn


def _insert_position(conn, *, broker_position_id, strategy_key, symbol="BTCUSD", direction="BUY",
                      volume=0.1, entry_price=100.0, status="CLOSED", opened_at=1_700_000_000,
                      closed_at=1_700_000_100, initial_risk=10.0):
    conn.execute(
        "INSERT INTO positions (broker_position_id, canonical_symbol, direction, volume, entry_price, "
        "initial_monetary_risk, strategy_key, status, opened_at_utc, closed_at_utc) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (broker_position_id, symbol, direction, volume, entry_price, initial_risk, strategy_key,
         status, opened_at, closed_at),
    )


def _insert_deal(conn, *, broker_position_id, entry_type, price=100.0, volume=0.1,
                  commission=-0.5, swap=0.0, profit=5.0, fee=0.0, occurred_at=1_700_000_050,
                  broker_deal_id=None):
    conn.execute(
        "INSERT INTO deals (broker_deal_id, broker_position_id, price, volume, commission, swap, "
        "profit, fee, entry_type, deal_type, occurred_at_utc) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (broker_deal_id or f"deal-{broker_position_id}-{entry_type}-{occurred_at}", broker_position_id,
         price, volume, commission, swap, profit, fee, entry_type, "BUY", occurred_at),
    )


# ---------------------------------------------------------------------------
# Empty data
# ---------------------------------------------------------------------------

def test_all_four_panels_are_ok_and_empty_on_a_fresh_database(tmp_path):
    conn = _database(tmp_path)
    now = 1_700_000_000
    for name in ("strategy_registry", "strategy_activity", "strategy_attribution", "strategy_performance"):
        result = compute_panel(conn, name, now=now)
        assert result["status"] == "OK", (name, result)
    registry = compute_panel(conn, "strategy_registry", now=now)
    keys = {s["strategy_key"] for s in registry["strategies"]}
    assert keys == {
        "momentum_continuation", "pullback_continuation", "range_breakout", "statistical_reversion",
        "volatility_expansion", "microstructure_acceleration",
    } | set(RETIRED_STRATEGY_KEYS)
    for strategy in registry["strategies"]:
        if strategy["strategy_key"] in RETIRED_STRATEGY_KEYS:
            assert strategy["lifecycle_stage"] == "RETIRED"
            assert strategy["registration_status"] == "RETIRED_PERMANENTLY"
        else:
            assert strategy["lifecycle_stage"] == "REGISTERED"
    activity = compute_panel(conn, "strategy_activity", now=now)
    assert activity["recent_evaluations"] == []
    attribution = compute_panel(conn, "strategy_attribution", now=now)
    assert attribution["attributed_positions"] == []
    assert attribution["unattributed_deals"] == []
    performance = compute_panel(conn, "strategy_performance", now=now)
    assert performance["sample_sizes"] == {"demo": 0, "paper": 0, "backtest": 0}
    conn.close()


# ---------------------------------------------------------------------------
# Retirement firewall stays visible even with historical evidence
# ---------------------------------------------------------------------------

def test_retired_strategies_are_never_shown_as_registered_even_with_evidence(tmp_path):
    conn = _database(tmp_path)
    now = 1_700_000_000
    retired_key = sorted(RETIRED_STRATEGY_KEYS)[0]
    append_event(conn, "hist:1", "SIGNAL_CREATED", now - 100, "BTCUSD",
                 {"direction": "BUY"}, strategy_key=retired_key)
    conn.commit()
    registry = compute_panel(conn, "strategy_registry", now=now)
    row = next(s for s in registry["strategies"] if s["strategy_key"] == retired_key)
    assert row["registration_status"] == "RETIRED_PERMANENTLY"
    assert row["lifecycle_stage"] == "RETIRED"
    assert row["source_module"] is None
    conn.close()


# ---------------------------------------------------------------------------
# Lifecycle: distinguishes proposed/selected/submitted/filled/closed
# ---------------------------------------------------------------------------

def test_lifecycle_distinguishes_proposed_from_actually_executed(tmp_path):
    conn = _database(tmp_path)
    now = 1_700_000_000
    append_event(conn, "entry:XAUUSD:1699999500:range_breakout", "SIGNAL_CREATED", now - 500, "XAUUSD",
                 {"direction": "BUY", "raw_confidence": 0.4}, strategy_key="range_breakout")
    append_event(conn, "entry:XAUUSD:1699999500:range_breakout", "PROPOSAL_CREATED", now - 500, "XAUUSD",
                 {"direction": "BUY"}, strategy_key="range_breakout")
    conn.commit()
    registry = compute_panel(conn, "strategy_registry", now=now)
    row = next(s for s in registry["strategies"] if s["strategy_key"] == "range_breakout")
    assert row["lifecycle_stage"] == "SELECTED"
    assert row["last_position"] is None  # never labelled executed for a mere proposal

    _insert_position(conn, broker_position_id="pos-1", strategy_key="range_breakout", status="OPEN")
    conn.commit()
    registry2 = compute_panel(conn, "strategy_registry", now=now)
    row2 = next(s for s in registry2["strategies"] if s["strategy_key"] == "range_breakout")
    assert row2["lifecycle_stage"] == "FILLED"
    conn.close()


# ---------------------------------------------------------------------------
# Broker-verified attribution: partial fills, multiple closing deals, no
# double counting, and an explicit UNATTRIBUTED bucket.
# ---------------------------------------------------------------------------

def test_attribution_sums_every_deal_exactly_once_and_flags_partial_fills(tmp_path):
    conn = _database(tmp_path)
    now = 1_700_000_200
    _insert_position(conn, broker_position_id="pos-partial", strategy_key="microstructure_acceleration",
                      volume=0.2)
    _insert_deal(conn, broker_position_id="pos-partial", entry_type="IN", profit=0.0, commission=-0.3,
                 occurred_at=1_700_000_000)
    _insert_deal(conn, broker_position_id="pos-partial", entry_type="OUT", profit=3.0, commission=-0.2,
                 volume=0.1, occurred_at=1_700_000_050)
    _insert_deal(conn, broker_position_id="pos-partial", entry_type="OUT", profit=2.0, commission=-0.2,
                 volume=0.1, occurred_at=1_700_000_090)
    conn.commit()
    result = compute_panel(conn, "strategy_attribution", now=now)
    assert len(result["attributed_positions"]) == 1
    row = result["attributed_positions"][0]
    assert row["deal_count"] == 3
    assert row["closing_deal_count"] == 2
    assert row["partial_fill_or_multi_close"] is True
    assert row["gross_pnl"] == 5.0
    assert row["costs"] == -0.7
    assert row["net_pnl"] == 4.3
    assert result["unattributed_deals"] == []
    conn.close()


def test_attribution_reports_unmatched_broker_deals_as_unattributed_never_guessed(tmp_path):
    conn = _database(tmp_path)
    now = 1_700_000_200
    _insert_position(conn, broker_position_id="pos-known", strategy_key="momentum_continuation")
    _insert_deal(conn, broker_position_id="pos-known", entry_type="IN", profit=0.0)
    _insert_deal(conn, broker_position_id="pos-known", entry_type="OUT", profit=1.0)
    # A manual/unrelated broker deal on a position this system never tracked locally.
    _insert_deal(conn, broker_position_id="pos-manual-9999", entry_type="OUT", profit=42.0)
    conn.commit()
    result = compute_panel(conn, "strategy_attribution", now=now)
    assert len(result["attributed_positions"]) == 1
    assert len(result["unattributed_deals"]) == 1
    assert result["unattributed_deals"][0]["broker_position_id"] == "pos-manual-9999"
    conn.close()


# ---------------------------------------------------------------------------
# Performance: DEMO/PAPER/BACKTEST are separate, never pooled; missing
# costs never fabricated; sample size is real.
# ---------------------------------------------------------------------------

def test_performance_keeps_demo_paper_and_backtest_as_separate_evidence(tmp_path):
    conn = _database(tmp_path)
    now = 1_700_000_500
    _insert_position(conn, broker_position_id="pos-demo-1", strategy_key="volatility_expansion")
    _insert_deal(conn, broker_position_id="pos-demo-1", entry_type="IN", profit=0.0, commission=0.0)
    _insert_deal(conn, broker_position_id="pos-demo-1", entry_type="OUT", profit=7.0, commission=-1.0)
    conn.execute(
        "INSERT INTO paper_session_state (session_key, canonical_symbol, resolution, equity, "
        "created_at_utc, updated_at_utc) VALUES (?, ?, ?, ?, ?, ?)",
        ("PAPER:XAUUSD:M5:t", "XAUUSD", "M5", 10025.0, now, now),
    )
    conn.execute(
        "INSERT INTO paper_trades (session_key, canonical_symbol, strategy_key, direction, entry_time_utc, "
        "entry_price, volume, initial_monetary_risk, entry_regime, exit_time_utc, exit_price, exit_reason, "
        "realized_pnl, total_cost, origin, recorded_at_utc) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("PAPER:XAUUSD:M5:t", "XAUUSD", "volatility_expansion", "BUY", now - 3600, 2000.0, 0.01, 2.5,
         "TRENDING", now - 1800, 2002.0, "TARGET", 25.0, 1.0, "PAPER_LIVE_DATA", now),
    )
    conn.execute(
        "INSERT INTO datasets (dataset_id, created_at_utc, canonical_symbol, resolution, strategies_json, "
        "origin, feature_schema_version, row_count, excluded_row_count, range_start_utc, range_end_utc, "
        "checksum) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("dataset-1", now, "GBPJPY", "M5", "[]", "TEST", 1, 100, 0, now - 100000, now, "checksum-1"),
    )
    conn.execute(
        "INSERT INTO backtest_runs (run_id, created_at_utc, canonical_symbol, resolution, dataset_id, "
        "run_type, range_start_utc, range_end_utc, config_json, trade_count, gross_pnl, net_pnl, "
        "total_cost, metrics_json, origin) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("run-1", now, "GBPJPY", "M5", "dataset-1", "BACKTEST", now - 100000, now, "{}", 1, 5.0, 4.0,
         1.0, "{}", "TEST"),
    )
    conn.execute(
        "INSERT INTO backtest_trades (run_id, canonical_symbol, strategy_key, direction, entry_time_utc, "
        "entry_price, exit_time_utc, exit_price, exit_reason, volume, initial_monetary_risk, realized_r, "
        "realized_pnl, total_cost, entry_regime, exit_regime, strategy_version, gross_pnl) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("run-1", "GBPJPY", "volatility_expansion", "SELL", now - 90000, 190.0, now - 89000, 189.5,
         "target", 0.1, 5.0, 0.5, 4.0, 1.0, "RANGE", "RANGE", 1, 5.0),
    )
    conn.commit()
    result = compute_panel(conn, "strategy_performance", now=now)
    assert result["sample_sizes"] == {"demo": 1, "paper": 1, "backtest": 1}
    demo = result["demo_closed_trades"][0]
    assert demo["gross_pnl"] == 7.0 and demo["costs"] == -1.0 and demo["net_pnl"] == 6.0
    assert result["paper_trades"][0]["origin"] == "PAPER_LIVE_DATA"
    assert result["backtest_trades"][0]["run_id"] == "run-1"
    conn.close()


def test_performance_never_crashes_on_missing_cost_or_r_fields(tmp_path):
    conn = _database(tmp_path)
    now = 1_700_000_500
    conn.execute(
        "INSERT INTO paper_session_state (session_key, canonical_symbol, resolution, equity, "
        "created_at_utc, updated_at_utc) VALUES (?, ?, ?, ?, ?, ?)",
        ("PAPER:BTCUSD:M5:t", "BTCUSD", "M5", 9995.0, now, now),
    )
    conn.execute(
        "INSERT INTO paper_trades (session_key, canonical_symbol, strategy_key, direction, entry_time_utc, "
        "entry_price, volume, initial_monetary_risk, entry_regime, exit_time_utc, exit_price, exit_reason, "
        "realized_pnl, total_cost, realized_r, strategy_version, cost_provenance, gross_pnl, origin, "
        "recorded_at_utc) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("PAPER:BTCUSD:M5:t", "BTCUSD", "statistical_reversion", "SELL", now - 3600, 80000.0, 0.01, 2.5,
         "RANGE", now - 1800, 79950.0, "STOP", -5.0, 0.0, None, None, None, None, "PAPER_LIVE_DATA", now),
    )
    conn.commit()
    result = compute_panel(conn, "strategy_performance", now=now)
    assert result["status"] == "OK"
    row = result["paper_trades"][0]
    assert row["realized_r"] is None and row["strategy_version"] is None and row["cost_provenance"] is None
    assert row["gross_pnl"] is None
    conn.close()


# ---------------------------------------------------------------------------
# Page: new nav item, custom renderers present, and every existing
# no-mutating-control safety assertion still holds.
# ---------------------------------------------------------------------------

def test_page_exposes_strategy_lab_without_any_mutating_control():
    assert "Strategy Lab" in PAGE_HTML
    assert "strategy_registry" in PAGE_HTML and "strategy_performance" in PAGE_HTML
    assert "stratPerformancePanel" in PAGE_HTML
    assert "review-only" in PAGE_HTML.lower() or "review only" in PAGE_HTML.lower()
    # Same safety properties the base dashboard already enforces.
    assert "innerHTML" not in PAGE_HTML and "outerHTML" not in PAGE_HTML
    assert "eval(" not in PAGE_HTML
    assert "http://" not in PAGE_HTML.replace('"ws://"', "")
    assert "https://" not in PAGE_HTML
    assert "order_send" not in PAGE_HTML
    assert "kill-switch clear" not in PAGE_HTML
    assert '"@media(max-width:1080px)"'.strip('"') in PAGE_HTML
    assert "@media(max-width:740px)" in PAGE_HTML
