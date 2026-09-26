"""Tests for position reconciliation (directive section 31)."""

from __future__ import annotations

import pytest

from adaptive_scalper.execution.reconciliation import (
    BLOCKING_MISMATCH,
    CLEAN,
    MISSING_LOCAL_ORDER,
    MISSING_LOCAL_POSITION,
    ORPHAN_BROKER_ORDER,
    ORPHAN_BROKER_POSITION,
    PENDING_MISMATCH,
    RECONCILIATION_MISMATCH,
    RECOVERED,
    BrokerPositionSnapshot,
    LocalPositionRecord,
    classify_reconciliation,
    find_closing_deal,
    find_closing_deals,
    get_unresolved_incidents,
    has_dangerous_unresolved_unknown,
    reconcile_pending_orders,
    reconcile_positions,
    record_incident,
    resolve_incident,
    run_reconciliation,
)
from adaptive_scalper.execution.store import create_order, get_active_orders, transition_order_state
from adaptive_scalper.execution.state_machine import OrderState
from adaptive_scalper.gateway.fake_gateway import FakeGateway
from adaptive_scalper.gateway.types import HistoricalDeal, HistoricalOrder, PendingOrderSnapshot, PositionSnapshot
from adaptive_scalper.journal.queries import get_chain_events
from adaptive_scalper.persistence import connect, migrate


def _local(**overrides) -> LocalPositionRecord:
    defaults = dict(
        id=1, broker_position_id="pos-1", canonical_symbol="XAUUSD", direction="BUY",
        volume=0.05, entry_price=2000.0, initial_monetary_risk=20.0, strategy_key="momentum_continuation",
        opened_at_utc=1000,
    )
    defaults.update(overrides)
    return LocalPositionRecord(**defaults)


def _closing_deal(**overrides) -> HistoricalDeal:
    defaults = dict(
        ticket=900, order=800, time=4500, type=1, entry=1, magic=0, position_id=1,
        volume=0.05, price=2010.0, commission=-0.5, swap=0.0, profit=10.0, fee=0.0,
        symbol="XAUUSDm", comment="", external_id="",
    )
    defaults.update(overrides)
    return HistoricalDeal(**defaults)


def _broker(**overrides) -> BrokerPositionSnapshot:
    defaults = dict(broker_position_id="pos-1", canonical_symbol="XAUUSD", direction="BUY", volume=0.05)
    defaults.update(overrides)
    return BrokerPositionSnapshot(**defaults)


# --------------------------------------------------------------------------
# reconcile_positions
# --------------------------------------------------------------------------

def test_matching_position_produces_no_findings():
    findings = reconcile_positions([_local()], [_broker()])
    assert findings == []


def test_orphan_broker_position_detected():
    findings = reconcile_positions([], [_broker()])
    assert len(findings) == 1
    assert findings[0].finding_type == ORPHAN_BROKER_POSITION
    assert findings[0].broker_position_id == "pos-1"


def test_missing_local_position_detected():
    findings = reconcile_positions([_local()], [])
    assert len(findings) == 1
    assert findings[0].finding_type == MISSING_LOCAL_POSITION


def test_volume_mismatch_detected():
    findings = reconcile_positions([_local(volume=0.05)], [_broker(volume=0.03)])
    assert len(findings) == 1
    assert findings[0].finding_type == RECONCILIATION_MISMATCH
    assert "volume mismatch" in findings[0].detail


def test_direction_mismatch_detected():
    findings = reconcile_positions([_local(direction="BUY")], [_broker(direction="SELL")])
    assert any(f.finding_type == RECONCILIATION_MISMATCH and "direction mismatch" in f.detail for f in findings)


def test_multiple_positions_reconciled_independently():
    local = [_local(broker_position_id="pos-1"), _local(broker_position_id="pos-2")]
    broker = [_broker(broker_position_id="pos-1"), _broker(broker_position_id="pos-3")]
    findings = reconcile_positions(local, broker)
    types_by_id = {f.broker_position_id: f.finding_type for f in findings}
    assert types_by_id["pos-2"] == MISSING_LOCAL_POSITION
    assert types_by_id["pos-3"] == ORPHAN_BROKER_POSITION


