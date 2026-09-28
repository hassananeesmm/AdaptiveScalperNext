"""Strategy Lab evidence engine (dashboard/strategy_lab.py): durable-chain
trade attribution, source classification, partial-fill / multi-exit
accounting, broker-deal population reconciliation, honest metrics, strict
DEMO / PAPER / BACKTEST separation, filters, pagination, the runtime funnel
and the read-only HTTP surface. Temporary SQLite only; never MT5."""

from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient

from adaptive_scalper.dashboard import strategy_lab as sl
from adaptive_scalper.dashboard.app import create_app
from adaptive_scalper.journal.events import append_event
from adaptive_scalper.persistence.database import applied_versions, connect, migrate

LOGIN, SERVER, MAGIC = 5001, "Broker-Demo", 240924
T0 = 1_790_000_000
ACTIVE = {"momentum_continuation", "pullback_continuation", "range_breakout", "statistical_reversion",
          "volatility_expansion", "microstructure_acceleration"}


@pytest.fixture(autouse=True)
def _fresh_cache():
    sl.reset_cache()
    yield
    sl.reset_cache()


@pytest.fixture
def db(tmp_path):
    conn = connect(tmp_path / "lab.sqlite3")
    migrate(conn)
    conn.execute("INSERT INTO runtime_state (key, value_json, updated_at_utc) VALUES ('live_telemetry', ?, ?)",
                 (json.dumps({"account": {"currency": "USD", "trade_mode": "DEMO"}}), T0))
    conn.commit()
    yield conn
    conn.close()


# ---------------------------------------------------------------------------
# fixtures that write the exact records the DEMO runtime writes
# ---------------------------------------------------------------------------
def broker_deal(conn, ticket, *, position, entry, dtype=0, volume=0.1, price=100.0, profit=0.0,
                commission=0.0, swap=0.0, fee=0.0, magic=MAGIC, order=None, t=T0, reason=None,
                symbol="BTCUSD", comment="", login=LOGIN):
    conn.execute(
        "INSERT INTO broker_account_deals (login, server, ticket, order_ticket, time_utc, type, entry, magic, "
        "position_id, volume, price, commission, swap, profit, fee, symbol, comment, reason) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (login, SERVER, ticket, order if order is not None else ticket, t, dtype, entry, magic, position, volume,
         price, commission, swap, profit, fee, symbol, comment, reason))


def local_deal(conn, ticket, *, position, entry_type, volume=0.1, price=100.0, profit=0.0, commission=0.0,
               swap=0.0, fee=0.0, magic=MAGIC, order_ticket=None, t=T0, order_id=None):
    conn.execute(
        "INSERT INTO deals (order_id, broker_deal_id, broker_position_id, price, volume, commission, swap, profit, "
        "occurred_at_utc, fee, entry_type, deal_type, broker_order_ticket, magic, comment) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'BUY', ?, ?, '')",
        (order_id, str(ticket), str(position), price, volume, commission, swap, profit, t, fee, entry_type,
         str(order_ticket or ticket), magic))


def runtime_trade(conn, *, position, strategy="microstructure_acceleration", version=1, symbol="BTCUSD",
                  bar=None, entry_order_ticket=None, risk=20.0, status="CLOSED", regime="RANGE",
                  proposal_strategy=None, context_strategy=None, write_context=True):
    """Signal -> proposal -> allowed -> order (SUBMITTED, FILLED) -> position -> entry context."""
    bar = bar if bar is not None else T0 + position
    chain = f"entry:{symbol}:{bar}:{strategy}"
    append_event(conn, chain, "SIGNAL_CREATED", bar, symbol, {"strategy_version": version, "regime": regime},
                 strategy_key=strategy)
    append_event(conn, chain, "PROPOSAL_CREATED", bar, symbol, {"reason": "test"},
                 strategy_key=proposal_strategy or strategy)
    append_event(conn, chain, "ENTRY_ALLOWED", bar, symbol, {"decision": "ALLOW"}, strategy_key=None)
    cur = conn.execute(
        "INSERT INTO orders (client_request_id, chain_key, canonical_symbol, broker_symbol, direction, "
        "requested_volume, stop_loss, take_profit, state, broker_order_id, broker_position_id, created_at_utc, "
        "updated_at_utc, requested_monetary_risk, filled_volume, magic) "
        "VALUES (?, ?, ?, ?, 'BUY', 0.1, 90, 115, 'FILLED', ?, ?, ?, ?, ?, 0.1, ?)",
        (f"req-{position}", chain, symbol, symbol, str(entry_order_ticket or position), str(position), bar, bar,
         risk, MAGIC))
    order_id = cur.lastrowid
    for frm, to in ((None, "PROPOSED"), ("PROPOSED", "SUBMITTED"), ("SUBMITTED", "ACCEPTED"), ("ACCEPTED", "FILLED")):
        conn.execute("INSERT INTO order_state_transitions (order_id, from_state, to_state, occurred_at_utc) "
                     "VALUES (?, ?, ?, ?)", (order_id, frm, to, bar))
    conn.execute(
        "INSERT INTO positions (broker_position_id, canonical_symbol, direction, volume, entry_price, "
        "initial_monetary_risk, strategy_key, entry_order_id, status, opened_at_utc, closed_at_utc) "
        "VALUES (?, ?, 'BUY', 0.1, 100.0, ?, ?, ?, ?, ?, ?)",
        (str(position), symbol, risk, strategy, order_id, status, bar, bar + 300 if status == "CLOSED" else None))
    if write_context:
        conn.execute(
            "INSERT INTO position_entry_context (broker_position_id, canonical_symbol, strategy_key, strategy_version, "
            "direction, entry_regime, raw_confidence, stop_distance_price, target_distance_price, "
            "signal_bar_time_utc, chain_key, recorded_at_utc) VALUES (?, ?, ?, ?, 'BUY', ?, 0.8, 10, 15, ?, ?, ?)",
            (str(position), symbol, context_strategy or strategy, version, regime, bar, chain, bar))
    return order_id


