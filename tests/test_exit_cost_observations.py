"""Exit-side execution cost evidence (migration 0031).

The close path stores the round-2 quote it already validated (no extra
broker call, no change to what is sent or how it settles); the off-hot-path
sweep turns each closed position's broker exit deals into one
`exit_cost_observations` row. A reference price comes only from evidence
(the close quote, the broker's own "[sl X]"/"[tp X]" trigger comment, or an
entry TP that is never moved); otherwise it -- and the slippage -- stay NULL.
"""

from __future__ import annotations

import pytest

from adaptive_scalper.costs.observations import (
    record_exit_observations,
    summarize_exit_observations,
    sweep_exit_costs,
)
from adaptive_scalper.execution.close import FULLY_CLOSED
from adaptive_scalper.persistence import connect, migrate
from test_close_unknown_durability import NOW, _close, _done, _setup


def _db():
    conn = connect(":memory:")
    migrate(conn)
    return conn


def _closed_position(conn, pid: str, direction: str = "BUY", entry_order_id: int | None = None) -> None:
    conn.execute(
        "INSERT INTO positions (broker_position_id, canonical_symbol, direction, volume, entry_price, "
        "initial_monetary_risk, strategy_key, entry_order_id, status, opened_at_utc, closed_at_utc) "
        "VALUES (?, 'XAUUSD', ?, 0.05, 2000.0, 20.0, 'momentum_continuation', ?, 'CLOSED', 1000, 2000)",
        (pid, direction, entry_order_id),
    )


def _exit_deal(conn, pid: str, ticket: str, price: float, *, volume=0.05, comment="", reason=None, entry="OUT"):
    conn.execute(
        "INSERT INTO deals (broker_deal_id, broker_position_id, price, volume, commission, swap, profit, fee, "
        "entry_type, deal_type, comment, reason, occurred_at_utc) VALUES (?, ?, ?, ?, 0, 0, 0, 0, ?, 'SELL', ?, ?, 2000)",
        (ticket, pid, price, volume, entry, comment, reason),
    )


def _row(conn, pid):
    return conn.execute("SELECT * FROM exit_cost_observations WHERE broker_position_id = ?", (pid,)).fetchone()


# ----------------------------------------------------------- close path evidence

def test_a_real_close_records_its_quote_and_yields_an_agent_close_observation():
    conn, gw, ticket = _setup()
    gw.send_action = _done   # a DONE close with a real, timestamped broker deal (fill 1999.0)
    outcome = _close(gw, ticket, conn)
    assert outcome.status == FULLY_CLOSED
    request = conn.execute("SELECT * FROM close_requests WHERE broker_position_id = ?", (ticket,)).fetchone()
    assert (request["quote_bid"], request["quote_ask"]) == (1999.0, 2001.0)
    assert request["status"] == "RESOLVED_CLOSED"

    sweep_exit_costs(conn, now_utc=NOW + 5)
    row = _row(conn, ticket)
    assert row["exit_kind"] == "AGENT_CLOSE" and row["reference_source"] == "CLOSE_QUOTE"
    assert row["reference_price"] == 1999.0            # a BUY closes by selling at the bid
    assert row["quote_spread_price"] == pytest.approx(2.0)
    assert row["exit_slippage_price"] == pytest.approx(row["reference_price"] - row["exit_fill_price"])
    assert row["close_request_id"] == request["id"]


def test_the_close_path_makes_no_extra_broker_call_for_the_quote():
    conn, gw, ticket = _setup()
    calls = []
    original = gw.symbol_info_tick
    gw.symbol_info_tick = lambda symbol: (calls.append(symbol), original(symbol))[1]
    _close(gw, ticket, conn)
    assert len(calls) == 2   # exactly the two verification rounds, as before the change


# ------------------------------------------------------------ classification

def test_a_broker_stop_uses_the_trigger_level_from_the_deal_comment():
    conn = _db()
    _closed_position(conn, "7")
    _exit_deal(conn, "7", "d1", 1994.60, comment="[sl 1995.00]", reason=4)
    conn.commit()
    record_exit_observations(conn, now_utc=3000)
    row = _row(conn, "7")
    assert row["exit_kind"] == "STOP_LOSS" and row["deal_reason"] == 4
    assert row["reference_source"] == "DEAL_COMMENT_TRIGGER" and row["reference_price"] == 1995.00
    assert row["exit_slippage_price"] == pytest.approx(0.40)   # filled 0.40 below the stop: adverse


def test_a_stop_without_trigger_evidence_keeps_reference_and_slippage_null():
    # stops can be moved (breakeven), so the entry order's SL is NOT used as a reference
    conn = _db()
    conn.execute(
        "INSERT INTO orders (client_request_id, canonical_symbol, broker_symbol, direction, requested_volume, "
        "stop_loss, take_profit, state, created_at_utc, updated_at_utc) "
        "VALUES ('c1', 'XAUUSD', 'XAUUSDm', 'BUY', 0.05, 1990.0, 2010.0, 'FILLED', 1, 1)",
    )
    _closed_position(conn, "8", entry_order_id=1)
    _exit_deal(conn, "8", "d2", 1994.0, reason=4)
    conn.commit()
    record_exit_observations(conn, now_utc=3000)
    row = _row(conn, "8")
    assert row["exit_kind"] == "STOP_LOSS"
    assert row["reference_price"] is None and row["exit_slippage_price"] is None and row["reference_source"] == "NONE"


