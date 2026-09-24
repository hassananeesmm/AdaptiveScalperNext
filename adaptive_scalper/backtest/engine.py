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

Timing and price conventions (bars are MID prices, `Bar.spread` gives the
half spread either side):

- Anything decided at bar `i`'s close -- a new entry OR an adaptive
  FULL_CLOSE -- fills at bar `i+1`'s open. A decision on the last bar of
  an incremental call is returned as `pending_entry` /
  `OpenPositionState.pending_exit_reason` and fills at the next call's
  first new bar.
- The entry bar is a full bar: after filling at its open, its own
  high/low are checked against the new stop/target (the whole range
  printed after the fill), and the position is reviewed at its close.
- Protective SL/TP trigger on the executable side (bid for a long, ask
  for a short). A stop is a market order once triggered: it fills at the
  stop, or at the bar's open if the bar gapped through it, less
  slippage. A target is a limit order and fills at the target.
- Costs are charged exactly once. `entry_price`/`exit_price` are
  execution prices that already embed spread and slippage; commission
  and swap (per UTC rollover crossed) are deducted at close.
  `SimulatedTrade.total_cost` reports every component (entry and exit
  friction measured against mid, commission, swap), so
  `realized_pnl + total_cost` is the mid-to-mid P/L.
- Open positions are marked to the executable exit side (bid for a
  long), matching MT5's `position.profit`; `peak_r` is the running
  maximum of that current_r starting at 0.0, like the live state store.
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
from adaptive_scalper.simulation.fill_model import (
    FillAssumptions,
    _spread_price,
    money_from_price_distance,
    round_trip_commission_price,
    simulate_fill,
)
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
    # `total_cost` here is the entry-side friction only; see OpenPositionState.
    __slots__ = (
        "strategy_key", "direction", "entry_time_utc", "entry_price", "volume", "initial_monetary_risk",
        "entry_regime", "stop_price", "target_price", "initial_stop_distance_price", "total_cost",
        "entry_features", "entry_raw_confidence", "peak_r", "pending_exit_reason",
    )

    def __init__(self, **kwargs) -> None:
        for k, v in kwargs.items():
            setattr(self, k, v)


def _in_news_window(t: int, windows: tuple[tuple[int, int], ...]) -> bool:
    return any(start <= t < end for start, end in windows)


def _intrabar_stop_or_target_hit(
    trade: _OpenTrade, bar: Bar, point_size: float, slippage_price: float,
) -> tuple[str, float, float] | None:
    """Returns `(reason, exit_price, exit_friction_price)`. Triggers on the
    executable side of the book; if BOTH stop and target are in range the
    stop is assumed hit FIRST (worse for the trader, never optimistic)."""
    half_spread = _spread_price(bar, point_size) / 2.0
    if trade.direction == "BUY":
        stop_hit = bar.low - half_spread <= trade.stop_price
        target_hit = trade.target_price is not None and bar.high - half_spread >= trade.target_price
        stop_fill = min(trade.stop_price, bar.open - half_spread) - slippage_price
    else:
        stop_hit = bar.high + half_spread >= trade.stop_price
        target_hit = trade.target_price is not None and bar.low + half_spread <= trade.target_price
        stop_fill = max(trade.stop_price, bar.open + half_spread) + slippage_price

    if stop_hit:
        return STOP_LOSS_HIT, stop_fill, half_spread + slippage_price
    if target_hit:
        return TAKE_PROFIT_HIT, trade.target_price, half_spread
    return None


def _mark_price(trade: _OpenTrade, bar: Bar, point_size: float) -> float:
    half_spread = _spread_price(bar, point_size) / 2.0
    return bar.close - half_spread if trade.direction == "BUY" else bar.close + half_spread




