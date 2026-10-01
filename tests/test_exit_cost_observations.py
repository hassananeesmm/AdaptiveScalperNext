"""Exit-side execution cost evidence, one observation per ECONOMIC EXIT EVENT
(migration 0031).

An economic exit event is one broker ORDER that closed (part of) a
position. Fills of the same order are volume-weighted into one event;
separate orders on one position (a partial agent close, then the stop loss
for the rest) are separate events. Evidence only from the DB (no extra
broker call); an unproven reference leaves reference and slippage NULL.
PROVISIONAL rows follow late deals; FINAL rows are immutable.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from adaptive_scalper.costs.observations import (
    EXIT_SETTLE_HORIZON_SECONDS,
    MIN_SAMPLES_FOR_EVIDENCE,
    record_exit_observations,
    summarize_exit_observations,
    sweep_exit_costs,
)
from adaptive_scalper.execution.close import FULLY_CLOSED
from adaptive_scalper.execution.reconciliation import run_reconciliation
from adaptive_scalper.gateway.mt5_gateway import Mt5QueryError
from adaptive_scalper.persistence import connect, migrate
from test_close_unknown_durability import MAGIC, NOW, _close, _done, _setup

T0 = 1_000_000


def _db(path=":memory:"):
    conn = connect(path)
    migrate(conn)
    return conn


def _position(conn, pid: str, *, direction="BUY", volume=0.05, status="CLOSED", opened=T0, closed=T0 + 600):
    conn.execute(
        "INSERT INTO positions (broker_position_id, canonical_symbol, direction, volume, entry_price, "
        "initial_monetary_risk, strategy_key, status, opened_at_utc, closed_at_utc) "
        "VALUES (?, 'XAUUSD', ?, ?, 2000.0, 20.0, 'momentum_continuation', ?, ?, ?)",
        (pid, direction, volume, status, opened, closed if status == "CLOSED" else None),
    )


def _deal(conn, pid: str, ticket: str, price: float, *, volume=0.05, entry="OUT", order=None, comment="",
          reason=None, at=T0 + 600, magic=MAGIC):
    conn.execute(
        "INSERT INTO deals (broker_deal_id, broker_position_id, price, volume, commission, swap, profit, fee, "
        "entry_type, deal_type, broker_order_ticket, magic, comment, reason, occurred_at_utc) "
        "VALUES (?, ?, ?, ?, 0, 0, 0, 0, ?, 'SELL', ?, ?, ?, ?, ?)",
        (ticket, pid, price, volume, entry, order, magic, comment, reason, at),
    )


def _entry(conn, pid: str, volume=0.05, at=T0):
    _deal(conn, pid, f"in-{pid}", 2000.0, volume=volume, entry="IN", order=f"o-in-{pid}", reason=3, at=at)


def _request(conn, pid: str, *, ticket=None, bid=1999.0, ask=2001.0, magic=MAGIC):
    conn.execute(
        "INSERT INTO close_requests (broker_position_id, broker_symbol, position_direction, requested_volume, magic, "
        "comment, requested_at_utc, send_outcome, status, quote_bid, quote_ask, broker_order_ticket) "
        "VALUES (?, 'XAUUSDm', 'BUY', 0.05, ?, 'ASN exit', ?, 'FULLY_CLOSED', 'RESOLVED_CLOSED', ?, ?, ?)",
        (pid, magic, T0 + 100, bid, ask, ticket),
    )


def _events(conn, pid):
    return conn.execute("SELECT * FROM exit_cost_observations WHERE broker_position_id = ? ORDER BY first_deal_time_utc, "
                        "event_key", (pid,)).fetchall()


# ----------------------------------------------------------- close path evidence

def test_a_real_close_records_quote_and_order_ticket_and_yields_a_final_agent_close_event():
    conn, gw, ticket = _setup()
    _entry(conn, ticket, at=1000)
    conn.commit()
    gw.send_action = _done   # DONE close with a real deal: order 9000, deal 9001, fill 1999.0
    outcome = _close(gw, ticket, conn)
    assert outcome.status == FULLY_CLOSED
    request = conn.execute("SELECT * FROM close_requests WHERE broker_position_id = ?", (ticket,)).fetchone()
    assert (request["quote_bid"], request["quote_ask"]) == (1999.0, 2001.0)
    assert request["broker_order_ticket"] == "9000" and request["status"] == "RESOLVED_CLOSED"

    sweep_exit_costs(conn, now_utc=NOW + 5)
    [event] = _events(conn, ticket)
    assert event["event_key"] == f"{ticket}:ORDER:9000"
    assert event["exit_kind"] == "AGENT_CLOSE" and event["reference_source"] == "CLOSE_QUOTE"
    assert event["close_request_id"] == request["id"]
    assert event["reference_price"] == 1999.0            # a BUY closes by selling at the bid
    assert event["exit_slippage_price"] == pytest.approx(0.0)
    assert event["status"] == "FINAL" and event["exclusion_reason"] is None


def test_the_close_path_makes_no_extra_broker_call_for_the_quote():
    conn, gw, ticket = _setup()
    calls = []
    original = gw.symbol_info_tick
    gw.symbol_info_tick = lambda symbol: (calls.append(symbol), original(symbol))[1]
    _close(gw, ticket, conn)
    assert len(calls) == 2   # exactly the two verification rounds, as before the change


# ------------------------------------------------- separate economic exit events

def test_partial_agent_close_then_stop_loss_are_two_events_never_one_vwap():
    conn = _db()
    _position(conn, "11")
    _entry(conn, "11")
    _request(conn, "11", ticket="o-close")
    _deal(conn, "11", "d1", 1999.0, volume=0.02, order="o-close", reason=3, at=T0 + 300)
    _deal(conn, "11", "d2", 1994.6, volume=0.03, order="o-sl", comment="[sl 1995.0]", reason=4, at=T0 + 600)
    conn.commit()
    record_exit_observations(conn, now_utc=T0 + 700)
    close, stop = _events(conn, "11")
    assert close["exit_kind"] == "AGENT_CLOSE" and close["exit_volume"] == pytest.approx(0.02)
    assert close["exit_fill_price"] == pytest.approx(1999.0) and close["exit_slippage_price"] == pytest.approx(0.0)
    assert stop["exit_kind"] == "STOP_LOSS" and stop["exit_volume"] == pytest.approx(0.03)
    assert stop["reference_price"] == 1995.0 and stop["exit_slippage_price"] == pytest.approx(0.4)
    assert {close["status"], stop["status"]} == {"FINAL"}


def test_partial_close_then_take_profit_are_two_events():
    conn = _db()
    _position(conn, "12", direction="SELL")
    _entry(conn, "12")
    _request(conn, "12", ticket="o-close", bid=1999.0, ask=2001.0)
    _deal(conn, "12", "d1", 2001.5, volume=0.01, order="o-close", reason=3, at=T0 + 300)
    _deal(conn, "12", "d2", 1990.0, volume=0.04, order="o-tp", comment="[tp 1990.0]", reason=5, at=T0 + 600)
    conn.commit()
    record_exit_observations(conn, now_utc=T0 + 700)
    close, target = _events(conn, "12")
    assert close["exit_kind"] == "AGENT_CLOSE" and close["reference_price"] == 2001.0   # SELL closes at the ask
    assert close["exit_slippage_price"] == pytest.approx(0.5)                          # bought 0.5 above: adverse
    assert target["exit_kind"] == "TAKE_PROFIT" and target["exit_slippage_price"] == pytest.approx(0.0)


def test_multiple_fills_of_one_close_instruction_are_one_volume_weighted_event():
    conn = _db()
    _position(conn, "13")
    _entry(conn, "13")
    _request(conn, "13", ticket="o-close")
    _deal(conn, "13", "d1", 1999.0, volume=0.02, order="o-close", reason=3, at=T0 + 300)
    _deal(conn, "13", "d2", 1998.0, volume=0.03, order="o-close", reason=3, at=T0 + 301)
    conn.commit()
    record_exit_observations(conn, now_utc=T0 + 700)
    [event] = _events(conn, "13")
    assert event["deal_count"] == 2 and json.loads(event["deal_tickets"]) == ["d1", "d2"]
    assert event["exit_fill_price"] == pytest.approx((1999.0 * 0.02 + 1998.0 * 0.03) / 0.05)
    assert event["exit_slippage_price"] == pytest.approx(1999.0 - event["exit_fill_price"])


def test_two_agent_close_instructions_on_one_position_are_two_events():
    conn = _db()
    _position(conn, "35")
    _entry(conn, "35")
    _request(conn, "35", ticket="o-close-1", bid=1999.0, ask=2001.0)
    _request(conn, "35", ticket="o-close-2", bid=1998.0, ask=2000.0)
    _deal(conn, "35", "d1", 1998.9, volume=0.02, order="o-close-1", reason=3, at=T0 + 300)
    _deal(conn, "35", "d2", 1997.8, volume=0.03, order="o-close-2", reason=3, at=T0 + 600)
    conn.commit()
    record_exit_observations(conn, now_utc=T0 + 700)
    first, second = _events(conn, "35")
    assert first["event_key"] == "35:ORDER:o-close-1" and second["event_key"] == "35:ORDER:o-close-2"
    assert first["close_request_id"] != second["close_request_id"]
    assert first["reference_price"] == 1999.0 and second["reference_price"] == 1998.0   # each its OWN quote
    assert first["exit_slippage_price"] == pytest.approx(0.1) and second["exit_slippage_price"] == pytest.approx(0.2)
    assert {first["exit_kind"], second["exit_kind"]} == {"AGENT_CLOSE"}


def test_mixed_reasons_inside_one_order_are_excluded_not_guessed():
    conn = _db()
    _position(conn, "14")
    _entry(conn, "14")
    _deal(conn, "14", "d1", 1999.0, volume=0.02, order="o-x", reason=3)
    _deal(conn, "14", "d2", 1998.0, volume=0.03, order="o-x", comment="[sl 1998.5]", reason=4)
    conn.commit()
    record_exit_observations(conn, now_utc=T0 + 700)
    [event] = _events(conn, "14")
    assert event["exit_kind"] == "UNKNOWN" and event["deal_reason"] is None
    assert event["exclusion_reason"] == "REASON_CONFLICT" and event["exit_slippage_price"] is None


# ------------------------------------------------------ lifecycle / settlement

def test_a_delayed_second_deal_updates_the_provisional_event_before_it_is_final():
    conn = _db()
    _position(conn, "15")
    _entry(conn, "15")
    _request(conn, "15", ticket="o-close")
    _deal(conn, "15", "d1", 1999.0, volume=0.02, order="o-close", reason=3, at=T0 + 300)
    conn.commit()
    assert record_exit_observations(conn, now_utc=T0 + 700) == 1
    [first] = _events(conn, "15")
    assert first["status"] == "PROVISIONAL" and first["exit_volume"] == pytest.approx(0.02) and first["version"] == 1

    _deal(conn, "15", "d2", 1998.0, volume=0.03, order="o-close", reason=3, at=T0 + 301)   # arrives later
    conn.commit()
    assert record_exit_observations(conn, now_utc=T0 + 800) == 1
    [final] = _events(conn, "15")
    assert final["id"] == first["id"] and final["version"] == 2
    assert final["status"] == "FINAL" and final["finalized_at_utc"] == T0 + 800
    assert final["exit_volume"] == pytest.approx(0.05) and final["deal_count"] == 2


def test_an_open_position_with_a_partial_close_stays_provisional():
    conn = _db()
    _position(conn, "16", status="OPEN")
    _entry(conn, "16")
    _request(conn, "16", ticket="o-close")
    _deal(conn, "16", "d1", 1999.0, volume=0.02, order="o-close", reason=3)
    conn.commit()
    record_exit_observations(conn, now_utc=T0 + EXIT_SETTLE_HORIZON_SECONDS * 2)
    [event] = _events(conn, "16")
    assert event["status"] == "PROVISIONAL" and event["exit_kind"] == "AGENT_CLOSE"


def test_unprovable_volume_finalizes_only_after_the_horizon_and_is_excluded():
    conn = _db()
    _position(conn, "17")                              # no IN deal recorded: completeness unprovable
    _deal(conn, "17", "d1", 1994.6, order="o-sl", comment="[sl 1995.0]", reason=4)
    conn.commit()
    record_exit_observations(conn, now_utc=T0 + 700)
    assert _events(conn, "17")[0]["status"] == "PROVISIONAL"
    record_exit_observations(conn, now_utc=T0 + 600 + EXIT_SETTLE_HORIZON_SECONDS)
    [event] = _events(conn, "17")
    assert event["status"] == "FINAL" and event["exclusion_reason"] == "UNSETTLED_VOLUME"
    assert summarize_exit_observations(conn, "XAUUSD")["by_kind"]["STOP_LOSS"]["with_reference"] == 0


def test_restart_before_finalization_resumes_the_same_event(tmp_path):
    path = str(tmp_path / "restart.sqlite3")
    conn = _db(path)
    _position(conn, "18")
    _entry(conn, "18")
    _deal(conn, "18", "d1", 1994.6, volume=0.02, order="o-sl", comment="[sl 1995.0]", reason=4)
    conn.commit()
    record_exit_observations(conn, now_utc=T0 + 700)
    conn.close()

    conn = _db(path)                                   # process restart
    _deal(conn, "18", "d2", 1994.4, volume=0.03, order="o-sl", comment="[sl 1995.0]", reason=4)
    conn.commit()
    record_exit_observations(conn, now_utc=T0 + 800)
    [event] = _events(conn, "18")
    assert event["status"] == "FINAL" and event["version"] == 2 and event["deal_count"] == 2
    assert conn.execute("SELECT COUNT(*) FROM exit_cost_observations").fetchone()[0] == 1


def test_duplicate_processing_is_idempotent():
    conn = _db()
    _position(conn, "19")
    _entry(conn, "19")
    _deal(conn, "19", "d1", 1994.6, order="o-sl", comment="[sl 1995.0]", reason=4)
    conn.commit()
    assert record_exit_observations(conn, now_utc=T0 + 700) == 1
    assert record_exit_observations(conn, now_utc=T0 + 701) == 0
    assert record_exit_observations(conn, now_utc=T0 + 702) == 0
    [event] = _events(conn, "19")
    assert event["version"] == 1 and event["updated_at_utc"] == T0 + 700


def test_final_rows_are_immutable_and_undeletable():
    conn = _db()
    _position(conn, "20")
    _entry(conn, "20")
    _deal(conn, "20", "d1", 1994.6, order="o-sl", comment="[sl 1995.0]", reason=4)
    conn.commit()
    record_exit_observations(conn, now_utc=T0 + 700)
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        conn.execute("UPDATE exit_cost_observations SET exit_slippage_price = 0 WHERE broker_position_id = '20'")
    with pytest.raises(sqlite3.IntegrityError, match="cannot be deleted"):
        conn.execute("DELETE FROM exit_cost_observations WHERE broker_position_id = '20'")


# ---------------------------------------------------------- reference evidence

def test_stop_without_trigger_evidence_keeps_reference_and_slippage_null():
    # stops move (breakeven), so no order-level SL is ever used as a reference
    conn = _db()
    _position(conn, "21")
    _entry(conn, "21")
    _deal(conn, "21", "d1", 1994.0, order="o-sl", reason=4)
    conn.commit()
    record_exit_observations(conn, now_utc=T0 + 700)
    [event] = _events(conn, "21")
    assert event["exit_kind"] == "STOP_LOSS" and event["reference_source"] == "NONE"
    assert event["reference_price"] is None and event["exit_slippage_price"] is None


def test_take_profit_without_trigger_comment_is_never_backfilled_from_the_entry_order():
    conn = _db()
    conn.execute(
        "INSERT INTO orders (client_request_id, canonical_symbol, broker_symbol, direction, requested_volume, "
        "stop_loss, take_profit, state, created_at_utc, updated_at_utc) "
        "VALUES ('c2', 'XAUUSD', 'XAUUSDm', 'SELL', 0.05, 2010.0, 1990.0, 'FILLED', 1, 1)")
    _position(conn, "22", direction="SELL")
    conn.execute("UPDATE positions SET entry_order_id = 1 WHERE broker_position_id = '22'")
    _entry(conn, "22")
    _deal(conn, "22", "d1", 1990.0, order="o-tp", reason=5)
    conn.commit()
    record_exit_observations(conn, now_utc=T0 + 700)
    [event] = _events(conn, "22")
    assert event["exit_kind"] == "TAKE_PROFIT" and event["reference_price"] is None


def test_agent_close_linked_without_a_recorded_quote_has_no_reference():
    conn = _db()
    _position(conn, "23")
    _entry(conn, "23")
    _request(conn, "23", ticket="o-close", bid=None, ask=None)   # pre-0031 request: no quote
    _deal(conn, "23", "d1", 1999.0, order="o-close", reason=3)
    conn.commit()
    record_exit_observations(conn, now_utc=T0 + 700)
    [event] = _events(conn, "23")
    assert event["exit_kind"] == "AGENT_CLOSE" and event["reference_price"] is None
    assert event["exit_slippage_price"] is None


def test_an_expert_close_is_never_linked_to_the_latest_request_without_its_ticket():
    conn = _db()
    _position(conn, "24")
    _entry(conn, "24")
    _request(conn, "24", ticket="o-other")            # a different close instruction
    _deal(conn, "24", "d1", 1999.0, order="o-close", reason=3)
    conn.commit()
    record_exit_observations(conn, now_utc=T0 + 700)
    [event] = _events(conn, "24")
    assert event["exit_kind"] == "AGENT_CLOSE"         # our magic, but NOT this request's ticket
    assert event["close_request_id"] is None and event["reference_price"] is None


@pytest.mark.parametrize("reason, kind", [(0, "MANUAL"), (2, "MANUAL"), (6, "STOP_OUT"), (7, "OTHER"),
                                          (None, "UNKNOWN")])
def test_deal_reason_mapping_and_unknowns_are_never_guessed(reason, kind):
    conn = _db()
    _position(conn, "25")
    _entry(conn, "25")
    _deal(conn, "25", "d1", 1999.0, order="o-1", reason=reason)
    conn.commit()
    record_exit_observations(conn, now_utc=T0 + 700)
    [event] = _events(conn, "25")
    assert event["exit_kind"] == kind and event["exit_slippage_price"] is None


def test_an_expert_close_from_a_foreign_magic_is_other_not_agent_close():
    conn = _db()
    _position(conn, "26")
    _entry(conn, "26")
    _deal(conn, "26", "d1", 1999.0, order="o-1", reason=3, magic=424242)
    conn.commit()
    record_exit_observations(conn, now_utc=T0 + 700)
    assert _events(conn, "26")[0]["exit_kind"] == "OTHER"


# ------------------------------------------------------------- INOUT / OUT_BY

def test_inout_netting_reversal_is_recorded_but_excluded_and_never_settles_the_position():
    conn = _db()
    _position(conn, "27")
    _entry(conn, "27")
    _deal(conn, "27", "d1", 1999.0, volume=0.08, entry="INOUT", order="o-rev", reason=0)
    conn.commit()
    record_exit_observations(conn, now_utc=T0 + 700)
    [event] = _events(conn, "27")
    assert event["exclusion_reason"] == "INOUT" and event["exit_slippage_price"] is None
    assert event["status"] == "PROVISIONAL"            # INOUT volume can never prove completeness
    record_exit_observations(conn, now_utc=T0 + 600 + EXIT_SETTLE_HORIZON_SECONDS)
    [event] = _events(conn, "27")
    assert event["status"] == "FINAL" and event["exclusion_reason"] == "INOUT"
    assert summarize_exit_observations(conn, "XAUUSD")["excluded"] == {"INOUT": 1}


def test_out_by_close_is_excluded():
    conn = _db()
    _position(conn, "28")
    _entry(conn, "28")
    _deal(conn, "28", "d1", 1999.0, entry="OUT_BY", order="o-by", reason=0)
    conn.commit()
    record_exit_observations(conn, now_utc=T0 + 700)
    [event] = _events(conn, "28")
    assert event["exclusion_reason"] == "OUT_BY" and event["exit_slippage_price"] is None
    assert event["exit_kind"] not in ("STOP_LOSS", "TAKE_PROFIT", "AGENT_CLOSE")
    assert summarize_exit_observations(conn, "XAUUSD")["excluded"] == {"OUT_BY": 1}


# ------------------------------------------------------- evidence sources

def _account_deal(conn, pid, ticket, *, entry, dtype, price, volume, order, at, reason=None, comment=""):
    conn.execute(
        "INSERT INTO broker_account_deals (login, server, ticket, order_ticket, time_utc, type, entry, magic, "
        "position_id, volume, price, commission, swap, profit, fee, symbol, comment, reason) "
        "VALUES (1, 'Demo', ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, 0, 0, 'XAUUSDm', ?, ?)",
        (ticket, order, at, dtype, entry, MAGIC, int(pid), volume, price, comment, reason),
    )


def test_imported_account_history_supplies_a_late_deal_once_its_identity_is_proven():
    conn = _db()
    _position(conn, "29")
    _entry(conn, "29")
    _deal(conn, "29", "31", 1994.6, volume=0.02, order="41", comment="[sl 1995.0]", reason=4)
    _account_deal(conn, "29", 30, entry=0, dtype=0, price=2000.0, volume=0.05, order=40, at=T0 + 5, reason=3)
    _account_deal(conn, "29", 31, entry=1, dtype=1, price=1994.6, volume=0.02, order=41, at=T0 + 600, reason=4)
    _account_deal(conn, "29", 32, entry=1, dtype=1, price=1994.4, volume=0.03, order=41, at=T0 + 601, reason=4,
                  comment="[sl 1995.0]")
    conn.commit()
    record_exit_observations(conn, now_utc=T0 + 700)
    [event] = _events(conn, "29")
    assert event["deal_count"] == 2 and json.loads(event["deal_tickets"]) == ["31", "32"]   # de-duplicated


def test_account_history_of_another_account_is_ignored():
    conn = _db()
    _position(conn, "33")
    _entry(conn, "33")
    # same position number, but its opening deal is a SELL a day earlier: not this position
    _account_deal(conn, "33", 50, entry=0, dtype=1, price=2100.0, volume=0.05, order=51, at=T0 - 86400)
    _account_deal(conn, "33", 52, entry=1, dtype=0, price=2090.0, volume=0.05, order=53, at=T0 - 80000, reason=5)
    conn.commit()
    assert record_exit_observations(conn, now_utc=T0 + 700) == 0
    assert _events(conn, "33") == []


# --------------------------------------------- broker history None vs empty

def test_history_unavailable_raises_and_records_nothing():
    conn, gw, ticket = _setup()
    gw._open_positions.clear()                        # broker no longer reports the position
    gw.truth_down = True                              # MT5 query returned None -> Mt5QueryError
    with pytest.raises(Mt5QueryError):
        run_reconciliation(conn, gw, "recon:test", now_utc=NOW)
    record_exit_observations(conn, now_utc=NOW + 5)
    assert conn.execute("SELECT status FROM positions WHERE broker_position_id = ?", (ticket,)).fetchone()[0] == "OPEN"
    assert conn.execute("SELECT COUNT(*) FROM exit_cost_observations").fetchone()[0] == 0


def test_empty_history_leaves_the_position_unrepaired_and_records_nothing():
    conn, gw, ticket = _setup()
    gw._open_positions.clear()
    gw._historical_deals.clear()                      # a real, EMPTY answer: no closing deal known yet
    report = run_reconciliation(conn, gw, "recon:test", now_utc=NOW)
    assert ticket in report.unrepaired_position_ids
    record_exit_observations(conn, now_utc=NOW + 5)
    assert conn.execute("SELECT COUNT(*) FROM exit_cost_observations").fetchone()[0] == 0


# ------------------------------------------------------------------- summary

def test_sufficiency_is_per_exit_kind_never_pooled():
    conn = _db()
    for i in range(MIN_SAMPLES_FOR_EVIDENCE):
        pid = str(1000 + i)
        _position(conn, pid)
        _entry(conn, pid)
        _deal(conn, pid, f"s{i}", 1995.0 - 0.01 * (i % 5), order=f"o{i}", comment="[sl 1995.0]", reason=4)
    for i in range(3):
        pid = str(2000 + i)
        _position(conn, pid)
        _entry(conn, pid)
        _deal(conn, pid, f"t{i}", 2010.0, order=f"p{i}", comment="[tp 2010.0]", reason=5)
    conn.commit()
    record_exit_observations(conn, now_utc=T0 + 700)
    summary = summarize_exit_observations(conn, "XAUUSD")
    assert "sufficient" not in summary and "meets_min_samples" not in summary   # no pooled flag
    assert summary["by_kind"]["STOP_LOSS"]["meets_min_samples"] is True
    assert summary["by_kind"]["TAKE_PROFIT"]["meets_min_samples"] is False
    assert "never a recalibration trigger" in summary["note"]
    assert summary["by_kind"]["STOP_LOSS"]["p50"] == pytest.approx(0.02)


def test_provisional_events_are_counted_but_never_measured():
    conn = _db()
    _position(conn, "34", status="OPEN")
    _entry(conn, "34")
    _deal(conn, "34", "d1", 1994.6, volume=0.02, order="o-sl", comment="[sl 1995.0]", reason=4)
    conn.commit()
    record_exit_observations(conn, now_utc=T0 + 700)
    summary = summarize_exit_observations(conn, "XAUUSD")
    assert summary["provisional"] == 1 and summary["by_kind"]["STOP_LOSS"]["with_reference"] == 0


def test_the_exit_evidence_module_cannot_trade_or_write_config():
    import ast
    from pathlib import Path

    source = Path(__file__).resolve().parents[1] / "adaptive_scalper" / "costs" / "observations.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    imported = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    assert not any(m.startswith(("adaptive_scalper.execution", "adaptive_scalper.gateway", "adaptive_scalper.config"))
                   for m in imported)
    assert "order_send" not in source.read_text(encoding="utf-8")