def test_empty_both_sides_produces_no_findings():
    assert reconcile_positions([], []) == []


# --------------------------------------------------------------------------
# execution incidents (persistence)
# --------------------------------------------------------------------------

@pytest.fixture()
def db(tmp_path):
    conn = connect(tmp_path / "test.sqlite3")
    migrate(conn)
    yield conn
    conn.close()


def test_record_and_resolve_incident(db):
    incident_id = record_incident(db, "UNKNOWN_OUTCOME", "order req-1 outcome uncertain", now_utc=1000)
    unresolved = get_unresolved_incidents(db)
    assert len(unresolved) == 1
    assert unresolved[0]["id"] == incident_id

    resolve_incident(db, incident_id, "resolved: broker confirms FILLED", now_utc=2000)
    assert get_unresolved_incidents(db) == []


def test_record_incident_dedup_key_updates_existing_row_instead_of_duplicating(db):
    # External review finding #15: repeated reconciliation of the SAME
    # unresolved mismatch must not create an unbounded stream of rows.
    first_id = record_incident(
        db, "RECONCILIATION_MISMATCH", "volume mismatch", dedup_key="RECONCILIATION_MISMATCH:pos-1", now_utc=1000,
    )
    second_id = record_incident(
        db, "RECONCILIATION_MISMATCH", "volume mismatch (still)", dedup_key="RECONCILIATION_MISMATCH:pos-1", now_utc=1500,
    )
    assert second_id == first_id

    unresolved = get_unresolved_incidents(db)
    assert len(unresolved) == 1
    row = unresolved[0]
    assert row["occurrence_count"] == 2
    assert row["first_seen_at_utc"] == 1000
    assert row["last_seen_at_utc"] == 1500
    assert row["detail"] == "volume mismatch (still)"


def test_record_incident_dedup_key_does_not_merge_across_resolved_rows(db):
    # A NEW occurrence of a problem that was already resolved is a fresh
    # incident, not a silent reopen of history.
    first_id = record_incident(db, "RECONCILIATION_MISMATCH", "volume mismatch", dedup_key="k1", now_utc=1000)
    resolve_incident(db, first_id, "resolved", now_utc=1200)

    second_id = record_incident(db, "RECONCILIATION_MISMATCH", "volume mismatch again", dedup_key="k1", now_utc=2000)
    assert second_id != first_id
    unresolved = get_unresolved_incidents(db)
    assert len(unresolved) == 1
    assert unresolved[0]["id"] == second_id
    assert unresolved[0]["occurrence_count"] == 1


def test_record_incident_without_dedup_key_always_creates_new_row(db):
    record_incident(db, "UNKNOWN_OUTCOME", "uncertain", now_utc=1000)
    record_incident(db, "UNKNOWN_OUTCOME", "uncertain", now_utc=1000)
    assert len(get_unresolved_incidents(db)) == 2


def test_has_dangerous_unresolved_unknown_true_when_present(db):
    record_incident(db, "UNKNOWN_OUTCOME", "uncertain", now_utc=1000)
    assert has_dangerous_unresolved_unknown(db) is True


def test_has_dangerous_unresolved_unknown_false_when_none(db):
    assert has_dangerous_unresolved_unknown(db) is False


def test_has_dangerous_unresolved_unknown_false_once_resolved(db):
    incident_id = record_incident(db, "UNKNOWN_OUTCOME", "uncertain", now_utc=1000)
    resolve_incident(db, incident_id, "resolved", now_utc=2000)
    assert has_dangerous_unresolved_unknown(db) is False


def test_reconciliation_mismatch_incidents_do_not_count_as_dangerous_unknown(db):
    # Only UNKNOWN_OUTCOME blocks new entries per directive section 30 —
    # a plain reconciliation mismatch is tracked but handled separately.
    record_incident(db, "RECONCILIATION_MISMATCH", "volume mismatch", now_utc=1000)
    assert has_dangerous_unresolved_unknown(db) is False