def closed_trade(conn, position, *, profit, commission=-0.5, strategy="microstructure_acceleration", version=1,
                 t=None, symbol="BTCUSD", regime="RANGE"):
    t = t if t is not None else T0 + position
    runtime_trade(conn, position=position, strategy=strategy, version=version, symbol=symbol, bar=t, regime=regime)
    broker_deal(conn, position * 10, position=position, entry=0, order=position, t=t, commission=commission,
                symbol=symbol)
    broker_deal(conn, position * 10 + 1, position=position, entry=1, dtype=1, order=position * 10 + 1,
                t=t + 300, profit=profit, commission=commission, magic=MAGIC, symbol=symbol)


def trade(ledger, pid):
    return next(t for t in ledger["trades"] if t["trade_id"] == str(pid))


# ---------------------------------------------------------------------------
# attribution
# ---------------------------------------------------------------------------
def test_full_durable_chain_attributes_the_trade_to_its_strategy(db):
    closed_trade(db, 1, profit=12.0)
    db.commit()
    t = trade(sl.build_demo_ledger(db), 1)
    assert t["source_class"] == sl.ATTRIBUTED and t["strategy_key"] == "microstructure_acceleration"
    assert t["strategy_version"] == 1 and t["regime"] == "RANGE"
    assert all(c["passed"] for c in t["attribution_checks"]) and len(t["attribution_checks"]) == 4


@pytest.mark.parametrize("breakage", ["proposal_mismatch", "context_mismatch", "ticket_mismatch", "non_runtime_chain"])
def test_any_broken_chain_link_is_unattributed_never_guessed(db, breakage):
    kwargs = {}
    if breakage == "proposal_mismatch":
        kwargs["proposal_strategy"] = "range_breakout"
    if breakage == "context_mismatch":
        kwargs["context_strategy"] = "range_breakout"
    if breakage == "ticket_mismatch":
        kwargs["entry_order_ticket"] = 999999
    runtime_trade(db, position=7, **kwargs)
    if breakage == "non_runtime_chain":
        db.execute("UPDATE orders SET chain_key = 'XAUUSD-legacy-chain' WHERE broker_position_id = '7'")
    broker_deal(db, 70, position=7, entry=0, order=7)
    broker_deal(db, 71, position=7, entry=1, dtype=1, profit=5.0)
    db.commit()
    t = trade(sl.build_demo_ledger(db), 7)
    assert t["source_class"] == sl.ASN_UNATTRIBUTED
    assert t["strategy_key"] is None and t["attribution"] == "UNATTRIBUTED"
    assert any(not c["passed"] for c in t["attribution_checks"])


def test_same_symbol_direction_time_or_comment_never_imply_strategy(db):
    closed_trade(db, 1, profit=3.0)
    # An identical-looking broker trade with our magic and a suggestive comment but NO local record.
    broker_deal(db, 500, position=50, entry=0, t=T0 + 1, comment="ASN microstructure_acceleration")
    broker_deal(db, 501, position=50, entry=1, dtype=1, t=T0 + 301, profit=3.0)
    db.commit()
    t = trade(sl.build_demo_ledger(db), 50)
    assert t["source_class"] == sl.ASN_UNATTRIBUTED and t["strategy_key"] is None


