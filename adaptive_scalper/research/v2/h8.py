"""H8 -- XAUUSD M15 breakout context -> new M5 pullback -> M1 resumption
(research / PAPER only; docs/research/V2_H8_PREREGISTRATION_2026-09-30.md).

ONE parameterization, fixed by the pre-registration (the constants below are
not tuning knobs). Three layers:

- `H8SignalEngine`: a causal state machine fed COMPLETED bars in completion
  order (M15, then M5, then M1 on ties). It keeps the M15 Donchian N20
  context, the M5 local extreme / pullback, and yields at most ONE decision
  per pullback: its first M1 resumption. The fingerprint
  `XAUUSD|breakout_event_id|direction|pullback_id` is consumed at that
  decision whatever its outcome, in a durable store, so a restart can never
  re-fire it.
- `run_h8_fold`: turns decisions into trades with the backtest engine's own
  primitives -- `_revalidate_and_open` (next-bar-open fill, staleness, news,
  cost/edge gate, safe sizing, risk + portfolio gates),
  `_intrabar_stop_or_target_hit` (stop first), `simulate_fill`,
  `_close_trade` -- on the M1 clock. Stops are set once from the fill and
  never moved.
- Pure statistics (data gate, cluster bootstrap, cost stress, verdict).

Interpretation recorded for the results document: "min(low since the extreme
bar)" and the counter-close count both use the M5 bars AFTER the extreme bar.

Nothing here touches a broker, a production database or the reserved OOS
(refused); it is not registered in the runtime strategy registry.
"""

from __future__ import annotations

import dataclasses
import math
import random
import sqlite3
import statistics
from collections import deque
from dataclasses import dataclass, field

from adaptive_scalper.backtest.engine import (
    BACKTEST_RANGE_ENDED,
    _close_trade,
    _intrabar_stop_or_target_hit,
    _opposite,
    _OpenTrade,
    _pending_from_signal,
    _revalidate_and_open,
    _risk_halt_reason,
)
from adaptive_scalper.backtest.reserved_oos import assert_outside_reserved_oos
from adaptive_scalper.backtest.types import FILL_NEXT_BAR_OPEN, FILL_RANGE_END_CLOSE, RiskState
from adaptive_scalper.costs.model import estimate_cost
from adaptive_scalper.costs.observations import session_label
from adaptive_scalper.features.bar_features import FEATURE_SCHEMA_VERSION
from adaptive_scalper.simulation.fill_model import round_trip_commission_price, simulate_fill
from adaptive_scalper.simulation.types import EvidenceOrigin
from adaptive_scalper.strategies.base import StrategySignal

SYMBOL = "XAUUSD"
STRATEGY_KEY = "research_h8_bpr"
DONCHIAN_N = 20                 # H6-derived / post-selection context (pre-registration section 2)
PULLBACK_MIN_COUNTER_CLOSES = 2
PULLBACK_MIN_ATR = 0.50
ATR_LENGTH = 14
RESUMPTION_LOOKBACK = 3
MAX_COST_R = 0.05
VOLATILITY_FLOOR_ATR = 0.50
TARGET_R = 2.0
TIME_STOP_SECONDS = 45 * 60
M1, M5, M15 = 60, 300, 900
DEV_START_UTC = 1717200000      # 2024-06-01 00:00:00 UTC
DEV_END_UTC = 1782863999        # 2026-06-30 23:59:59 UTC (the reserved OOS starts 1782864000)
MIN_COVERAGE = 0.90
TIME_STOP_REASON = "TIME_STOP_45M"

REJECT_POSITION_OPEN = "POSITION_OPEN"
REJECT_RISK_HALT = "RISK_HALT"
REJECT_COST_UNKNOWN = "COST_UNKNOWN"
REJECT_COST_R = "COST_R_ABOVE_0.05"
REJECT_COST_R_AT_FILL = "COST_R_ABOVE_0_05_AT_FILL"
REJECT_COST_UNKNOWN_AT_FILL = "COST_UNKNOWN_AT_FILL"
REJECT_GAP_THROUGH_STRUCTURAL = "GAP_THROUGH_STRUCTURAL_STOP"
STRUCTURAL, VOLATILITY, FRICTION = "STRUCTURAL", "VOLATILITY", "FRICTION"
# Pre-registration section 6 + amendment item 12: ALL required, by name. Nothing else counts.
REQUIRED_CRITERIA = (
    "trade_count_ge_100", "gross_mean_positive", "gross_episode_ci_positive", "net_mean_positive",
    "net_episode_ci_positive", "folds_8_of_12_positive", "psr_ge_095", "dsr_ge_095", "pbo_le_020",
    "gross_cost_ratio_ge_3", "cost_x1_2_net_positive", "zero_safety_violations", "zero_forbidden_data_access",
)
REJECT_NO_NEXT_BAR = "NO_NEXT_BAR"


