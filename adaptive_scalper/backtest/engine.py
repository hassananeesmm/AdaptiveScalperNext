"""Causal backtest engine (directive section 80).

Walks a single symbol/resolution's bar history FORWARD ONLY, using
`adaptive_scalper.features.bar_features.compute_bar_features()`'s own
no-lookahead guarantee (it only ever sees `bars[0:i+1]` at step `i`) and
one additional discipline this engine enforces itself: a signal computed
from bar `i`'s close cannot fill at bar `i`'s own price — the earliest
causal fill is bar `i+1`'s OPEN (directive: "NO LOOKAHEAD"). This is
deliberately conservative (a live system reacting instantly to a closed
bar could in principle do slightly better), never optimistic.

Reuses the SAME production decision cores this engine's live counterpart
uses — `strategies.registry`/`selector.select_proposal`,
`costs.model`/`costs.edge`, `regimes.classifier`,
`position_management.expectancy`/`adaptive_exit`,
`risk.governor.calculate_safe_volume` — rather than a separate
reimplementation, since a backtest that doesn't exercise the real
decision logic isn't validating the real system (directive: "Do not
create decorative/showpiece subsystems disconnected from the real
decision path").

Honest, named scope limits (documented, not silently assumed away):

- Single symbol per run. Cross-symbol correlation/portfolio-heat gating
  (directive section 35) is not exercised here — it depends on the
  full multi-symbol runtime loop (still pending; see PROJECT_STATUS.md),
  and a synchronized multi-symbol backtest is a real, separate design
  this module does not attempt to fake.
- Broker SL/TP intrabar execution uses OHLC bars only, never real tick
  data, so same-bar SL-and-TP-both-in-range is genuinely ambiguous (no
  way to know which was touched first from OHLC alone). This engine
  resolves that ambiguity CONSERVATIVELY: the worse-for-the-trader outcome
  (stop loss) is assumed to have been hit first. Documented, never hidden.
- Directive section 81 (historical news limitation): if the caller does
  not supply point-in-time `news_windows`, `BacktestResult
  .news_limitation_note` states plainly that no historical news blocking
  was applied — this backtest never pretends it knew future historical
  news schedules it wasn't given.
"""

from __future__ import annotations

import time
from dataclasses import replace

from adaptive_scalper.backtest.dataset import build_dataset_snapshot
from adaptive_scalper.backtest.types import (
    BacktestConfig,
    BacktestMetrics,
    BacktestResult,
    OpenPositionState,
    PendingEntryState,
    RegimeTrackerState,
    SimulatedTrade,
)
from adaptive_scalper.costs.edge import BLOCK_COST as _COST_BLOCK
from adaptive_scalper.costs.model import estimate_cost
from adaptive_scalper.features.bar_features import compute_bar_features, numeric_feature_vector
from adaptive_scalper.gateway.types import Bar, SymbolSpec
from adaptive_scalper.history.resolutions import resolution_seconds
from adaptive_scalper.position_management.adaptive_exit import (
    FULL_CLOSE,
    HOLD,
    MOVE_PROTECTIVE_STOP,
    compute_current_r,
    evaluate_adaptive_exit,
    resolve_new_stop_price,
)
from adaptive_scalper.position_management.expectancy import ExpectancyEvidence, evaluate_position_expectancy
from adaptive_scalper.regimes.classifier import RegimeTracker, classify_regime
from adaptive_scalper.risk.governor import calculate_safe_volume
from adaptive_scalper.selector.selector import select_proposal
from adaptive_scalper.simulation.fill_model import money_from_price_distance, simulate_fill
from adaptive_scalper.strategies import build_active_registry
from adaptive_scalper.strategies.base import StrategySignal

STOP_LOSS_HIT = "STOP_LOSS_HIT"
TAKE_PROFIT_HIT = "TAKE_PROFIT_HIT"
NEWS_LIMITATION_NOTE = (
    "no point-in-time historical news calendar was supplied to this run -- "
    "news blocking was NOT applied; results do not reflect any news-driven "
    "entry restriction (directive section 81)"
)


class _OpenTrade:
    __slots__ = (
        "strategy_key", "direction", "entry_time_utc", "entry_price", "volume", "initial_monetary_risk",
        "entry_regime", "stop_price", "target_price", "initial_stop_distance_price", "total_cost",
        "entry_features", "entry_raw_confidence",
    )

    def __init__(self, **kwargs) -> None:
        for k, v in kwargs.items():
            setattr(self, k, v)


