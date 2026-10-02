"""H9 -- BTCUSD M15 volatility-conditioned sparse time-trend continuation
(research only; docs/research/V2_H9_PREREGISTRATION_2026-10-02.md).

ONE parameterization, fixed by the pre-registration (the constants below are
not tuning knobs). Three layers:

- Pure, causal signal math: the 96-bar log-price trend slope t-statistic
  with a Newey-West (Bartlett, lag 3) variance, RV24, its trailing 30-day
  75th percentile and ATR14. Every value at bar t reads bars `<= t` only.
- `detect_events`: threshold-cross episodes (one decision per crossing,
  never while the statistic merely stays beyond the threshold); the
  volatility condition is evaluated AT the crossing.
- `run_h9_fold`: decisions -> trades on the M15 clock with the backtest
  engine's own primitives (`simulate_fill` next-bar-open, gap-through stop
  via `_intrabar_stop_or_target_hit`, `_close_trade`, `calculate_safe_volume`,
  `evaluate_risk_gate`, `evaluate_portfolio_risk_gate`). The engine's
  `_revalidate_and_open` is NOT used because its expected-edge gate treats the
  V1 heuristic `raw_confidence` as a probability; `open_h9_entry` runs the
  same gates minus that one, replaced by the cost_R gate (pre-registration
  section 5). The stop is set once from the fill and never moved.

Nothing here touches a broker, a gateway or a production database, and it is
not registered in the runtime strategy registry. The reserved OOS is refused.
"""

from __future__ import annotations

import dataclasses
import hashlib
import math
import sqlite3
import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from adaptive_scalper.backtest.engine import (
    BACKTEST_RANGE_ENDED,
    STOP_LOSS_HIT,
    _close_trade,
    _in_news_window,
    _intrabar_stop_or_target_hit,
    _opposite,
    _OpenTrade,
    _risk_halt_reason,
)
from adaptive_scalper.backtest.reserved_oos import RESERVED_OOS_INTERVALS, assert_outside_reserved_oos
from adaptive_scalper.backtest.types import FILL_NEXT_BAR_OPEN, FILL_RANGE_END_CLOSE, RiskState
from adaptive_scalper.costs.model import estimate_cost
from adaptive_scalper.costs.observations import session_label
from adaptive_scalper.portfolio.exposure import ALLOW as PORTFOLIO_ALLOW
from adaptive_scalper.portfolio.exposure import evaluate_portfolio_risk_gate, portfolio_risk_limits_from_risk_limits
from adaptive_scalper.research.v2.h8 import FingerprintStore, cluster_bootstrap_ci
from adaptive_scalper.risk.governor import ALLOW as RISK_ALLOW
from adaptive_scalper.risk.governor import RiskGateInput, calculate_safe_volume, evaluate_risk_gate
from adaptive_scalper.simulation.fill_model import money_from_price_distance, round_trip_commission_price, simulate_fill
from adaptive_scalper.simulation.types import EvidenceOrigin

SYMBOL = "BTCUSD"
STRATEGY_KEY = "research_h9_btc_vol_trend"
M15 = 900
TREND_BARS = 96                 # 24 h of M15 bars, including bar t
HAC_MAX_LAG = 3                 # Newey-West, Bartlett kernel, no prewhitening
T_THRESHOLD = 2.0
BASELINE_SECONDS = 30 * 86_400  # the previous 30 UTC days
VOL_PERCENTILE = 75.0
ATR_LENGTH = 14
STOP_ATR = 1.5
MAX_COST_R = 0.05
HOLD_SECONDS = 4 * 3600         # 16 completed M15 bars
MAX_FILL_DELAY_SECONDS = M15    # the fill bar may open at most one bar after the decision close

# The immutable H6 BTCUSD M15 dataset (pre-registration section 2).
DATA_START_UTC = 1698796800     # 2023-11-01 00:00 UTC (first bar open)
DATA_END_UTC = 1782777600       # 2026-06-30 00:00 UTC (last bar open, inclusive)
EXPECTED_BARS = 92_660
EXPECTED_CHECKSUM = "ede3898a45df163d66207fb5ad6ed745fde3853d358010da4e648de3315d03a8"
H6_RESULTS_SHA256 = "7770fc6f5dc125177a20079ed912557e674bf32a735451bda04bcc18c0104645"
H6_BTC_TRIALS = 39
N_FOLDS = 12
BOOTSTRAP_RESAMPLES = 10_000
BOOTSTRAP_SEED = 20261002
COST_MULTIPLIERS = (1.0, 1.2, 1.5, 2.0)
RISK_LIMITS = {"risk_per_trade_pct": 0.25, "max_total_open_risk_pct": 0.75, "max_daily_loss_pct": 2.0,
               "max_drawdown_pct": 5.0, "max_open_positions": 2, "max_positions_per_symbol": 1}

TIME_EXIT_REASON = "TIME_EXIT_4H"
EXIT_REASONS = frozenset({STOP_LOSS_HIT, TIME_EXIT_REASON, BACKTEST_RANGE_ENDED})

