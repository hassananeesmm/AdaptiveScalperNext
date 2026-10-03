"""Counterfactual lifecycle evaluator for shadow candidates (audit section 9).

Fixed-horizon outcomes (shadow/observer.py) are not the payoff the runtime
earns: the position-management lifecycle decides the exit. This module
replays THAT lifecycle for a candidate that was never traded, causally, using
only bars that have already closed:

- entry: market fill at the open of the first bar at/after the decision time;
- every later bar: the SAME functions run_backtest/PAPER use
  (`backtest.engine.fill_pending_exit` / `manage_open_trade`), which call the
  same pure decision core as the DEMO position manager
  (`position_management.expectancy` + `adaptive_exit` + `resolve_new_stop_price`):
  broker stop / target (stop wins a same-bar tie, gap-through fills at the
  open), +1 R early profit, profit giveback, breakeven stop, thesis
  invalidation, regime reversal, remaining-edge invalidation, max hold.
  There is no second implementation of any exit rule here.
- features and regime are recomputed per bar from history up to that bar.

Nothing here can reach an order path (tests/test_shadow_observer.py AST test).
It reads bars and configuration and returns a value; the caller persists it.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass

from adaptive_scalper.backtest.engine import (
    _close_trade,
    fill_pending_exit,
    manage_open_trade,
    open_simulated_trade,
)
from adaptive_scalper.backtest.types import BacktestConfig
from adaptive_scalper.costs.swap_horizon import rollovers_crossed
from adaptive_scalper.features.bar_features import compute_bar_features
from adaptive_scalper.gateway.types import Bar, SymbolSpec
from adaptive_scalper.regimes.classifier import RegimeTracker, classify_regime
from adaptive_scalper.simulation.fill_model import money_from_price_distance
from adaptive_scalper.simulation.types import EvidenceOrigin

LIFECYCLE_EVALUATOR_VERSION = "shadow_lifecycle/v1"
RESOLVED = "RESOLVED"
INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
NOT_YET = "NOT_YET"


@dataclass(frozen=True)
class LifecycleOutcome:
    status: str
    entry_time_utc: int | None = None
    entry_price: float | None = None
    initial_stop_price: float | None = None
    initial_target_price: float | None = None
    stop_distance_price: float | None = None
    mfe_r: float | None = None
    mae_r: float | None = None
    time_to_mfe_seconds: int | None = None
    time_to_mae_seconds: int | None = None
    exit_time_utc: int | None = None
    exit_price: float | None = None
    exit_reason: str | None = None
    gross_r: float | None = None
    cost_r: float | None = None
    net_r: float | None = None              # None when any required cost is unknown
    holding_seconds: int | None = None
    bars_used: int | None = None


def simulate_candidate_lifecycle(
    *, canonical_symbol: str, resolution: str, bar_seconds: int, decision_time_utc: int, strategy, direction: str,
    stop_distance: float, target_distance: float, raw_score: float, entry_regime: str, bars: list[Bar],
    symbol_spec: SymbolSpec, config: BacktestConfig, costs_known: bool, now_utc: int,
) -> LifecycleOutcome:
    """`bars` must include history before the decision (features) and may
    include a forming bar; only bars closed by `now_utc` are used. Returns
    NOT_YET while the lifecycle could still be running."""
    closed = sorted((b for b in bars if b.time + bar_seconds <= now_utc), key=lambda b: b.time)
    history = [b for b in closed if b.time < decision_time_utc]
    forward = [b for b in closed if b.time >= decision_time_utc]
    if len(history) < config.feature_lookback + 1:
        return LifecycleOutcome(INSUFFICIENT_DATA)
    if not forward:
        return LifecycleOutcome(NOT_YET)
    if forward[0].time != decision_time_utc:
        return LifecycleOutcome(INSUFFICIENT_DATA)            # the causal fill bar is missing (gap)

    # Swap unknown for a lifecycle that crossed a rollover -> net unknown (never 0).
    fills = config.fill_assumptions
    swap_unknown = fills.swap_monetary_per_lot_per_day is None
    if swap_unknown:
        fills = dataclasses.replace(fills, swap_monetary_per_lot_per_day=0.0)
        config = dataclasses.replace(config, fill_assumptions=fills)
    volume = 1.0
    monetary_risk = money_from_price_distance(stop_distance, volume, tick_size=symbol_spec.trade_tick_size,
                                              tick_value=symbol_spec.trade_tick_value)
    trade = open_simulated_trade(
        strategy_key=strategy.key, strategy_version=strategy.version, direction=direction,
        stop_distance=stop_distance, target_distance=target_distance, regime=entry_regime,
        raw_confidence=raw_score, signal_time_utc=decision_time_utc - bar_seconds, entry_features={},
        bar=forward[0], volume=volume, monetary_risk=monetary_risk, symbol_spec=symbol_spec, fills=fills,
    )
    entry, sign = trade.entry_price, (1.0 if direction == "BUY" else -1.0)
    initial_stop, initial_target = trade.stop_price, trade.target_price
    # Warm the regime tracker exactly as run_backtest does: fresh, from bar
    # index feature_lookback + 1, one update per closed bar.
    tracker = RegimeTracker(min_confirmations=config.regime_min_confirmations)
    confirmed = entry_regime
    for j in range(config.feature_lookback + 1, len(history)):
        warm = compute_bar_features(canonical_symbol, resolution, history[: j + 1], lookback=config.feature_lookback,
                                    point_size=symbol_spec.point, now=history[j].time)
        confirmed = tracker.update(classify_regime(warm))
    mfe = mae = 0.0
    t_mfe = t_mae = 0
    exit_event = exit_bar = None
    for i, bar in enumerate(forward):
        if i > 0:
            exit_event = fill_pending_exit(trade, bar, symbol_spec.point, fills)
            if exit_event is not None:
                exit_bar = bar
                break
        fav = (bar.high - entry) if sign > 0 else (entry - bar.low)
        adv = (entry - bar.low) if sign > 0 else (bar.high - entry)
        if fav > mfe:
            mfe, t_mfe = fav, bar.time - forward[0].time
        if adv > mae:
            mae, t_mae = adv, bar.time - forward[0].time
        window = history + forward[: i + 1]
        features = compute_bar_features(canonical_symbol, resolution, window, lookback=config.feature_lookback,
                                        point_size=symbol_spec.point, now=bar.time)
        confirmed = tracker.update(classify_regime(features))
        exit_event = manage_open_trade(trade, bar, features=features, confirmed_regime=confirmed,
                                       active_strategies=[strategy], symbol_spec=symbol_spec, config=config,
                                       bar_seconds=bar_seconds)
        if exit_event is not None:
            exit_bar = bar
            break
    if exit_event is None:
        return LifecycleOutcome(NOT_YET)                       # still open in the counterfactual
    simulated = _close_trade(
        trade, exit_bar.time, exit_event.exit_price, exit_event.reason, confirmed, symbol_spec,
        exit_spread_price=exit_event.exit_spread_price, exit_slippage_price=exit_event.exit_slippage_price,
        fills=fills, fill_reference=exit_event.fill_reference, decision_time=exit_event.decision_time_utc,
        origin=EvidenceOrigin.SIMULATED, config_fingerprint=LIFECYCLE_EVALUATOR_VERSION,
        server_time_rule=config.server_time_rule,
    )
    crossed = rollovers_crossed(config.server_time_rule, trade.entry_time_utc, exit_bar.time)
    net_known = costs_known and not (swap_unknown and crossed)
    risk = trade.initial_monetary_risk
    return LifecycleOutcome(
        RESOLVED, trade.entry_time_utc, entry, initial_stop, initial_target, stop_distance,
        mfe / stop_distance, mae / stop_distance, t_mfe, t_mae, exit_bar.time, exit_event.exit_price,
        exit_event.reason, simulated.gross_pnl / risk, simulated.total_cost / risk,
        (simulated.realized_r if net_known else None), exit_bar.time - trade.entry_time_utc,
        forward.index(exit_bar) + 1,
    )


def resolve_lifecycles(
    conn, canonical_symbol: str, bars: list[Bar], *, now_utc: int, symbol_spec: SymbolSpec, config: BacktestConfig,
    costs_known: bool, cost_provenance: str, strategies: dict, grace_bars: int = 6,
) -> int:
    """Persist the counterfactual lifecycle outcome of every shadow candidate
    of `canonical_symbol` whose exit has happened in closed bars. Append-only
    and idempotent (one row per candidate, lifecycle version and evaluator
    version). Only candidates recorded under the strategy version and lifecycle
    version this process runs are evaluated -- a v1 candidate is never scored
    with v2 code. A candidate that still cannot be
    resolved `grace_bars` after its maximum possible hold is recorded as
    INSUFFICIENT_DATA rather than guessed. Returns rows written."""
    from adaptive_scalper.shadow.lifecycle import lifecycle_for

    params = config.adaptive_exit_params
    rows = conn.execute(
        "SELECT c.id, c.resolution, c.bar_seconds, c.decision_time_utc, c.strategy_key, c.strategy_version, "
        "c.lifecycle_version, c.direction, c.stop_distance, c.target_distance, c.raw_score, c.confirmed_regime "
        "FROM shadow_candidates c "
        "LEFT JOIN shadow_lifecycle_outcomes l ON l.candidate_id = c.id AND l.lifecycle_version = c.lifecycle_version "
        "AND l.evaluator_version = ? "
        "WHERE c.canonical_symbol = ? AND l.id IS NULL AND c.decision_time_utc <= ?",
        (LIFECYCLE_EVALUATOR_VERSION, canonical_symbol, now_utc),
    ).fetchall()
    written = 0
    for (cid, resolution, bar_seconds, decision, key, strategy_version, lifecycle_version, direction, stop, target,
         score, regime) in rows:
        limit = (params.max_holding_seconds if params.max_holding_enabled else 86_400) + bar_seconds
        expired = now_utc > decision + limit + grace_bars * bar_seconds
        strategy = strategies.get(key)
        try:
            current_lifecycle = lifecycle_for(key).lifecycle_version
        except KeyError:
            current_lifecycle = "UNDECLARED"
        if strategy is not None and (strategy.version != strategy_version or current_lifecycle != lifecycle_version):
            continue                                   # recorded under other code; never re-scored with this one
        if strategy is None:
            outcome = LifecycleOutcome(INSUFFICIENT_DATA)
        else:
            outcome = simulate_candidate_lifecycle(
                canonical_symbol=canonical_symbol, resolution=resolution, bar_seconds=bar_seconds,
                decision_time_utc=decision, strategy=strategy, direction=direction, stop_distance=stop,
                target_distance=target, raw_score=score, entry_regime=regime or "UNKNOWN", bars=bars,
                symbol_spec=symbol_spec, config=config, costs_known=costs_known, now_utc=now_utc,
            )
        if outcome.status == NOT_YET or (outcome.status == INSUFFICIENT_DATA and not expired):
            if not expired:
                continue
            outcome = LifecycleOutcome(INSUFFICIENT_DATA)
        o = outcome
        conn.execute(
            "INSERT OR IGNORE INTO shadow_lifecycle_outcomes (candidate_id, status, evaluator_version, "
            "lifecycle_version, resolved_at_utc, entry_time_utc, entry_price, initial_stop_price, initial_target_price, "
            "stop_distance_price, mfe_r, mae_r, time_to_mfe_seconds, time_to_mae_seconds, exit_time_utc, exit_price, "
            "exit_reason, gross_r, cost_r, net_r, holding_seconds, cost_provenance, bars_used) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (cid, o.status, LIFECYCLE_EVALUATOR_VERSION, lifecycle_version, now_utc, o.entry_time_utc, o.entry_price,
             o.initial_stop_price, o.initial_target_price, o.stop_distance_price, o.mfe_r, o.mae_r,
             o.time_to_mfe_seconds, o.time_to_mae_seconds, o.exit_time_utc, o.exit_price, o.exit_reason, o.gross_r,
             o.cost_r, o.net_r, o.holding_seconds, cost_provenance, o.bars_used),
        )
        written += 1
    conn.commit()
    return written