def _pending_from_signal(
    signal: StrategySignal, features: dict[str, float | None], signal_time_utc: int,
) -> PendingEntryState:
    return PendingEntryState(
        strategy_key=signal.strategy_key, direction=signal.direction, stop_distance=signal.stop_distance,
        target_distance=signal.target_distance, regime=signal.regime, raw_confidence=signal.raw_confidence,
        signal_time_utc=signal_time_utc, entry_features=features,
    )


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
    """`resume_open_position`/`resume_pending_entry`/`resume_regime_tracker`/
    `force_close_at_range_end=False` are for an
    ONGOING incremental caller (PAPER mode, `adaptive_scalper/paper/`) that
    calls this repeatedly as new bars arrive, rather than once over a
    fixed historical range. CONTRACT the caller must uphold: `bars` on a
    resuming call must be [the trailing `feature_lookback + 1` bars of
    CONTEXT ending right before the new region] + [only the genuinely NEW
    bars since the previous call] -- never the full accumulated history
    again. Every bar from `start_index` onward is treated as a fresh
    decision point, so re-passing already-processed bars re-decides them
    against the resumed position's CURRENT (already-moved-forward) stop/
    target using OLD price action, which is nonsensical. `adaptive_scalper
    .paper.engine` owns this windowing so callers never have to get it
    right by hand.

    A bounded run needs at least two decision bars (`feature_lookback + 3`
    bars); an incremental run needs only one new bar, because live bars
    arrive one at a time and PAPER must not lag a bar behind them."""
    min_bars = config.feature_lookback + (3 if force_close_at_range_end else 2)
    if len(bars) < min_bars:
        raise ValueError(f"need at least {min_bars} bars for this mode, got {len(bars)}")
    if resume_open_position is not None and resume_pending_entry is not None:
        raise ValueError("cannot resume both an open position and a pending entry -- entries are only scanned while flat")
    now = now_utc if now_utc is not None else int(time.time())
    fills = config.fill_assumptions
    point = symbol_spec.point

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

    def close(trade: _OpenTrade, bar_time: int, exit_price: float, reason: str, exit_friction_price: float) -> None:
        nonlocal equity, peak_equity, max_drawdown
        trades.append(_close_trade(
            trade, bar_time, exit_price, reason, regime_tracker.confirmed_regime, symbol_spec,
            exit_friction_price=exit_friction_price, fills=fills,
        ))
        equity = _apply_trade_to_equity(equity, trades[-1])
        peak_equity = max(peak_equity, equity)
        max_drawdown = max(max_drawdown, peak_equity - equity)
        equity_curve.append((bar_time, equity))

    start_index = config.feature_lookback + 1

    for i in range(start_index, len(bars)):
        bar = bars[i]

        # 1. Fill whatever was decided at the PRIOR bar's close, at THIS
        # bar's open -- the earliest causal fill.
        if open_trade is not None and open_trade.pending_exit_reason is not None:
            fill = simulate_fill(bar, _opposite(open_trade.direction), point, fills)
            close(open_trade, bar.time, fill.price, open_trade.pending_exit_reason,
                  fill.spread_cost_price / 2.0 + fill.slippage_cost_price)
            open_trade = None
        elif pending_entry is not None and open_trade is None:
            open_trade = _open_trade(pending_entry, bar, equity, symbol_spec, config)
        pending_entry = None

        # 2. Compute features/regime causally from bars[0:i+1] only --
        # on EVERY bar, including an entry bar.
        window = bars[: i + 1]
        features = compute_bar_features(
            canonical_symbol, resolution, window, lookback=config.feature_lookback,
            point_size=point, now=bar.time,
        )
        raw_regime = classify_regime(features)
        confirmed_regime = regime_tracker.update(raw_regime)

        # 3. Manage an existing open trade FIRST (directive: existing
        # positions have priority over new-entry scanning). An entry bar
        # is managed too: its whole range printed after the open fill.
        if open_trade is not None:
            intrabar = _intrabar_stop_or_target_hit(open_trade, bar, point, fills.slippage_price)
            if intrabar is not None:
                reason, exit_price, exit_friction = intrabar
                close(open_trade, bar.time, exit_price, reason, exit_friction)
                open_trade = None
            else:
                _review_open_trade(open_trade, bar, features, confirmed_regime, active_strategies, symbol_spec, config)

        # 4. Scan for a new entry only when flat, and only outside any
        # supplied news-block window. A selection fills at the next open.
        if open_trade is None and not _in_news_window(bar.time, config.news_windows):
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
                    pending_entry = _pending_from_signal(selection.selected, numeric_feature_vector(features), bar.time)

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
            fill = simulate_fill(last_bar, _opposite(open_trade.direction), point, fills, at="close")
            close(open_trade, last_bar.time, fill.price, "BACKTEST_RANGE_ENDED",
                  fill.spread_cost_price / 2.0 + fill.slippage_cost_price)
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
        final_regime_tracker_state=RegimeTrackerState(final_confirmed, final_candidate, final_candidate_count),
        pending_entry=None if force_close_at_range_end else pending_entry,
    )


def _open_trade(
    pending: PendingEntryState, bar: Bar, equity: float, symbol_spec: SymbolSpec, config: BacktestConfig,
) -> _OpenTrade | None:
    fill = simulate_fill(bar, pending.direction, symbol_spec.point, config.fill_assumptions)
    safe_volume = calculate_safe_volume(
        equity=equity, risk_per_trade_pct=config.risk_per_trade_pct,
        stop_distance_price=pending.stop_distance, symbol_spec=symbol_spec,
    )
    if not safe_volume.approved:
        return None
    sign = 1.0 if pending.direction == "BUY" else -1.0
    entry_friction_price = fill.spread_cost_price / 2.0 + fill.slippage_cost_price
    return _OpenTrade(
        strategy_key=pending.strategy_key, direction=pending.direction, entry_time_utc=bar.time,
        entry_price=fill.price, volume=safe_volume.volume, initial_monetary_risk=safe_volume.monetary_risk,
        entry_regime=pending.regime, stop_price=fill.price - sign * pending.stop_distance,
        target_price=fill.price + sign * pending.target_distance,
        initial_stop_distance_price=pending.stop_distance,
        total_cost=money_from_price_distance(
            entry_friction_price, safe_volume.volume,
            tick_size=symbol_spec.trade_tick_size, tick_value=symbol_spec.trade_tick_value,
        ),
        entry_features=pending.entry_features, entry_raw_confidence=pending.raw_confidence,
        peak_r=0.0, pending_exit_reason=None,
    )