def test_a_take_profit_falls_back_to_the_unmoved_entry_target_for_a_sell():
    conn = _db()
    conn.execute(
        "INSERT INTO orders (client_request_id, canonical_symbol, broker_symbol, direction, requested_volume, "
        "stop_loss, take_profit, state, created_at_utc, updated_at_utc) "
        "VALUES ('c2', 'XAUUSD', 'XAUUSDm', 'SELL', 0.05, 2010.0, 1990.0, 'FILLED', 1, 1)",
    )
    _closed_position(conn, "9", direction="SELL", entry_order_id=1)
    _exit_deal(conn, "9", "d3", 1990.0, reason=5)
    conn.commit()
    record_exit_observations(conn, now_utc=3000)
    row = _row(conn, "9")
    assert row["exit_kind"] == "TAKE_PROFIT" and row["reference_source"] == "ENTRY_ORDER_LEVEL"
    assert row["exit_slippage_price"] == pytest.approx(0.0)


@pytest.mark.parametrize("reason, kind", [(0, "MANUAL"), (2, "MANUAL"), (3, "AGENT_CLOSE"), (6, "STOP_OUT"),
                                          (7, "OTHER"), (None, "UNKNOWN")])
def test_deal_reason_mapping_and_unknowns_are_never_guessed(reason, kind):
    conn = _db()
    _closed_position(conn, "10")
    _exit_deal(conn, "10", "d4", 1999.0, reason=reason)
    conn.commit()
    record_exit_observations(conn, now_utc=3000)
    row = _row(conn, "10")
    assert row["exit_kind"] == kind
    assert row["exit_slippage_price"] is None   # no close quote and no trigger evidence


def test_mixed_exit_reasons_are_not_collapsed_into_one_kind():
    conn = _db()
    _closed_position(conn, "11")
    _exit_deal(conn, "11", "d5", 1999.0, volume=0.02, reason=3)
    _exit_deal(conn, "11", "d6", 1998.0, volume=0.03, comment="[sl 1998.5]", reason=4)
    conn.commit()
    record_exit_observations(conn, now_utc=3000)
    row = _row(conn, "11")
    assert row["deal_reason"] is None and row["exit_kind"] == "STOP_LOSS"   # only the comment is unambiguous
    assert row["exit_fill_price"] == pytest.approx((1999.0 * 0.02 + 1998.0 * 0.03) / 0.05)
    assert row["exit_volume"] == pytest.approx(0.05) and row["exit_deal_count"] == 2


def test_open_positions_and_positions_without_exit_deals_are_skipped_and_the_sweep_is_idempotent():
    conn = _db()
    _closed_position(conn, "12")                  # closed but no exit deal recorded yet
    conn.execute(
        "INSERT INTO positions (broker_position_id, canonical_symbol, direction, volume, entry_price, "
        "initial_monetary_risk, status, opened_at_utc) VALUES ('13', 'XAUUSD', 'BUY', 0.05, 2000, 20, 'OPEN', 1)")
    _exit_deal(conn, "13", "d7", 1999.0, reason=3)
    conn.commit()
    assert record_exit_observations(conn, now_utc=3000) == 0
    _exit_deal(conn, "12", "d8", 1999.0, comment="[tp 1999.0]", reason=5)
    conn.commit()
    assert record_exit_observations(conn, now_utc=3001) == 1
    assert record_exit_observations(conn, now_utc=3002) == 0
    assert _row(conn, "12")["recorded_at_utc"] == 3001


def test_summary_reports_percentiles_per_kind_and_needs_enough_samples():
    conn = _db()
    for i in range(5):
        pid = str(100 + i)
        _closed_position(conn, pid)
        _exit_deal(conn, pid, f"s{i}", 1995.0 - 0.1 * i, comment="[sl 1995.0]", reason=4)
    conn.commit()
    record_exit_observations(conn, now_utc=3000)
    summary = summarize_exit_observations(conn, "XAUUSD")
    stop = summary["by_kind"]["STOP_LOSS"]
    assert stop["exits"] == 5 and stop["with_reference"] == 5
    assert stop["p50"] == pytest.approx(0.2) and stop["p95"] == pytest.approx(0.4) and stop["adverse_share"] == 0.8
    assert summary["sufficient"] is False


def test_the_exit_evidence_module_cannot_trade_or_write_config():
    import ast
    from pathlib import Path

    source = Path(__file__).resolve().parents[1] / "adaptive_scalper" / "costs" / "observations.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    imported = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    assert not any(m.startswith(("adaptive_scalper.execution", "adaptive_scalper.gateway", "adaptive_scalper.config"))
                   for m in imported)
    assert "order_send" not in source.read_text(encoding="utf-8")