def test_source_classification_manual_external_and_unknown(db):
    broker_deal(db, 1, position=100, entry=0, magic=0, reason=0)            # CLIENT -> MANUAL
    broker_deal(db, 2, position=100, entry=1, dtype=1, magic=0, reason=0, profit=1.0)
    broker_deal(db, 3, position=200, entry=0, magic=770115)                  # foreign EA
    broker_deal(db, 4, position=200, entry=1, dtype=1, magic=770115, profit=-2.0, reason=4)
    broker_deal(db, 5, position=300, entry=0, magic=0)                       # no reason recorded
    broker_deal(db, 6, position=300, entry=1, dtype=1, magic=0, profit=4.0)
    db.commit()
    ledger = sl.build_demo_ledger(db)
    assert trade(ledger, 100)["source_class"] == sl.MANUAL
    assert trade(ledger, 100)["exit_reason"] == "MANUAL_CLOSE"
    assert trade(ledger, 200)["source_class"] == sl.EXTERNAL_EXPERT
    assert trade(ledger, 200)["exit_reason"] == "BROKER_STOP_LOSS"
    assert trade(ledger, 300)["source_class"] == sl.UNKNOWN_SOURCE
    assert all(trade(ledger, p)["strategy_key"] is None for p in (100, 200, 300))


def test_exit_reason_comment_is_only_a_labelled_hint(db):
    closed_trade(db, 1, profit=-3.0)
    db.execute("UPDATE broker_account_deals SET comment = '[sl 99.5]' WHERE ticket = 11")
    db.commit()
    t = trade(sl.build_demo_ledger(db), 1)
    assert t["exit_reason"] == "BROKER_STOP_LOSS" and "hint" in t["exit_reason_evidence"]


# ---------------------------------------------------------------------------
# accounting
# ---------------------------------------------------------------------------
def test_multiple_entry_fills_and_multiple_exits_sum_each_deal_once(db):
    runtime_trade(db, position=9)
    broker_deal(db, 90, position=9, entry=0, order=9, volume=0.06, price=100, commission=-0.3)
    broker_deal(db, 91, position=9, entry=0, order=9, volume=0.04, price=101, commission=-0.2, t=T0 + 1)
    broker_deal(db, 92, position=9, entry=1, dtype=1, volume=0.05, profit=4.0, commission=-0.25, t=T0 + 100)
    broker_deal(db, 93, position=9, entry=1, dtype=1, volume=0.05, profit=6.0, commission=-0.25, t=T0 + 200)
    # the runtime also recorded two of these deals itself: they must not be double counted
    local_deal(db, 90, position=9, entry_type="IN", volume=0.06, commission=-0.3)
    local_deal(db, 93, position=9, entry_type="OUT", volume=0.05, profit=6.0, commission=-0.25, t=T0 + 200)
    db.commit()
    ledger = sl.build_demo_ledger(db)
    t = trade(ledger, 9)
    assert t["status"] == "CLOSED" and t["entry_deal_count"] == 2 and t["exit_deal_count"] == 2
    assert t["entry_volume"] == pytest.approx(0.10) and t["entry_price"] == pytest.approx(100.4)
    assert t["gross_pnl"] == pytest.approx(10.0) and t["commission"] == pytest.approx(-1.0)
    assert t["net_pnl"] == pytest.approx(9.0) == pytest.approx(t["realized_net_pnl"])
    assert ledger["deal_source_discrepancies"] == []


def test_partial_close_allocates_entry_costs_by_closed_volume(db):
    runtime_trade(db, position=11, status="OPEN")
    broker_deal(db, 110, position=11, entry=0, order=11, volume=1.0, commission=-10.0)
    broker_deal(db, 111, position=11, entry=1, dtype=1, volume=0.4, profit=20.0, commission=-4.0, t=T0 + 50)
    db.commit()
    t = trade(sl.build_demo_ledger(db), 11)
    assert t["status"] == "PARTIALLY_CLOSED"
    # realized = exit profit + exit commission + 40 % of the entry commission
    assert t["realized_net_pnl"] == pytest.approx(20.0 - 4.0 - 4.0)
    assert t["open_volume_entry_costs"] == pytest.approx(-6.0)
    assert t["realized_net_pnl"] + t["open_volume_entry_costs"] == pytest.approx(t["net_pnl"])
    m = sl.compute_metrics([t])
    assert m["closed_trades"] == 0 and m["sample_status"] == "NO_CLOSED_TRADES"  # a partial close is not a closed trade


def test_open_position_is_never_a_completed_trade_and_has_no_realized_pnl(db):
    runtime_trade(db, position=12, status="OPEN")
    broker_deal(db, 120, position=12, entry=0, order=12, commission=-0.7)
    db.commit()
    s = sl.summary(db, "DEMO", {}, {})
    row = next(r for r in s["strategies"] if r["strategy_key"] == "microstructure_acceleration")
    assert row["closed_trades"] == 0 and row["open_positions"] == 1 and row["win_rate"] is None
    assert row["open_exposure_initial_risk"] == pytest.approx(20.0)
    t = trade(sl.build_demo_ledger(db), 12)
    assert t["realized_net_pnl"] == pytest.approx(0.0) and t["open_volume_entry_costs"] == pytest.approx(-0.7)