def _in_news_window(t: int, windows: tuple[tuple[int, int], ...]) -> bool:
    return any(start <= t < end for start, end in windows)


def _intrabar_stop_or_target_hit(trade: _OpenTrade, bar: Bar) -> tuple[str, float] | None:
    """Conservative resolution of OHLC-only ambiguity: if BOTH the stop
    and target fall within this bar's [low, high] range, the stop is
    assumed hit FIRST (worse for the trader, never optimistic)."""
    if trade.direction == "BUY":
        stop_hit = bar.low <= trade.stop_price
        target_hit = trade.target_price is not None and bar.high >= trade.target_price
    else:
        stop_hit = bar.high >= trade.stop_price
        target_hit = trade.target_price is not None and bar.low <= trade.target_price

    if stop_hit:
        return STOP_LOSS_HIT, trade.stop_price
    if target_hit:
        return TAKE_PROFIT_HIT, trade.target_price
    return None


def run_backtest(
    bars: list[Bar],
    canonical_symbol: str,
    resolution: str,
    symbol_spec: SymbolSpec,
    *,
    config: BacktestConfig = BacktestConfig(),
    now_utc: int | None = None,
    resume_open_position: OpenPositionState | None = None,
    resume_pending_entry: PendingEntryState | None = None,
    resume_regime_tracker: RegimeTrackerState | None = None,
    force_close_at_range_end: bool = True,
) -> BacktestResult:
    """`resume_open_position`/`resume_pending_entry`/
    `resume_regime_tracker`/`force_close_at_range_end=False` are for an
    ONGOING incremental caller (PAPER mode, `adaptive_scalper/paper/`) that
    calls this repeatedly as new bars arrive, rather than once over a
    fixed historical range. CONTRACT the caller must uphold: `bars` on a
    resuming call must be [the trailing `feature_lookback` bars of
    CONTEXT ending right before the new region] + [only the genuinely NEW
    bars since the previous call] -- never the full accumulated history
    again. Every bar from `start_index` onward is treated as a fresh
    decision point, so re-passing already-processed bars re-decides them
    against the resumed position's CURRENT (already-moved-forward) stop/
    target using OLD price action, which is nonsensical. `adaptive_scalper
    .paper.engine` owns this windowing so callers never have to get it
    right by hand."""
    if len(bars) < config.feature_lookback + 3:
        raise ValueError(
            f"need at least feature_lookback+3 ({config.feature_lookback + 3}) bars, got {len(bars)}"
        )
    if resume_open_position is not None and resume_pending_entry is not None:
        raise ValueError("cannot resume an open position and a pending entry simultaneously")
    if resume_pending_entry is not None and resume_pending_entry.signal.canonical_symbol != canonical_symbol:
        raise ValueError(
            "pending entry canonical symbol does not match this run "
            f"({resume_pending_entry.signal.canonical_symbol!r} != {canonical_symbol!r})"
        )
    now = now_utc if now_utc is not None else int(time.time())

    registry = build_active_registry()
    active_strategies = registry.all_active()
    if resume_regime_tracker is not None:
        regime_tracker = RegimeTracker(
            min_confirmations=config.regime_min_confirmations, initial_regime=resume_regime_tracker.confirmed,
            initial_candidate=resume_regime_tracker.candidate,
            initial_candidate_count=resume_regime_tracker.candidate_count,
        )
    else:
        regime_tracker = RegimeTracker(min_confirmations=config.regime_min_confirmations)

    equity = config.initial_equity
    peak_equity = equity
    max_drawdown = 0.0

    open_trade: _OpenTrade | None = (
        _OpenTrade(**resume_open_position.__dict__) if resume_open_position is not None else None
    )
    pending_entry: PendingEntryState | None = resume_pending_entry
    trades: list[SimulatedTrade] = []
    equity_curve: list[tuple[int, float]] = []

    start_index = config.feature_lookback + 1

    for i in range(start_index, len(bars)):
        bar = bars[i]

        # 1. Execute a deferred entry from the PRIOR bar's signal, at
        # THIS bar's open -- the earliest causal fill.
        if pending_entry is not None and open_trade is None:
            # A deferred signal is valid only for its causal next-bar-open
            # opportunity. If the feed jumped far enough to reach the
            # deterministic expiry, cancel it rather than filling a stale
            # scalping setup after a market/data gap.
            if bar.time >= pending_entry.expires_at_utc:
                pending_entry = None
            else:
                signal = pending_entry.signal
                signal_features = pending_entry.entry_features
                pending_entry = None
                fill = simulate_fill(bar, signal.direction, symbol_spec.point, config.fill_assumptions)
                safe_volume = calculate_safe_volume(
                    equity=equity, risk_per_trade_pct=config.risk_per_trade_pct,
                    stop_distance_price=signal.stop_distance, symbol_spec=symbol_spec,
                )
                if safe_volume.approved:
                    stop_price = fill.price - signal.stop_distance if signal.direction == "BUY" else fill.price + signal.stop_distance
                    target_price = fill.price + signal.target_distance if signal.direction == "BUY" else fill.price - signal.target_distance
                    entry_cost_price = fill.spread_cost_price / 2.0 + fill.slippage_cost_price
                    open_trade = _OpenTrade(
                        strategy_key=signal.strategy_key, direction=signal.direction, entry_time_utc=bar.time,
                        entry_price=fill.price, volume=safe_volume.volume, initial_monetary_risk=safe_volume.monetary_risk,
                        entry_regime=signal.regime, stop_price=stop_price, target_price=target_price,
                        initial_stop_distance_price=signal.stop_distance,
                        total_cost=money_from_price_distance(
                            entry_cost_price, safe_volume.volume,
                            tick_size=symbol_spec.trade_tick_size, tick_value=symbol_spec.trade_tick_value,
                        ),
                        entry_features=signal_features, entry_raw_confidence=signal.raw_confidence,
                    )
                # Whether filled or rejected by deterministic sizing, this
                # one-shot deferred entry is consumed. It is never blindly
                # retried on another bar.
                continue

        # 2. Compute features/regime causally from bars[0:i+1] only.
        window = bars[: i + 1]
        features = compute_bar_features(
            canonical_symbol, resolution, window, lookback=config.feature_lookback,
            point_size=symbol_spec.point, now=bar.time,
        )
        raw_regime = classify_regime(features)
        confirmed_regime = regime_tracker.update(raw_regime)

        # 3. Manage an existing open trade FIRST (directive: existing
        # positions have priority over new-entry scanning).
        if open_trade is not None:
            intrabar = _intrabar_stop_or_target_hit(open_trade, bar)
            if intrabar is not None:
                reason, exit_price = intrabar
                trades.append(_close_trade(open_trade, bar.time, exit_price, reason, confirmed_regime, symbol_spec))
                equity = _apply_trade_to_equity(equity, trades[-1])
                peak_equity = max(peak_equity, equity)
                max_drawdown = max(max_drawdown, peak_equity - equity)
                equity_curve.append((bar.time, equity))
                open_trade = None
            else:
                unrealized_pnl = money_from_price_distance(
                    (bar.close - open_trade.entry_price) if open_trade.direction == "BUY" else (open_trade.entry_price - bar.close),
                    open_trade.volume, tick_size=symbol_spec.trade_tick_size, tick_value=symbol_spec.trade_tick_value,
                )
                current_r = compute_current_r(open_trade.initial_monetary_risk, unrealized_pnl)
                holding_seconds = bar.time - open_trade.entry_time_utc
                setup_signal = _reevaluate_setup(active_strategies, open_trade, features, confirmed_regime)
                cost = _estimate_cost(bar, symbol_spec, config)
                current_net_edge = (
                    (open_trade.target_price - bar.close if open_trade.direction == "BUY" else bar.close - open_trade.target_price)
                    - cost.total_cost if cost is not None else None
                )
                expectancy = evaluate_position_expectancy(ExpectancyEvidence(
                    entry_regime=open_trade.entry_regime, current_regime=confirmed_regime,
                    strategy_setup_still_valid=setup_signal is not None,
                    current_net_edge_price=current_net_edge, min_required_edge_price=config.min_net_edge_price,
                    holding_seconds=holding_seconds, current_r=current_r, peak_r=current_r,
                ))
                if current_r is None:
                    pass  # quarantined R -- HOLD, matching live position_management.manager's behavior
                else:
                    decision = evaluate_adaptive_exit(
                        current_r=current_r, peak_r=current_r, holding_seconds=holding_seconds,
                        thesis_valid=expectancy.thesis_valid, regime_reversed=expectancy.regime_reversed,
                        params=config.adaptive_exit_params,
                    )
                    if decision.action == FULL_CLOSE:
                        fill = simulate_fill(bar, _opposite(open_trade.direction), symbol_spec.point, config.fill_assumptions)
                        trades.append(_close_trade(open_trade, bar.time, fill.price, decision.reason, confirmed_regime, symbol_spec))
                        equity = _apply_trade_to_equity(equity, trades[-1])
                        peak_equity = max(peak_equity, equity)
                        max_drawdown = max(max_drawdown, peak_equity - equity)
                        equity_curve.append((bar.time, equity))
                        open_trade = None
                    elif decision.action == MOVE_PROTECTIVE_STOP:
                        proposed = (
                            open_trade.entry_price + decision.new_stop_r * open_trade.initial_stop_distance_price
                            if open_trade.direction == "BUY"
                            else open_trade.entry_price - decision.new_stop_r * open_trade.initial_stop_distance_price
                        )
                        open_trade.stop_price = resolve_new_stop_price(open_trade.stop_price, proposed, open_trade.direction)

        # 4. Scan for a new entry only when flat and nothing pending, and
        # only outside any supplied news-block window.
        if open_trade is None and pending_entry is None and not _in_news_window(bar.time, config.news_windows):
            candidates = []
            regime_obj = raw_regime.__class__(confirmed_regime, raw_regime.confidence, raw_regime.version, raw_regime.reason)
            for strategy in active_strategies:
                signal = strategy.evaluate(features, regime_obj)
                if signal is not None:
                    candidates.append(signal)
            if candidates:
                cost = _estimate_cost(bar, symbol_spec, config)
                selection = select_proposal(
                    candidates, {canonical_symbol: cost},
                    min_net_edge_price=config.min_net_edge_price, min_raw_confidence=config.min_raw_confidence,
                )
                if selection.selected is not None:
                    signal = selection.selected
                    bar_seconds = resolution_seconds(resolution)
                    pending_entry = PendingEntryState(
                        signal=signal,
                        entry_features=numeric_feature_vector(features),
                        signal_bar_time_utc=bar.time,
                        # Permit the expected next bar (t + one interval).
                        # A feed gap reaching t + two intervals is stale.
                        expires_at_utc=bar.time + 2 * bar_seconds,
                    )

    # Force-close any still-open trade at the final bar's close so
    # metrics are never computed over an artificially-truncated position
    # -- UNLESS the caller is an ongoing/incremental process (PAPER mode,
    # directive section 132) that will resume this same position next
    # call via `resume_open_position`, in which case force-closing it here
    # would fabricate a trade exit that never actually happened.
    open_position_state: OpenPositionState | None = None
    if open_trade is not None:
        if force_close_at_range_end:
            last_bar = bars[-1]
            fill = simulate_fill(last_bar, _opposite(open_trade.direction), symbol_spec.point, config.fill_assumptions)
            trades.append(_close_trade(open_trade, last_bar.time, fill.price, "BACKTEST_RANGE_ENDED", regime_tracker.confirmed_regime, symbol_spec))
            equity = _apply_trade_to_equity(equity, trades[-1])
            peak_equity = max(peak_equity, equity)
            max_drawdown = max(max_drawdown, peak_equity - equity)
            equity_curve.append((last_bar.time, equity))
        else:
            open_position_state = OpenPositionState(**{
                name: getattr(open_trade, name) for name in _OpenTrade.__slots__
            })

    metrics = _compute_metrics(trades, config.initial_equity, equity, max_drawdown)
    dataset = build_dataset_snapshot(
        bars, canonical_symbol=canonical_symbol, resolution=resolution,
        strategies=tuple(s.key for s in active_strategies), feature_schema_version=1, now_utc=now,
    )

    final_confirmed, final_candidate, final_candidate_count = regime_tracker.state
    return BacktestResult(
        canonical_symbol=canonical_symbol, resolution=resolution, dataset_id=dataset.dataset_id,
        range_start_utc=bars[0].time, range_end_utc=bars[-1].time, trades=tuple(trades), metrics=metrics,
        equity_curve=tuple(equity_curve),
        news_limitation_note=None if config.news_windows else NEWS_LIMITATION_NOTE,
        config=config, open_position=open_position_state,
        pending_entry=pending_entry if not force_close_at_range_end else None,
        final_regime_tracker_state=RegimeTrackerState(final_confirmed, final_candidate, final_candidate_count),
    )