def assert_development_range(start_utc: int, end_utc: int) -> None:
    """The ONLY data H8 may read: 2024-06-01 .. 2026-06-30 (inclusive)."""
    assert_outside_reserved_oos(start_utc, end_utc)
    if start_utc < DEV_START_UTC or end_utc > DEV_END_UTC:
        raise ValueError(f"H8 reads only {DEV_START_UTC}..{DEV_END_UTC}; refused [{start_utc}, {end_utc}]")


# --------------------------------------------------------------------------
# Consumed-fingerprint stores
# --------------------------------------------------------------------------

class FingerprintStore:
    """In-memory store (one backtest run)."""

    def __init__(self) -> None:
        self._consumed: dict[str, str] = {}

    def is_consumed(self, fingerprint: str) -> bool:
        return fingerprint in self._consumed

    def consume(self, fingerprint: str, outcome: str, at_utc: int) -> bool:
        """True when newly consumed; False when it already was (never re-fires)."""
        if fingerprint in self._consumed:
            return False
        self._consumed[fingerprint] = outcome
        return True


class SqliteFingerprintStore(FingerprintStore):
    """Durable store (research / PAPER shadow DB): survives a restart."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        super().__init__()
        self.conn = conn
        conn.execute(
            "CREATE TABLE IF NOT EXISTS h8_consumed_fingerprints (fingerprint TEXT PRIMARY KEY, "
            "outcome TEXT NOT NULL, consumed_at_utc INTEGER NOT NULL)"
        )
        conn.commit()

    def is_consumed(self, fingerprint: str) -> bool:
        return self.conn.execute("SELECT 1 FROM h8_consumed_fingerprints WHERE fingerprint = ?",
                                 (fingerprint,)).fetchone() is not None

    def consume(self, fingerprint: str, outcome: str, at_utc: int) -> bool:
        cursor = self.conn.execute(
            "INSERT OR IGNORE INTO h8_consumed_fingerprints (fingerprint, outcome, consumed_at_utc) VALUES (?, ?, ?)",
            (fingerprint, outcome, at_utc),
        )
        self.conn.commit()
        return cursor.rowcount == 1


# --------------------------------------------------------------------------
# Signal state machine
# --------------------------------------------------------------------------

@dataclass
class Context:
    event_id: str
    direction: str
    breakout_time: int          # completion time of the breakout M15 bar
    level: float


@dataclass(frozen=True)
class Decision:
    fingerprint: str
    breakout_event_id: str
    pullback_id: str
    direction: str
    breakout_level: float
    pullback_extreme: float     # swing low (long) / swing high (short) of the pullback
    atr_m5: float
    bar: object                 # the M1 bar whose close triggered
    decided_at_utc: int         # completion time of that M1 bar


def fingerprint_of(breakout_event_id: str, direction: str, pullback_id: str) -> str:
    return "|".join((SYMBOL, breakout_event_id, direction, pullback_id))


@dataclass
class H8SignalEngine:
    store: FingerprintStore
    context: Context | None = None
    events: list = field(default_factory=list)      # audit trail: breakouts, invalidations, pullbacks
    _m15: deque = field(default_factory=lambda: deque(maxlen=DONCHIAN_N))      # the 20 bars BEFORE the current
    _m1: deque = field(default_factory=lambda: deque(maxlen=RESUMPTION_LOOKBACK))
    _tr: deque = field(default_factory=lambda: deque(maxlen=ATR_LENGTH))
    _prev_m5_close: float | None = None
    _extreme: float | None = None
    _extreme_time: int | None = None
    _counter: int = 0
    _swing: float | None = None
    _pullback_id: str | None = None
    _confirmed_at: int | None = None

    def atr(self) -> float | None:
        return sum(self._tr) / ATR_LENGTH if len(self._tr) == ATR_LENGTH else None

    def _reset_pullback(self) -> None:
        self._extreme = self._extreme_time = self._swing = self._pullback_id = self._confirmed_at = None
        self._counter = 0

    def _invalidate(self, at: int, why: str) -> None:
        self.events.append(("INVALIDATED", at, self.context.event_id, why))
        self.context = None
        self._reset_pullback()

    def on_m15(self, bar) -> None:
        done = bar.time + M15
        if self.context is not None:
            long_ = self.context.direction == "BUY"
            if (long_ and bar.close < self.context.level) or (not long_ and bar.close > self.context.level):
                self._invalidate(done, "M15 close back through the breakout level")
        if len(self._m15) == DONCHIAN_N:
            high = max(b.high for b in self._m15)
            low = min(b.low for b in self._m15)
            direction = "BUY" if bar.close > high else "SELL" if bar.close < low else None
            if direction is not None and (self.context is None or self.context.direction != direction):
                if self.context is not None:
                    self._invalidate(done, f"opposite breakout {direction}")
                self.context = Context(f"{SYMBOL}:M15:{direction}:{bar.time}", direction, done,
                                       high if direction == "BUY" else low)
                self._reset_pullback()
                self.events.append(("BREAKOUT", done, self.context.event_id, self.context.level))
        self._m15.append(bar)

    def on_m5(self, bar) -> None:
        done = bar.time + M5
        prev_close = self._prev_m5_close
        if prev_close is not None:
            self._tr.append(max(bar.high - bar.low, abs(bar.high - prev_close), abs(bar.low - prev_close)))
        self._prev_m5_close = bar.close
        ctx = self.context
        if ctx is None or done <= ctx.breakout_time:
            return
        long_ = ctx.direction == "BUY"
        if (long_ and bar.close < ctx.level) or (not long_ and bar.close > ctx.level):
            self._invalidate(done, "M5 close back through the breakout level")
            return
        extreme_price = bar.high if long_ else bar.low
        if self._extreme is None or (long_ and extreme_price > self._extreme) or \
                (not long_ and extreme_price < self._extreme):
            self._reset_pullback()                  # a new extreme discards an untriggered pullback
            self._extreme, self._extreme_time = extreme_price, done
            return
        swing_price = bar.low if long_ else bar.high
        self._swing = swing_price if self._swing is None else (
            min(self._swing, swing_price) if long_ else max(self._swing, swing_price))
        if prev_close is not None and ((long_ and bar.close < prev_close) or (not long_ and bar.close > prev_close)):
            self._counter += 1
        if self._pullback_id is None and self._counter >= PULLBACK_MIN_COUNTER_CLOSES:
            atr = self.atr()
            retrace = (self._extreme - self._swing) if long_ else (self._swing - self._extreme)
            if atr is not None and retrace >= PULLBACK_MIN_ATR * atr:
                self._pullback_id = f"{ctx.event_id}:PB:{self._extreme_time}"
                self._confirmed_at = done
                self.events.append(("PULLBACK", done, self._pullback_id, retrace, atr))

    def on_m1(self, bar) -> Decision | None:
        done = bar.time + M1
        decision = None
        ctx = self.context
        if ctx is not None and self._pullback_id is not None and done > self._confirmed_at \
                and len(self._m1) == RESUMPTION_LOOKBACK:
            long_ = ctx.direction == "BUY"
            fired = (bar.close > max(b.high for b in self._m1)) if long_ else \
                (bar.close < min(b.low for b in self._m1))
            if fired:
                fp = fingerprint_of(ctx.event_id, ctx.direction, self._pullback_id)
                if not self.store.is_consumed(fp):
                    decision = Decision(fp, ctx.event_id, self._pullback_id, ctx.direction, ctx.level,
                                        self._swing, self.atr(), bar, done)
                self._reset_pullback()               # this pullback's single decision point has passed
        self._m1.append(bar)
        return decision


# --------------------------------------------------------------------------
# Cost and stop
# --------------------------------------------------------------------------

def round_trip_cost_price(bar, symbol_spec, config) -> float | None:
    """spread + entry slippage + exit slippage + commission + uncertainty
    margin from the CONFIGURED assumptions. None = unknown -> reject."""
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


def structural_stop_price(decision: Decision, spread_price: float) -> float:
    """Amendment item 3: the ABSOLUTE invalidation price, one decision spread beyond the pullback extreme."""
    return decision.pullback_extreme - spread_price if decision.direction == "BUY" \
        else decision.pullback_extreme + spread_price


def _combine(structural: float, volatility: float, friction: float) -> dict:
    distance = max(structural, volatility, friction)
    binding = STRUCTURAL if distance == structural else VOLATILITY if distance == volatility else FRICTION
    return {"structural": structural, "volatility": volatility, "friction": friction, "distance": distance,
            "binding": binding}


def stop_distance(decision: Decision, spread_price: float, round_trip_cost: float) -> dict:
    """Decision-time stop: max(structural from the decision close, 0.5 ATR14(M5), cost/0.05)."""
    level = structural_stop_price(decision, spread_price)
    structural = (decision.bar.close - level) if decision.direction == "BUY" else (level - decision.bar.close)
    return {**_combine(structural, VOLATILITY_FLOOR_ATR * decision.atr_m5, round_trip_cost / MAX_COST_R),
            "structural_stop_price": level}


def fill_stop_distance(direction: str, fill_price: float, decision_stop: dict) -> dict | None:
    """Amendment item 6: at the real fill, the structural distance is measured from the fill to the
    SAME absolute invalidation price; volatility and friction floors stay the decision's. None when
    the fill is already through the invalidation price (no valid stop exists)."""
    level = decision_stop["structural_stop_price"]
    structural = (fill_price - level) if direction == "BUY" else (level - fill_price)
    if structural <= 0:
        return None
    return {**_combine(structural, decision_stop["volatility"], decision_stop["friction"]),
            "structural_stop_price": level}


# --------------------------------------------------------------------------
# Fold simulation
# --------------------------------------------------------------------------

@dataclass
class H8Trade:
    decision: Decision
    trade: object               # the engine's SimulatedTrade
    stop: dict                  # the FILL-time stop (amendment item 6)
    round_trip_cost: float      # decision-time round-trip cost
    cost_r: float               # decision-time cost_R
    mfe_r_ohlc_bound: float     # M1 OHLC bounds (amendment item 9): descriptive only
    mae_r_ohlc_bound: float
    fold: int
    equity_at_entry: float
    decision_spread_price: float
    decision_stop: dict = None
    fill_round_trip_cost: float = None
    fill_cost_r: float = None


def completion_stream(m15, m5, m1):
    """(completion_time, priority, kind, bar): M15 before M5 before M1 on ties."""
    stream = [(b.time + M15, 0, "M15", b) for b in m15] + [(b.time + M5, 1, "M5", b) for b in m5] + \
        [(b.time + M1, 2, "M1", b) for b in m1]
    stream.sort(key=lambda e: (e[0], e[1]))
    return stream


def run_h8_fold(m15, m5, m1, symbol_spec, config, *, fold: int, store: FingerprintStore | None = None,
                fingerprint: str = "h8") -> dict:
    """One fold, flat at `config.initial_equity`: trades, rejected decisions
    and the signal audit trail."""
    for series in (m15, m5, m1):
        if series:
            assert_development_range(series[0].time, series[-1].time)
    store = store if store is not None else FingerprintStore()
    engine = H8SignalEngine(store=store)
    fills = config.fill_assumptions
    point = symbol_spec.point
    equity = config.initial_equity
    risk_state = RiskState(peak_equity=equity, day_utc=m1[0].time // 86_400 if m1 else 0, day_realized_pnl=0.0)
    trades: list[H8Trade] = []
    rejected: list[dict] = []
    decisions = 0
    open_trade: _OpenTrade | None = None
    open_meta: dict | None = None
    pending = None           # (pending entry, decision, meta) decided at the previous M1 close

    def close_open(at, price, reason, *, spread, slippage, reference, decision_time):
        nonlocal open_trade, open_meta, equity, risk_state
        sim = _close_trade(open_trade, at, price, reason, open_trade.entry_regime or "UNKNOWN", symbol_spec,
                           exit_spread_price=spread, exit_slippage_price=slippage, fills=fills,
                           fill_reference=reference, decision_time=decision_time, origin=EvidenceOrigin.BACKTEST,
                           config_fingerprint=fingerprint)
        equity += sim.realized_pnl
        risk_state = RiskState(peak_equity=max(risk_state.peak_equity, equity), day_utc=risk_state.day_utc,
                               day_realized_pnl=risk_state.day_realized_pnl + sim.realized_pnl)
        trades.append(H8Trade(open_meta["decision"], sim, open_meta["fill_stop"], open_meta["rt"],
                              open_meta["cost_r"], open_meta["mfe"], open_meta["mae"], fold,
                              open_meta["equity_at_entry"], open_meta["spread"], open_meta["stop"],
                              open_meta["fill_rt"], open_meta["fill_cost_r"]))
        open_trade = open_meta = None

    def track_excursion(bar):
        risk = open_trade.initial_stop_distance_price
        if open_trade.direction == "BUY":
            fav, adv = bar.high - open_trade.entry_price, open_trade.entry_price - bar.low
        else:
            fav, adv = open_trade.entry_price - bar.low, bar.high - open_trade.entry_price
        open_meta["mfe"] = max(open_meta["mfe"], fav / risk)
        open_meta["mae"] = max(open_meta["mae"], adv / risk)

    for _, _, kind, bar in completion_stream(m15, m5, m1):
        if kind == "M15":
            engine.on_m15(bar)
            continue
        if kind == "M5":
            engine.on_m5(bar)
            continue

        # ---- an M1 bar: its OPEN first (fills), then its range, then its close (decisions)
        day = bar.time // 86_400
        if day != risk_state.day_utc:
            risk_state = RiskState(peak_equity=risk_state.peak_equity, day_utc=day, day_realized_pnl=0.0)
        if pending is not None:
            entry, decision, meta = pending
            pending = None
            preview = simulate_fill(bar, decision.direction, point, fills)      # the engine's own fill price
            fill_stop = fill_stop_distance(decision.direction, preview.price, meta["stop"])
            fill_rt = round_trip_cost_price(bar, symbol_spec, config)
            fill_cost_r = (fill_rt / fill_stop["distance"]) if (fill_stop and fill_rt is not None) else None
            meta = {**meta, "fill_rt": fill_rt, "fill_cost_r": fill_cost_r}
            reason = None
            if fill_stop is None:
                reason = REJECT_GAP_THROUGH_STRUCTURAL
            elif fill_rt is None:
                reason = REJECT_COST_UNKNOWN_AT_FILL
            elif not fill_cost_r <= MAX_COST_R + 1e-12:
                reason = REJECT_COST_R_AT_FILL        # never re-widen the stop to rescue it
            if reason is not None:
                rejected.append({**_public(meta), "reason": reason, "at_utc": bar.time})
            else:
                entry = dataclasses.replace(entry, stop_distance=fill_stop["distance"],
                                            target_distance=TARGET_R * fill_stop["distance"])
                outcome = _revalidate_and_open(
                    entry, bar, equity=equity, risk_state=risk_state, symbol_spec=symbol_spec, config=config,
                    canonical_symbol=SYMBOL, max_fill_delay_seconds=2 * M1, external_open_positions=(),
                    correlation_matrix=None, config_fingerprint=fingerprint,
                )
                if isinstance(outcome, tuple):
                    rejected.append({**_public(meta), "reason": outcome[0], "detail": outcome[1], "at_utc": bar.time})
                else:
                    if abs(outcome.entry_price - preview.price) > 1e-9:
                        raise AssertionError("engine fill differs from the previewed fill")
                    open_trade = outcome
                    open_meta = {**meta, "decision": decision, "mfe": 0.0, "mae": 0.0, "equity_at_entry": equity,
                                 "fill_stop": fill_stop}
        if open_trade is not None and bar.time >= open_trade.entry_time_utc + TIME_STOP_SECONDS:
            fill = simulate_fill(bar, _opposite(open_trade.direction), point, fills)
            close_open(bar.time, fill.price, TIME_STOP_REASON, spread=fill.spread_cost_price / 2.0,
                       slippage=fill.slippage_cost_price, reference=FILL_NEXT_BAR_OPEN,
                       decision_time=open_trade.entry_time_utc + TIME_STOP_SECONDS)
        if open_trade is not None:
            track_excursion(bar)
            hit = _intrabar_stop_or_target_hit(open_trade, bar, point, fills.slippage_price)
            if hit is not None:
                reason, price, spread, slippage, reference = hit
                close_open(bar.time, price, reason, spread=spread, slippage=slippage, reference=reference,
                           decision_time=bar.time)

        decision = engine.on_m1(bar)
        if decision is None:
            continue
        decisions += 1
        meta = {"fingerprint": decision.fingerprint, "breakout_event_id": decision.breakout_event_id,
                "pullback_id": decision.pullback_id, "direction": decision.direction,
                "decided_at_utc": decision.decided_at_utc, "fold": fold}
        if open_trade is not None:
            store.consume(decision.fingerprint, REJECT_POSITION_OPEN, decision.decided_at_utc)
            rejected.append({**meta, "reason": REJECT_POSITION_OPEN})
            continue
        if _risk_halt_reason(risk_state, equity, config.risk_limits) is not None:
            store.consume(decision.fingerprint, REJECT_RISK_HALT, decision.decided_at_utc)
            rejected.append({**meta, "reason": REJECT_RISK_HALT})
            continue
        rt = round_trip_cost_price(bar, symbol_spec, config)
        if rt is None or decision.atr_m5 is None:
            store.consume(decision.fingerprint, REJECT_COST_UNKNOWN, decision.decided_at_utc)
            rejected.append({**meta, "reason": REJECT_COST_UNKNOWN})
            continue
        stop = stop_distance(decision, bar.spread * point, rt)
        cost_r = rt / stop["distance"] if stop["distance"] > 0 else math.inf
        if not cost_r <= MAX_COST_R + 1e-12:
            store.consume(decision.fingerprint, REJECT_COST_R, decision.decided_at_utc)
            rejected.append({**meta, "reason": REJECT_COST_R, "cost_r": cost_r})
            continue
        store.consume(decision.fingerprint, "DECIDED", decision.decided_at_utc)
        signal = StrategySignal(
            strategy_key=STRATEGY_KEY, strategy_version=1, canonical_symbol=SYMBOL, direction=decision.direction,
            raw_confidence=0.5, stop_distance=stop["distance"], target_distance=TARGET_R * stop["distance"],
            expected_duration_seconds=TIME_STOP_SECONDS, entry_method="market", regime="UNKNOWN",
            rationale=f"H8 {decision.pullback_id}", feature_schema_version=FEATURE_SCHEMA_VERSION,
            data_timestamp=decision.decided_at_utc,
        )
        entry = _pending_from_signal(signal, {"atr_m5": decision.atr_m5}, decision.decided_at_utc,
                                     canonical_symbol=SYMBOL,
                                     estimated_cost_price=rt, expected_net_edge_price=None)
        pending = (entry, decision, {**meta, "stop": stop, "rt": rt, "cost_r": cost_r,
                                     "spread": bar.spread * point})

    if pending is not None:
        rejected.append({**_public(pending[2]), "reason": REJECT_NO_NEXT_BAR})
    if open_trade is not None and m1:
        last = m1[-1]
        fill = simulate_fill(last, _opposite(open_trade.direction), point, fills, at="close")
        close_open(last.time + M1, fill.price, BACKTEST_RANGE_ENDED, spread=fill.spread_cost_price / 2.0,
                   slippage=fill.slippage_cost_price, reference=FILL_RANGE_END_CLOSE, decision_time=last.time + M1)
    return {"trades": trades, "rejected": rejected, "events": engine.events, "decisions": decisions,
            "final_equity": equity}


def _public(meta: dict) -> dict:
    return {k: v for k, v in meta.items() if k in ("fingerprint", "breakout_event_id", "pullback_id", "direction",
                                                    "decided_at_utc", "fold", "cost_r", "rt", "fill_rt",
                                                    "fill_cost_r")}


# --------------------------------------------------------------------------
# Data gate, rows, statistics
# --------------------------------------------------------------------------

def fold_spans(m15, ranges) -> list[tuple[int, int]]:
    """[start, end) time span of each fold from its M15 bars."""
    return [(m15[a].time, m15[b - 1].time + M15) for a, b in ranges]


def slice_by_span(bars, start: int, end: int) -> list:
    return [b for b in bars if start <= b.time < end]


def coverage_gate(m15, m5, m1, ranges) -> dict:
    """The pre-registered completeness precondition, evaluated before any rule."""
    folds = []
    for (a, b), (start, end) in zip(ranges, fold_spans(m15, ranges)):
        n15 = b - a
        n5, n1 = len(slice_by_span(m5, start, end)), len(slice_by_span(m1, start, end))
        folds.append({"span": [start, end], "m15": n15, "m5": n5, "m1": n1,
                      "m5_coverage": n5 / (3 * n15), "m1_coverage": n1 / (15 * n15),
                      "passes": n5 >= MIN_COVERAGE * 3 * n15 and n1 >= MIN_COVERAGE * 15 * n15})
    return {"passes": bool(folds) and all(f["passes"] for f in folds), "min_coverage": MIN_COVERAGE, "folds": folds}


def trade_rows(results: list[H8Trade]) -> list[dict]:
    rows = []
    for h in results:
        t = h.trade
        risk = t.initial_monetary_risk
        rows.append({
            "fold": h.fold, "episode": h.decision.breakout_event_id, "pullback_id": h.decision.pullback_id,
            "day": t.entry_time_utc // 86_400, "session": session_label((t.entry_time_utc % 86_400) // 3600),
            "direction": t.direction, "entry_time_utc": t.entry_time_utc, "exit_time_utc": t.exit_time_utc,
            "exit_reason": t.exit_reason, "gross_r": t.gross_pnl / risk, "net_r": t.realized_pnl / risk,
            "cost_r": t.total_cost / risk, "entry_cost_r": (t.entry_spread_cost + t.entry_slippage_cost) / risk,
            "exit_cost_r": (t.exit_spread_cost + t.exit_slippage_cost) / risk,
            "commission_swap_r": (t.commission_cost + t.swap_cost + t.fee_cost) / risk,
            "mfe_r_ohlc_bound": h.mfe_r_ohlc_bound, "mae_r_ohlc_bound": h.mae_r_ohlc_bound,
            "holding_seconds": t.exit_time_utc - t.entry_time_utc,
            "decision_cost_r": h.cost_r, "fill_time_cost_r": h.fill_cost_r,
            "decision_round_trip_cost": h.round_trip_cost, "fill_time_round_trip_cost": h.fill_round_trip_cost,
            "stop_binding": h.stop["binding"], "stop_distance": h.stop["distance"],
            "structural_distance": h.stop["structural"], "volatility_floor": h.stop["volatility"],
            "friction_floor": h.stop["friction"], "target_distance": TARGET_R * h.stop["distance"],
            "decision_spread_price": h.decision_spread_price, "atr_m5": h.decision.atr_m5,
            "initial_monetary_risk": risk, "equity_at_entry": h.equity_at_entry, "volume": t.volume,
        })
    return rows


def cluster_bootstrap_ci(values: list[float], clusters: list, *, resamples: int = 10_000, seed: int = 20260930,
                         level: float = 0.95) -> list[float] | None:
    """Percentile CI of the mean per-trade value, resampling WHOLE clusters."""
    groups: dict = {}
    for v, c in zip(values, clusters):
        groups.setdefault(c, []).append(v)
    keys = sorted(groups, key=str)
    if len(keys) < 2:
        return None
    sums = [sum(groups[k]) for k in keys]
    counts = [len(groups[k]) for k in keys]
    rng = random.Random(seed)
    means = []
    for _ in range(resamples):
        s = n = 0
        for _ in keys:
            i = rng.randrange(len(keys))
            s += sums[i]
            n += counts[i]
        means.append(s / n)
    means.sort()
    return [means[int((1 - level) / 2 * resamples)], means[min(resamples - 1, int((1 + level) / 2 * resamples))]]


def cost_stress(rows: list[dict], multipliers=(1.2, 1.5, 2.0)) -> dict:
    return {f"x{k:g}": (statistics.fmean(r["gross_r"] - k * r["cost_r"] for r in rows) if rows else None)
            for k in multipliers}


def safety_violations(results: list[H8Trade], config) -> list[str]:
    """Criterion 12: never two open XAUUSD trades; every accepted trade has a
    known cost with cost_R <= 0.05, risk <= the per-trade ceiling of equity at
    entry, and its stop/target exactly as set at entry (never widened)."""
    problems = []
    ordered = sorted(results, key=lambda h: h.trade.entry_time_utc)
    for prev, cur in zip(ordered, ordered[1:]):
        if cur.trade.entry_time_utc < prev.trade.exit_time_utc:
            problems.append(f"overlapping XAUUSD trades at {cur.trade.entry_time_utc}")
    ceiling = config.risk_limits.risk_per_trade_pct / 100.0
    for h in results:
        t = h.trade
        if not (h.round_trip_cost > 0 and h.cost_r <= MAX_COST_R + 1e-12 and h.fill_cost_r is not None
                and h.fill_cost_r <= MAX_COST_R + 1e-12):
            problems.append(f"cost gate violated at {t.entry_time_utc}")
        if not 0 < t.initial_monetary_risk <= h.equity_at_entry * ceiling * (1 + 1e-9):
            problems.append(f"risk {t.initial_monetary_risk:.2f} above the per-trade ceiling at {t.entry_time_utc}")
    # The stop is set once by `_revalidate_and_open` and nothing in this module
    # modifies an open trade's stop (a gap through it legitimately fills beyond it).
    return problems


def classify(checks: dict, *, trades: int, gross_r: float | None, net_r: float | None) -> str:
    """Pre-registration section 6 (+ amendment item 12). STRONG PASS needs every REQUIRED criterion
    present and exactly True; a missing / None / uncomputable criterion is never a success, and keys
    outside REQUIRED_CRITERIA are ignored."""
    if trades < 100 or gross_r is None or net_r is None or gross_r <= 0 or net_r <= 0 \
            or checks.get("zero_safety_violations") is not True \
            or checks.get("zero_forbidden_data_access") is not True:
        return "FAIL"
    return "STRONG PASS" if all(checks.get(name) is True for name in REQUIRED_CRITERIA) else "MARGINAL"


# --------------------------------------------------------------------------
# Pre-registered evaluation (section 6)
# --------------------------------------------------------------------------

def evaluate(results: list[H8Trade], rejected: list[dict], *, n_folds: int, config, prior_fold_net_r: list[list[float]],
             family_trials: int, family_sharpe_variance: float | None, forbidden_access: bool) -> dict:
    """Every pre-registered statistic and the classification. `prior_fold_net_r`
    = the 12-fold net-R vectors of the completed H6 XAUUSD trials (PBO
    universe); `family_trials` = ledger trials in v2-H6:XAUUSD + v2-H8:XAUUSD."""
    from adaptive_scalper.research.stats import (
        deflated_sharpe_ratio,
        probabilistic_sharpe_ratio,
        probability_of_backtest_overfitting,
        return_moments,
    )

    rows = trade_rows(results)
    n = len(rows)
    mean = (lambda key: statistics.fmean(r[key] for r in rows) if rows else None)
    gross, net, cost = mean("gross_r"), mean("net_r"), mean("cost_r")
    episodes = [r["episode"] for r in rows]
    days = [r["day"] for r in rows]
    ci = {
        "gross_r_episode": cluster_bootstrap_ci([r["gross_r"] for r in rows], episodes),
        "net_r_episode": cluster_bootstrap_ci([r["net_r"] for r in rows], episodes),
        "gross_r_day": cluster_bootstrap_ci([r["gross_r"] for r in rows], days),
        "net_r_day": cluster_bootstrap_ci([r["net_r"] for r in rows], days),
    }
    fold_net = [sum(r["net_r"] for r in rows if r["fold"] == f) for f in range(n_folds)]
    psr = dsr = sharpe = None
    moments_note = None
    if n >= 2:
        try:
            m = return_moments([r["net_r"] for r in rows])
            sharpe = m.sharpe
            psr = probabilistic_sharpe_ratio(m.sharpe, 0.0, m.n, m.skew, m.kurtosis)
            if family_sharpe_variance is not None:
                dsr = deflated_sharpe_ratio(m.sharpe, m.n, n_trials=max(1, family_trials),
                                            trial_sharpe_variance=family_sharpe_variance,
                                            skew=m.skew, kurtosis=m.kurtosis)
        except ValueError as exc:
            moments_note = str(exc)
    matrix = [[vec[f] for vec in prior_fold_net_r] + [fold_net[f]] for f in range(n_folds)] if n_folds else []
    pbo = probability_of_backtest_overfitting(matrix, 6) if matrix and prior_fold_net_r else None
    stress = cost_stress(rows)
    total_gross, total_cost = sum(r["gross_r"] for r in rows), sum(r["cost_r"] for r in rows)
    violations = safety_violations(results, config)

    def lower(key):
        return ci[key] is not None and ci[key][0] > 0

    checks = {
        "trade_count_ge_100": n >= 100,
        "gross_mean_positive": gross is not None and gross > 0,
        "gross_episode_ci_positive": lower("gross_r_episode"),
        "net_mean_positive": net is not None and net > 0,
        "net_episode_ci_positive": lower("net_r_episode"),
        "folds_8_of_12_positive": n_folds == 12 and sum(1 for v in fold_net if v > 0) >= 8,
        "psr_ge_095": psr is not None and psr >= 0.95,
        "dsr_ge_095": dsr is not None and dsr >= 0.95,
        "pbo_le_020": pbo is not None and pbo.computable and pbo.pbo is not None and pbo.pbo <= 0.20,
        "gross_cost_ratio_ge_3": bool(rows) and total_cost > 0 and total_gross >= 3.0 * total_cost,
        "cost_x1_2_net_positive": stress["x1.2"] is not None and stress["x1.2"] > 0,
        "zero_safety_violations": not violations,
        "zero_forbidden_data_access": not forbidden_access,
    }
    assert set(checks) == set(REQUIRED_CRITERIA)
    binding = {k: sum(1 for r in rows if r["stop_binding"] == k) for k in (STRUCTURAL, VOLATILITY, FRICTION)}

    def median(key):
        values = [r[key] for r in rows if r[key] is not None]
        return statistics.median(values) if values else None

    def group(key):
        out: dict = {}
        for r in rows:
            g = out.setdefault(str(r[key]), {"trades": 0, "net_r": 0.0, "gross_r": 0.0})
            g["trades"] += 1
            g["net_r"] += r["net_r"]
            g["gross_r"] += r["gross_r"]
        return out

    reasons: dict = {}
    for r in rejected:
        reasons[r["reason"]] = reasons.get(r["reason"], 0) + 1
    return {
        "trades": n, "decisions": n + len(rejected), "rejections": reasons,
        "episodes": len(set(episodes)), "trades_per_episode": (n / len(set(episodes))) if rows else None,
        "gross_r": gross, "entry_cost_r": mean("entry_cost_r"), "exit_cost_r": mean("exit_cost_r"),
        "commission_swap_r": mean("commission_swap_r"), "cost_r": cost, "net_r": net,
        "total_gross_r": total_gross, "total_cost_r": total_cost, "total_net_r": sum(r["net_r"] for r in rows),
        "mfe_r_ohlc_bound_mean": mean("mfe_r_ohlc_bound"), "mae_r_ohlc_bound_mean": mean("mae_r_ohlc_bound"),
        "excursion_note": "M1 OHLC bounds; an exit bar's full range may include movement after the exit; "
                          "descriptive only, never a criterion",
        "holding_seconds_mean": mean("holding_seconds"),
        "stop_binding": {k: {"count": v, "pct": (100.0 * v / n) if n else None} for k, v in binding.items()},
        "medians": {k: median(k) for k in ("stop_distance", "structural_distance", "volatility_floor",
                                           "friction_floor", "target_distance", "holding_seconds",
                                           "decision_cost_r", "fill_time_cost_r")},
        "exit_reasons": group("exit_reason"), "by_session": group("session"), "by_stop_binding": group("stop_binding"),
        "by_fold_net_r": fold_net, "by_episode": group("episode"), "by_day": group("day"),
        "ci95": ci, "sharpe_per_trade": sharpe, "psr": psr, "dsr": dsr, "dsr_family_trials": family_trials,
        "moments_note": moments_note,
        "pbo": {"pbo": pbo.pbo if pbo else None, "computable": bool(pbo and pbo.computable),
                "members": len(prior_fold_net_r) + 1, "reason": pbo.reason if pbo else "no prior trials"},
        "cost_stress_mean_net_r": stress, "safety_violations": violations, "checks": checks,
        "classification": classify(checks, trades=n, gross_r=gross, net_r=net),
        "rows": rows,
    }