def _telemetry(conn, positions, *, age=0, connected=True):
    conn.execute("UPDATE runtime_state SET value_json = ?, updated_at_utc = ? WHERE key = 'live_telemetry'",
                 (json.dumps({"account": {"currency": "USD", "trade_mode": "DEMO"},
                              "terminal": {"connected": connected}, "positions": positions}),
                  int(time.time()) - age))
    conn.commit()


def _row(s, key="microstructure_acceleration"):
    return next(r for r in s["strategies"] if r["strategy_key"] == key)


def test_unrealized_pnl_is_live_broker_floating_and_never_realized(db):
    runtime_trade(db, position=12, status="OPEN")
    broker_deal(db, 120, position=12, entry=0, order=12, commission=-0.7)
    closed_trade(db, 1, profit=4.0, commission=0.0)
    db.commit()
    _telemetry(db, [{"broker_position_id": "12", "floating_pnl": -3.25}])
    row = _row(sl.summary(db, "DEMO", {}, {}))
    assert row["unrealized_pnl"] == pytest.approx(-3.25) and row["unrealized_note"] is None
    assert row["closed_trades"] == 1 and row["net_pnl"] == pytest.approx(4.0)  # floating never enters realized
    idle = _row(sl.summary(db, "DEMO", {}, {}), "range_breakout")
    assert idle["unrealized_pnl"] is None and idle["unrealized_note"] == "NO_OPEN_POSITIONS"


@pytest.mark.parametrize("age,connected,positions,note", [
    (60, True, [{"broker_position_id": "12", "floating_pnl": 1.0}], "NO_FRESH_BROKER_SAMPLE"),
    (0, False, [{"broker_position_id": "12", "floating_pnl": 1.0}], "NO_FRESH_BROKER_SAMPLE"),
    (0, True, [{"broker_position_id": "99", "floating_pnl": 1.0}], "POSITION_NOT_IN_BROKER_SAMPLE"),
    # positions_get failed (Mt5QueryError) while the terminal stayed connected: unknown, never a crash or "flat"
    (0, True, None, "NO_FRESH_BROKER_SAMPLE"),
])
def test_unrealized_pnl_is_not_available_rather_than_guessed(db, age, connected, positions, note):
    runtime_trade(db, position=12, status="OPEN")
    broker_deal(db, 120, position=12, entry=0, order=12)
    db.commit()
    _telemetry(db, positions, age=age, connected=connected)
    row = _row(sl.summary(db, "DEMO", {}, {}))
    assert row["unrealized_pnl"] is None and row["unrealized_note"] == note


def test_gross_profit_and_loss_split_winners_and_losers_in_summary_and_csv(db):
    closed_trade(db, 1, profit=10.0, commission=-1.0)
    closed_trade(db, 2, profit=-20.0, commission=-1.0)
    closed_trade(db, 3, profit=6.0, commission=-1.0)
    db.commit()
    s = sl.summary(db, "DEMO", {}, {})
    row = _row(s)
    assert row["gross_profit_of_winners"] == pytest.approx(8.0 + 4.0)  # net of both commissions
    assert row["gross_loss_of_losers"] == pytest.approx(-22.0)
    assert row["profit_factor"] == pytest.approx(12.0 / 22.0)
    assert row["net_pnl"] == pytest.approx(row["gross_profit_of_winners"] + row["gross_loss_of_losers"])
    csv_row = sl.comparison_csv_rows(s, ["microstructure_acceleration"])[0]
    assert csv_row["gross_profit_of_winners"] == pytest.approx(12.0) and csv_row["currency"] == "USD"
    assert "unrealized_pnl" in csv_row


def test_broker_history_and_local_record_disagreement_is_reported(db):
    closed_trade(db, 13, profit=8.0)
    local_deal(db, 131, position=13, entry_type="OUT", profit=7.5, commission=-0.5)
    db.commit()
    ledger = sl.build_demo_ledger(db)
    assert ledger["deal_source_discrepancies"] == [
        {"ticket": "131", "field": "profit", "broker_history": 8.0, "local_runtime_record": 7.5,
         "used": "broker_history"}]


