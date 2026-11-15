from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, replace

import pytest
from chaos_harness import demo_account
from runtime_helpers import FakeClock, build_engine

from adaptive_scalper.core.drawdown_recovery import recover
from adaptive_scalper.core.kill_switch import engage, get_state
from adaptive_scalper.core.operator_authority import OperatorAuthority
from adaptive_scalper.gateway.fake_gateway import FakeGateway
from adaptive_scalper.gateway.types import HistoricalDeal, PositionSnapshot
from adaptive_scalper.persistence.database import connect, migrate
from adaptive_scalper.risk.project_drawdown import (
    AccountingPolicy,
    BrokerEvidence,
    DrawdownEvidenceError,
    latest,
    observe,
    observe_gateway,
)

NOW = 1_792_000_000
MAGIC = 12345
ACCOUNT = demo_account(balance=9759.63, equity=9759.63)
POLICY = AccountingPolicy(ACCOUNT.login, ACCOUNT.server, ACCOUNT.currency, MAGIC, NOW - 1000,
                          9759.63, 9759.63, 9759.63, 9759.63)


def deal(ticket, *, magic=MAGIC, entry=0, profit=0.0, commission=0.0, swap=0.0, fee=0.0,
         position=1, kind=0, volume=0.1):
    return HistoricalDeal(ticket, ticket, NOW - 100 + ticket, kind, entry, magic, position, volume,
                          100.0, commission, swap, profit, fee, 'XAUUSD', '', '')


def snapshot(*, deals=(), positions=(), balance=None, equity=None, now=NOW, account=ACCOUNT):
    balance = account.balance if balance is None else balance
    equity = balance if equity is None else equity
    return BrokerEvidence(replace(account, balance=balance, equity=equity), tuple(deals), tuple(positions),
                          (), now, POLICY.start_utc,
                          frozenset(str(item.order) for item in deals if item.magic == MAGIC and item.entry == 0))


def approval(tmp_path, policy=POLICY):
    path = tmp_path / 'approval.json'
    path.write_text(json.dumps({'approved_by': 'test-human', 'decision': 'APPROVE_DRAWDOWN_RECOVERY',
                                'policy': asdict(policy), 'historical_evidence_reference': 'synthetic flat ledger'}))
    return path


def human_recover(conn, tmp_path, evidence, *, policy=POLICY, authority=None):
    return recover(conn, authority=authority or OperatorAuthority('test-human'), policy=policy,
                   evidence=evidence, approval_path=approval(tmp_path, policy),
                   reason='Reviewed synthetic accounting evidence and historical peaks',
                   acknowledge_prior_breach=True, now=NOW)


@pytest.fixture
def conn(tmp_path):
    connection = connect(tmp_path / 'drawdown.sqlite3')
    migrate(connection)
    engage(connection, 'Synthetic operator recovery test only', actor='test-human')
    yield connection
    connection.close()


@pytest.fixture
def approved(conn, tmp_path):
    human_recover(conn, tmp_path, snapshot())
    return conn


def loss_snapshot(*, foreign=0.0, deposit=0.0):
    deals = [deal(1), deal(2, entry=1, profit=-507.0)]
    if foreign:
        deals += [deal(3, magic=770115, position=2), deal(4, magic=770115, position=2, entry=1, profit=foreign)]
    if deposit:
        deals += [deal(5, magic=0, position=0, kind=2, profit=deposit, volume=0)]
    return snapshot(deals=deals, balance=9252.63 + foreign + deposit)


def test_migration_preserves_known_breach_pending_policy(conn):
    result = observe(conn, snapshot(balance=20000), now=NOW)
    assert result.blocked and '5.19%' in result.reason
    assert latest(conn)['event_type'] == 'BLOCKED'
    assert latest(conn)['latched'] == 1


def test_october_14_foreign_profit_69_46_cannot_erase_breach(approved):
    first = observe(approved, loss_snapshot(), now=NOW)
    second = observe(approved, loss_snapshot(foreign=69.46), now=NOW)
    assert first.project_drawdown_pct == pytest.approx(5.194869, abs=0.00001)
    assert second.account_drawdown_pct == pytest.approx(4.483162, abs=0.00001)
    assert second.project_equity == 9252.63
    assert first.blocked and second.blocked
    assert json.loads(latest(approved)['state_json'])['foreign_deals'] == 2


def test_deposit_and_later_project_recovery_never_automatically_clear(approved):
    observe(approved, loss_snapshot(), now=NOW)
    assert observe(approved, loss_snapshot(deposit=10000), now=NOW).blocked
    recovered = loss_snapshot(deposit=10000)
    recovered = replace(recovered, deals=(*recovered.deals, deal(6, position=3),
                                         deal(7, position=3, entry=1, profit=600)),
                        account=replace(recovered.account, balance=19852.63, equity=19852.63),
                        project_entry_order_ids=frozenset({'1', '6'}))
    status = observe(approved, recovered, now=NOW)
    assert status.project_drawdown_pct == 0
    assert status.blocked