def _opposite(direction: str) -> str:
    return "SELL" if direction == "BUY" else "BUY"


def _estimate_cost(bar: Bar, symbol_spec: SymbolSpec, config: BacktestConfig):
    from adaptive_scalper.simulation.fill_model import round_trip_commission_price, _spread_price

    spread_price = _spread_price(bar, symbol_spec.point)
    commission_price = round_trip_commission_price(
        config.fill_assumptions, tick_size=symbol_spec.trade_tick_size, tick_value=symbol_spec.trade_tick_value,
    )
    return estimate_cost(
        spread_price=spread_price, commission_price_equivalent=commission_price,
        expected_slippage_price=config.fill_assumptions.slippage_price, swap_price_equivalent=0.0,
        uncertainty_margin_pct=config.uncertainty_margin_pct,
    )


def _reevaluate_setup(active_strategies, open_trade: _OpenTrade, features, confirmed_regime: str) -> StrategySignal | None:
    """"Is the ORIGINAL strategy's own entry condition still true right
    now?" -- re-runs that SAME strategy's `evaluate()` against current
    features/regime; a fresh signal in the SAME direction means the
    setup still holds. Never invents validity from unrelated strategies."""
    from adaptive_scalper.regimes.classifier import RegimeClassification

    strategy = next((s for s in active_strategies if s.key == open_trade.strategy_key), None)
    if strategy is None:
        return None
    regime_obj = RegimeClassification(confirmed_regime, 1.0, 1, "re-evaluation")
    fresh = strategy.evaluate(features, regime_obj)
    if fresh is not None and fresh.direction == open_trade.direction:
        return fresh
    return None


