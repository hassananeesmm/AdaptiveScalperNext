"""Persistent project and account drawdown gates. No automatic recovery.

An operator-approved, flat accounting anchor is required. Account cash flows
reconcile broker history, but only attributable trades change project equity.
History omissions, revisions, mixed ownership and unknown charges block.
The existing 0033 peak is an additional floor, never a replacement anchor.
"""

from __future__ import annotations

import hashlib
import json
import math
import time
from dataclasses import asdict, dataclass

from adaptive_scalper.gateway.types import TradeMode

MAX_DRAWDOWN_PCT = 5.0
MAX_EVIDENCE_AGE_SECONDS = 5
MONEY_TOLERANCE = 0.01
COLLECT_ATTEMPTS = 3


class DrawdownEvidenceError(ValueError):
    """Evidence cannot establish a safe accounting state."""


class TransientDrawdownEvidence(Exception):
    """The account moved during every snapshot attempt: entries stay blocked
    for this cycle, but nothing inconsistent was observed, so nothing latches."""


@dataclass(frozen=True)
class AccountingPolicy:
    login: int
    server: str
    currency: str
    magic: int
    start_utc: int
    opening_balance: float
    opening_project_equity: float
    historical_project_peak: float
    historical_account_peak: float

    def __post_init__(self):
        for value in (self.login, self.magic, self.start_utc):
            if type(value) is not int or value <= 0:
                raise DrawdownEvidenceError('positive account, magic and anchor timestamp required')
        if not self.server.strip() or not self.currency.strip():
            raise DrawdownEvidenceError('server and currency required')
        for value in (self.opening_balance, self.opening_project_equity,
                      self.historical_project_peak, self.historical_account_peak):
            if not _finite(value) or value <= 0:
                raise DrawdownEvidenceError('documented positive finite accounting amounts required')
        if self.historical_project_peak < self.opening_project_equity:
            raise DrawdownEvidenceError('project historical peak below opening equity')
        if self.historical_account_peak < self.opening_balance:
            raise DrawdownEvidenceError('account historical peak below opening balance')


@dataclass(frozen=True)
class BrokerEvidence:
    account: object
    deals: tuple
    positions: tuple
    orders: tuple
    observed_at_utc: int
    requested_start_utc: int
    project_entry_order_ids: frozenset[str] = frozenset()


@dataclass(frozen=True)
class DrawdownStatus:
    blocked: bool
    reason: str
    project_equity: float | None = None
    project_drawdown_pct: float | None = None
    account_drawdown_pct: float | None = None


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def latest(conn):
    return conn.execute('SELECT * FROM project_drawdown_events ORDER BY id DESC LIMIT 1').fetchone()


def known_deals(conn):
    return dict(conn.execute('SELECT ticket, sha256 FROM project_drawdown_deals').fetchall())


def retain_deals(conn, state):
    for ticket, digest in state.pop('deals').items():
        conn.execute('INSERT OR IGNORE INTO project_drawdown_deals VALUES (?, ?)', (ticket, digest))


def _append(conn, *, event, policy, state, latched, reason, now, actor='drawdown_guard', digest=None):
    conn.execute(
        'INSERT INTO project_drawdown_events (event_type, account_login, account_server, account_currency, '
        'magic, latched, reason, actor, recorded_at_utc, state_json, evidence_sha256) '
        'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
        (event, policy.login if policy else None, policy.server if policy else None,
         policy.currency if policy else None, policy.magic if policy else None, int(latched), reason,
         actor, now, json.dumps(state, sort_keys=True, allow_nan=False), digest),
    )


def _repeats(row, *, event, latched, reason, state):
    """True when appending would only restate the latest row (called several
    times per entry cycle): the trail records changes, not every tick."""
    if row is None or row['event_type'] != event or bool(row['latched']) != latched:
        return False
    if latched and row['reason'] != reason:
        return False
    previous = json.loads(row['state_json'])
    return all(previous.get(key) == state.get(key)
               for key in ('policy', 'project_peak', 'account_peak', 'foreign_deals', 'cash_flows'))