def test_floating_loss_and_all_costs_cross_strict_five_percent(approved):
    position = PositionSnapshot('1', 'XAUUSD', 'BUY', 0.1, 100, 90, 110, -480, MAGIC, '', swap=-5)
    evidence = snapshot(deals=[deal(1, commission=-2, fee=-1)], positions=[position],
                        balance=9756.63, equity=9271.63)
    status = observe(approved, evidence, now=NOW)
    assert status.project_equity == pytest.approx(9271.63)
    assert status.project_drawdown_pct > 5
    assert status.blocked


def test_restart_keeps_breach(approved, tmp_path):
    observe(approved, loss_snapshot(), now=NOW)
    restarted = connect(tmp_path / 'drawdown.sqlite3')
    try:
        assert observe(restarted, loss_snapshot(foreign=69.46), now=NOW).blocked
    finally:
        restarted.close()


@pytest.mark.parametrize('evidence', [None, snapshot(now=NOW - 6), snapshot(now=NOW + 1),
                                    snapshot(equity=float('nan'))])
def test_missing_stale_future_nonfinite_evidence_latches(approved, evidence):
    assert observe(approved, evidence, now=NOW).blocked
    assert observe(approved, snapshot(), now=NOW).blocked


@pytest.mark.parametrize('field,value', [('login', ACCOUNT.login + 1), ('server', 'wrong-server'),
                                        ('currency', 'EUR'), ('trade_mode', 2)])
def test_wrong_account_latches(approved, field, value):
    evidence = snapshot(account=replace(ACCOUNT, **{field: value}))
    assert observe(approved, evidence, now=NOW).blocked
    assert latest(approved)['account_login'] == POLICY.login
    assert observe(approved, snapshot(), now=NOW).blocked


@pytest.mark.parametrize('deals', [
    [deal(1, magic=0)], [deal(1, entry=1)], [deal(1, kind=7)],
    [deal(1), deal(2, entry=1, magic=999)], [deal(1), deal(1)],
])
def test_unknown_conflicting_or_partial_history_blocks(approved, deals):
    assert observe(approved, snapshot(deals=deals), now=NOW).blocked


def test_missing_positions_and_unexplained_balance_block(approved):
    assert observe(approved, snapshot(deals=[deal(1)]), now=NOW).blocked
    assert observe(approved, snapshot(balance=10000), now=NOW).blocked


def test_previous_history_cannot_disappear_or_be_revised(approved):
    good = snapshot(deals=[deal(1), deal(2, entry=1, profit=-1)], balance=9758.63)
    assert not observe(approved, good, now=NOW).blocked
    assert observe(approved, snapshot(), now=NOW).blocked


def test_foreign_loss_independently_breaches_account_gate(approved):
    evidence = snapshot(deals=[deal(1, magic=770115), deal(2, magic=770115, entry=1, profit=-500)],
                        balance=9259.63)
    status = observe(approved, evidence, now=NOW)
    assert status.project_drawdown_pct == 0
    assert status.account_drawdown_pct > 5 and status.blocked


def test_unauthorized_recovery_writes_nothing(conn, tmp_path):
    before = latest(conn)['id']
    with pytest.raises(DrawdownEvidenceError, match='human'):
        human_recover(conn, tmp_path, snapshot(), authority='not-human-authority')
    assert latest(conn)['id'] == before


def test_recovery_cannot_clear_current_breach_or_change_anchor(approved, tmp_path):
    observe(approved, loss_snapshot(), now=NOW)
    before = latest(approved)['id']
    with pytest.raises(DrawdownEvidenceError, match='recovery refused'):
        human_recover(approved, tmp_path, loss_snapshot(foreign=69.46))
    with pytest.raises(DrawdownEvidenceError, match='cannot change'):
        human_recover(approved, tmp_path, snapshot(), policy=replace(POLICY, opening_project_equity=9500))
    assert latest(approved)['id'] == before


def test_human_recovery_requires_evidence_retains_kill_and_audit(conn, tmp_path):
    before = get_state(conn)
    human_recover(conn, tmp_path, snapshot())
    assert get_state(conn) == before
    row = latest(conn)
    assert row['event_type'] == 'OPERATOR_RECOVERY' and len(row['evidence_sha256']) == 64
    for sql in ('DELETE FROM project_drawdown_events', 'UPDATE project_drawdown_events SET latched=0'):
        with pytest.raises(sqlite3.IntegrityError, match='append-only'):
            conn.execute(sql)


