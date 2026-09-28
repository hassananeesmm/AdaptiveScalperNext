"""ASN-022: the broker's own SL/TP execution order of one of OUR open positions,
seen for an instant in orders_get(), is a PROTECTIVE_CLOSE_IN_PROGRESS
(informational, non-blocking) -- never an ORPHAN_BROKER_ORDER -- but only while
every condition holds and only for PROTECTIVE_CLOSE_GRACE_SECONDS. Anything
else stays a blocking orphan (fail closed). FakeGateway + temp SQLite only.
"""

from __future__ import annotations

import dataclasses
from types import SimpleNamespace

import pytest

from adaptive_scalper.execution.reconciliation import (
    BLOCKING_MISMATCH,
    CLEAN,
    ORPHAN_BROKER_ORDER,
    PROTECTIVE_CLOSE_GRACE_SECONDS,
    PROTECTIVE_CLOSE_IN_PROGRESS,
    RECOVERED,
    LocalPositionRecord,
    get_unresolved_incidents,
    reconcile_pending_orders,
    run_reconciliation,
)
from adaptive_scalper.gateway.fake_gateway import FakeGateway
from adaptive_scalper.gateway.mt5_gateway import _pending_order_snapshot
from adaptive_scalper.gateway.types import HistoricalDeal, PendingOrderSnapshot, PositionSnapshot
from adaptive_scalper.journal.queries import get_chain_events
from adaptive_scalper.persistence import connect, migrate

MAGIC = 240924
NOW = 10_000


@pytest.fixture
def db(tmp_path):
    conn = connect(tmp_path / "asn022.sqlite3")
    migrate(conn)
    yield conn
    conn.close()


def _position(**overrides) -> LocalPositionRecord:
    base = dict(id=1, broker_position_id="1966991469", canonical_symbol="XAUUSD", direction="BUY", volume=0.05,
                entry_price=4270.0, initial_monetary_risk=23.5, strategy_key="microstructure_acceleration",
                opened_at_utc=NOW - 600)
    base.update(overrides)
    return LocalPositionRecord(**base)


def _sl_order(**overrides) -> PendingOrderSnapshot:
    """The live pattern: MARKET SELL closing our BUY, our magic, broker comment."""
    base = dict(broker_order_id="1966998439", symbol="XAUUSD", direction="SELL", volume=0.05, price=4265.17,
                magic=MAGIC, comment="[sl 4265.17]", order_type=1, position_id="1966991469", time_setup_utc=NOW)
    base.update(overrides)
    return PendingOrderSnapshot(**base)


def _classify(order, *, positions=None, now=NOW, magic=MAGIC):
    positions = {p.broker_position_id: p for p in (positions or [_position()])}
    findings = reconcile_pending_orders([], [order], local_open_positions=positions, now_utc=now, own_magic=magic)
    assert len(findings) == 1
    return findings[0].finding_type


# ---------------------------------------------------------------------------
# classification
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("comment", ["[sl 4265.17]", "[tp 4290.00]", "[SL 1.0]"])
def test_the_brokers_own_sl_or_tp_execution_is_a_protective_close(comment):
    assert _classify(_sl_order(comment=comment)) == PROTECTIVE_CLOSE_IN_PROGRESS


def test_a_short_sell_position_closed_by_a_buy_is_also_recognised():
    pos = _position(direction="SELL")
    assert _classify(_sl_order(direction="BUY", order_type=0), positions=[pos]) == PROTECTIVE_CLOSE_IN_PROGRESS


@pytest.mark.parametrize("case,overrides", [
    ("pending order type (BUY_LIMIT)", dict(order_type=2)),
    ("pending order type (SELL_STOP)", dict(order_type=5)),
    ("order type not reported", dict(order_type=None)),
    ("not one of our open positions", dict(position_id="999")),
    ("no position id", dict(position_id=None)),
    ("same direction as the position (adds exposure)", dict(direction="BUY", order_type=0)),
    ("volume larger than the position", dict(volume=0.06)),
    ("another expert's magic", dict(magic=770115)),
    ("manual close (magic 0)", dict(magic=0)),
    ("no broker comment", dict(comment="")),
    ("stop-out is not a protective SL/TP", dict(comment="[so 50.00%]")),
    ("our own close request", dict(comment="ASN exit")),
    ("creation time not reported", dict(time_setup_utc=None)),
    ("older than the grace window", dict(time_setup_utc=NOW - PROTECTIVE_CLOSE_GRACE_SECONDS - 1)),
    ("implausibly in the future", dict(time_setup_utc=NOW + 6)),
])
def test_every_condition_is_required_otherwise_it_stays_a_blocking_orphan(case, overrides):
    assert _classify(_sl_order(**overrides)) == ORPHAN_BROKER_ORDER, case