VOL_OK = "OK"
REJECT_INSUFFICIENT_HISTORY = "NO_SIGNAL_INSUFFICIENT_VOL_HISTORY"
REJECT_VOLATILITY = "VOLATILITY_CONDITION_NOT_MET"
REJECT_POSITION_OPEN = "POSITION_OPEN"
REJECT_RISK_HALT = "RISK_HALT"
REJECT_COST_UNKNOWN = "COST_UNKNOWN"
REJECT_COST_UNKNOWN_SWAP = "COST_UNKNOWN_SWAP"
REJECT_ATR_UNAVAILABLE = "ATR_UNAVAILABLE"
REJECT_COST_R = "COST_R_ABOVE_0_05"
REJECT_NO_NEXT_BAR = "NO_NEXT_BAR_IN_FOLD"
REJECT_STALE = "STALE_SIGNAL"
REJECT_NEWS = "NEWS_WINDOW"
REJECT_COST_UNKNOWN_AT_FILL = "COST_UNKNOWN_AT_FILL"
REJECT_COST_UNKNOWN_SWAP_AT_FILL = "COST_UNKNOWN_SWAP_AT_FILL"
REJECT_COST_R_AT_FILL = "COST_R_ABOVE_0_05_AT_FILL"
REJECT_STOP_INVALID = "STOP_INVALID"
REJECT_RISK_HALT_AT_FILL = "RISK_HALT_AT_FILL"
REJECT_SIZING = "SIZING"
REJECT_RISK_GATE = "RISK_GATE"
REJECT_PORTFOLIO = "PORTFOLIO_RISK"
REJECT_ALREADY_CONSUMED = "FINGERPRINT_ALREADY_CONSUMED"

# Pre-registration section 8: ALL required, by name. Nothing else counts.
REQUIRED_CRITERIA = (
    "trade_count_ge_100", "gross_mean_positive", "gross_episode_ci_lower_positive", "net_mean_positive",
    "net_episode_ci_lower_positive", "folds_8_of_12_positive", "psr_ge_095", "dsr_ge_095", "pbo_le_020",
    "gross_cost_ratio_ge_3", "cost_x1_2_net_positive", "zero_safety_violations", "zero_forbidden_data_access",
)
REJECTED = "H9 REJECTED"
MARGINAL = "H9 MARGINAL"
CANDIDATE = "H9 DEVELOPMENT CANDIDATE -- NOT VALIDATED"


def assert_development_range(start_utc: int, end_utc: int) -> None:
    """The ONLY data H9 may read: the H6 dataset, bar opens 2023-11-01 00:00 .. 2026-06-30 00:00."""
    assert_outside_reserved_oos(start_utc, end_utc)
    if start_utc < DATA_START_UTC or end_utc > DATA_END_UTC:
        raise ValueError(f"H9 reads only {DATA_START_UTC}..{DATA_END_UTC}; refused [{start_utc}, {end_utc}]")


def forbidden_bars(bars) -> list[int]:
    """Times of loaded bars outside the H9 range or inside any reserved OOS interval."""
    return [b.time for b in bars if b.time < DATA_START_UTC or b.time > DATA_END_UTC
            or any(start <= b.time < end for start, end in RESERVED_OOS_INTERVALS)]


# --------------------------------------------------------------------------
# Pure signal math
# --------------------------------------------------------------------------

def hac_slope_tstat(y: list[float], max_lag: int = HAC_MAX_LAG) -> float | None:
    """t-statistic of b in OLS y_i = a + b*i + e_i (i = 0..n-1) with the
    Newey-West variance (Bartlett weights 1 - l/(L+1), no prewhitening, no
    small-sample correction). The [b,b] element of (X'X)^-1 S (X'X)^-1 equals
    (1/Sxx^2) * [sum u_t^2 + 2 sum_l w_l sum_t u_t u_{t-l}] with
    u_t = (i_t - mean(i)) e_t. None when the variance is not positive/finite."""
    n = len(y)
    if n < max_lag + 3:
        return None
    xbar = (n - 1) / 2.0
    ybar = math.fsum(y) / n
    dx = [i - xbar for i in range(n)]
    sxx = math.fsum(d * d for d in dx)
    b = math.fsum(d * (v - ybar) for d, v in zip(dx, y)) / sxx
    a = ybar - b * xbar
    u = [d * (v - a - b * i) for i, (d, v) in enumerate(zip(dx, y))]
    s = math.fsum(v * v for v in u)
    for lag in range(1, max_lag + 1):
        w = 1.0 - lag / (max_lag + 1.0)
        s += 2.0 * w * math.fsum(u[t] * u[t - lag] for t in range(lag, n))
    var = s / (sxx * sxx)
    if not (var > 0 and math.isfinite(var)):
        return None
    t = b / math.sqrt(var)
    return t if math.isfinite(t) else None


def percentile_linear(values: list[float], q: float) -> float:
    """Hyndman-Fan type 7 (numpy 'linear'): interpolate between closest ranks."""
    if not values:
        raise ValueError("percentile of an empty set")
    s = sorted(values)
    h = (len(s) - 1) * q / 100.0
    lo = math.floor(h)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (h - lo) * (s[hi] - s[lo])


@dataclass(frozen=True)
class Series:
    """Causal per-bar features; index k uses bars <= k only."""
    tstat: list          # float | None
    rv24: list           # float | None
    atr14: list          # float | None


def compute_series(bars) -> Series:
    n = len(bars)
    logs = [math.log(b.close) for b in bars]
    tstat: list = [None] * n
    for t in range(TREND_BARS - 1, n):
        tstat[t] = hac_slope_tstat(logs[t - TREND_BARS + 1:t + 1])
    r2 = [None] + [(logs[k] - logs[k - 1]) ** 2 for k in range(1, n)]
    rv24: list = [None] * n
    for t in range(TREND_BARS, n):
        rv24[t] = math.sqrt(math.fsum(r2[t - TREND_BARS + 1:t + 1]))
    tr = [None] + [max(bars[k].high - bars[k].low, abs(bars[k].high - bars[k - 1].close),
                       abs(bars[k].low - bars[k - 1].close)) for k in range(1, n)]
    atr: list = [None] * n
    for t in range(ATR_LENGTH, n):
        atr[t] = math.fsum(tr[t - ATR_LENGTH + 1:t + 1]) / ATR_LENGTH
    return Series(tstat, rv24, atr)