def test_reconciliation_attributed_plus_unattributed_plus_non_trade_equals_population(db):
    broker_deal(db, 1, position=0, entry=0, dtype=2, profit=10_000.0, magic=0)       # deposit
    closed_trade(db, 1, profit=12.0)
    closed_trade(db, 2, profit=-7.0)
    broker_deal(db, 30, position=300, entry=0, magic=770115, commission=-1.0)
    broker_deal(db, 31, position=300, entry=1, dtype=1, magic=770115, profit=-3.0, commission=-1.0)
    runtime_trade(db, position=4, status="OPEN")
    broker_deal(db, 40, position=4, entry=0, order=4, volume=1.0, commission=-2.0)
    broker_deal(db, 41, position=4, entry=1, dtype=1, volume=0.5, profit=5.0, commission=-1.0)
    local_deal(db, 999, position=5, entry_type="IN", magic=MAGIC)  # runtime-only record, not yet imported
    db.commit()
    s = sl.summary(db, "DEMO", {}, {})
    r = s["reconciliation"]
    expected = 10_000 + (12 - 1) + (-7 - 1) + (-3 - 2) + (5 - 3)
    assert r["population_net_sql"] == pytest.approx(expected)
    assert r["population_net_ledger"] == pytest.approx(expected)
    assert r["reconciles"] is True and r["ledger_vs_sql_deal_count_discrepancy"] == 0
    assert r["non_trade_deals_net"] == pytest.approx(10_000)
    assert r["closed_count_check"]["attributed_closed_positions"] == 2
    classes = r["by_source_class"]
    assert classes[sl.ATTRIBUTED]["positions"] == 3
    assert classes[sl.EXTERNAL_EXPERT]["net_all_deals"] == pytest.approx(-5)
    assert classes[sl.ASN_UNATTRIBUTED]["positions"] == 1       # the runtime-only deal of position 5
    assert classes[sl.ATTRIBUTED]["open_volume_entry_costs"] == pytest.approx(-1.0)


# ---------------------------------------------------------------------------
# metrics
# ---------------------------------------------------------------------------
def test_all_six_strategies_present_and_no_trade_strategies_say_no_closed_trades(db):
    s = sl.summary(db, "DEMO", {}, {})
    assert {r["strategy_key"] for r in s["strategies"]} == ACTIVE
    for row in s["strategies"]:
        assert row["sample_status"] == "NO_CLOSED_TRADES" and row["win_rate"] is None
        assert row["profit_factor"] is None and row["profit_factor_note"] == "NO_CLOSED_TRADES"
    assert set(s["retired_strategies"]) == {"failed_breakout_fade", "support_resistance_reaction"}
    assert not ACTIVE & set(s["retired_strategies"])


def test_zero_loss_profit_factor_is_undefined_not_infinite(db):
    closed_trade(db, 1, profit=5.0, commission=0.0)
    closed_trade(db, 2, profit=3.0, commission=0.0)
    db.commit()
    row = next(r for r in sl.summary(db, "DEMO", {}, {})["strategies"] if r["closed_trades"])
    assert row["wins"] == 2 and row["losses"] == 0 and row["win_rate"] == 1.0
    assert row["profit_factor"] is None and row["profit_factor_note"].startswith("NO_LOSSES")


def test_metrics_wins_losses_breakeven_r_drawdown(db):
    closed_trade(db, 1, profit=10.0, commission=0.0)
    closed_trade(db, 2, profit=-20.0, commission=0.0)
    closed_trade(db, 3, profit=0.0, commission=0.0)
    closed_trade(db, 4, profit=5.0, commission=0.0)
    db.commit()
    m = sl.compute_metrics(sl.build_demo_ledger(db)["trades"])
    assert (m["wins"], m["losses"], m["breakevens"]) == (2, 1, 1)
    assert m["profit_factor"] == pytest.approx(15 / 20)
    assert m["avg_r"] == pytest.approx((10 - 20 + 0 + 5) / 20 / 4)
    assert m["max_drawdown_closed_trade_basis"] == pytest.approx(20.0)
    assert m["expectancy_per_trade"] == pytest.approx(-5 / 4)


def _dataset_and_run(db, run_id="run-1", provenance=None):
    db.execute("INSERT OR IGNORE INTO datasets (dataset_id, created_at_utc, canonical_symbol, resolution, strategies_json, "
               "origin, feature_schema_version, row_count, range_start_utc, range_end_utc, checksum) "
               "VALUES ('ds', 1, 'BTCUSD', 'M5', '[]', 'TEST', 1, 1, 1, 2, 'x')")
    db.execute("INSERT INTO backtest_runs (run_id, created_at_utc, canonical_symbol, resolution, dataset_id, run_type, "
               "range_start_utc, range_end_utc, config_json, trade_count, gross_pnl, net_pnl, total_cost, metrics_json, "
               "origin, cost_provenance) VALUES (?, 5, 'BTCUSD', 'M5', 'ds', 'BACKTEST', 1, 2, '{}', 1, 0, 0, 0, "
               "'{}', 'TEST', ?)", (run_id, provenance))


def test_unknown_costs_stay_unknown_never_zero(db):
    _dataset_and_run(db, provenance="UNVERIFIED_ASSUMPTION")
    db.execute("INSERT INTO backtest_trades (run_id, canonical_symbol, strategy_key, direction, entry_time_utc, entry_price, "
               "exit_time_utc, exit_price, exit_reason, volume, initial_monetary_risk, realized_r, realized_pnl, total_cost) "
               "VALUES ('run-1', 'BTCUSD', 'range_breakout', 'SELL', 100, 1.0, 200, 0.9, 'TP', 1.0, 10.0, 0.5, 5.0, 1.0)")
    db.commit()
    row = next(r for r in sl.summary(db, "BACKTEST", {}, {})["strategies"] if r["strategy_key"] == "range_breakout")
    assert row["closed_trades"] == 1 and row["net_pnl"] == pytest.approx(5.0)
    assert row["gross_pnl"] is None and row["commission"] is None   # not recorded -> N/A, never 0


