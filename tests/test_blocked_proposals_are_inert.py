"""ASN-035 (2026-10-09 audit): the 612 PROPOSED rows in the production
`orders` table are submissions a gate blocked BEFORE order_send. They stay
PROPOSED by design (tests/test_execution_service.py and
tests/test_broker_chaos.py pin it: nothing was sent, and a crash before
order_check stays retryable) and each DEMO bar is decided once
(`last_decided_bar`), so they are never retried.

These tests pin the property that makes them harmless audit records: a
PROPOSED order without a broker id is never an active order, never pending
exposure, never a duplicate and never quarantined as interrupted."""

from __future__ import annotations

import pytest

from adaptive_scalper.execution.recovery import quarantine_interrupted_submissions
from adaptive_scalper.execution.state_machine import OrderState
from adaptive_scalper.execution.store import create_order, get_active_orders
from adaptive_scalper.persistence import connect, migrate
from adaptive_scalper.runtime.demo import _OPEN_EXPOSURE_ORDER_STATES


@pytest.fixture()
def db(tmp_path):
    conn = connect(tmp_path / "proposed.sqlite3")
    migrate(conn)
    yield conn
    conn.close()


def _blocked_proposals(db, n=50):
    for i in range(n):
        symbol = "XAUUSD" if i % 2 else "BTCUSD"
        create_order(db, f"{symbol}:{1_790_000_000 + 300 * i}:statistical_reversion:BUY", symbol, symbol, "BUY",
                     0.01, now_utc=1_790_000_000 + 300 * i)


def test_blocked_proposals_are_not_active_orders(db):
    _blocked_proposals(db)
    assert db.execute("SELECT COUNT(*) FROM orders WHERE state = 'PROPOSED'").fetchone()[0] == 50
    assert get_active_orders(db) == []          # no duplicate block, no reconciliation comparison


def test_proposed_is_not_an_exposure_state():
    assert OrderState.PROPOSED.value not in _OPEN_EXPOSURE_ORDER_STATES


def test_blocked_proposals_are_never_quarantined_as_interrupted(db):
    _blocked_proposals(db, n=5)
    assert quarantine_interrupted_submissions(db, now_utc=1_791_000_000) == []
    assert db.execute("SELECT COUNT(*) FROM orders WHERE state = 'PROPOSED'").fetchone()[0] == 5
