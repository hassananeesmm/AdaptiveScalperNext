"""Conservative daily-loss accounting (risk.daily_loss): deposits never
offset losses, foreign-EA or manual profits never mask them, every cost of a
project deal counts, the UTC day boundary and day-start baseline are explicit
and restart-proof, and unusable evidence blocks entries."""

from __future__ import annotations

import math
from dataclasses import replace

import pytest
from chaos_harness import demo_account
from runtime_helpers import STEP, T0, FakeClock, build_engine, step

from adaptive_scalper.core.kill_switch import KillSwitchStatus
from adaptive_scalper.core.kill_switch import bootstrap as bootstrap_kill_switch
from adaptive_scalper.core.kill_switch import get_state as get_kill_switch_state
from adaptive_scalper.core.operator_authority import OperatorAuthority
from adaptive_scalper.gateway.types import HistoricalDeal, PositionSnapshot
from adaptive_scalper.risk.daily_loss import (
    DAY_SECONDS,
    DailyLossEvidenceError,
    check_baseline,
    collect,
    compute,
    utc_day_start,
)
from adaptive_scalper.risk.governor import effective_daily_loss_pct

MAGIC = 240924
NOW = 1_792_000_000 - 1_792_000_000 % DAY_SECONDS + 12 * 3600       # 12:00 UTC
ACCOUNT = demo_account(balance=10000.0, equity=10000.0)


def deal(ticket, *, magic=MAGIC, position=1, kind=0, entry=1, profit=0.0, commission=0.0, swap=0.0, fee=0.0,
         at=None):
    return HistoricalDeal(ticket, ticket, NOW - 3600 + ticket if at is None else at, kind, entry, magic, position,
                          0.1, 100.0, commission, swap, profit, fee, "XAUUSD", "", "")


def position(pid, *, magic=MAGIC, profit=0.0, swap=0.0):
    return PositionSnapshot(str(pid), "XAUUSD", "BUY", 0.1, 100.0, 90.0, 110.0, profit, magic, "", swap=swap)


def loss_of(deals=(), positions=(), *, balance=10000.0, own_ids=(), now=NOW):
    floating = sum(p.profit + p.swap for p in positions)
    account = replace(ACCOUNT, balance=balance, equity=balance + floating)
    return compute(account, deals, positions, own_magic=MAGIC, own_position_ids=own_ids, now=now), account


def pct(loss, account):
    return effective_daily_loss_pct(realized_pnl=loss.realized, floating_pnl=loss.floating,
                                    day_start_equity=loss.day_start_balance, equity=account.equity)


def test_foreign_ea_profit_cannot_mask_a_project_loss():
    deals = [deal(1, profit=-250.0), deal(2, magic=550022, position=2, profit=300.0)]
    loss, account = loss_of(deals, balance=10050.0)
    assert loss.project_realized == -250.0 and loss.foreign_realized == 300.0
    assert pct(loss, account) == pytest.approx(2.5)                 # the account alone would show +0.5 %


def test_a_foreign_or_manual_loss_still_counts():
    loss, account = loss_of([deal(1, magic=0, position=9, profit=-250.0)], balance=9750.0)
    assert pct(loss, account) == pytest.approx(100 * 250 / 9750)      # denominator: min(day start, equity)


def test_a_deposit_never_offsets_a_trading_loss_and_is_not_in_the_baseline():
    deals = [deal(1, profit=-250.0), deal(2, magic=0, position=0, kind=2, profit=1000.0)]
    loss, account = loss_of(deals, balance=10750.0)
    assert loss.cash_flows == 1000.0 and loss.day_start_balance == pytest.approx(10000.0)
    assert pct(loss, account) == pytest.approx(2.5)


def test_a_withdrawal_is_not_a_trading_loss():
    loss, account = loss_of([deal(1, magic=0, position=0, kind=2, profit=-500.0)], balance=9500.0)
    assert loss.realized == 0.0 and pct(loss, account) == 0.0


def test_every_transaction_cost_of_a_project_deal_counts():
    deals = [deal(1, entry=0, commission=-50.0), deal(2, commission=-50.0, swap=-100.0, fee=-50.0)]
    loss, account = loss_of(deals, balance=9750.0)
    assert loss.project_realized == pytest.approx(-250.0)
    assert pct(loss, account) == pytest.approx(100 * 250 / 9750)      # denominator: min(day start, equity)


def test_partial_closes_and_a_broker_side_close_of_a_project_position_count_as_project():
    deals = [deal(1, profit=-100.0), deal(2, profit=-100.0),             # two partial closes, project magic
             deal(3, magic=0, position=1, profit=-50.0)]                  # stop-loss executed by the broker
    loss, _ = loss_of(deals, balance=9750.0, own_ids={"1"})
    assert loss.project_realized == pytest.approx(-250.0) and loss.foreign_realized == 0.0


def test_simultaneous_positions_floating_loss_counts_while_a_foreign_profit_is_ignored():
    positions = [position(1, profit=-150.0, swap=-10.0), position(2, profit=-90.0),     # two project positions
                 position(3, magic=0, profit=1900.0)]                                    # manual, no stop
    loss, account = loss_of(positions=positions)
    assert loss.floating == pytest.approx(-250.0) and loss.reconciled
    assert pct(loss, account) == pytest.approx(2.5)


def test_an_unexplained_equity_drop_still_counts_but_does_not_reconcile():
    account = replace(ACCOUNT, equity=9700.0)
    loss = compute(account, (), (), own_magic=MAGIC, own_position_ids=(), now=NOW)
    assert loss.floating == -300.0 and not loss.reconciled


