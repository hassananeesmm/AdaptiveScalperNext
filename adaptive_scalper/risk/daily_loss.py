"""Conservative daily-loss accounting for the shared DEMO account (UTC day).

The day is the UTC calendar day `[now - now % 86400, now]`. Its baseline is
the broker balance at the start of that day, reconstructed from broker truth
(current balance minus every deal of the day), so a restart cannot reset it;
the first value seen each day is persisted and a later disagreement (history
missing or revised) blocks.

Loss counted against the 2 % limit (never more lenient than the account):
- this project's realized P&L today (profit + commission + swap + fee of every
  deal on a project position, whatever magic closed it);
- this project's floating P&L (profit + accrued swap of its open positions);
- foreign (other EA / manual) realized and floating P&L only when NEGATIVE;
- unknown non-trade charges only when negative;
- deposits and withdrawals (balance deals) never count either way.
Floating is additionally capped by the broker's own equity - balance, so an
unexplained equity drop still counts. Positions opened before midnight count
their whole floating P&L today (conservative).

Evidence that cannot be used (duplicate tickets, non-finite amounts, deals
outside the day, a history that changed since it was first observed) raises
`DailyLossEvidenceError`; evidence that does not reconcile (equity !=
balance + marked positions) is reported as `reconciled=False`, which blocks
new entries but still lets the loss itself trip the circuit breaker.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

DAY_SECONDS = 86400
MONEY_TOLERANCE = 0.01
DEAL_BALANCE = 2
TRADE_DEAL_TYPES = (0, 1)
BASELINE_STATE_KEY = "daily_loss_baseline"


class DailyLossEvidenceError(ValueError):
    """Broker evidence cannot establish today's loss."""


@dataclass(frozen=True)
class DailyLoss:
    utc_day: int
    day_start_balance: float
    realized: float          # conservative realized P&L counted against the limit
    floating: float          # conservative floating P&L counted against the limit
    project_realized: float
    project_floating: float
    foreign_realized: float
    foreign_floating: float
    cash_flows: float        # deposits/withdrawals today: excluded
    other_charges: float     # non-trade, non-balance deals: counted only when negative
    reconciled: bool


def _finite(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def utc_day_start(now: int) -> int:
    return now - now % DAY_SECONDS


def compute(account, deals, positions, *, own_magic: int, own_position_ids, now: int) -> DailyLoss:
    if account is None or not all(_finite(v) for v in (account.balance, account.equity)):
        raise DailyLossEvidenceError("account balance/equity unavailable or non-finite")
    day_start = utc_day_start(now)
    own_ids = {str(p) for p in own_position_ids}
    seen: set[int] = set()
    project_r = foreign_r = cash = other = 0.0
    for deal in deals:
        if deal.ticket in seen:
            raise DailyLossEvidenceError(f"duplicate broker deal {deal.ticket}")
        seen.add(deal.ticket)
        if not day_start <= deal.time <= now:
            raise DailyLossEvidenceError(f"broker deal {deal.ticket} outside the UTC day")
        amounts = (deal.profit, deal.commission, deal.swap, deal.fee)
        if not all(_finite(v) for v in amounts):
            raise DailyLossEvidenceError(f"broker deal {deal.ticket} has non-finite amounts")
        net = sum(amounts)
        if deal.type == DEAL_BALANCE:
            cash += net
        elif deal.type in TRADE_DEAL_TYPES:
            if deal.magic == own_magic or str(deal.position_id) in own_ids:
                project_r += net
            else:
                foreign_r += net
        else:
            other += net
    project_f = foreign_f = 0.0
    for position in positions:
        if not _finite(position.profit) or not _finite(position.swap):
            raise DailyLossEvidenceError(f"position {position.broker_position_id} floating P&L or swap unavailable")
        net = position.profit + position.swap
        if position.magic == own_magic or str(position.broker_position_id) in own_ids:
            project_f += net
        else:
            foreign_f += net
    day_start_balance = account.balance - (project_r + foreign_r + cash + other)
    if day_start_balance <= 0:
        raise DailyLossEvidenceError(f"reconstructed day-start balance {day_start_balance:.2f} is not positive")
    broker_floating = account.equity - account.balance
    reconciled = abs(broker_floating - (project_f + foreign_f)) <= MONEY_TOLERANCE
    return DailyLoss(
        utc_day=day_start // DAY_SECONDS, day_start_balance=day_start_balance,
        realized=project_r + min(0.0, foreign_r) + min(0.0, other),
        floating=min(project_f + min(0.0, foreign_f), broker_floating),
        project_realized=project_r, project_floating=project_f, foreign_realized=foreign_r,
        foreign_floating=foreign_f, cash_flows=cash, other_charges=other, reconciled=reconciled,
    )


def check_baseline(stored: dict | None, loss: DailyLoss) -> dict:
    """The first day-start balance seen for a UTC day is authoritative; a
    later different reconstruction means today's history was incomplete or
    revised. Returns the value to persist."""
    if stored and stored.get("utc_day") == loss.utc_day:
        if abs(float(stored["day_start_balance"]) - loss.day_start_balance) > MONEY_TOLERANCE:
            raise DailyLossEvidenceError(
                f"today's broker history changed: day-start balance {loss.day_start_balance:.2f} vs "
                f"{float(stored['day_start_balance']):.2f} first observed")
        return stored
    return {"utc_day": loss.utc_day, "day_start_balance": loss.day_start_balance}


def collect(gateway, now: int, attempts: int = 3):
    """(account, deals, positions) from one settled snapshot: the account must
    not move while the day's deals and positions are read."""
    day_start = utc_day_start(now)
    for _ in range(attempts):
        account = gateway.account_info()
        deals = tuple(gateway.history_deals_get(day_start, now))
        positions = tuple(gateway.positions_get())
        after = gateway.account_info()
        if account is None or after is None:
            raise DailyLossEvidenceError("account_info unavailable")
        if (account.login, account.server) != (after.login, after.server):
            raise DailyLossEvidenceError("account identity changed while reading the day's evidence")
        if (account.balance, account.equity) == (after.balance, after.equity):
            return account, deals, positions
    raise DailyLossEvidenceError("account balance/equity moved during every daily-loss snapshot")
