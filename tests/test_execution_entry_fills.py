"""Tests for execution.entry_fills (external review findings #7, #8, #9,
#10, 2026-09-21): entry deal persistence, safe aggregate recomputation
across repeated/partial fills, and a hard stop once R-based management
is already active for a position."""

from __future__ import annotations

import pytest

from adaptive_scalper.execution.entry_fills import (
    RECOMPUTED,
    REFUSED_NO_EVIDENCE,
    REFUSED_STATE_LOCKED,
    record_entry_fills,
)
from adaptive_scalper.execution.position_resolution import EntryFillEvidence
from adaptive_scalper.execution.store import create_order
from adaptive_scalper.gateway.types import HistoricalDeal
from adaptive_scalper.persistence import connect, migrate
from adaptive_scalper.position_management import state_store


@pytest.fixture()
def db(tmp_path):
    conn = connect(tmp_path / "test.sqlite3")
    migrate(conn)
    yield conn
    conn.close()


def _deal(**overrides) -> HistoricalDeal:
    defaults = dict(
        ticket=500, order=100, time=1000, type=0, entry=0, magic=7, position_id=900,
        volume=0.05, price=2000.0, commission=-0.5, swap=0.0, profit=0.0, fee=-0.1,
        symbol="XAUUSDm", comment="ASN:abc", external_id="",
    )
    defaults.update(overrides)
    return HistoricalDeal(**defaults)


def _order_row(db):
    return create_order(db, "req-1", "XAUUSD", "XAUUSDm", "BUY", 0.05, now_utc=1000)


def test_records_a_single_fill_and_creates_the_position(db):
    order = _order_row(db)
    evidence = EntryFillEvidence(True, "900", (_deal(),), 0.05, 2000.0, "resolved")

    result = record_entry_fills(
        db, order_id=order.id, canonical_symbol="XAUUSD", direction="BUY", strategy_key="momentum_continuation",
        evidence=evidence, requested_volume=0.05, requested_monetary_risk=20.0, now_utc=1000,
    )
    assert result.status == RECOMPUTED
    assert result.volume == pytest.approx(0.05)
    assert result.entry_price == pytest.approx(2000.0)
    assert result.initial_monetary_risk == pytest.approx(20.0)

    row = db.execute("SELECT * FROM positions WHERE broker_position_id = '900'").fetchone()
    assert row is not None
    assert row["status"] == "OPEN"
    assert row["volume"] == pytest.approx(0.05)
    assert row["entry_price"] == pytest.approx(2000.0)


def test_deal_is_persisted_with_fee_entry_type_deal_type_magic_comment(db):
    order = _order_row(db)
    evidence = EntryFillEvidence(True, "900", (_deal(),), 0.05, 2000.0, "resolved")
    record_entry_fills(
        db, order_id=order.id, canonical_symbol="XAUUSD", direction="BUY", strategy_key="momentum_continuation",
        evidence=evidence, requested_volume=0.05, requested_monetary_risk=20.0, now_utc=1000,
    )
    deal_row = db.execute("SELECT * FROM deals WHERE broker_deal_id = '500'").fetchone()
    assert deal_row is not None
    assert deal_row["fee"] == pytest.approx(-0.1)
    assert deal_row["entry_type"] == "IN"
    assert deal_row["deal_type"] == "BUY"
    assert deal_row["broker_order_ticket"] == "100"
    assert deal_row["magic"] == 7
    assert deal_row["comment"] == "ASN:abc"


def test_second_fill_recomputes_weighted_average_from_all_persisted_deals(db):
    # External review finding #7: a SECOND fill trickling into the SAME
    # broker_position_id must recompute the aggregate from ALL persisted
    # entry deals, never silently keep the first fill's stale values.
    order = _order_row(db)
    first_evidence = EntryFillEvidence(True, "900", (_deal(ticket=500, volume=0.02, price=2000.0),), 0.02, 2000.0, "r1")
    record_entry_fills(
        db, order_id=order.id, canonical_symbol="XAUUSD", direction="BUY", strategy_key="momentum_continuation",
        evidence=first_evidence, requested_volume=0.05, requested_monetary_risk=20.0, now_utc=1000,
    )

    second_evidence = EntryFillEvidence(True, "900", (_deal(ticket=501, volume=0.03, price=2010.0),), 0.03, 2010.0, "r2")
    result = record_entry_fills(
        db, order_id=order.id, canonical_symbol="XAUUSD", direction="BUY", strategy_key="momentum_continuation",
        evidence=second_evidence, requested_volume=0.05, requested_monetary_risk=20.0, now_utc=1010,
    )
    assert result.status == RECOMPUTED
    assert result.volume == pytest.approx(0.05)
    # weighted avg = (2000*0.02 + 2010*0.03) / 0.05 = 2006.0
    assert result.entry_price == pytest.approx(2006.0)
    # per-unit risk = 20.0/0.05 = 400/lot; aggregate for 0.05 lot = 20.0
    assert result.initial_monetary_risk == pytest.approx(20.0)

    row = db.execute("SELECT * FROM positions WHERE broker_position_id = '900'").fetchone()
    assert row["volume"] == pytest.approx(0.05)
    assert row["entry_price"] == pytest.approx(2006.0)

    deal_count = db.execute("SELECT COUNT(*) AS n FROM deals WHERE broker_position_id = '900'").fetchone()["n"]
    assert deal_count == 2