def volatility_condition(bars, rv24: list, t: int) -> dict:
    """RV24_t against the 75th percentile of RV24_s over the previous 30 UTC
    days, every s ending before the current 96-bar window (s <= t-96)."""
    insufficient = {"status": REJECT_INSUFFICIENT_HISTORY, "rv24": rv24[t] if t < len(rv24) else None,
                    "p75": None, "baseline_n": 0, "rv_rank": None}
    if t < 2 * TREND_BARS or rv24[t] is None:
        return insufficient
    w0 = bars[t - TREND_BARS + 1].time
    floor = w0 - BASELINE_SECONDS
    if bars[TREND_BARS].time > floor:
        return insufficient
    baseline = [rv24[s] for s in range(TREND_BARS, t - TREND_BARS + 1)
                if bars[s].time >= floor and rv24[s] is not None]
    if not baseline:
        return insufficient
    p75 = percentile_linear(baseline, VOL_PERCENTILE)
    rank = sum(1 for v in baseline if v <= rv24[t]) / len(baseline)
    return {"status": VOL_OK if rv24[t] >= p75 else REJECT_VOLATILITY, "rv24": rv24[t], "p75": p75,
            "baseline_n": len(baseline), "rv_rank": rank}


def fingerprint_of(direction: str, decision_time: int) -> str:
    return "|".join(("H9", SYMBOL, direction, str(decision_time)))


@dataclass(frozen=True)
class Event:
    index: int                  # bar t whose close is the decision
    direction: str
    tstat: float
    prev_tstat: float
    decision_time: int          # bars[t].time + 900
    fingerprint: str


def detect_events(bars, tstat: list) -> list[Event]:
    """Threshold crossings between consecutive defined evaluations."""
    events = []
    for t in range(1, len(bars)):
        prev, cur = tstat[t - 1], tstat[t]
        if prev is None or cur is None:
            continue
        direction = "BUY" if (prev < T_THRESHOLD <= cur) else "SELL" if (prev > -T_THRESHOLD >= cur) else None
        if direction is not None:
            decision_time = bars[t].time + M15
            events.append(Event(t, direction, cur, prev, decision_time, fingerprint_of(direction, decision_time)))
    return events


# --------------------------------------------------------------------------
# Cost
# --------------------------------------------------------------------------

def _nth_sunday_utc(year: int, month: int, n: int, hour_utc: int) -> int:
    first = datetime(year, month, 1, tzinfo=timezone.utc)
    return int((first + timedelta(days=(6 - first.weekday()) % 7 + 7 * (n - 1), hours=hour_utc)).timestamp())


def server_time(utc_ts: int) -> int:
    """The broker clock under `[mt5] server_time_rule = "UTC+2/US_DST"` (UTC+3 while US DST is in effect).
    A local copy because research/v2 never imports the gateway; tests assert it equals
    `gateway.server_time.utc_to_server` hour by hour over the whole dataset."""
    year = datetime.fromtimestamp(utc_ts, tz=timezone.utc).year
    dst = _nth_sunday_utc(year, 3, 2, 7) <= utc_ts < _nth_sunday_utc(year, 11, 1, 6)
    return utc_ts + (3 if dst else 2) * 3600


def crosses_rollover(entry_utc: int) -> bool:
    """True when [entry, entry + 4 h] contains a broker server midnight (swap would apply; BTCUSD swap is unknown)."""
    start, end = server_time(entry_utc), server_time(entry_utc + HOLD_SECONDS)
    return end // 86_400 != start // 86_400 or start % 86_400 == 0


def round_trip_cost_price(bar, symbol_spec, config) -> float | None:
    """Full spread (half at entry + half at exit) + entry and exit slippage +
    commission, times (1 + uncertainty margin), from the FROZEN assumptions.
    Swap is 0 only for a hold that never crosses the rollover (checked by
    the caller). None = a required cost is unknown -> reject."""
    fills = config.fill_assumptions
    if bar.spread is None or bar.spread <= 0 or fills.slippage_price is None or fills.slippage_price <= 0:
        return None
    commission = round_trip_commission_price(fills, tick_size=symbol_spec.trade_tick_size,
                                             tick_value=symbol_spec.trade_tick_value)
    return estimate_cost(
        spread_price=bar.spread * symbol_spec.point, commission_price_equivalent=commission,
        expected_slippage_price=2.0 * fills.slippage_price, swap_price_equivalent=0.0,
        uncertainty_margin_pct=config.uncertainty_margin_pct,
    ).total_cost


def _conservative(bar, fill_spread_points: int):
    """The bar with max(its spread, the fill bar's spread): a 0 spread is missing evidence, never free."""
    return dataclasses.replace(bar, spread=max(bar.spread or 0, fill_spread_points))


# --------------------------------------------------------------------------
# Opening (research wrapper over the generic gates)
# --------------------------------------------------------------------------