def _deal_digest(deal):
    return hashlib.sha256(json.dumps(asdict(deal), sort_keys=True, allow_nan=False).encode()).hexdigest()


def evaluate(policy, evidence, previous, *, now, limit=MAX_DRAWDOWN_PCT):
    if not _finite(limit) or not 0 < limit <= MAX_DRAWDOWN_PCT:
        raise DrawdownEvidenceError('drawdown limit may only tighten the 5% ceiling')
    if evidence is None or not 0 <= now - evidence.observed_at_utc <= MAX_EVIDENCE_AGE_SECONDS:
        raise DrawdownEvidenceError('missing, stale or future equity evidence')
    if evidence.observed_at_utc < previous.get('observed_at_utc', policy.start_utc):
        raise DrawdownEvidenceError('equity evidence moved backwards in time')
    for key in ('project_peak', 'account_peak'):
        if not _finite(previous.get(key)) or previous[key] <= 0:
            raise DrawdownEvidenceError('historical peak evidence missing or invalid')
    account = evidence.account
    if account is None or (account.login, account.server, account.currency) != (
            policy.login, policy.server, policy.currency) or account.trade_mode != TradeMode.DEMO:
        raise DrawdownEvidenceError('wrong or unavailable DEMO account identity')
    if evidence.requested_start_utc != policy.start_utc or now < policy.start_utc:
        raise DrawdownEvidenceError('incomplete history interval')
    if any(not _finite(value) for value in (account.balance, account.equity)):
        raise DrawdownEvidenceError('non-finite account equity or balance')
    if evidence.deals is None or evidence.positions is None or evidence.orders is None:
        raise DrawdownEvidenceError('partial broker snapshot')
    seen, owners, volumes = {}, {}, {}
    cash, realized, foreign, deposits = 0.0, 0.0, 0, 0.0
    for deal in sorted(evidence.deals, key=lambda item: (item.time, item.ticket)):
        if str(deal.ticket) in seen or deal.ticket <= 0 or not policy.start_utc < deal.time <= evidence.observed_at_utc:
            raise DrawdownEvidenceError('duplicate deal or history outside approved interval')
        if any(not _finite(value) for value in (deal.profit, deal.commission, deal.swap, deal.fee, deal.volume)):
            raise DrawdownEvidenceError('non-finite deal amounts')
        seen[str(deal.ticket)] = _deal_digest(deal)
        net = deal.profit + deal.commission + deal.swap + deal.fee
        cash += net
        if deal.type == 2 and deal.position_id == 0 and deal.magic == 0:
            deposits += net
            continue
        if deal.type not in (0, 1) or deal.position_id <= 0 or deal.entry not in (0, 1):
            raise DrawdownEvidenceError('unknown broker attribution or unsupported accounting deal')
        position = str(deal.position_id)
        if deal.entry == 0:
            if deal.magic <= 0:
                raise DrawdownEvidenceError('unattributed entry deal')
            if deal.magic == policy.magic and str(deal.order) not in evidence.project_entry_order_ids:
                raise DrawdownEvidenceError('project magic without a matching durable local entry order')
            owner = owners.setdefault(position, deal.magic)
            if owner != deal.magic:
                raise DrawdownEvidenceError('conflicting position ownership')
            volumes[position] = volumes.get(position, 0.0) + deal.volume
        elif position not in owners or deal.magic not in (0, owners[position]):
            raise DrawdownEvidenceError('exit without attributable entry or conflicting magic')
        else:
            volumes[position] -= deal.volume
        if deal.volume <= 0 or volumes[position] < -1e-8:
            raise DrawdownEvidenceError('partial or inconsistent position history')
        if owners[position] == policy.magic:
            realized += net
        else:
            foreign += 1
    for ticket, digest in previous.get('deals', {}).items():
        if seen.get(ticket) != digest:
            raise DrawdownEvidenceError('previously observed history missing or revised')
    if abs(policy.opening_balance + cash - account.balance) > MONEY_TOLERANCE:
        raise DrawdownEvidenceError('broker cash history does not reconcile to balance')
    floating, all_floating, position_ids = 0.0, 0.0, set()
    for position in evidence.positions:
        position_id = str(position.broker_position_id)
        if position_id in position_ids or owners.get(position_id) != position.magic:
            raise DrawdownEvidenceError('unknown or conflicting open-position attribution')
        position_ids.add(position_id)
        if not _finite(position.volume) or abs(volumes.get(position_id, 0) - position.volume) > 1e-8:
            raise DrawdownEvidenceError('open volume conflicts with broker deal history')
        if not _finite(position.profit) or not _finite(position.swap):
            raise DrawdownEvidenceError('floating P&L or accrued swap unavailable')
        net = position.profit + position.swap
        all_floating += net
        if position.magic == policy.magic:
            floating += net
    if any(volume > 1e-8 and position not in position_ids for position, volume in volumes.items()):
        raise DrawdownEvidenceError('position missing from partial broker snapshot')
    if abs(account.balance + all_floating - account.equity) > MONEY_TOLERANCE:
        raise DrawdownEvidenceError('broker equity conflicts with marked positions and swap')
    if any(order.magic != policy.magic for order in evidence.orders):
        raise DrawdownEvidenceError('foreign or unattributed pending order')
    equity = policy.opening_project_equity + realized + floating
    project_peak = max(policy.historical_project_peak, previous.get('project_peak', 0), equity)
    account_peak = max(policy.historical_account_peak, previous.get('account_peak', 0), account.equity)
    project_pct = max(0.0, 100 * (project_peak - equity) / project_peak)
    account_pct = max(0.0, 100 * (account_peak - account.equity) / account_peak)
    blocked = project_pct >= limit or account_pct >= limit
    reason = (f'project drawdown {project_pct:.6f}%; account drawdown {account_pct:.6f}%; '
              f'limit {limit}%; foreign deals {foreign}; cash flows excluded from project {deposits:.2f}')
    state = {'policy': asdict(policy), 'project_peak': project_peak, 'account_peak': account_peak,
             'project_equity': equity, 'realized': realized, 'floating': floating, 'deals': seen,
             'foreign_deals': foreign, 'cash_flows': deposits, 'observed_at_utc': evidence.observed_at_utc}
    return DrawdownStatus(blocked, reason, equity, project_pct, account_pct), state


