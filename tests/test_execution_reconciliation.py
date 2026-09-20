"""Tests for position reconciliation (directive section 31)."""

from __future__ import annotations

import pytest

from adaptive_scalper.execution.reconciliation import (
    BLOCKING_MISMATCH,
    CLEAN,
    MISSING_LOCAL_POSITION,
    ORPHAN_BROKER_POSITION,
    RECONCILIATION_MISMATCH,
    RECOVERED,
    BrokerPositionSnapshot,
    LocalPositionRecord,
    classify_reconciliation,
    get_unresolved_incidents,
    has_dangerous_unresolved_unknown,
    reconcile_positions,
    record_incident,
    resolve_incident,
    run_reconciliation,
)
from adaptive_scalper.gateway.fake_gateway import FakeGateway
from adaptive_scalper.gateway.types import PositionSnapshot
from adaptive_scalper.journal.queries import get_chain_events
from adaptive_scalper.persistence import connect, migrate


def _local(**overrides) -> LocalPositionRecord:
    defaults = dict(
        id=1, broker_position_id="pos-1", canonical_symbol="XAUUSD", direction="BUY",
        volume=0.05, entry_price=2000.0, initial_monetary_risk=20.0, strategy_key="momentum_continuation",
    )
    defaults.update(overrides)
    return LocalPositionRecord(**defaults)


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


def test_run_reconciliation_recovered_does_not_record_incident(db):
    _insert_local_position(db)
    gw = FakeGateway()  # broker reports nothing — position must have closed

    report = run_reconciliation(db, gw, "recon-3", now_utc=5000)
    assert report.status == RECOVERED
    assert get_unresolved_incidents(db) == []


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