# --------------------------------------------------------------------------
# classify_reconciliation (execution-safety review finding #4)
# --------------------------------------------------------------------------

def test_classify_no_findings_is_clean():
    assert classify_reconciliation([]) == CLEAN


def test_classify_orphan_is_blocking():
    findings = reconcile_positions([], [_broker()])
    assert classify_reconciliation(findings) == BLOCKING_MISMATCH


def test_classify_mismatch_is_blocking():
    findings = reconcile_positions([_local(volume=0.05)], [_broker(volume=0.03)])
    assert classify_reconciliation(findings) == BLOCKING_MISMATCH


def test_classify_missing_local_only_is_recovered():
    findings = reconcile_positions([_local()], [])
    assert classify_reconciliation(findings) == RECOVERED


def test_classify_mixed_missing_and_orphan_is_blocking():
    local = [_local(broker_position_id="pos-1")]
    broker = [_broker(broker_position_id="pos-2")]
    findings = reconcile_positions(local, broker)
    assert classify_reconciliation(findings) == BLOCKING_MISMATCH


# --------------------------------------------------------------------------
# run_reconciliation (real gateway truth, actionable status, journaling)
# --------------------------------------------------------------------------

def _insert_local_position(conn, **overrides):
    row = dict(
        broker_position_id="pos-1", canonical_symbol="XAUUSD", direction="BUY", volume=0.05,
        entry_price=2000.0, initial_monetary_risk=20.0, strategy_key="momentum_continuation",
        status="OPEN", opened_at_utc=1000,
    )
    row.update(overrides)
    conn.execute(
        """
        INSERT INTO positions
            (broker_position_id, canonical_symbol, direction, volume, entry_price,
             initial_monetary_risk, strategy_key, status, opened_at_utc)
        VALUES (:broker_position_id, :canonical_symbol, :direction, :volume, :entry_price,
                :initial_monetary_risk, :strategy_key, :status, :opened_at_utc)
        """,
        row,
    )
    conn.commit()


def test_run_reconciliation_clean_when_broker_and_local_agree(db):
    _insert_local_position(db)
    gw = FakeGateway()
    gw.inject_open_position(PositionSnapshot("pos-1", "XAUUSDm", "BUY", 0.05, 2000.0, 1990.0, 2010.0, 0.0, 0, ""))

    report = run_reconciliation(db, gw, "recon-1", now_utc=5000)
    assert report.status == CLEAN
    assert get_unresolved_incidents(db) == []


def test_run_reconciliation_blocking_records_incident_and_journals(db):
    gw = FakeGateway()
    gw.inject_open_position(PositionSnapshot("pos-orphan", "XAUUSDm", "BUY", 0.05, 2000.0, 1990.0, 2010.0, 0.0, 0, ""))

    report = run_reconciliation(db, gw, "recon-2", now_utc=5000)
    assert report.status == BLOCKING_MISMATCH

    incidents = get_unresolved_incidents(db)
    assert len(incidents) == 1
    assert incidents[0]["incident_type"] == ORPHAN_BROKER_POSITION

    events = get_chain_events(db, "recon-2")
    assert len(events) == 1
    assert events[0].event_type == "RECONCILIATION_ACTION"
    assert events[0].payload["status"] == BLOCKING_MISMATCH