def _close_trade(
    trade: _OpenTrade, exit_time_utc: int, exit_price: float, exit_reason: str, exit_regime: str, symbol_spec: SymbolSpec,
) -> SimulatedTrade:
    price_distance = (exit_price - trade.entry_price) if trade.direction == "BUY" else (trade.entry_price - exit_price)
    gross_pnl = money_from_price_distance(
        price_distance, trade.volume, tick_size=symbol_spec.trade_tick_size, tick_value=symbol_spec.trade_tick_value,
    )
    net_pnl = gross_pnl - trade.total_cost
    realized_r = net_pnl / trade.initial_monetary_risk if trade.initial_monetary_risk > 0 else None
    return SimulatedTrade(
        strategy_key=trade.strategy_key, direction=trade.direction, entry_time_utc=trade.entry_time_utc,
        entry_price=trade.entry_price, volume=trade.volume, initial_monetary_risk=trade.initial_monetary_risk,
        entry_regime=trade.entry_regime, exit_time_utc=exit_time_utc, exit_price=exit_price, exit_reason=exit_reason,
        exit_regime=exit_regime, realized_r=realized_r, realized_pnl=net_pnl, total_cost=trade.total_cost,
        entry_features=trade.entry_features, entry_raw_confidence=trade.entry_raw_confidence,
    )