def test_without_our_magic_or_a_clock_there_is_no_exception():
    assert _classify(_sl_order(), magic=None) == ORPHAN_BROKER_ORDER
    assert _classify(_sl_order(), now=None) == ORPHAN_BROKER_ORDER
    # the historical call signature (no position context) is unchanged: orphan
    assert reconcile_pending_orders([], [_sl_order()])[0].finding_type == ORPHAN_BROKER_ORDER


def test_the_grace_window_is_inclusive_and_then_closes():
    assert _classify(_sl_order(), now=NOW + PROTECTIVE_CLOSE_GRACE_SECONDS) == PROTECTIVE_CLOSE_IN_PROGRESS
    assert _classify(_sl_order(), now=NOW + PROTECTIVE_CLOSE_GRACE_SECONDS + 1) == ORPHAN_BROKER_ORDER
    assert _classify(_sl_order(), now=NOW - 5) == PROTECTIVE_CLOSE_IN_PROGRESS  # small broker/local clock skew


def test_a_genuine_orphan_next_to_a_protective_close_still_blocks():
    positions = {"1966991469": _position()}
    stray = _sl_order(broker_order_id="555", position_id=None, comment="", order_type=2, direction="BUY")
    findings = reconcile_pending_orders([], [_sl_order(), stray], local_open_positions=positions, now_utc=NOW,
                                        own_magic=MAGIC)
    assert sorted(f.finding_type for f in findings) == [ORPHAN_BROKER_ORDER, PROTECTIVE_CLOSE_IN_PROGRESS]


# ---------------------------------------------------------------------------
# run_reconciliation: status, incidents, journal, time bound
# ---------------------------------------------------------------------------

def _insert_open(conn, position: LocalPositionRecord) -> None:
    conn.execute(
        "INSERT INTO positions (broker_position_id, canonical_symbol, direction, volume, entry_price, "
        "initial_monetary_risk, strategy_key, status, opened_at_utc) VALUES (?, ?, ?, ?, ?, ?, ?, 'OPEN', ?)",
        (position.broker_position_id, position.canonical_symbol, position.direction, position.volume,
         position.entry_price, position.initial_monetary_risk, position.strategy_key, position.opened_at_utc),
    )
    conn.commit()


def _broker_position(pos: LocalPositionRecord) -> PositionSnapshot:
    return PositionSnapshot(pos.broker_position_id, "XAUUSD", pos.direction, pos.volume, pos.entry_price,
                            4265.17, 4290.0, -2.0, MAGIC, "ASN")


def test_run_reconciliation_is_clean_and_audited_while_the_broker_closes_our_position(db):
    pos = _position()
    _insert_open(db, pos)
    gw = FakeGateway()
    gw.inject_open_position(_broker_position(pos))
    gw.inject_pending_order(_sl_order())

    report = run_reconciliation(db, gw, "recon-asn022", now_utc=NOW + 1, journal_clean=False, own_magic=MAGIC)
    assert report.status == CLEAN
    assert [f.finding_type for f in report.order_findings] == [PROTECTIVE_CLOSE_IN_PROGRESS]
    assert get_unresolved_incidents(db) == []
    events = get_chain_events(db, "recon-asn022")  # journaled although CLEAN runs are normally silent
    assert len(events) == 1 and events[0].payload["status"] == CLEAN
    assert events[0].payload["order_findings"] == [{"type": PROTECTIVE_CLOSE_IN_PROGRESS,
                                                    "broker_order_id": "1966998439"}]


def test_a_protective_close_that_lingers_past_the_grace_window_blocks_and_then_clears(db):
    pos = _position()
    _insert_open(db, pos)
    gw = FakeGateway()
    gw.inject_open_position(_broker_position(pos))
    gw.inject_pending_order(_sl_order())

    late = NOW + PROTECTIVE_CLOSE_GRACE_SECONDS + 1
    report = run_reconciliation(db, gw, "recon-late", now_utc=late, journal_clean=False, own_magic=MAGIC)
    assert report.status == BLOCKING_MISMATCH
    assert [i["incident_type"] for i in get_unresolved_incidents(db)] == ["RECONCILIATION_MISMATCH"]

    gw.remove_pending_order("1966998439")
    report = run_reconciliation(db, gw, "recon-after", now_utc=late + 1, journal_clean=False, own_magic=MAGIC)
    assert report.status == CLEAN and get_unresolved_incidents(db) == []


