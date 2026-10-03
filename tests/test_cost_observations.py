"""DEMO execution cost observations (completion directive Phase 7)."""

from __future__ import annotations

import pytest

from adaptive_scalper.core.kill_switch import bootstrap as bootstrap_kill_switch
from adaptive_scalper.core.operator_authority import OperatorAuthority
from adaptive_scalper.costs.observations import (
    MIN_SAMPLES_FOR_EVIDENCE,
    news_proximity,
    session_label,
    summarize_observations,
    sweep_exit_costs,
)
from runtime_helpers import STEP, T0, FakeClock, build_engine, step
from edge_fixtures import FixtureValidatedProvider

START_AT = T0 + 60 * STEP + 10


@pytest.mark.parametrize("hour,label", [(0, "ASIA"), (6, "ASIA"), (7, "LONDON"), (13, "LONDON_NEW_YORK"),
                                        (18, "NEW_YORK"), (23, "LATE")])
def test_sessions(hour, label):
    assert session_label(hour) == label


def test_news_proximity():
    windows = ((1000, 2000), (5000, 6000))
    assert news_proximity(windows, 500) == (500, None)
    assert news_proximity(windows, 1500) == (0, None)
    assert news_proximity(windows, 3000) == (2000, 1000)
    assert news_proximity((), 3000) == (None, None)


def _demo(tmp_path):
    clock = FakeClock(START_AT)
    engine, conn, gateway = build_engine(tmp_path, mode="DEMO", clock=clock, edge_evidence=FixtureValidatedProvider())
    bootstrap_kill_switch(conn, OperatorAuthority("test-operator"), reason="test setup")
    engine.startup()
    return engine, conn, gateway, clock


def test_a_filled_demo_entry_records_its_execution_evidence(tmp_path):
    engine, conn, _, clock = _demo(tmp_path)
    step(engine, clock, seconds=STEP * 4, tick=4)
    rows = conn.execute("SELECT * FROM execution_cost_observations").fetchall()
    assert rows, "the DEMO pipeline filled at least one entry"
    row = rows[0]
    order = conn.execute("SELECT * FROM orders WHERE id = ?", (row["order_id"],)).fetchone()
    assert row["outcome_status"] == "FILLED" and row["fill_type"] == "FULL"
    assert row["filled_volume"] == pytest.approx(order["requested_volume"])
    assert row["bid"] > 0 and row["ask"] > row["bid"] and row["spread_price"] == pytest.approx(row["ask"] - row["bid"])
    assert row["requested_price"] == (row["ask"] if row["direction"] == "BUY" else row["bid"])
    assert row["fill_price"] is not None and row["slippage_price"] is not None
    assert row["broker_retcode"] == order["last_broker_retcode"] and row["deal_count"] >= 1
    assert row["estimated_total_price"] is not None and row["session"] and row["atr"] is not None
    assert row["broker_position_id"] is not None


def test_the_sweep_completes_observations_when_positions_close(tmp_path):
    engine, conn, _, clock = _demo(tmp_path)
    step(engine, clock, seconds=STEP * 4, tick=4)
    row = conn.execute("SELECT * FROM execution_cost_observations").fetchone()
    position_id = row["broker_position_id"]
    # pin a known state: position closed with one extra exit deal, observation not yet completed
    conn.execute("UPDATE execution_cost_observations SET exit_recorded_at_utc = NULL, exit_commission = NULL, "
                 "exit_fee = NULL, swap = NULL WHERE id = ?", (row["id"],))
    conn.execute("UPDATE positions SET status = 'CLOSED' WHERE broker_position_id = ?", (position_id,))
    conn.execute(
        "INSERT INTO deals (broker_deal_id, broker_position_id, price, volume, commission, swap, profit, fee, "
        "entry_type, deal_type, occurred_at_utc) VALUES ('exit-x', ?, 1, 0.01, -0.35, -0.10, 1.0, 0.05, 'OUT', "
        "'SELL', ?)", (position_id, START_AT),
    )
    conn.commit()
    assert sweep_exit_costs(conn, now_utc=START_AT + 1) >= 1
    expected = conn.execute(
        "SELECT SUM(commission), SUM(fee) FROM deals WHERE broker_position_id = ? AND entry_type != 'IN'",
        (position_id,)).fetchone()
    swap = conn.execute("SELECT SUM(swap) FROM deals WHERE broker_position_id = ?", (position_id,)).fetchone()[0]
    done = conn.execute("SELECT * FROM execution_cost_observations WHERE id = ?", (row["id"],)).fetchone()
    assert done["exit_commission"] == pytest.approx(expected[0]) and done["exit_fee"] == pytest.approx(expected[1])
    assert done["swap"] == pytest.approx(swap) and done["exit_recorded_at_utc"] == START_AT + 1
    sweep_exit_costs(conn, now_utc=START_AT + 2)  # idempotent: a completed row is never rewritten
    assert conn.execute("SELECT exit_recorded_at_utc FROM execution_cost_observations WHERE id = ?",
                        (row["id"],)).fetchone()[0] == START_AT + 1


def test_an_observation_failure_never_changes_the_order(tmp_path, monkeypatch):
    import adaptive_scalper.runtime.demo as demo_module

    def broken(*_a, **_k):
        raise RuntimeError("disk full")

    monkeypatch.setattr(demo_module, "record_entry_observation", broken)
    engine, conn, _, clock = _demo(tmp_path)
    step(engine, clock, seconds=STEP * 4, tick=4)
    filled_symbols = {r[0] for r in conn.execute(
        "SELECT canonical_symbol FROM entry_decisions WHERE stage = 'EXECUTION' AND decision = 'FILLED'")}
    assert filled_symbols
    failed = {r[0] for r in conn.execute(
        "SELECT canonical_symbol FROM runtime_events WHERE event = 'COST_OBSERVATION_FAILED'")}
    assert failed == filled_symbols
    assert conn.execute("SELECT COUNT(*) FROM execution_cost_observations").fetchone()[0] == 0


def test_a_summary_is_not_evidence_until_enough_closed_fills(tmp_path):
    engine, conn, _, clock = _demo(tmp_path)
    step(engine, clock, seconds=STEP * 4, tick=4)
    symbol = conn.execute("SELECT canonical_symbol FROM execution_cost_observations").fetchone()[0]
    summary = summarize_observations(conn, symbol)
    assert summary.filled >= 1 and summary.median_spread_price is not None
    assert not summary.sufficient and str(MIN_SAMPLES_FOR_EVIDENCE) in summary.detail


def test_the_observation_module_cannot_trade_or_write_config():
    import ast
    from pathlib import Path

    tree = ast.parse(Path("adaptive_scalper/costs/observations.py").read_text())
    imports = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert not any(m.startswith(("adaptive_scalper.gateway", "adaptive_scalper.execution", "adaptive_scalper.config",
                                 "adaptive_scalper.core")) for m in imports)
    calls = {n.func.attr for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    assert not calls & {"order_send", "order_check", "engage", "clear", "bootstrap"}