def test_run_reconciliation_does_not_duplicate_incident_across_repeated_cycles(db):
    # External review finding #15: the same unresolved orphan position,
    # observed on every ~0.5-1s reconciliation cycle, must accumulate on
    # ONE incident row, not spawn a new row per cycle.
    gw = FakeGateway()
    gw.inject_open_position(PositionSnapshot("pos-orphan", "XAUUSDm", "BUY", 0.05, 2000.0, 1990.0, 2010.0, 0.0, 0, ""))

    run_reconciliation(db, gw, "recon-2a", now_utc=5000)
    run_reconciliation(db, gw, "recon-2b", now_utc=5500)
    report = run_reconciliation(db, gw, "recon-2c", now_utc=6000)
    assert report.status == BLOCKING_MISMATCH

    incidents = get_unresolved_incidents(db)
    assert len(incidents) == 1
    assert incidents[0]["incident_type"] == ORPHAN_BROKER_POSITION
    assert incidents[0]["occurrence_count"] == 3
    assert incidents[0]["first_seen_at_utc"] == 5000
    assert incidents[0]["last_seen_at_utc"] == 6000


def test_run_reconciliation_resolves_position_incident_after_finding_disappears(db):
    gw = FakeGateway()
    gw.inject_open_position(
        PositionSnapshot("pos-orphan", "XAUUSDm", "BUY", 0.05, 2000.0, 1990.0, 2010.0, 0.0, 0, "")
    )
    run_reconciliation(db, gw, "recon-present", now_utc=5000)
    assert len(get_unresolved_incidents(db)) == 1

    gw.remove_open_position("pos-orphan")
    report = run_reconciliation(db, gw, "recon-gone", now_utc=5100)

    assert report.status == CLEAN
    assert get_unresolved_incidents(db) == []
    resolved = db.execute("SELECT resolved_at_utc, resolution FROM execution_incidents").fetchone()
    assert resolved["resolved_at_utc"] == 5100
    assert "latest broker reconciliation snapshot" in resolved["resolution"]


def test_run_reconciliation_recovers_from_real_closing_deal(db):
    # broker_position_id must be numeric-string, matching a real MT5
    # ticket, since HistoricalDeal.position_id is compared against it.
    _insert_local_position(db, broker_position_id="1", opened_at_utc=1000)
    gw = FakeGateway(historical_deals=[_closing_deal(position_id=1, ticket=900, time=4500, price=2010.0)])

    report = run_reconciliation(db, gw, "recon-3", now_utc=5000)
    assert report.status == RECOVERED
    assert report.recovered_position_ids == ["1"]
    assert get_unresolved_incidents(db) == []

    # Local state is ACTUALLY repaired, not merely relabeled:
    row = db.execute("SELECT status, closed_at_utc FROM positions WHERE broker_position_id = '1'").fetchone()
    assert row["status"] == "CLOSED"
    assert row["closed_at_utc"] == 4500

    deal_row = db.execute("SELECT * FROM deals WHERE broker_deal_id = '900'").fetchone()
    assert deal_row is not None
    assert deal_row["price"] == 2010.0
    assert deal_row["profit"] == 10.0
    # External review finding #10: fee/entry_type/deal_type/broker_order_ticket
    # /magic/comment must never be silently discarded on recovery either.
    assert deal_row["entry_type"] == "OUT"
    assert deal_row["deal_type"] == "SELL"
    assert deal_row["broker_order_ticket"] == "800"

    events = [e for e in get_chain_events(db, "recon-3:position:1") if e.event_type == "POSITION_CLOSED"]
    assert len(events) == 1
    assert events[0].payload["price"] == 2010.0


def test_run_reconciliation_never_uses_current_price_when_no_closing_deal_found(db):
    # No matching historical deal exists -- must NOT be labeled RECOVERED,
    # must NOT fabricate a close price, and must block new entries.
    _insert_local_position(db, broker_position_id="1", opened_at_utc=1000)
    gw = FakeGateway()  # no historical deals at all

    report = run_reconciliation(db, gw, "recon-3b", now_utc=5000)
    assert report.status == BLOCKING_MISMATCH
    assert report.recovered_position_ids == []
    assert report.unrepaired_position_ids == ["1"]

    incidents = get_unresolved_incidents(db)
    assert len(incidents) == 1
    assert incidents[0]["incident_type"] == MISSING_LOCAL_POSITION

    row = db.execute("SELECT status FROM positions WHERE broker_position_id = '1'").fetchone()
    assert row["status"] == "OPEN"  # untouched -- never marked closed without real evidence