@pytest.mark.parametrize("deals,positions", [
    ([deal(1), deal(1)], ()),                                              # duplicate ticket
    ([deal(1, profit=math.nan)], ()),                                      # non-finite amount
    ([deal(1, at=NOW - 13 * 3600)], ()),                                   # yesterday's deal
    ((), (replace(position(1), swap=None),)),                              # swap unavailable
])
def test_unusable_evidence_raises(deals, positions):
    with pytest.raises(DailyLossEvidenceError):
        compute(ACCOUNT, deals, positions, own_magic=MAGIC, own_position_ids=(), now=NOW)


def test_the_utc_day_boundary_and_baseline_roll_over_at_midnight():
    midnight = utc_day_start(NOW) + DAY_SECONDS
    today, _ = loss_of([deal(1, profit=-100.0)], balance=9900.0)
    stored = check_baseline(None, today)
    assert stored == {"utc_day": NOW // DAY_SECONDS, "day_start_balance": 10000.0}
    tomorrow, _ = loss_of(balance=9900.0, now=midnight + 1)               # yesterday's deal no longer in range
    assert check_baseline(stored, tomorrow) == {"utc_day": midnight // DAY_SECONDS, "day_start_balance": 9900.0}


def test_a_baseline_that_changes_within_the_day_means_history_was_missing_or_revised():
    first, _ = loss_of([deal(1, profit=-100.0)], balance=9900.0)
    stored = check_baseline(None, first)
    vanished, _ = loss_of((), balance=9900.0)                              # today's loss deal disappeared
    with pytest.raises(DailyLossEvidenceError, match="history changed"):
        check_baseline(stored, vanished)


def test_collect_reads_only_today_and_refuses_an_account_that_never_settles():
    class Gateway:
        ticks = 0

        def account_info(self):
            Gateway.ticks += 1
            return replace(ACCOUNT, equity=10000.0 + Gateway.ticks)

        def history_deals_get(self, start, end):
            assert start == utc_day_start(NOW) and end == NOW
            return []

        def positions_get(self):
            return []
    with pytest.raises(DailyLossEvidenceError, match="moved"):
        collect(Gateway(), NOW)


# ---- runtime gates -------------------------------------------------------

START_AT = T0 + 60 * STEP + 10


def _started(tmp_path):
    clock = FakeClock(START_AT)
    engine, conn, gateway = build_engine(tmp_path, mode="DEMO", clock=clock)
    bootstrap_kill_switch(conn, OperatorAuthority("test-operator"), reason="test setup")
    engine.startup()
    return clock, engine, conn, gateway


def _runtime_deal(ticket, now, *, magic, position, profit):
    return HistoricalDeal(ticket, ticket, now - 5, 0, 1, magic, position, 0.1, 1.0, 0.0, 0.0, profit, 0.0,
                          "XAUUSD", "", "")


def test_runtime_foreign_profit_today_cannot_lift_the_daily_limit(tmp_path):
    clock, engine, _conn, gateway = _started(tmp_path)
    now = int(clock.now)
    gateway._historical_deals += [_runtime_deal(9001, now, magic=engine.config.runtime.magic, position=77, profit=-250.0),
                                  _runtime_deal(9002, now, magic=550022, position=78, profit=300.0)]
    gateway.set_account(demo_account(balance=10050.0, equity=10050.0))
    assert engine.demo._daily_loss(now, gateway.account_info())[0] == pytest.approx(2.5)
    step(engine, clock, seconds=3)                       # the position cycle runs the circuit breaker
    assert get_kill_switch_state(_conn).status == KillSwitchStatus.ENGAGED
    assert engine.demo.global_entry_block(int(clock.now)) is not None
    assert gateway.calls["order_send"] == 0


def test_runtime_unusable_evidence_blocks_entries(tmp_path):
    clock, engine, _conn, gateway = _started(tmp_path)
    now = int(clock.now)
    duplicate = _runtime_deal(9101, now, magic=0, position=90, profit=0.0)
    gateway._historical_deals += [duplicate, duplicate]
    with pytest.raises(DailyLossEvidenceError):
        engine.demo._daily_loss_evidence(now)
    block = engine.demo.global_entry_block(now)
    assert block is not None
    assert gateway.calls["order_send"] == 0


def test_runtime_baseline_is_persisted_and_detects_vanished_history_after_a_restart(tmp_path):
    clock, engine, _conn, gateway = _started(tmp_path)
    now = int(clock.now)
    gateway._historical_deals.append(_runtime_deal(9201, now, magic=engine.config.runtime.magic, position=91,
                                                   profit=-100.0))
    gateway.set_account(demo_account(balance=9900.0, equity=9900.0))
    loss, _ = engine.demo._daily_loss_evidence(now)
    assert loss.day_start_balance == pytest.approx(10000.0)
    # A fresh runtime on the same database (a restart) reads the persisted baseline:
    restarted, conn2, _ = build_engine(tmp_path, mode="DEMO", clock=clock, gateway=gateway)
    restarted.startup()
    try:
        gateway._historical_deals = [d for d in gateway._historical_deals if d.ticket != 9201]
        with pytest.raises(DailyLossEvidenceError, match="history changed"):
            restarted.demo._daily_loss_evidence(now + 1)
        step(engine, clock, seconds=3)
        assert gateway.calls["order_send"] == 0
    finally:
        conn2.close()