def open_h9_entry(direction: str, stop_distance: float, decision_time: int, bar, *, equity: float,
                  risk_state: RiskState, symbol_spec, config, fingerprint: str):
    """Everything knowable at the fill bar's OPEN, in pre-registered order.
    Returns `(_OpenTrade, meta)` or `(reason, detail)`. Reads only
    `bar.time`, `bar.open` and `bar.spread` (never the fill bar's
    high/low/close)."""
    delay = bar.time - decision_time
    if delay < 0:
        raise AssertionError("fill bar opens before the decision time")
    if delay > MAX_FILL_DELAY_SECONDS:
        return REJECT_STALE, f"fill bar opened {delay}s after the decision close"
    if _in_news_window(bar.time, config.news_windows):
        return REJECT_NEWS, "fill bar inside a HIGH-impact news window"
    rt = round_trip_cost_price(bar, symbol_spec, config)
    if rt is None:
        return REJECT_COST_UNKNOWN_AT_FILL, "fill bar spread unknown"
    if crosses_rollover(bar.time):
        return REJECT_COST_UNKNOWN_SWAP_AT_FILL, "4 h hold from the fill crosses the broker rollover; swap unknown"
    fill_cost_r = rt / stop_distance
    if not fill_cost_r <= MAX_COST_R + 1e-12:
        return REJECT_COST_R_AT_FILL, f"fill cost_R {fill_cost_r:.4f}"
    if stop_distance < symbol_spec.trade_stops_level * symbol_spec.point:
        return REJECT_STOP_INVALID, "stop inside the broker stops level"
    halt = _risk_halt_reason(risk_state, equity, config.risk_limits)
    if halt is not None:
        return REJECT_RISK_HALT_AT_FILL, halt
    safe = calculate_safe_volume(equity=equity, risk_per_trade_pct=config.risk_per_trade_pct,
                                 stop_distance_price=stop_distance, symbol_spec=symbol_spec)
    if not safe.approved:
        return REJECT_SIZING, safe.reason
    risk_decision, risk_reason = evaluate_risk_gate(RiskGateInput(
        proposed_symbol=SYMBOL, proposed_monetary_risk=safe.monetary_risk, equity=equity,
        current_total_open_risk=0.0, current_total_pending_risk=0.0, current_positions_count=0,
        current_positions_for_symbol=0, daily_realized_pnl=risk_state.day_realized_pnl,
        peak_equity=risk_state.peak_equity,
    ), config.risk_limits)
    if risk_decision != RISK_ALLOW:
        return REJECT_RISK_GATE, risk_reason
    portfolio_decision, portfolio_reason = evaluate_portfolio_risk_gate(
        proposed_symbol=SYMBOL, proposed_direction=direction, proposed_monetary_risk=safe.monetary_risk,
        equity=equity, open_positions=[], pending_positions=[], correlation_matrix={},
        limits=portfolio_risk_limits_from_risk_limits(config.risk_limits.max_total_open_risk_pct),
    )
    if portfolio_decision != PORTFOLIO_ALLOW:
        return REJECT_PORTFOLIO, portfolio_reason

    fill = simulate_fill(bar, direction, symbol_spec.point, config.fill_assumptions)
    sign = 1.0 if direction == "BUY" else -1.0

    def money(price_distance: float) -> float:
        return money_from_price_distance(price_distance, safe.volume, tick_size=symbol_spec.trade_tick_size,
                                         tick_value=symbol_spec.trade_tick_value)

    entry_spread, entry_slippage = money(fill.spread_cost_price / 2.0), money(fill.slippage_cost_price)
    trade = _OpenTrade(
        strategy_key=STRATEGY_KEY, direction=direction, entry_time_utc=bar.time, entry_price=fill.price,
        volume=safe.volume, initial_monetary_risk=safe.monetary_risk, entry_regime="UNKNOWN",
        stop_price=fill.price - sign * stop_distance, target_price=None, initial_stop_distance_price=stop_distance,
        total_cost=entry_spread + entry_slippage, entry_features=None, entry_raw_confidence=None, peak_r=0.0,
        strategy_version=1, signal_time_utc=decision_time, entry_spread_cost=entry_spread,
        entry_slippage_cost=entry_slippage,
        entry_evidence={"h9": {"fingerprint": fingerprint, "fill_time_utc": bar.time, "fill_cost_r": fill_cost_r,
                               "fill_round_trip_cost": rt, "risk_gate": risk_reason,
                               "portfolio_gate": portfolio_reason}, "raw_confidence": "NOT USED"},
    )
    return trade, {"fill_rt": rt, "fill_cost_r": fill_cost_r, "fill_spread_points": bar.spread}


# --------------------------------------------------------------------------
# Fold simulation
# --------------------------------------------------------------------------

@dataclass
class H9Trade:
    event: Event
    trade: object               # the engine's SimulatedTrade
    fold: int
    stop_distance: float
    atr14: float
    vol: dict
    decision_rt: float
    decision_cost_r: float
    fill_rt: float
    fill_cost_r: float
    equity_at_entry: float
    stop_price_at_entry: float
    mfe_r_ohlc_bound: float
    mae_r_ohlc_bound: float


