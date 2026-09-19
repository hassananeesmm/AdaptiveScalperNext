"""Tests for estimated-vs-realized cost tracking (directive section 34)."""

from __future__ import annotations

import pytest

from adaptive_scalper.costs.model import estimate_cost
from adaptive_scalper.costs.tracking import record_estimated_cost, record_realized_cost
from adaptive_scalper.persistence import connect, migrate


@pytest.fixture()
def db(tmp_path):
    conn = connect(tmp_path / "test.sqlite3")
    migrate(conn)
    yield conn
    conn.close()


def test_record_estimated_cost_persists_all_components(db):
    cost = estimate_cost(spread_price=1.0, commission_price_equivalent=0.5, uncertainty_margin_pct=0.1)
    obs_id = record_estimated_cost(db, "XAUUSD", cost, chain_key="chain-1", now_utc=1000)
    row = db.execute("SELECT * FROM cost_observations WHERE id = ?", (obs_id,)).fetchone()
    assert row["canonical_symbol"] == "XAUUSD"
    assert row["chain_key"] == "chain-1"
    assert row["estimated_spread_cost"] == pytest.approx(1.0)
    assert row["estimated_total_cost"] == pytest.approx(cost.total_cost)
    assert row["realized_total_cost"] is None


def test_record_realized_cost_computes_prediction_error(db):
    cost = estimate_cost(spread_price=1.0, uncertainty_margin_pct=0.0)  # estimated total = 1.0
    obs_id = record_estimated_cost(db, "XAUUSD", cost, now_utc=1000)

    error = record_realized_cost(
        db, obs_id, realized_spread_cost=1.3, realized_commission_cost=0.0,
        realized_slippage_cost=0.0, realized_swap_cost=0.0, now_utc=2000,
    )
    assert error == pytest.approx(0.3)  # realized 1.3 vs estimated 1.0

    row = db.execute("SELECT * FROM cost_observations WHERE id = ?", (obs_id,)).fetchone()
    assert row["realized_total_cost"] == pytest.approx(1.3)
    assert row["prediction_error"] == pytest.approx(0.3)
    assert row["realized_at_utc"] == 2000


def test_record_realized_cost_negative_error_when_cheaper_than_estimated(db):
    cost = estimate_cost(spread_price=1.0, uncertainty_margin_pct=0.0)
    obs_id = record_estimated_cost(db, "XAUUSD", cost, now_utc=1000)
    error = record_realized_cost(
        db, obs_id, realized_spread_cost=0.7, realized_commission_cost=0.0,
        realized_slippage_cost=0.0, now_utc=2000,
    )
    assert error == pytest.approx(-0.3)


def test_record_realized_cost_raises_for_unknown_observation(db):
    with pytest.raises(ValueError):
        record_realized_cost(db, 9999, realized_spread_cost=1.0, realized_commission_cost=0.0, realized_slippage_cost=0.0)


def test_record_realized_cost_cannot_be_recorded_twice(db):
    cost = estimate_cost(spread_price=1.0, uncertainty_margin_pct=0.0)
    obs_id = record_estimated_cost(db, "XAUUSD", cost, now_utc=1000)
    record_realized_cost(db, obs_id, realized_spread_cost=1.0, realized_commission_cost=0.0, realized_slippage_cost=0.0)
    with pytest.raises(ValueError):
        record_realized_cost(db, obs_id, realized_spread_cost=2.0, realized_commission_cost=0.0, realized_slippage_cost=0.0)


def test_estimated_cost_persists_across_a_fresh_connection(tmp_path):
    path = tmp_path / "test.sqlite3"
    conn1 = connect(path)
    migrate(conn1)
    cost = estimate_cost(spread_price=1.0)
    obs_id = record_estimated_cost(conn1, "XAUUSD", cost, now_utc=1000)
    conn1.close()

    conn2 = connect(path)
    row = conn2.execute("SELECT estimated_total_cost FROM cost_observations WHERE id = ?", (obs_id,)).fetchone()
    assert row["estimated_total_cost"] == pytest.approx(cost.total_cost)
    conn2.close()