def test_find_closing_deal_ignores_opening_deals():
    opening = _closing_deal(entry=0, ticket=100, position_id=1)  # entry=0 -> IN, not a close
    gw = FakeGateway(historical_deals=[opening])
    assert find_closing_deal(gw, "1", 0, 9999999999) is None


def test_find_closing_deal_picks_the_latest_when_multiple_match():
    early = _closing_deal(entry=1, ticket=100, position_id=1, time=1000)
    late = _closing_deal(entry=1, ticket=200, position_id=1, time=2000)
    gw = FakeGateway(historical_deals=[early, late])
    result = find_closing_deal(gw, "1", 0, 9999999999)
    assert result.ticket == 200


# --------------------------------------------------------------------------
# find_closing_deals (external review finding #15: OUT/INOUT/OUT_BY, not
# just OUT; every deal, not just the latest)
# --------------------------------------------------------------------------

def test_find_closing_deals_returns_every_matching_deal_oldest_first():
    early = _closing_deal(entry=1, ticket=100, position_id=1, time=1000)
    late = _closing_deal(entry=1, ticket=200, position_id=1, time=2000)
    gw = FakeGateway(historical_deals=[late, early])  # deliberately out of order
    result = find_closing_deals(gw, "1", 0, 9999999999)
    assert [d.ticket for d in result] == [100, 200]


def test_find_closing_deals_includes_inout_and_out_by():
    out = _closing_deal(entry=1, ticket=100, position_id=1, time=1000)
    inout = _closing_deal(entry=2, ticket=200, position_id=1, time=2000)
    out_by = _closing_deal(entry=3, ticket=300, position_id=1, time=3000)
    gw = FakeGateway(historical_deals=[out, inout, out_by])
    result = find_closing_deals(gw, "1", 0, 9999999999)
    assert [d.ticket for d in result] == [100, 200, 300]


def test_find_closing_deals_ignores_opening_deals():
    opening = _closing_deal(entry=0, ticket=100, position_id=1)
    gw = FakeGateway(historical_deals=[opening])
    assert find_closing_deals(gw, "1", 0, 9999999999) == []


def test_find_closing_deals_ignores_other_positions():
    other = _closing_deal(entry=1, ticket=100, position_id=999)
    gw = FakeGateway(historical_deals=[other])
    assert find_closing_deals(gw, "1", 0, 9999999999) == []


# --------------------------------------------------------------------------
# multi-deal reconciliation recovery (finding #15) + atomicity (finding #16)
# --------------------------------------------------------------------------

def test_run_reconciliation_recovers_and_records_every_partial_close_deal(db):
    _insert_local_position(db, broker_position_id="1", opened_at_utc=1000)
    first_close = _closing_deal(ticket=901, order=800, position_id=1, time=4000, volume=0.02, price=2005.0,
                                 commission=-0.2, swap=0.0, profit=4.0)
    second_close = _closing_deal(ticket=902, order=800, position_id=1, time=4500, volume=0.03, price=2010.0,
                                  commission=-0.3, swap=-0.1, profit=6.0)
    gw = FakeGateway(historical_deals=[first_close, second_close])

    report = run_reconciliation(db, gw, "recon-multi", now_utc=5000)
    assert report.status == RECOVERED
    assert report.recovered_position_ids == ["1"]

    # BOTH deals are persisted -- never just the last one.
    rows = db.execute("SELECT * FROM deals WHERE broker_position_id = '1' ORDER BY occurred_at_utc").fetchall()
    assert [r["broker_deal_id"] for r in rows] == ["901", "902"]
    assert rows[0]["profit"] == 4.0
    assert rows[1]["profit"] == 6.0

    # the position closes at the LATEST deal's time.
    row = db.execute("SELECT status, closed_at_utc FROM positions WHERE broker_position_id = '1'").fetchone()
    assert row["status"] == "CLOSED"
    assert row["closed_at_utc"] == 4500

    events = [e for e in get_chain_events(db, "recon-multi:position:1") if e.event_type == "POSITION_CLOSED"]
    assert len(events) == 1
    assert events[0].payload["deal_count"] == 2
    assert events[0].payload["total_profit"] == pytest.approx(10.0)
    assert events[0].payload["total_commission"] == pytest.approx(-0.5)
    assert events[0].payload["total_swap"] == pytest.approx(-0.1)