class SqliteFingerprintStore(FingerprintStore):
    """Durable consumed-event store in the RESEARCH DB (survives a restart)."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        super().__init__()
        self.conn = conn
        conn.execute("CREATE TABLE IF NOT EXISTS h9_consumed_fingerprints (fingerprint TEXT PRIMARY KEY, "
                     "outcome TEXT NOT NULL, consumed_at_utc INTEGER NOT NULL)")
        conn.commit()

    def is_consumed(self, fingerprint: str) -> bool:
        return self.conn.execute("SELECT 1 FROM h9_consumed_fingerprints WHERE fingerprint = ?",
                                 (fingerprint,)).fetchone() is not None

    def consume(self, fingerprint: str, outcome: str, at_utc: int) -> bool:
        cursor = self.conn.execute("INSERT OR IGNORE INTO h9_consumed_fingerprints (fingerprint, outcome, "
                                   "consumed_at_utc) VALUES (?, ?, ?)", (fingerprint, outcome, at_utc))
        self.conn.commit()
        return cursor.rowcount == 1


def run_h9_fold(bars, series: Series, events: list[Event], fold_range: tuple[int, int], symbol_spec, config, *,
                fold: int, store: FingerprintStore, fingerprint: str = "h9") -> dict:
    """One fold [a, b) of the global bar list, flat at `config.initial_equity`.
    `series`/`events` are computed over the whole (causal) series; only
    events whose decision bar lies in the fold are handled here."""
    a, b = fold_range
    if a < b:
        assert_development_range(bars[a].time, bars[b - 1].time)
    fills = config.fill_assumptions
    point = symbol_spec.point
    equity = config.initial_equity
    risk_state = RiskState(peak_equity=equity, day_utc=bars[a].time // 86_400 if a < b else 0, day_realized_pnl=0.0)
    by_index = {e.index: e for e in events if a <= e.index < b}
    trades: list[H9Trade] = []
    rejected: list[dict] = []
    open_trade = None
    meta: dict | None = None
    pending: dict | None = None

    def close_open(at, price, reason, *, spread, slippage, reference, decision_time):
        nonlocal open_trade, meta, equity, risk_state
        sim = _close_trade(open_trade, at, price, reason, "UNKNOWN", symbol_spec, exit_spread_price=spread,
                           exit_slippage_price=slippage, fills=fills, fill_reference=reference,
                           decision_time=decision_time, origin=EvidenceOrigin.BACKTEST, config_fingerprint=fingerprint)
        equity += sim.realized_pnl
        risk_state = RiskState(peak_equity=max(risk_state.peak_equity, equity), day_utc=risk_state.day_utc,
                               day_realized_pnl=risk_state.day_realized_pnl + sim.realized_pnl)
        trades.append(H9Trade(meta["event"], sim, fold, meta["d"], meta["atr"], meta["vol"], meta["rt"],
                              meta["cost_r"], meta["fill_rt"], meta["fill_cost_r"], meta["equity_at_entry"],
                              meta["stop_price"], meta["mfe"], meta["mae"]))
        open_trade = meta = None

    def excursion(bar):
        d = open_trade.initial_stop_distance_price
        if open_trade.direction == "BUY":
            fav, adv = bar.high - open_trade.entry_price, open_trade.entry_price - bar.low
        else:
            fav, adv = open_trade.entry_price - bar.low, bar.high - open_trade.entry_price
        meta["mfe"], meta["mae"] = max(meta["mfe"], fav / d), max(meta["mae"], adv / d)

    def reject(event: Event, reason: str, **extra):
        rejected.append({"fingerprint": event.fingerprint, "direction": event.direction, "index": event.index,
                         "decision_time": event.decision_time, "fold": fold, "reason": reason, **extra})

    for k in range(a, b):
        bar = bars[k]
        day = bar.time // 86_400
        if day != risk_state.day_utc:
            risk_state = RiskState(peak_equity=risk_state.peak_equity, day_utc=day, day_realized_pnl=0.0)

        # ---- the bar's OPEN: a pending entry fills (or is rejected) here
        if pending is not None and pending["fill_index"] == k:
            p, pending = pending, None
            ev = p["event"]
            outcome = open_h9_entry(ev.direction, p["d"], ev.decision_time, bar, equity=equity, risk_state=risk_state,
                                    symbol_spec=symbol_spec, config=config, fingerprint=ev.fingerprint)
            if not isinstance(outcome[0], _OpenTrade):
                reject(ev, outcome[0], detail=outcome[1], decision_cost_r=p["cost_r"], stop_distance=p["d"])
            else:
                open_trade, fill_meta = outcome
                meta = {**p, **fill_meta, "equity_at_entry": equity, "stop_price": open_trade.stop_price,
                        "mfe": 0.0, "mae": 0.0}

        # ---- an open position: 4 h time exit at this OPEN, else the stop inside this bar's range
        if open_trade is not None:
            safe_bar = _conservative(bar, meta["fill_spread_points"])
            if bar.time >= open_trade.entry_time_utc + HOLD_SECONDS:
                fill = simulate_fill(safe_bar, _opposite(open_trade.direction), point, fills)
                close_open(bar.time, fill.price, TIME_EXIT_REASON, spread=fill.spread_cost_price / 2.0,
                           slippage=fill.slippage_cost_price, reference=FILL_NEXT_BAR_OPEN,
                           decision_time=open_trade.entry_time_utc + HOLD_SECONDS)
            else:
                excursion(bar)
                hit = _intrabar_stop_or_target_hit(open_trade, safe_bar, point, fills.slippage_price)
                if hit is not None:
                    reason, price, spread, slippage, reference = hit
                    if reason != STOP_LOSS_HIT:
                        raise AssertionError(f"H9 has no target; unexpected exit {reason}")
                    close_open(bar.time, price, reason, spread=spread, slippage=slippage, reference=reference,
                               decision_time=bar.time)

        # ---- the bar's CLOSE: a threshold-cross event decides here
        ev = by_index.get(k)
        if ev is None:
            continue
        if not store.consume(ev.fingerprint, "EVENT", ev.decision_time):
            reject(ev, REJECT_ALREADY_CONSUMED)
            continue
        vol = volatility_condition(bars, series.rv24, k)
        atr = series.atr14[k]
        base = {"tstat": ev.tstat, "rv24": vol["rv24"], "p75": vol["p75"], "atr14": atr}
        if vol["status"] != VOL_OK:
            reject(ev, vol["status"], **base)
            continue
        if open_trade is not None:
            reject(ev, REJECT_POSITION_OPEN, **base)
            continue
        if _risk_halt_reason(risk_state, equity, config.risk_limits) is not None:
            reject(ev, REJECT_RISK_HALT, **base)
            continue
        rt = round_trip_cost_price(bar, symbol_spec, config)
        if rt is None:
            reject(ev, REJECT_COST_UNKNOWN, **base)
            continue
        if crosses_rollover(ev.decision_time):
            reject(ev, REJECT_COST_UNKNOWN_SWAP, **base)
            continue
        if atr is None or not atr > 0:
            reject(ev, REJECT_ATR_UNAVAILABLE, **base)
            continue
        d = STOP_ATR * atr
        cost_r = rt / d
        if not cost_r <= MAX_COST_R + 1e-12:
            reject(ev, REJECT_COST_R, decision_cost_r=cost_r, stop_distance=d, **base)
            continue
        if k + 1 >= b:
            reject(ev, REJECT_NO_NEXT_BAR, decision_cost_r=cost_r, stop_distance=d, **base)
            continue
        pending = {"event": ev, "fill_index": k + 1, "d": d, "atr": atr, "vol": vol, "rt": rt, "cost_r": cost_r}

    if open_trade is not None and a < b:
        last = bars[b - 1]
        fill = simulate_fill(_conservative(last, meta["fill_spread_points"]), _opposite(open_trade.direction), point,
                             fills, at="close")
        close_open(last.time + M15, fill.price, BACKTEST_RANGE_ENDED, spread=fill.spread_cost_price / 2.0,
                   slippage=fill.slippage_cost_price, reference=FILL_RANGE_END_CLOSE, decision_time=last.time + M15)
    return {"trades": trades, "rejected": rejected, "events": len(by_index), "final_equity": equity}


# --------------------------------------------------------------------------
# Data gate
# --------------------------------------------------------------------------

def checksum(bars) -> str:
    """Identical to backtest.dataset.compute_bars_checksum (asserted in tests)."""
    hasher = hashlib.sha256()
    for b in bars:
        hasher.update(f"{b.time}|{b.open}|{b.high}|{b.low}|{b.close}|{b.tick_volume}|{b.spread}|{b.real_volume}\n"
                      .encode("utf-8"))
    return hasher.hexdigest()


def data_gate(bars) -> dict:
    """Pre-registration section 2, evaluated BEFORE any rule. Structural only."""
    times = [b.time for b in bars]
    gaps = [times[i + 1] - times[i] for i in range(len(times) - 1) if times[i + 1] - times[i] != M15]
    digest = checksum(bars)
    checks = {
        "count": len(bars) == EXPECTED_BARS,
        "first": bool(bars) and bars[0].time == DATA_START_UTC,
        "last": bool(bars) and bars[-1].time == DATA_END_UTC,
        "checksum": digest == EXPECTED_CHECKSUM,
        "no_forbidden_bars": not forbidden_bars(bars),
        "strictly_increasing": all(times[i] < times[i + 1] for i in range(len(times) - 1)),
        "aligned_900s": all(t % M15 == 0 for t in times),
        "finite_positive_ohlc": all(all(math.isfinite(v) and v > 0 for v in (b.open, b.high, b.low, b.close))
                                    for b in bars),
        "ohlc_consistent": all(b.high >= max(b.open, b.close, b.low) and b.low <= min(b.open, b.close, b.high)
                               for b in bars),
        "spread_nonnegative": all(b.spread is not None and b.spread >= 0 for b in bars),
    }
    return {"passes": all(checks.values()), "checks": checks, "bars": len(bars),
            "first": bars[0].time if bars else None, "last": bars[-1].time if bars else None,
            "checksum": digest, "gaps": len(gaps), "max_gap_seconds": max(gaps) if gaps else 0,
            "gaps_1h": sum(1 for g in gaps if g == 3600), "zero_spread_bars": sum(1 for b in bars if not b.spread)}


# --------------------------------------------------------------------------
# Rows, safety, statistics, classification
# --------------------------------------------------------------------------

def trade_rows(results: list[H9Trade]) -> list[dict]:
    rows = []
    for h in results:
        t = h.trade
        risk = t.initial_monetary_risk
        rows.append({
            "fold": h.fold, "episode": h.event.fingerprint, "day": t.entry_time_utc // 86_400,
            "session": session_label((t.entry_time_utc % 86_400) // 3600), "direction": t.direction,
            "decision_time": h.event.decision_time, "entry_time_utc": t.entry_time_utc,
            "exit_time_utc": t.exit_time_utc, "exit_reason": t.exit_reason,
            "gross_r": t.gross_pnl / risk, "net_r": t.realized_pnl / risk, "cost_r": t.total_cost / risk,
            "entry_cost_r": (t.entry_spread_cost + t.entry_slippage_cost) / risk,
            "exit_cost_r": (t.exit_spread_cost + t.exit_slippage_cost) / risk,
            "commission_fee_swap_r": (t.commission_cost + t.swap_cost + t.fee_cost) / risk,
            "trend_tstat": h.event.tstat, "rv24": h.vol["rv24"], "p75": h.vol["p75"], "rv_rank": h.vol["rv_rank"],
            "atr14": h.atr14, "stop_distance": h.stop_distance, "decision_cost_r": h.decision_cost_r,
            "fill_cost_r": h.fill_cost_r, "holding_seconds": t.exit_time_utc - t.entry_time_utc,
            "mfe_r_ohlc_bound": h.mfe_r_ohlc_bound, "mae_r_ohlc_bound": h.mae_r_ohlc_bound,
            "entry_price": t.entry_price, "exit_price": t.exit_price, "volume": t.volume,
            "initial_monetary_risk": risk, "equity_at_entry": h.equity_at_entry,
        })
    return rows


def safety_violations(results: list[H9Trade], config) -> list[str]:
    problems = []
    ordered = sorted(results, key=lambda h: h.trade.entry_time_utc)
    for prev, cur in zip(ordered, ordered[1:]):
        if cur.trade.entry_time_utc < prev.trade.exit_time_utc:
            problems.append(f"overlapping BTCUSD positions at {cur.trade.entry_time_utc}")
    ceiling = config.risk_limits.risk_per_trade_pct / 100.0
    for h in results:
        t = h.trade
        if not (h.decision_rt and h.decision_rt > 0 and h.decision_cost_r <= MAX_COST_R + 1e-12
                and h.fill_rt and h.fill_rt > 0 and h.fill_cost_r <= MAX_COST_R + 1e-12):
            problems.append(f"cost gate violated at {t.entry_time_utc}")
        if crosses_rollover(t.entry_time_utc):
            problems.append(f"swap-exposed hold accepted at {t.entry_time_utc}")
        if not 0 < t.initial_monetary_risk <= h.equity_at_entry * ceiling * (1 + 1e-9):
            problems.append(f"risk {t.initial_monetary_risk:.2f} above the per-trade ceiling at {t.entry_time_utc}")
        sign = 1.0 if t.direction == "BUY" else -1.0
        if abs(h.stop_price_at_entry - (t.entry_price - sign * h.stop_distance)) > 1e-6:
            problems.append(f"stop not at fill -/+ d at {t.entry_time_utc}")
        if abs(h.stop_distance - STOP_ATR * h.atr14) > 1e-9:
            problems.append(f"stop distance is not 1.5 x ATR14 at {t.entry_time_utc}")
        if t.exit_reason not in EXIT_REASONS:
            problems.append(f"unexpected exit {t.exit_reason} at {t.entry_time_utc}")
    return problems


def _finite(x) -> bool:
    return x is not None and isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def classify(checks: dict, *, trades: int, gross_r, net_r, gross_ci_lower) -> str:
    """Pre-registration section 8. Missing / None / NaN / inf is never a success."""
    def true(name):
        return checks.get(name) is True

    if trades < 100 or not _finite(gross_r) or not _finite(net_r) or gross_r <= 0 or net_r <= 0 \
            or not _finite(gross_ci_lower) or gross_ci_lower <= 0 \
            or not true("zero_safety_violations") or not true("zero_forbidden_data_access"):
        return REJECTED
    return CANDIDATE if all(true(name) for name in REQUIRED_CRITERIA) else MARGINAL


def cost_stress(rows: list[dict]) -> dict:
    return {f"x{k:g}": (statistics.fmean(r["gross_r"] - k * r["cost_r"] for r in rows) if rows else None)
            for k in COST_MULTIPLIERS}


def evaluate(results: list[H9Trade], rejected: list[dict], *, config, prior_fold_net_r: list[list[float]] | None,
             family_trials: int | None, family_sharpe_variance: float | None, forbidden_access: bool,
             span_seconds: int) -> dict:
    """Every pre-registered statistic, the 13 criteria and the classification.
    `prior_fold_net_r` = the 39 immutable H6 BTCUSD 12-fold net-R vectors (None
    when unverifiable -> PBO criterion FALSE)."""
    from adaptive_scalper.research.stats import (
        deflated_sharpe_ratio,
        probabilistic_sharpe_ratio,
        probability_of_backtest_overfitting,
        return_moments,
    )

    rows = sorted(trade_rows(results), key=lambda r: r["entry_time_utc"])
    n = len(rows)

    def mean(key):
        return statistics.fmean(r[key] for r in rows) if rows else None

    gross, net, cost = mean("gross_r"), mean("net_r"), mean("cost_r")
    episodes, days = [r["episode"] for r in rows], [r["day"] for r in rows]

    def ci(key, clusters):
        return cluster_bootstrap_ci([r[key] for r in rows], clusters, resamples=BOOTSTRAP_RESAMPLES,
                                    seed=BOOTSTRAP_SEED, level=0.95) if rows else None

    cis = {"gross_r_episode": ci("gross_r", episodes), "net_r_episode": ci("net_r", episodes),
           "gross_r_day": ci("gross_r", days), "net_r_day": ci("net_r", days)}
    fold_net = [math.fsum(r["net_r"] for r in rows if r["fold"] == f) for f in range(N_FOLDS)]
    psr = dsr = sharpe = None
    moments = None
    note = None
    if n >= 3:
        try:
            m = return_moments([r["net_r"] for r in rows])
            sharpe = m.sharpe
            moments = {"n": m.n, "mean": m.mean, "std": m.std, "skew": m.skew, "kurtosis": m.kurtosis}
            psr = probabilistic_sharpe_ratio(m.sharpe, 0.0, m.n, m.skew, m.kurtosis)
            if family_sharpe_variance is not None and family_trials:
                dsr = deflated_sharpe_ratio(m.sharpe, m.n, n_trials=family_trials,
                                            trial_sharpe_variance=family_sharpe_variance,
                                            skew=m.skew, kurtosis=m.kurtosis)
        except ValueError as exc:
            note = str(exc)
    pbo = None
    if prior_fold_net_r is not None and len(prior_fold_net_r) == H6_BTC_TRIALS \
            and all(len(v) == N_FOLDS for v in prior_fold_net_r):
        matrix = [[vec[f] for vec in prior_fold_net_r] + [fold_net[f]] for f in range(N_FOLDS)]
        pbo = probability_of_backtest_overfitting(matrix, 6)
    stress = cost_stress(rows)
    total_gross, total_cost = math.fsum(r["gross_r"] for r in rows), math.fsum(r["cost_r"] for r in rows)
    violations = safety_violations(results, config)

    def lower(key):
        return cis[key] is not None and _finite(cis[key][0]) and cis[key][0] > 0

    checks = {
        "trade_count_ge_100": n >= 100,
        "gross_mean_positive": _finite(gross) and gross > 0,
        "gross_episode_ci_lower_positive": lower("gross_r_episode"),
        "net_mean_positive": _finite(net) and net > 0,
        "net_episode_ci_lower_positive": lower("net_r_episode"),
        "folds_8_of_12_positive": sum(1 for v in fold_net if v > 0) >= 8,
        "psr_ge_095": _finite(psr) and psr >= 0.95,
        "dsr_ge_095": _finite(dsr) and dsr >= 0.95,
        "pbo_le_020": pbo is not None and pbo.computable and _finite(pbo.pbo) and pbo.pbo <= 0.20,
        "gross_cost_ratio_ge_3": bool(rows) and total_gross > 0 and total_cost > 0 and total_gross >= 3.0 * total_cost,
        "cost_x1_2_net_positive": _finite(stress["x1.2"]) and stress["x1.2"] > 0,
        "zero_safety_violations": not violations,
        "zero_forbidden_data_access": not forbidden_access,
    }
    assert set(checks) == set(REQUIRED_CRITERIA)
    gross_lower = cis["gross_r_episode"][0] if cis["gross_r_episode"] else None
    reasons: dict = {}
    for r in rejected:
        reasons[r["reason"]] = reasons.get(r["reason"], 0) + 1
    return {
        "trades": n, "decisions": n + len(rejected), "rejections": dict(sorted(reasons.items())),
        "episodes": len(set(episodes)),
        "gross_r": gross, "entry_cost_r": mean("entry_cost_r"), "exit_cost_r": mean("exit_cost_r"),
        "commission_fee_swap_r": mean("commission_fee_swap_r"), "cost_r": cost, "net_r": net,
        "total_gross_r": total_gross, "total_cost_r": total_cost, "total_net_r": math.fsum(r["net_r"] for r in rows),
        "gross_to_cost_aggregate": (total_gross / total_cost) if total_cost > 0 else None,
        "ci95": cis, "fold_net_r": fold_net, "positive_folds": sum(1 for v in fold_net if v > 0),
        "sharpe_per_trade": sharpe, "moments": moments, "moments_note": note, "psr": psr, "dsr": dsr,
        "dsr_family_trials": family_trials, "dsr_trial_sharpe_variance": family_sharpe_variance,
        "pbo": {"pbo": pbo.pbo if pbo else None, "computable": bool(pbo and pbo.computable),
                "members": (len(prior_fold_net_r) + 1) if prior_fold_net_r is not None else None,
                "reason": pbo.reason if pbo else "H6 fold vectors unavailable/unverified"},
        "cost_stress_mean_net_r": stress, "safety_violations": violations, "checks": checks,
        "classification": classify(checks, trades=n, gross_r=gross, net_r=net, gross_ci_lower=gross_lower),
        "diagnostics": diagnostics(rows, rejected, span_seconds),
        "rows": rows,
    }


def _summary(values: list) -> dict | None:
    values = [v for v in values if _finite(v)]
    if not values:
        return None
    s = sorted(values)
    return {"n": len(s), "min": s[0], "p25": percentile_linear(s, 25), "median": percentile_linear(s, 50),
            "p75": percentile_linear(s, 75), "max": s[-1], "mean": statistics.fmean(s)}


def diagnostics(rows: list[dict], rejected: list[dict], span_seconds: int) -> dict:
    """DESCRIPTIVE ONLY (pre-registration section 10) -- never a criterion, never a rescue."""
    def group(key_fn):
        out: dict = {}
        for r in rows:
            g = out.setdefault(str(key_fn(r)), {"trades": 0, "gross_r": 0.0, "net_r": 0.0})
            g["trades"] += 1
            g["gross_r"] += r["gross_r"]
            g["net_r"] += r["net_r"]
        for g in out.values():
            g["mean_gross_r"], g["mean_net_r"] = g["gross_r"] / g["trades"], g["net_r"] / g["trades"]
        return dict(sorted(out.items()))

    rv = sorted(r["rv24"] for r in rows)
    cuts = [percentile_linear(rv, q) for q in (25, 50, 75)] if rv else []

    def quartile(r):
        return "Q" + str(1 + sum(1 for c in cuts if r["rv24"] > c))

    n = len(rows)
    days = span_seconds / 86_400 if span_seconds else None
    return {
        "note": "descriptive only; a profitable subgroup inside a failed H9 is not a finding",
        "trades_per_day": (n / days) if days else None, "trades_per_week": (7 * n / days) if days else None,
        "by_direction": group(lambda r: r["direction"]), "by_session": group(lambda r: r["session"]),
        "by_volatility_quartile": group(quartile), "volatility_quartile_cuts_rv24": cuts,
        "by_exit_reason": group(lambda r: r["exit_reason"]), "by_fold": group(lambda r: r["fold"]),
        "trend_tstat": _summary([r["trend_tstat"] for r in rows]), "rv24": _summary(rv),
        "rv_rank_in_baseline": _summary([r["rv_rank"] for r in rows]),
        "stop_distance": _summary([r["stop_distance"] for r in rows]), "atr14": _summary([r["atr14"] for r in rows]),
        "decision_cost_r": _summary([r["decision_cost_r"] for r in rows]),
        "fill_cost_r": _summary([r["fill_cost_r"] for r in rows]),
        "holding_seconds": _summary([r["holding_seconds"] for r in rows]),
        "mfe_r_ohlc_bound": _summary([r["mfe_r_ohlc_bound"] for r in rows]),
        "mae_r_ohlc_bound": _summary([r["mae_r_ohlc_bound"] for r in rows]),
        "excursion_note": "M15 OHLC bounds; a stop bar's full range may include movement after the exit",
        "rejected_decision_cost_r": _summary([r["decision_cost_r"] for r in rejected if "decision_cost_r" in r]),
        "rejections_by_direction": {d: sum(1 for r in rejected if r["direction"] == d) for d in ("BUY", "SELL")},
    }