# ---------------------------------------------------------------------------
# evidence separation, filters, versions, pagination, cache
# ---------------------------------------------------------------------------
def _paper_and_backtest(db):
    db.execute("INSERT INTO paper_session_state (session_key, canonical_symbol, resolution, equity, created_at_utc, "
               "updated_at_utc) VALUES ('v2:BTCUSD', 'BTCUSD', 'M5', 10000, 1, 1)")
    db.execute("INSERT INTO paper_trades (session_key, canonical_symbol, strategy_key, direction, entry_time_utc, "
               "entry_price, volume, initial_monetary_risk, entry_regime, exit_time_utc, exit_price, exit_reason, "
               "realized_pnl, total_cost, recorded_at_utc, gross_pnl) VALUES ('v2:BTCUSD', 'BTCUSD', "
               "'statistical_reversion', 'BUY', 100, 1, 1, 10, 'RANGE', 200, 2, 'TP', 111.0, 1.0, 200, 112.0)")
    _dataset_and_run(db)
    db.execute("INSERT INTO backtest_trades (run_id, canonical_symbol, strategy_key, direction, entry_time_utc, entry_price, "
               "exit_time_utc, exit_price, exit_reason, volume, initial_monetary_risk, realized_pnl, total_cost) "
               "VALUES ('run-1', 'BTCUSD', 'volatility_expansion', 'SELL', 100, 1.0, 200, 0.9, 'TP', 1.0, 10.0, -222.0, 1.0)")


def test_demo_paper_and_backtest_are_never_pooled(db):
    closed_trade(db, 1, profit=7.0, commission=0.0)
    _paper_and_backtest(db)
    db.commit()
    demo = sl.summary(db, "DEMO", {}, {})
    paper = sl.summary(db, "PAPER", {}, {})
    bt = sl.summary(db, "BACKTEST", {}, {})

    def net(s, k):
        return next(r for r in s["strategies"] if r["strategy_key"] == k)["net_pnl"]

    assert net(demo, "microstructure_acceleration") == pytest.approx(7.0)
    assert net(demo, "statistical_reversion") == 0.0 and net(demo, "volatility_expansion") == 0.0
    assert net(paper, "statistical_reversion") == pytest.approx(111.0) and net(paper, "microstructure_acceleration") == 0.0
    assert net(bt, "volatility_expansion") == pytest.approx(-222.0) and bt["run_id"] == "run-1"
    assert "reconciliation" in demo and "reconciliation" not in paper and "reconciliation" not in bt
    assert "never one pooled portfolio" in paper["evidence_note"]
    with pytest.raises(ValueError):
        sl.summary(db, "LIVE", {}, {})


def test_filters_strategy_version_date_and_unattributed(db):
    closed_trade(db, 1, profit=5.0, version=1, t=T0)
    closed_trade(db, 2, profit=-2.0, version=2, t=T0 + 86400 * 3)
    broker_deal(db, 90, position=90, entry=0, magic=0)
    broker_deal(db, 91, position=90, entry=1, dtype=1, magic=0, profit=1.0)
    db.commit()
    trades = sl.build_demo_ledger(db)["trades"]
    assert {t["trade_id"] for t in sl.apply_filters(trades, {"version": "2"})} == {"2"}
    assert {t["trade_id"] for t in sl.apply_filters(trades, {"date_from": T0 + 86400})} == {"2"}
    assert {t["trade_id"] for t in sl.apply_filters(trades, {"strategy": "UNATTRIBUTED"})} == {"90"}
    s = sl.summary(db, "DEMO", {"version": "1"}, {})
    row = next(r for r in s["strategies"] if r["strategy_key"] == "microstructure_acceleration")
    assert row["closed_trades"] == 1 and row["versions_in_evidence"] == [1]
    all_rows = sl.summary(db, "DEMO", {}, {})
    row2 = next(r for r in all_rows["strategies"] if r["strategy_key"] == "microstructure_acceleration")
    assert row2["versions_in_evidence"] == [1, 2]
    # a losing trade is never dropped by an unrelated filter
    kept = sl.apply_filters(trades, {"symbol": "BTCUSD", "strategy": "microstructure_acceleration"})
    assert {t["trade_id"] for t in kept} == {"1", "2"}