def test_run_reconciliation_recovers_from_out_by_deal(db):
    # Hedging-account close-by-opposite-position semantics (entry=3).
    _insert_local_position(db, broker_position_id="1", opened_at_utc=1000)
    out_by = _closing_deal(entry=3, ticket=903, position_id=1, time=4500, profit=7.0)
    gw = FakeGateway(historical_deals=[out_by])

    report = run_reconciliation(db, gw, "recon-outby", now_utc=5000)
    assert report.status == RECOVERED
    deal_row = db.execute("SELECT * FROM deals WHERE broker_deal_id = '903'").fetchone()
    assert deal_row is not None
    assert deal_row["profit"] == 7.0


def test_recovered_deal_order_id_is_null_when_unprovable(db):
    # finding #15: never falsely link a closing deal to the entry order
    # (or any other unproven local order) -- NULL when it can't be shown.
    _insert_local_position(db, broker_position_id="1", opened_at_utc=1000)
    closing = _closing_deal(order=999999, ticket=904, position_id=1, time=4500)  # no local order with this ticket
    gw = FakeGateway(historical_deals=[closing])

    run_reconciliation(db, gw, "recon-nulllink", now_utc=5000)
    deal_row = db.execute("SELECT order_id FROM deals WHERE broker_deal_id = '904'").fetchone()
    assert deal_row["order_id"] is None


def test_recovery_is_atomic_position_deals_and_journal_together(db):
    # finding #16: if the atomic recovery transaction is interrupted
    # before COMMIT, NONE of its writes (position, deals, journal) are
    # observed -- simulated here by making the journal insert fail after
    # the position/deal writes have already been issued inside the same
    # transaction, and confirming everything rolls back together.
    import sqlite3 as _sqlite3

    from adaptive_scalper.execution import reconciliation as recon_module

    _insert_local_position(db, broker_position_id="1", opened_at_utc=1000)
    closing = _closing_deal(ticket=905, position_id=1, time=4500)
    gw = FakeGateway(historical_deals=[closing])

    def failing_append_event_locked(*a, **kw):
        raise _sqlite3.IntegrityError("simulated failure after position/deal writes")

    original = recon_module._append_event_locked
    recon_module._append_event_locked = failing_append_event_locked
    try:
        with pytest.raises(_sqlite3.IntegrityError):
            run_reconciliation(db, gw, "recon-atomic", now_utc=5000)
    finally:
        recon_module._append_event_locked = original

    # NONE of the position update or deal insert survived the rollback.
    row = db.execute("SELECT status FROM positions WHERE broker_position_id = '1'").fetchone()
    assert row["status"] == "OPEN"
    assert db.execute("SELECT * FROM deals WHERE broker_deal_id = '905'").fetchone() is None


# --------------------------------------------------------------------------
# reconcile_pending_orders / run_reconciliation pending-order integration
# (external review finding #12, 2026-09-21)
# --------------------------------------------------------------------------

def _local_order(**overrides):
    defaults = dict(
        id=1, client_request_id="req-1", chain_key=None, canonical_symbol="XAUUSD",
        broker_symbol="XAUUSDm", direction="BUY", requested_volume=0.05, stop_loss=None,
        take_profit=None, state=OrderState.RESTING, broker_order_id="500", broker_position_id=None,
        last_broker_retcode=None, last_broker_comment=None, created_at_utc=1000, updated_at_utc=1000,
    )
    defaults.update(overrides)
    from adaptive_scalper.execution.store import OrderRecord
    return OrderRecord(**defaults)


