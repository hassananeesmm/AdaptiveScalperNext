"""Tests for position reconciliation (directive section 31)."""

from __future__ import annotations

import pytest

from adaptive_scalper.execution.reconciliation import (
    MISSING_LOCAL_POSITION,
    ORPHAN_BROKER_POSITION,
    RECONCILIATION_MISMATCH,
    BrokerPositionSnapshot,
    LocalPositionRecord,
    get_unresolved_incidents,
    has_dangerous_unresolved_unknown,
    reconcile_positions,
    record_incident,
    resolve_incident,
)
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