def test_gateway_failure_and_changed_identity_fail_closed(approved):
    class BrokenGateway(FakeGateway):
        def account_info(self):
            raise RuntimeError('broker disconnected')
    result = observe_gateway(approved, BrokenGateway(), magic=MAGIC, clock=lambda: NOW)
    assert result.blocked and latest(approved)['latched'] == 1


def test_runtime_final_permission_gets_invalid_peak_with_pending_breach(tmp_path):
    clock = FakeClock(NOW)
    engine, connection, gateway = build_engine(tmp_path, mode='DEMO', clock=clock, approved_drawdown=False)
    try:
        engine.startup()
        assert engine.demo._project_drawdown().blocked
        assert engine.demo._risk_gate_peak(gateway.account_info(), NOW) != engine.demo._risk_gate_peak(
            gateway.account_info(), NOW)
        assert gateway.calls['order_send'] == 0
    finally:
        connection.close()


def test_partial_fills_aggregate_and_an_over_close_blocks(approved):
    fills = [deal(1, volume=0.1), deal(2, volume=0.1), deal(3, entry=1, volume=0.2, profit=-5)]
    status = observe(approved, snapshot(deals=fills, balance=9754.63), now=NOW)
    assert not status.blocked and status.project_equity == pytest.approx(9754.63)
    over = [deal(1, volume=0.1), deal(2, entry=1, volume=0.3, profit=-5)]
    assert observe(approved, snapshot(deals=over, balance=9754.63), now=NOW).blocked


def test_a_partially_closed_position_must_still_be_open_at_the_broker(approved):
    deals = [deal(1, volume=0.2), deal(2, entry=1, volume=0.1, profit=-5)]
    assert observe(approved, snapshot(deals=deals, balance=9754.63), now=NOW).blocked


def test_a_partially_closed_position_open_at_the_broker_is_accounted(approved):
    deals = [deal(1, volume=0.2), deal(2, entry=1, volume=0.1, profit=-5)]
    rest = PositionSnapshot('1', 'XAUUSD', 'BUY', 0.1, 100, 90, 110, -3.0, MAGIC, '', swap=-1.0)
    status = observe(approved, snapshot(deals=deals, positions=[rest], balance=9754.63, equity=9750.63), now=NOW)
    assert not status.blocked and status.project_equity == pytest.approx(9750.63)


def _gateway(account=ACCOUNT):
    gateway = FakeGateway()
    gateway.set_account(account)
    return gateway


def test_an_account_moving_on_every_tick_blocks_this_cycle_without_latching(approved):
    class Ticking(FakeGateway):
        n = 0

        def account_info(self):
            Ticking.n += 1
            return replace(ACCOUNT, equity=ACCOUNT.equity + Ticking.n / 100)
    before = latest(approved)['id']
    result = observe_gateway(approved, Ticking(), magic=MAGIC, clock=lambda: NOW)
    assert result.blocked and 'unsettled' in result.reason
    assert latest(approved)['id'] == before and latest(approved)['latched'] == 0
    assert not observe_gateway(approved, _gateway(), magic=MAGIC, clock=lambda: NOW).blocked


def test_an_identity_change_during_collection_latches(approved):
    class Switching(FakeGateway):
        calls = 0

        def account_info(self):
            Switching.calls += 1
            return ACCOUNT if Switching.calls == 1 else replace(ACCOUNT, login=ACCOUNT.login + 1)
    assert observe_gateway(approved, Switching(), magic=MAGIC, clock=lambda: NOW).blocked
    assert latest(approved)['latched'] == 1
    assert observe_gateway(approved, _gateway(), magic=MAGIC, clock=lambda: NOW).blocked


def test_repeated_identical_observations_do_not_grow_the_audit_trail(approved):
    count = lambda: approved.execute('SELECT COUNT(*) FROM project_drawdown_events').fetchone()[0]
    observe(approved, snapshot(), now=NOW)
    settled = count()
    for _ in range(5):
        assert not observe(approved, snapshot(), now=NOW).blocked
    assert count() == settled
    raised = snapshot(deals=[deal(1), deal(2, entry=1, profit=10)], balance=9769.63)
    assert not observe(approved, raised, now=NOW).blocked
    assert count() == settled + 1
    assert json.loads(latest(approved)['state_json'])['project_peak'] == pytest.approx(9769.63)
    loss = snapshot(deals=[*raised.deals, deal(3, position=2), deal(4, position=2, entry=1, profit=-600)],
                    balance=9169.63)
    status = observe(approved, loss, now=NOW)                       # 6.14% from the raised peak: latches
    assert status.blocked and status.project_drawdown_pct > 5
    breached = count()
    for _ in range(5):
        assert observe(approved, loss, now=NOW).blocked
    assert count() == breached