def _pending(broker_order_id="500", **overrides):
    defaults = dict(broker_order_id=broker_order_id, symbol="XAUUSDm", direction="BUY", volume=0.05, price=1990.0, magic=0, comment="")
    defaults.update(overrides)
    return PendingOrderSnapshot(**defaults)


def test_reconcile_pending_orders_no_findings_when_matching():
    local = [_local_order()]
    broker = [_pending()]
    findings = reconcile_pending_orders(local, broker)
    assert findings == []


def test_reconcile_pending_orders_detects_orphan_broker_order():
    findings = reconcile_pending_orders([], [_pending()])
    assert len(findings) == 1
    assert findings[0].finding_type == ORPHAN_BROKER_ORDER
    assert findings[0].broker_position_id == "500"  # holds broker_order_id here


def test_reconcile_pending_orders_detects_missing_local_order():
    findings = reconcile_pending_orders([_local_order()], [])
    assert len(findings) == 1
    assert findings[0].finding_type == MISSING_LOCAL_ORDER


def test_reconcile_pending_orders_detects_symbol_and_direction_mismatch():
    findings = reconcile_pending_orders([_local_order()], [_pending(symbol="GBPJPYm", direction="SELL")])
    types = {f.finding_type for f in findings}
    assert types == {PENDING_MISMATCH}
    assert len(findings) == 2  # one for symbol, one for direction


def test_run_reconciliation_blocks_on_orphan_broker_order(db):
    gw = FakeGateway()
    gw.inject_pending_order(_pending())
    report = run_reconciliation(db, gw, "recon-order-1", now_utc=5000)
    assert report.status == BLOCKING_MISMATCH
    assert any(f.finding_type == ORPHAN_BROKER_ORDER for f in report.order_findings)
    incidents = get_unresolved_incidents(db)
    assert len(incidents) == 1


def test_run_reconciliation_resolves_order_incident_after_pending_order_disappears(db):
    gw = FakeGateway()
    gw.inject_pending_order(_pending())
    run_reconciliation(db, gw, "recon-order-present", now_utc=5000)
    assert len(get_unresolved_incidents(db)) == 1

    gw.remove_pending_order("500")
    report = run_reconciliation(db, gw, "recon-order-gone", now_utc=5100)

    assert report.status == CLEAN
    assert get_unresolved_incidents(db) == []


def test_reconciliation_does_not_auto_resolve_unknown_or_unscoped_incidents(db):
    record_incident(db, "UNKNOWN_OUTCOME", "still needs explicit recovery", now_utc=4900)
    record_incident(
        db, "RECONCILIATION_MISMATCH", "manual unscoped incident", dedup_key="manual:incident", now_utc=4900,
    )

    report = run_reconciliation(db, FakeGateway(), "recon-clean", now_utc=5000)

    assert report.status == CLEAN
    unresolved = get_unresolved_incidents(db)
    assert {row["dedup_key"] for row in unresolved} == {None, "manual:incident"}


def test_run_reconciliation_recovers_missing_local_order_as_cancelled(db):
    order = create_order(db, "req-cancel", "XAUUSD", "XAUUSDm", "BUY", 0.05, now_utc=1000)
    transition_order_state(db, order.id, OrderState.SUBMITTED, now_utc=1000)
    transition_order_state(db, order.id, OrderState.RESTING, broker_order_id="500", now_utc=1000)

    gw = FakeGateway(historical_orders=[
        HistoricalOrder(ticket=500, time_setup=1000, time_done=1010, type=0, state=2, magic=0, position_id=0,
                         volume_initial=0.05, volume_current=0.0, price_open=1990.0, sl=0.0, tp=0.0,
                         price_current=1990.0, symbol="XAUUSDm", comment="", external_id=""),
    ])
    # broker no longer reports the pending order at all -- MISSING_LOCAL_ORDER
    report = run_reconciliation(db, gw, "recon-order-2", now_utc=5000)
    assert "500" in report.recovered_order_ids
    assert report.status != BLOCKING_MISMATCH or any(f.finding_type != MISSING_LOCAL_ORDER for f in report.order_findings)

    reloaded = [o for o in get_active_orders(db) if o.id == order.id]
    assert reloaded == []  # no longer active -- CANCELLED is terminal
    final = db.execute("SELECT state FROM orders WHERE id = ?", (order.id,)).fetchone()
    assert final["state"] == "CANCELLED"