def test_large_history_is_paginated_and_fast(db):
    for p in range(1, 1201):
        broker_deal(db, p * 10, position=p, entry=0, magic=770115, t=T0 + p)
        broker_deal(db, p * 10 + 1, position=p, entry=1, dtype=1, magic=770115, profit=(-1) ** p, t=T0 + p + 60)
    db.commit()
    started = time.perf_counter()
    page = sl.trades_page(db, "DEMO", {}, {}, page=3, page_size=50)
    assert time.perf_counter() - started < 5.0
    assert page["total"] == 1200 and page["pages"] == 24 and len(page["trades"]) == 50
    assert page["trades"][0]["trade_id"] == str(1200 - 100)          # newest first
    assert "attribution_checks" not in page["trades"][0]
    assert sl.trades_page(db, "DEMO", {}, {}, page=999, page_size=500)["page_size"] == 200  # bounded


def test_cache_is_invalidated_when_new_broker_evidence_arrives(db):
    closed_trade(db, 1, profit=5.0)
    db.commit()
    first, fresh1 = sl.cached_demo_ledger(db)
    again, fresh2 = sl.cached_demo_ledger(db)
    assert fresh1["cache"] == "MISS" and fresh2["cache"] == "HIT" and again is first
    closed_trade(db, 2, profit=6.0)
    db.commit()
    updated, fresh3 = sl.cached_demo_ledger(db)
    assert fresh3["cache"] == "MISS" and len(updated["trades"]) == 2


# ---------------------------------------------------------------------------
# funnel: runtime chains only; blocked order rows are not submissions
# ---------------------------------------------------------------------------
def test_funnel_counts_runtime_chains_only_and_never_counts_unsent_orders(db):
    closed_trade(db, 1, profit=1.0)
    # a legacy / CLI chain signal must not inflate the runtime funnel
    append_event(db, "XAUUSD-statistical_reversion-1-abc", "SIGNAL_CREATED", T0, "XAUUSD", {},
                 strategy_key="statistical_reversion")
    # a runtime proposal whose order row was created but blocked before any broker call
    chain = f"entry:BTCUSD:{T0 + 50}:statistical_reversion"
    append_event(db, chain, "SIGNAL_CREATED", T0 + 50, "BTCUSD", {}, strategy_key="statistical_reversion")
    append_event(db, chain, "PROPOSAL_CREATED", T0 + 50, "BTCUSD", {}, strategy_key="statistical_reversion")
    append_event(db, chain, "ENTRY_BLOCKED", T0 + 50, "BTCUSD", {"decision": "BLOCK"}, strategy_key=None)
    cur = db.execute("INSERT INTO orders (client_request_id, chain_key, canonical_symbol, broker_symbol, direction, "
                     "requested_volume, state, created_at_utc, updated_at_utc, magic) "
                     "VALUES ('blocked', ?, 'BTCUSD', 'BTCUSD', 'BUY', 0.1, 'PROPOSED', ?, ?, ?)",
                     (chain, T0 + 50, T0 + 50, MAGIC))
    db.execute("INSERT INTO order_state_transitions (order_id, from_state, to_state, occurred_at_utc) VALUES (?, NULL, "
               "'PROPOSED', ?)", (cur.lastrowid, T0 + 50))
    db.commit()
    f = sl.demo_funnel(db, None, None)
    rev = f["by_strategy"]["statistical_reversion"]
    assert rev["signals"] == 1 and rev["selected_proposals"] == 1 and rev["entry_blocked_chains"] == 1
    assert rev["orders_created"] == 1 and rev["orders_submitted"] == 0 and rev["orders_filled"] == 0
    assert rev["signal_to_order_conversion"] == 0.0 and rev["order_to_fill_conversion"] is None
    micro = f["by_strategy"]["microstructure_acceleration"]
    assert micro["orders_submitted"] == 1 and micro["orders_filled"] == 1 and micro["entry_allowed_chains"] == 1
    assert f["non_runtime_signal_events"] == 1


def test_trade_lifecycle_links_every_stage(db):
    closed_trade(db, 1, profit=4.0)
    db.execute("INSERT INTO execution_cost_observations (order_id, canonical_symbol, broker_symbol, direction, "
               "outcome_status, decided_at_utc, requested_volume, recorded_at_utc, spread_price, slippage_price, session) "
               "VALUES (1, 'BTCUSD', 'BTCUSD', 'BUY', 'FILLED', ?, 0.1, ?, 5.0, 0.5, 'LONDON')", (T0, T0))
    db.commit()
    lc = sl.trade_lifecycle(db, "DEMO", "1", {})
    assert lc["signal"]["strategy_key"] == "microstructure_acceleration"
    assert lc["selection"]["event_type"] == "PROPOSAL_CREATED"
    assert [p["event_type"] for p in lc["permission_checks"]] == ["ENTRY_ALLOWED"]
    assert lc["local_order"]["client_request_id"] == "req-1"
    assert [t["to_state"] for t in lc["order_transitions"]] == ["PROPOSED", "SUBMITTED", "ACCEPTED", "FILLED"]
    assert {d["entry"] for d in lc["broker_deals"]} == {"IN", "OUT"}
    assert lc["execution_cost_evidence"]["spread_price"] == 5.0 and lc["trade"]["session"] == "LONDON"
    assert lc["accounting"]["net_pnl"] == pytest.approx(3.0) and lc["accounting"]["currency"] == "USD"
    assert sl.trade_lifecycle(db, "DEMO", "does-not-exist", {}) is None