def test_repeated_identical_deal_is_idempotent(db):
    order = _order_row(db)
    evidence = EntryFillEvidence(True, "900", (_deal(),), 0.05, 2000.0, "resolved")
    record_entry_fills(
        db, order_id=order.id, canonical_symbol="XAUUSD", direction="BUY", strategy_key="momentum_continuation",
        evidence=evidence, requested_volume=0.05, requested_monetary_risk=20.0, now_utc=1000,
    )
    result = record_entry_fills(
        db, order_id=order.id, canonical_symbol="XAUUSD", direction="BUY", strategy_key="momentum_continuation",
        evidence=evidence, requested_volume=0.05, requested_monetary_risk=20.0, now_utc=1010,
    )
    assert result.volume == pytest.approx(0.05)  # not doubled
    deal_count = db.execute("SELECT COUNT(*) AS n FROM deals WHERE broker_position_id = '900'").fetchone()["n"]
    assert deal_count == 1


def test_refuses_to_mutate_once_r_management_is_active_but_still_persists_the_deal(db):
    order = _order_row(db)
    first_evidence = EntryFillEvidence(True, "900", (_deal(ticket=500, volume=0.02, price=2000.0),), 0.02, 2000.0, "r1")
    record_entry_fills(
        db, order_id=order.id, canonical_symbol="XAUUSD", direction="BUY", strategy_key="momentum_continuation",
        evidence=first_evidence, requested_volume=0.05, requested_monetary_risk=20.0, now_utc=1000,
    )
    position_row = db.execute("SELECT id FROM positions WHERE broker_position_id = '900'").fetchone()
    state_store.get_or_create_state(db, position_row["id"], initial_monetary_risk=8.0, entry_regime="TRENDING_UP", now_utc=1005)

    second_evidence = EntryFillEvidence(True, "900", (_deal(ticket=501, volume=0.03, price=2010.0),), 0.03, 2010.0, "r2")
    result = record_entry_fills(
        db, order_id=order.id, canonical_symbol="XAUUSD", direction="BUY", strategy_key="momentum_continuation",
        evidence=second_evidence, requested_volume=0.05, requested_monetary_risk=20.0, now_utc=1010,
    )
    assert result.status == REFUSED_STATE_LOCKED

    # the position's aggregate was NOT mutated -- still the first fill's values
    row = db.execute("SELECT volume, entry_price FROM positions WHERE broker_position_id = '900'").fetchone()
    assert row["volume"] == pytest.approx(0.02)
    assert row["entry_price"] == pytest.approx(2000.0)

    # but the second deal WAS persisted -- never dropped
    deal_count = db.execute("SELECT COUNT(*) AS n FROM deals WHERE broker_position_id = '900'").fetchone()["n"]
    assert deal_count == 2

    # and an incident was recorded
    incident = db.execute(
        "SELECT * FROM execution_incidents WHERE incident_type = 'RECONCILIATION_MISMATCH'"
    ).fetchone()
    assert incident is not None


def test_refuses_when_evidence_unresolved(db):
    order = _order_row(db)
    unresolved = EntryFillEvidence(False, None, (), 0.0, None, "no evidence")
    result = record_entry_fills(
        db, order_id=order.id, canonical_symbol="XAUUSD", direction="BUY", strategy_key="momentum_continuation",
        evidence=unresolved, requested_volume=0.05, requested_monetary_risk=20.0, now_utc=1000,
    )
    assert result.status == REFUSED_NO_EVIDENCE
    assert db.execute("SELECT COUNT(*) AS n FROM positions").fetchone()["n"] == 0
