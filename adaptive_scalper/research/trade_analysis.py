"""Gross-versus-net trade analysis for research reports (loss investigation).

Pure functions over a normalised `TradeView`, built either from a
`SimulatedTrade` (backtest / independent session / PAPER) or from a stored
`backtest_trades` / `paper_trades` row. Nothing here reads the clock, the
broker or future data: every number is derived from the closed trades it
is given.

Conventions (the same as the Strategy Lab and the backtest engine):

- `net` is the realized P/L after every cost; `gross = net + total_cost`
  is the mid-to-mid P/L before spread, slippage, commission, swap and fee.
- R values divide by the trade's own `initial_monetary_risk`; a trade with
  no positive initial risk has no R and is excluded from R averages (it is
  still counted in money totals), never given a fabricated R.
- Win / loss / breakeven use the Strategy Lab thresholds: net > +0.005,
  net < -0.005, otherwise breakeven.

Loss classes separate what the market did from what trading cost:

- `COST_ONLY_LOSS` -- gross >= 0 but net < 0: price moved the right way (or
  not at all) and costs alone made it a loser.
- `COST_DOMINATED_LOSS` -- gross < 0 and costs exceed the gross loss: an
  adverse move, but most of the damage is cost.
- `ADVERSE_MARKET_LOSS` -- gross < 0 and the gross loss is at least the
  cost: the market outcome itself dominates.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Iterable
from dataclasses import dataclass

BREAKEVEN_EPSILON = 0.005

WIN = "WIN"
BREAKEVEN = "BREAKEVEN"
COST_ONLY_LOSS = "COST_ONLY_LOSS"
COST_DOMINATED_LOSS = "COST_DOMINATED_LOSS"
ADVERSE_MARKET_LOSS = "ADVERSE_MARKET_LOSS"
LOSS_CLASSES = (COST_ONLY_LOSS, COST_DOMINATED_LOSS, ADVERSE_MARKET_LOSS)

# UTC trading-session buckets (hour ranges, start inclusive).
SESSION_BUCKETS: tuple[tuple[str, int, int], ...] = (
    ("ASIA 00-07 UTC", 0, 7),
    ("LONDON 07-12 UTC", 7, 12),
    ("LONDON/NY OVERLAP 12-16 UTC", 12, 16),
    ("NEW YORK 16-21 UTC", 16, 21),
    ("LATE 21-24 UTC", 21, 24),
)

# Holding-duration buckets (seconds, upper bound exclusive).
DURATION_BUCKETS: tuple[tuple[str, float], ...] = (
    ("< 5 min", 300),
    ("5-15 min", 900),
    ("15-60 min", 3600),
    ("1-4 h", 4 * 3600),
    (">= 4 h", math.inf),
)

CONFIDENCE_BUCKETS: tuple[tuple[str, float, float], ...] = (
    ("0.00-0.25", 0.0, 0.25),
    ("0.25-0.50", 0.25, 0.50),
    ("0.50-0.75", 0.50, 0.75),
    ("0.75-0.99", 0.75, 0.99),
    ("1.00 (capped)", 0.99, 1.01),
)


@dataclass(frozen=True)
class TradeView:
    symbol: str
    strategy_key: str
    direction: str
    entry_time_utc: int
    exit_time_utc: int
    exit_reason: str | None
    entry_regime: str | None
    initial_risk: float
    net: float
    total_cost: float
    spread_cost: float = 0.0
    slippage_cost: float = 0.0
    commission_cost: float = 0.0
    swap_cost: float = 0.0
    fee_cost: float = 0.0
    raw_confidence: float | None = None
    cost_provenance: str | None = None
    source: str = ""

    @property
    def gross(self) -> float:
        return self.net + self.total_cost

    @property
    def holding_seconds(self) -> int:
        return max(0, self.exit_time_utc - self.entry_time_utc)

    def r(self, money: float) -> float | None:
        return money / self.initial_risk if self.initial_risk > 0 else None


def from_simulated(trade, symbol: str, *, source: str = "") -> TradeView | None:
    """A closed `SimulatedTrade` -> TradeView; None for an open trade."""
    if trade.exit_time_utc is None or trade.realized_pnl is None:
        return None
    evidence = trade.entry_evidence or {}
    confidence = trade.entry_raw_confidence
    if confidence is None:
        confidence = (evidence.get("strategy") or {}).get("raw_confidence")
    return TradeView(
        symbol=symbol, strategy_key=trade.strategy_key, direction=trade.direction,
        entry_time_utc=trade.entry_time_utc, exit_time_utc=trade.exit_time_utc, exit_reason=trade.exit_reason,
        entry_regime=trade.entry_regime, initial_risk=trade.initial_monetary_risk or 0.0, net=trade.realized_pnl,
        total_cost=trade.total_cost or 0.0,
        spread_cost=(trade.entry_spread_cost or 0.0) + (trade.exit_spread_cost or 0.0),
        slippage_cost=(trade.entry_slippage_cost or 0.0) + (trade.exit_slippage_cost or 0.0),
        commission_cost=trade.commission_cost or 0.0, swap_cost=trade.swap_cost or 0.0,
        fee_cost=trade.fee_cost or 0.0, raw_confidence=confidence, cost_provenance=trade.cost_provenance,
        source=source,
    )


def from_trade_row(row, *, source: str = "") -> TradeView | None:
    """A stored `backtest_trades` / `paper_trades` row -> TradeView."""
    keys = row.keys()
    if row["exit_time_utc"] is None or row["realized_pnl"] is None:
        return None

    def get(name: str, default=0.0):
        return row[name] if name in keys and row[name] is not None else default

    confidence = get("entry_raw_confidence", None)
    if confidence is None and get("evidence_json", None):
        try:
            confidence = (json.loads(row["evidence_json"]).get("strategy") or {}).get("raw_confidence")
        except (ValueError, AttributeError):
            confidence = None
    return TradeView(
        symbol=row["canonical_symbol"], strategy_key=row["strategy_key"], direction=row["direction"],
        entry_time_utc=row["entry_time_utc"], exit_time_utc=row["exit_time_utc"], exit_reason=row["exit_reason"],
        entry_regime=get("entry_regime", None), initial_risk=get("initial_monetary_risk"), net=row["realized_pnl"],
        total_cost=get("total_cost"), spread_cost=get("entry_spread_cost") + get("exit_spread_cost"),
        slippage_cost=get("entry_slippage_cost") + get("exit_slippage_cost"),
        commission_cost=get("commission_cost"), swap_cost=get("swap_cost"), fee_cost=get("fee_cost"),
        raw_confidence=confidence, cost_provenance=get("cost_provenance", None), source=source,
    )


def outcome_class(trade: TradeView) -> str:
    if trade.net > BREAKEVEN_EPSILON:
        return WIN
    if trade.net >= -BREAKEVEN_EPSILON:
        return BREAKEVEN
    if trade.gross >= 0:
        return COST_ONLY_LOSS
    if trade.total_cost > -trade.gross:
        return COST_DOMINATED_LOSS
    return ADVERSE_MARKET_LOSS


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def summarize(trades: Iterable[TradeView]) -> dict:
    """Headline gross/net/cost figures for a set of closed trades. Values
    that are undefined for the sample (no trades, no losses, no R) are None
    with no substitute number."""
    trades = list(trades)
    n = len(trades)
    classes = [outcome_class(t) for t in trades]
    wins = [t for t, c in zip(trades, classes) if c == WIN]
    losses = [t for t, c in zip(trades, classes) if c in LOSS_CLASSES]
    gross = sum(t.gross for t in trades)
    net = sum(t.net for t in trades)
    cost = sum(t.total_cost for t in trades)
    winners = sum(t.net for t in wins)
    losers = sum(t.net for t in losses)
    with_r = [t for t in trades if t.initial_risk > 0]
    gross_r = [t.r(t.gross) for t in with_r]
    cost_r = [t.r(t.total_cost) for t in with_r]
    net_r = [t.r(t.net) for t in with_r]
    return {
        "trades": n,
        "wins": len(wins),
        "losses": len(losses),
        "breakevens": classes.count(BREAKEVEN),
        "win_rate": len(wins) / n if n else None,
        "gross_pnl": gross,
        "total_cost": cost,
        "net_pnl": net,
        "gross_winners": winners,
        "gross_losers": losers,
        "profit_factor": (winners / abs(losers)) if losers < 0 else None,
        "expectancy_per_trade": net / n if n else None,
        "avg_gross_r": _mean(gross_r),
        "avg_cost_r": _mean(cost_r),
        "avg_net_r": _mean(net_r),
        "r_sample": len(with_r),
        "cost_to_abs_gross": (cost / abs(gross)) if gross else None,
        "spread_cost": sum(t.spread_cost for t in trades),
        "slippage_cost": sum(t.slippage_cost for t in trades),
        "commission_cost": sum(t.commission_cost for t in trades),
        "swap_cost": sum(t.swap_cost for t in trades),
        "fee_cost": sum(t.fee_cost for t in trades),
        "avg_holding_seconds": _mean([float(t.holding_seconds) for t in trades]),
        "loss_classes": {c: classes.count(c) for c in LOSS_CLASSES},
        "gross_positive_share": (sum(1 for t in trades if t.gross > 0) / n) if n else None,
    }


def session_bucket(t: TradeView) -> str:
    hour = (t.entry_time_utc % 86_400) // 3600
    return next(name for name, start, end in SESSION_BUCKETS if start <= hour < end)


def duration_bucket(t: TradeView) -> str:
    return next(name for name, upper in DURATION_BUCKETS if t.holding_seconds < upper)


def confidence_bucket(t: TradeView) -> str:
    if t.raw_confidence is None:
        return "UNKNOWN"
    return next((name for name, lo, hi in CONFIDENCE_BUCKETS if lo <= t.raw_confidence < hi), "UNKNOWN")


DIMENSIONS: dict[str, Callable[[TradeView], str]] = {
    "symbol": lambda t: t.symbol,
    "strategy": lambda t: t.strategy_key,
    "regime": lambda t: t.entry_regime or "UNKNOWN",
    "session": session_bucket,
    "direction": lambda t: t.direction,
    "holding_duration": duration_bucket,
    "exit_reason": lambda t: t.exit_reason or "UNKNOWN",
    "raw_confidence": confidence_bucket,
    "cost_provenance": lambda t: t.cost_provenance or "UNKNOWN",
}


def breakdown(trades: Iterable[TradeView], key: Callable[[TradeView], str]) -> dict[str, dict]:
    groups: dict[str, list[TradeView]] = {}
    for t in trades:
        groups.setdefault(key(t), []).append(t)
    return {k: summarize(v) for k, v in sorted(groups.items())}


def full_breakdown(trades: Iterable[TradeView]) -> dict[str, dict[str, dict]]:
    trades = list(trades)
    return {name: breakdown(trades, fn) for name, fn in DIMENSIONS.items()}


def calibration(trades: Iterable[TradeView]) -> list[dict]:
    """Stated confidence versus realized outcome, per confidence bucket.
    `stated_p` is the mean raw confidence the selector treated as a win
    probability; `realized_gross_hit_rate` is the share of trades whose
    GROSS result was positive (the market-side outcome the probability is
    about, before costs)."""
    rows = []
    groups: dict[str, list[TradeView]] = {}
    for t in trades:
        groups.setdefault(confidence_bucket(t), []).append(t)
    order = [name for name, _, _ in CONFIDENCE_BUCKETS] + ["UNKNOWN"]
    for name in order:
        bucket = groups.get(name)
        if not bucket:
            continue
        stated = [t.raw_confidence for t in bucket if t.raw_confidence is not None]
        rows.append({
            "bucket": name, "trades": len(bucket), "stated_p": _mean(stated),
            "realized_gross_hit_rate": sum(1 for t in bucket if t.gross > 0) / len(bucket),
            "realized_net_win_rate": sum(1 for t in bucket if outcome_class(t) == WIN) / len(bucket),
            "avg_gross_r": _mean([t.r(t.gross) for t in bucket if t.initial_risk > 0]),
            "avg_net_r": _mean([t.r(t.net) for t in bucket if t.initial_risk > 0]),
        })
    return rows