def test_callers_without_our_magic_keep_the_previous_strict_behaviour(db):
    pos = _position()
    _insert_open(db, pos)
    gw = FakeGateway()
    gw.inject_open_position(_broker_position(pos))
    gw.inject_pending_order(_sl_order())
    report = run_reconciliation(db, gw, "recon-strict", now_utc=NOW + 1)  # e.g. execution/close.py
    assert report.status == BLOCKING_MISMATCH
    assert any(f.finding_type == ORPHAN_BROKER_ORDER for f in report.order_findings)


def test_the_live_race_position_already_gone_is_recovered_not_blocked(db):
    """Observed live: in the same second the broker no longer lists the
    position, its closing deal exists, and the SL order is still listed."""
    pos = _position(broker_position_id="1966991469")
    _insert_open(db, pos)
    closing = HistoricalDeal(ticket=1581451303, order=1966998439, time=NOW, type=1, entry=1, magic=MAGIC,
                             position_id=1966991469, volume=0.05, price=4265.17, commission=0.0, swap=0.0,
                             profit=-24.15, fee=0.0, symbol="XAUUSD", comment="[sl 4265.17]", external_id="")
    gw = FakeGateway(historical_deals=[closing])
    gw.inject_pending_order(_sl_order())

    report = run_reconciliation(db, gw, "recon-race", now_utc=NOW + 1, journal_clean=False, own_magic=MAGIC)
    assert report.status == RECOVERED
    assert report.recovered_position_ids == ["1966991469"]
    assert [f.finding_type for f in report.order_findings] == [PROTECTIVE_CLOSE_IN_PROGRESS]
    assert get_unresolved_incidents(db) == []
    status = db.execute("SELECT status FROM positions WHERE broker_position_id = '1966991469'").fetchone()[0]
    assert status == "CLOSED"


# ---------------------------------------------------------------------------
# MT5 mapping and the DEMO pre-send check
# ---------------------------------------------------------------------------

def _raw_order(**overrides):
    base = dict(ticket=1966998439, symbol="XAUUSD", type=1, volume_current=0.05, price_open=4265.17, magic=MAGIC,
                comment="[sl 4265.17]", position_id=1966991469, time_setup=NOW)
    base.update(overrides)
    return SimpleNamespace(**base)


def test_the_mt5_snapshot_carries_type_position_and_utc_creation_time():
    snap = _pending_order_snapshot(_raw_order(), "UTC")
    assert (snap.order_type, snap.position_id, snap.time_setup_utc, snap.direction) == (1, "1966991469", NOW, "SELL")
    shifted = _pending_order_snapshot(_raw_order(time_setup=1_790_000_000 + 3 * 3600), "UTC+2/US_DST")
    assert shifted.time_setup_utc == 1_790_000_000  # server clock (UTC+3 in US summer time) converted to UTC
    bare = _pending_order_snapshot(_raw_order(position_id=0, time_setup=0), "UTC")
    assert bare.position_id is None and bare.time_setup_utc is None


def test_positional_snapshots_built_by_older_callers_never_qualify():
    legacy = PendingOrderSnapshot("1", "XAUUSD", "SELL", 0.05, 4265.0, MAGIC, "[sl 4265.00]")
    assert dataclasses.astuple(legacy)[-3:] == (None, None, None)
    assert _classify(legacy) == ORPHAN_BROKER_ORDER


def test_the_demo_pre_send_check_treats_the_protective_close_as_clean(tmp_path):
    from runtime_helpers import STEP, step
    from test_multi_position import _start

    engine, conn, gateway, clock, stub = _start(tmp_path)
    stub.fire = {"XAUUSD": "BUY"}
    step(engine, clock, seconds=STEP, tick=4)
    broker = next(p for p in gateway.positions_get() if p.symbol == "XAUUSD")
    now = int(clock.now)
    gateway.inject_pending_order(_sl_order(broker_order_id="42", position_id=broker.broker_position_id,
                                           volume=broker.volume, magic=engine.config.runtime.magic,
                                           time_setup_utc=now))
    assert engine.demo.readonly_reconciliation_status() == CLEAN
    gateway.inject_pending_order(_sl_order(broker_order_id="43", position_id="unknown", time_setup_utc=now))
    assert engine.demo.readonly_reconciliation_status() == BLOCKING_MISMATCH