def observe(conn, evidence, *, now, limit=MAX_DRAWDOWN_PCT):
    conn.execute('SAVEPOINT project_drawdown_observe')
    try:
        row = latest(conn)
        if row is None:
            raise DrawdownEvidenceError('drawdown audit history missing')
        state = json.loads(row['state_json'])
        policy = AccountingPolicy(**state['policy']) if 'policy' in state else None
        if policy is None:
            return DrawdownStatus(True, row['reason'])
        try:
            state['deals'] = known_deals(conn)
            legacy = conn.execute(
                'SELECT peak_equity FROM peak_equity_history WHERE account_login=? AND account_server=? '
                'ORDER BY id DESC LIMIT 1', (policy.login, policy.server)).fetchone()
            if legacy:
                state['account_peak'] = max(state.get('account_peak', 0), legacy['peak_equity'])
            status, updated = evaluate(policy, evidence, state, now=now, limit=limit)
            new_deals = set(updated['deals']) - set(state['deals'])
            retain_deals(conn, updated)
            state.pop('deals', None)
            latched = bool(row['latched']) or status.blocked
            reason = status.reason if not row['latched'] else row['reason']
            event = 'BLOCKED' if latched else 'OBSERVED'
            if new_deals or not _repeats(row, event=event, latched=latched, reason=reason, state=updated):
                _append(conn, event=event, policy=policy, state=updated, latched=latched, reason=reason, now=now)
            return DrawdownStatus(latched, reason, status.project_equity,
                                  status.project_drawdown_pct, status.account_drawdown_pct)
        except (DrawdownEvidenceError, ValueError, TypeError, AttributeError) as exc:
            state.pop('deals', None)
            if not _repeats(row, event='BLOCKED', latched=True, reason=str(exc), state=state):
                _append(conn, event='BLOCKED', policy=policy, state=state, latched=True, reason=str(exc), now=now)
            return DrawdownStatus(True, str(exc))
    except BaseException:
        conn.execute('ROLLBACK TO project_drawdown_observe')
        raise
    finally:
        conn.execute('RELEASE project_drawdown_observe')