def test_strategy_detail_reads_the_actual_registered_implementation(db):
    d = sl.strategy_detail(db, "momentum_continuation")
    src = d["source"]
    assert set(src["regime_gate"]["eligible"]) == {"TRENDING_UP", "TRENDING_DOWN"}
    assert src["stop_atr_multiple"] is not None and "def evaluate" in src["entry_rules_source"]
    assert d["demo_metrics"]["sample_status"] == "NO_CLOSED_TRADES"
    micro = sl.strategy_detail(db, "microstructure_acceleration")["source"]["regime_gate"]["eligible"]
    assert "COMPRESSION" not in micro and "RANGE" in micro
    retired = sl.strategy_detail(db, "failed_breakout_fade")
    assert retired["registration_status"] == "RETIRED_PERMANENTLY" and retired["source"] is None
    assert sl.strategy_detail(db, "made_up_strategy") is None


# ---------------------------------------------------------------------------
# migration and HTTP surface
# ---------------------------------------------------------------------------
def test_migration_0029_is_additive_and_idempotent(tmp_path):
    conn = connect(tmp_path / "m.sqlite3")
    migrate(conn)
    assert 29 in applied_versions(conn)
    assert "reason" in {r[1] for r in conn.execute("PRAGMA table_info(broker_account_deals)")}
    assert migrate(conn) == []
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    conn.close()


def test_http_endpoints_are_read_only_and_validated(db, tmp_path):
    closed_trade(db, 1, profit=4.0)
    db.commit()
    client = TestClient(create_app(tmp_path / "lab.sqlite3"))
    s = client.get("/api/strategy-lab/summary?evidence=DEMO&date_from=2026-01-01&date_to=2027-12-31").json()
    assert s["reconciliation"]["reconciles"] is True and {r["strategy_key"] for r in s["strategies"]} == ACTIVE
    assert client.get("/api/strategy-lab/trades?evidence=DEMO&page=1&page_size=10").json()["total"] == 1
    assert client.get("/api/strategy-lab/trade/DEMO/1").json()["trade"]["strategy_key"] == "microstructure_acceleration"
    assert client.get("/api/strategy-lab/trade/DEMO/nope").status_code == 404
    assert client.get("/api/strategy-lab/strategy/range_breakout").status_code == 200
    assert client.get("/api/strategy-lab/strategy/nope").status_code == 404
    assert client.get("/api/strategy-lab/summary?evidence=LIVE").status_code == 400
    assert client.get("/api/strategy-lab/summary?date_from=yesterday").status_code == 400
    csv_text = client.get("/api/strategy-lab/export.csv?evidence=DEMO&keys=microstructure_acceleration").text
    assert csv_text.startswith("evidence,strategy_key") and csv_text.strip().count("\n") == 1
    for path in ("/api/strategy-lab/summary", "/api/strategy-lab/trades", "/api/strategy-lab/export.csv"):
        assert client.post(path).status_code == 405
        assert client.delete(path).status_code == 405
    lab_routes = [r for r in client.app.routes if "strategy-lab" in getattr(r, "path", "")]
    assert lab_routes and all(set(r.methods) <= {"GET", "HEAD"} for r in lab_routes)


def test_close_requests_carry_the_runtime_magic_for_broker_side_identity():
    from adaptive_scalper.position_management.manager import PositionReviewInput

    fields = PositionReviewInput.__dataclass_fields__
    assert fields["close_magic"].default == 0 and fields["close_comment"].default == ""
    import inspect

    from adaptive_scalper.runtime import demo
    src = inspect.getsource(demo)
    assert "close_magic=self.config.runtime.magic" in src
    from adaptive_scalper.position_management import manager
    assert "magic=inp.close_magic" in inspect.getsource(manager.review_position_once)


def test_registry_panel_counts_runtime_chains_only(db):
    from adaptive_scalper.dashboard.panels import compute_panel

    append_event(db, "XAUUSD-statistical_reversion-1789624200-71054815", "SIGNAL_CREATED", T0, "XAUUSD", {},
                 strategy_key="statistical_reversion")
    append_event(db, f"entry:BTCUSD:{T0}:statistical_reversion", "SIGNAL_CREATED", T0, "BTCUSD", {},
                 strategy_key="statistical_reversion")
    db.commit()
    panel = compute_panel(db, "strategy_registry", now=T0 + 10)
    row = next(s for s in panel["strategies"] if s["strategy_key"] == "statistical_reversion")
    assert row["counts_last_30d"]["signals_created"] == 1
    assert panel["non_runtime_signal_events_excluded"] == 1