def test_run_reconciliation_stays_blocking_when_missing_local_order_unrepairable(db):
    order = create_order(db, "req-unknown", "XAUUSD", "XAUUSDm", "BUY", 0.05, now_utc=1000)
    transition_order_state(db, order.id, OrderState.SUBMITTED, now_utc=1000)
    transition_order_state(db, order.id, OrderState.RESTING, broker_order_id="501", now_utc=1000)

    gw = FakeGateway()  # no historical evidence at all
    report = run_reconciliation(db, gw, "recon-order-3", now_utc=5000)
    assert report.status == BLOCKING_MISMATCH
    assert "501" in report.unrepaired_order_ids
    final = db.execute("SELECT state FROM orders WHERE id = ?", (order.id,)).fetchone()
    assert final["state"] == "RESTING"  # untouched -- never guessed


def test_run_reconciliation_is_idempotent_across_repeated_calls(db):
    gw = FakeGateway()
    gw.inject_open_position(PositionSnapshot("pos-orphan", "XAUUSDm", "BUY", 0.05, 2000.0, 1990.0, 2010.0, 0.0, 0, ""))

    run_reconciliation(db, gw, "recon-4", now_utc=5000)
    run_reconciliation(db, gw, "recon-4", now_utc=6000)
    # each call independently records its own finding — not deduplicated
    # across runs (that's the caller's job, e.g. resolve_incident) — but
    # both calls must succeed without error and both must journal.
    events = get_chain_events(db, "recon-4")
    assert len(events) == 2
    assert all(e.event_type == "RECONCILIATION_ACTION" for e in events)


# --------------------------------------------------------------------------
# broker -> canonical symbol translation (BUG_BACKLOG #7)
# --------------------------------------------------------------------------

def test_broker_symbols_are_translated_through_the_persisted_mapping(tmp_path):
    from adaptive_scalper.execution.reconciliation import broker_to_canonical_map, canonical_for_broker_symbol
    from adaptive_scalper.gateway.symbol_resolver import ResolutionResult, persist_resolution
    from adaptive_scalper.persistence import connect, migrate

    conn = connect(tmp_path / "map.sqlite3")
    migrate(conn)
    persist_resolution(conn, ResolutionResult("XAUUSD", "XAUUSDm", True, "exact"))
    persist_resolution(conn, ResolutionResult("BTCUSD", None, False, "not offered"))
    mapping = broker_to_canonical_map(conn)
    assert mapping == {"XAUUSDm": "XAUUSD"}
    assert canonical_for_broker_symbol(mapping, "XAUUSDm") == "XAUUSD"
    assert canonical_for_broker_symbol(mapping, "EURUSD") == "UNMAPPED:EURUSD"  # never passed off as canonical


def test_a_proven_symbol_mismatch_is_a_blocking_finding():
    findings = reconcile_positions([_local(canonical_symbol="XAUUSD")], [_broker(canonical_symbol="GBPJPY")])
    assert [f.finding_type for f in findings] == [RECONCILIATION_MISMATCH]
    assert "symbol mismatch" in findings[0].detail


def test_an_unmapped_broker_symbol_is_labelled_not_flagged_as_a_mismatch():
    findings = reconcile_positions([_local()], [_broker(canonical_symbol="UNMAPPED:XAUUSDm")])
    assert findings == []
    orphan = reconcile_positions([], [_broker(canonical_symbol="UNMAPPED:EURUSD")])
    assert "UNMAPPED:EURUSD" in orphan[0].detail