def _identity(account):
    return (account.login, account.server, account.currency, account.trade_mode)


def collect(gateway, policy, clock=time.time, *, project_entry_order_ids=frozenset()):
    """One internally consistent broker snapshot. Equity, balance and margin
    move on every tick while a position is open, so a snapshot whose account
    moved during collection is retried; one that never settles blocks this
    cycle without latching. A changed account identity is never retried."""
    for _ in range(COLLECT_ATTEMPTS):
        started = int(clock())
        account = gateway.account_info()
        deals = tuple(gateway.history_deals_get(policy.start_utc + 1, started))
        positions = tuple(gateway.positions_get())
        orders = tuple(gateway.orders_get())
        after = gateway.account_info()
        if account is None or after is None or _identity(account) != _identity(after):
            raise DrawdownEvidenceError('account identity changed or unavailable while collecting evidence')
        if not 0 <= int(clock()) - started <= MAX_EVIDENCE_AGE_SECONDS:
            raise DrawdownEvidenceError('broker evidence collection stale')
        if (account.balance, account.equity) == (after.balance, after.equity):
            return BrokerEvidence(account, deals, positions, orders, started, policy.start_utc,
                                  project_entry_order_ids)
    raise TransientDrawdownEvidence('account balance or equity moved during every evidence snapshot')


def observe_gateway(conn, gateway, *, magic, clock=time.time, limit=MAX_DRAWDOWN_PCT):
    try:
        row = latest(conn)
        state = json.loads(row['state_json']) if row else {}
        if 'policy' not in state:
            return observe(conn, None, now=int(clock()), limit=limit)
        policy = AccountingPolicy(**state['policy'])
        if policy.magic != magic:
            raise DrawdownEvidenceError('runtime magic conflicts with approved project identity')
        project_orders = frozenset(str(item[0]) for item in conn.execute(
            'SELECT broker_order_id FROM orders WHERE broker_order_id IS NOT NULL AND magic=?', (magic,)))
        evidence = collect(gateway, policy, clock, project_entry_order_ids=project_orders)
        return observe(conn, evidence, now=int(clock()), limit=limit)
    except TransientDrawdownEvidence as exc:
        return DrawdownStatus(True, f'drawdown evidence unsettled: {exc}')
    except Exception as exc:  # noqa: BLE001 - evidence or persistence failure must block entries
        try:
            row = latest(conn)
            state = json.loads(row['state_json']) if row else {}
            policy = AccountingPolicy(**state['policy']) if 'policy' in state else None
            reason = f'evidence unavailable: {type(exc).__name__}'
            if not _repeats(row, event='BLOCKED', latched=True, reason=reason, state=state):
                _append(conn, event='BLOCKED', policy=policy, state=state, latched=True, reason=reason,
                        now=int(clock()))
        except Exception as audit_error:  # noqa: BLE001 - failed audit storage cannot authorize entries
            return DrawdownStatus(True, f'drawdown audit unavailable: {type(audit_error).__name__}')
        return DrawdownStatus(True, f'drawdown evidence unavailable: {type(exc).__name__}')