def _review_open_trade(
    open_trade: _OpenTrade, bar: Bar, features, confirmed_regime: str, active_strategies,
    symbol_spec: SymbolSpec, config: BacktestConfig,
) -> None:
    """Bar-close review: updates peak_r/stop in place, or sets a
    `pending_exit_reason` that fills at the next bar's open."""
    mark = _mark_price(open_trade, bar, symbol_spec.point)
    unrealized_pnl = money_from_price_distance(
        (mark - open_trade.entry_price) if open_trade.direction == "BUY" else (open_trade.entry_price - mark),
        open_trade.volume, tick_size=symbol_spec.trade_tick_size, tick_value=symbol_spec.trade_tick_value,
    )
    current_r = compute_current_r(open_trade.initial_monetary_risk, unrealized_pnl)
    if current_r is None:
        return  # quarantined R -- HOLD, matching live position_management.manager's behavior
    open_trade.peak_r = max(open_trade.peak_r, current_r)

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
        holding_seconds=holding_seconds, current_r=current_r, peak_r=open_trade.peak_r,
    ))
    decision = evaluate_adaptive_exit(
        current_r=current_r, peak_r=open_trade.peak_r, holding_seconds=holding_seconds,
        thesis_valid=expectancy.thesis_valid, regime_reversed=expectancy.regime_reversed,
        params=config.adaptive_exit_params,
    )
    if decision.action == FULL_CLOSE:
        open_trade.pending_exit_reason = decision.reason
    elif decision.action == MOVE_PROTECTIVE_STOP:
        proposed = (
            open_trade.entry_price + decision.new_stop_r * open_trade.initial_stop_distance_price
            if open_trade.direction == "BUY"
            else open_trade.entry_price - decision.new_stop_r * open_trade.initial_stop_distance_price
        )
        open_trade.stop_price = resolve_new_stop_price(open_trade.stop_price, proposed, open_trade.direction)


def _opposite(direction: str) -> str:
    return "SELL" if direction == "BUY" else "BUY"


def _estimate_cost(bar: Bar, symbol_spec: SymbolSpec, config: BacktestConfig):
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


_SECONDS_PER_DAY = 86_400


def _close_trade(
    trade: _OpenTrade, exit_time_utc: int, exit_price: float, exit_reason: str, exit_regime: str,
    symbol_spec: SymbolSpec, *, exit_friction_price: float, fills: FillAssumptions,
) -> SimulatedTrade:
    """`entry_price`/`exit_price` are execution prices (spread and
    slippage already inside them), so they are NOT deducted again;
    commission and swap are. Swap is charged per UTC rollover crossed."""
    def money(price_distance: float) -> float:
        return money_from_price_distance(
            price_distance, trade.volume, tick_size=symbol_spec.trade_tick_size, tick_value=symbol_spec.trade_tick_value,
        )

    price_distance = (exit_price - trade.entry_price) if trade.direction == "BUY" else (trade.entry_price - exit_price)
    execution_pnl = money(price_distance)
    commission = fills.commission_monetary_per_lot * trade.volume
    rollovers = max(0, exit_time_utc // _SECONDS_PER_DAY - trade.entry_time_utc // _SECONDS_PER_DAY)
    swap = fills.swap_monetary_per_lot_per_day * trade.volume * rollovers
    net_pnl = execution_pnl - commission - swap
    total_cost = trade.total_cost + money(exit_friction_price) + commission + swap
    realized_r = net_pnl / trade.initial_monetary_risk if trade.initial_monetary_risk > 0 else None
    return SimulatedTrade(
        strategy_key=trade.strategy_key, direction=trade.direction, entry_time_utc=trade.entry_time_utc,
        entry_price=trade.entry_price, volume=trade.volume, initial_monetary_risk=trade.initial_monetary_risk,
        entry_regime=trade.entry_regime, exit_time_utc=exit_time_utc, exit_price=exit_price, exit_reason=exit_reason,
        exit_regime=exit_regime, realized_r=realized_r, realized_pnl=net_pnl, total_cost=total_cost,
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