def _apply_trade_to_equity(equity: float, trade: SimulatedTrade) -> float:
    return equity + (trade.realized_pnl or 0.0)


def _compute_metrics(trades: list[SimulatedTrade], initial_equity: float, final_equity: float, max_drawdown: float) -> BacktestMetrics:
    closed = [t for t in trades if t.is_closed]
    gross_pnl = sum((t.realized_pnl or 0.0) + t.total_cost for t in closed)
    net_pnl = sum(t.realized_pnl or 0.0 for t in closed)
    total_cost = sum(t.total_cost for t in closed)
    wins = [t for t in closed if (t.realized_pnl or 0.0) > 0]
    losses = [t for t in closed if (t.realized_pnl or 0.0) < 0]
    win_rate = len(wins) / len(closed) if closed else None
    gross_win = sum(t.realized_pnl for t in wins)
    gross_loss = abs(sum(t.realized_pnl for t in losses))
    profit_factor = (gross_win / gross_loss) if gross_loss > 0 else None
    r_values = [t.realized_r for t in closed if t.realized_r is not None]
    avg_r = sum(r_values) / len(r_values) if r_values else None
    return BacktestMetrics(
        trade_count=len(trades), closed_trade_count=len(closed), gross_pnl=gross_pnl, net_pnl=net_pnl,
        total_cost=total_cost, win_rate=win_rate, profit_factor=profit_factor, avg_r=avg_r,
        max_drawdown=max_drawdown, final_equity=final_equity,
    )
