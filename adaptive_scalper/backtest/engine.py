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
`risk.governor.calculate_safe_volume`/`evaluate_risk_gate`,
`portfolio.exposure.evaluate_portfolio_risk_gate`,
`portfolio.correlation.evaluate_correlation_gate` — rather than a separate
reimplementation, since a backtest that doesn't exercise the real
decision logic isn't validating the real system (directive: "Do not
create decorative/showpiece subsystems disconnected from the real
decision path").

Honest, named scope limits (documented, not silently assumed away):

- Single symbol per run. A caller running several symbols side by side
  (the PAPER runtime) passes the OTHER symbols' open exposure in as
  `external_open_positions` (+ a `correlation_matrix`), and every
  deferred entry is re-checked against the same risk/portfolio/
  correlation gates DEMO uses. A bounded research backtest has no other
  symbols and passes nothing.
- Broker SL/TP intrabar execution uses OHLC bars only, never real tick
  data, so same-bar SL-and-TP-both-in-range is genuinely ambiguous (no
  way to know which was touched first from OHLC alone). This engine
  resolves that ambiguity CONSERVATIVELY: the worse-for-the-trader outcome
  (stop loss) is assumed to have been hit first. Documented, never hidden.

Timing and price conventions (bars are MID prices, `Bar.spread` gives the
half spread either side). Every fill records its causal reference
(`SimulatedTrade.entry_fill_reference`/`exit_fill_reference`):

- NEXT_BAR_OPEN: anything decided at bar `i`'s close -- a new entry OR an
  adaptive FULL_CLOSE -- fills at bar `i+1`'s open. A decision on the last
  bar of an incremental call is returned as `pending_entry` /
  `OpenPositionState.pending_exit_reason` and fills at the next call's
  first new bar.
- A pending entry is NOT a permanent authorization. Immediately before
  it fills it is re-validated against what is knowable at that open:
  signal staleness (gap since the decision), news window, cost and
  expected net edge at the fill bar's spread, safe sizing, and the hard
  risk ceilings (daily loss, drawdown, open positions, total risk,
  portfolio heat, correlation). A failure drops it and is recorded in
  `BacktestResult.entry_rejections`.
- STOP_TRIGGER / TARGET_TRIGGER: protective SL/TP are standing broker
  orders and trigger inside a bar, including the entry bar (its whole
  range printed after the open fill). They trigger on the executable side
  (bid for a long, ask for a short). A stop is a market order once
  triggered: it fills at the stop, or at the bar's open if the bar
  gapped through it, less slippage. A target is a limit order and fills
  at the target.
- RANGE_END_CLOSE: a bounded run force-closes at the last bar's close.
- Costs are charged exactly once. `entry_price`/`exit_price` are
  execution prices that already embed spread and slippage; commission
  and swap (per UTC rollover crossed) are deducted at close. Every
  component is reported separately on the trade and `total_cost` is their
  sum, so `realized_pnl + total_cost` is the mid-to-mid P/L.
- Open positions are marked to the executable exit side (bid for a
  long), matching MT5's `position.profit`; `peak_r` is the running
  maximum of that current_r starting at 0.0, like the live state store.
- Daily-loss and drawdown ceilings are enforced exactly as live: once
  reached, no new entries are scanned or filled (existing positions are
  still managed). The state survives incremental calls via
  `resume_risk_state`.
- Directive section 81 (historical news limitation): if the caller does
  not supply point-in-time `news_windows`, `BacktestResult
  .news_limitation_note` states plainly that no historical news blocking
  was applied — this backtest never pretends it knew future historical
  news schedules it wasn't given.
"""

from __future__ import annotations

import hashlib
import time

from adaptive_scalper.backtest.dataset import build_dataset_snapshot
from adaptive_scalper.backtest.fingerprint import compute_config_fingerprint
from adaptive_scalper.backtest.types import (
    FILL_NEXT_BAR_OPEN,
    FILL_RANGE_END_CLOSE,
    FILL_STOP_TRIGGER,
    FILL_TARGET_TRIGGER,
    LOST_TO_HIGHER_EDGE,
    NOT_CONSULTED,
    REJECT_CORRELATION,
    REJECT_COST,
    REJECT_EDGE_UNVALIDATED,
    REJECT_EXPECTED_EDGE,
    REJECT_NEWS,
    REJECT_PORTFOLIO_RISK,
    REJECT_RISK,
    REJECT_SIZING,
    REJECT_STALE_SIGNAL,
    BacktestConfig,
    BacktestMetrics,
    BacktestResult,
    CandidateRecord,
    EntryRejection,
    OpenPositionState,
    PendingEntryState,
    RegimeTrackerState,
    RiskState,
    SimulatedTrade,
)
from adaptive_scalper.costs.edge import ALLOW as _COST_ALLOW
from adaptive_scalper.costs.edge import BLOCK_EDGE_UNVALIDATED as _COST_BLOCK_EDGE_UNVALIDATED
from adaptive_scalper.costs.edge import BLOCK_EXPECTED_EDGE as _COST_BLOCK_EXPECTED_EDGE
from adaptive_scalper.costs.edge import evaluate_cost_gate
from adaptive_scalper.costs.edge_evidence import edge_provider_of
from adaptive_scalper.costs.model import remaining_exit_cost_from_evidence, round_trip_cost_from_evidence
from adaptive_scalper.costs.swap_horizon import rollovers_crossed, swap_price_for_horizon
from adaptive_scalper.features.bar_features import FEATURE_SCHEMA_VERSION, compute_bar_features, numeric_feature_vector
from adaptive_scalper.gateway.types import Bar, SymbolSpec
from adaptive_scalper.history.resolutions import resolution_seconds
from adaptive_scalper.portfolio.correlation import ALLOW as _CORRELATION_ALLOW
from adaptive_scalper.portfolio.correlation import CorrelationResult, evaluate_correlation_gate
from adaptive_scalper.portfolio.exposure import ALLOW as _PORTFOLIO_ALLOW
from adaptive_scalper.portfolio.exposure import (
    PositionExposure,
    evaluate_portfolio_risk_gate,
    portfolio_risk_limits_from_risk_limits,
)
from adaptive_scalper.position_management.adaptive_exit import (
    FULL_CLOSE,
    MOVE_PROTECTIVE_STOP,
    compute_current_r,
    evaluate_adaptive_exit,
    resolve_new_stop_price,
)
from adaptive_scalper.position_management.expectancy import ExpectancyEvidence, evaluate_position_expectancy
from adaptive_scalper.regimes.classifier import RegimeTracker, classify_regime
from adaptive_scalper.risk.governor import ALLOW as _RISK_ALLOW
from adaptive_scalper.risk.governor import RiskGateInput, RiskLimits, calculate_safe_volume, evaluate_risk_gate
from adaptive_scalper.selector.selector import select_proposal
from adaptive_scalper.selector.suspension import partition_suspended
from adaptive_scalper.simulation.fill_model import (
    FILL_MODEL_VERSION,
    FillAssumptions,
    _spread_price,
    money_from_price_distance,
    round_trip_commission_price,
    simulate_fill,
)
from adaptive_scalper.simulation.types import EvidenceOrigin
from adaptive_scalper.strategies import build_active_registry, select_active_strategies
from adaptive_scalper.strategies.base import StrategySignal

STOP_LOSS_HIT = "STOP_LOSS_HIT"
TAKE_PROFIT_HIT = "TAKE_PROFIT_HIT"
BACKTEST_RANGE_ENDED = "BACKTEST_RANGE_ENDED"
NEWS_LIMITATION_NOTE = (
    "no point-in-time historical news calendar was supplied to this run -- "
    "news blocking was NOT applied; results do not reflect any news-driven "
    "entry restriction (directive section 81)"
)
_SECONDS_PER_DAY = 86_400


class _OpenTrade:
    # `total_cost` here is the entry-side friction only; see OpenPositionState.
    __slots__ = tuple(OpenPositionState.__dataclass_fields__)

    def __init__(self, **kwargs) -> None:
        for name in self.__slots__:
            setattr(self, name, OpenPositionState.__dataclass_fields__[name].default
                    if name not in kwargs else kwargs[name])

    def to_state(self) -> OpenPositionState:
        return OpenPositionState(**{name: getattr(self, name) for name in self.__slots__})


def _in_news_window(t: int, windows: tuple[tuple[int, int], ...]) -> bool:
    return any(start <= t < end for start, end in windows)


def _intrabar_stop_or_target_hit(
    trade: _OpenTrade, bar: Bar, point_size: float, slippage_price: float,
) -> tuple[str, float, float, float, str] | None:
    """Returns `(reason, exit_price, exit_spread_price, exit_slippage_price,
    fill_reference)`. Triggers on the executable side of the book; if BOTH
    stop and target are in range the stop is assumed hit FIRST (worse for
    the trader, never optimistic)."""
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
        return STOP_LOSS_HIT, stop_fill, half_spread, slippage_price, FILL_STOP_TRIGGER
    if target_hit:
        return TAKE_PROFIT_HIT, trade.target_price, half_spread, 0.0, FILL_TARGET_TRIGGER
    return None


def _mark_price(trade: _OpenTrade, bar: Bar, point_size: float) -> float:
    half_spread = _spread_price(bar, point_size) / 2.0
    return bar.close - half_spread if trade.direction == "BUY" else bar.close + half_spread


def _pending_fingerprint(canonical_symbol: str, signal: StrategySignal, signal_time_utc: int) -> str:
    material = "|".join(str(v) for v in (
        canonical_symbol, signal.strategy_key, signal.strategy_version, signal.direction, signal_time_utc,
        signal.stop_distance, signal.target_distance, signal.raw_confidence,
    ))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]


def _pending_from_signal(
    signal: StrategySignal, features: dict[str, float | None], signal_time_utc: int, *,
    canonical_symbol: str, estimated_cost_price: float | None, expected_net_edge_price: float | None,
) -> PendingEntryState:
    return PendingEntryState(
        strategy_key=signal.strategy_key, direction=signal.direction, stop_distance=signal.stop_distance,
        target_distance=signal.target_distance, regime=signal.regime, raw_confidence=signal.raw_confidence,
        signal_time_utc=signal_time_utc, entry_features=features, strategy_version=signal.strategy_version,
        canonical_symbol=canonical_symbol, entry_method=signal.entry_method,
        expected_duration_seconds=signal.expected_duration_seconds,
        estimated_cost_price=estimated_cost_price, expected_net_edge_price=expected_net_edge_price,
        fingerprint=_pending_fingerprint(canonical_symbol, signal, signal_time_utc),
    )


def _signal_from_pending(pending: PendingEntryState, canonical_symbol: str) -> StrategySignal:
    return StrategySignal(
        strategy_key=pending.strategy_key, strategy_version=pending.strategy_version or 0,
        canonical_symbol=pending.canonical_symbol or canonical_symbol, direction=pending.direction,
        raw_confidence=pending.raw_confidence, stop_distance=pending.stop_distance,
        target_distance=pending.target_distance, expected_duration_seconds=pending.expected_duration_seconds or 0,
        entry_method=pending.entry_method or "market", regime=pending.regime,
        rationale="deferred-entry revalidation", feature_schema_version=FEATURE_SCHEMA_VERSION,
        data_timestamp=pending.signal_time_utc,
    )


def _risk_halt_reason(risk_state: RiskState, equity: float, limits: RiskLimits) -> str | None:
    """The daily-loss / drawdown ceilings that stop ALL new entries (not a
    per-proposal check) -- used to short-circuit scanning, directive
    section 45. The authoritative per-proposal check is evaluate_risk_gate."""
    if risk_state.day_realized_pnl < 0 and equity > 0:
        loss_pct = abs(risk_state.day_realized_pnl) / equity * 100.0
        if loss_pct >= limits.max_daily_loss_pct:
            return f"daily loss {loss_pct:.2f}% >= max_daily_loss_pct {limits.max_daily_loss_pct}%"
    if risk_state.peak_equity > 0:
        drawdown_pct = max(0.0, (risk_state.peak_equity - equity) / risk_state.peak_equity * 100.0)
        if drawdown_pct >= limits.max_drawdown_pct:
            return f"drawdown {drawdown_pct:.2f}% >= max_drawdown_pct {limits.max_drawdown_pct}%"
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
    resume_risk_state: RiskState | None = None,
    force_close_at_range_end: bool = True,
    origin: EvidenceOrigin = EvidenceOrigin.BACKTEST,
    external_open_positions: tuple[PositionExposure, ...] = (),
    correlation_matrix: dict[tuple[str, str], CorrelationResult] | None = None,
    entry_block_reason: str | None = None,
    strategy_keys: tuple[str, ...] | None = None,
    candidate_log: list[CandidateRecord] | None = None,
) -> BacktestResult:
    """`strategy_keys`: evaluate only these active strategies (None = all
    six). An independent research session passes exactly one, so its
    positions, risk, equity and costs are its own and never compete with
    another strategy in the selector. The subset is part of the config
    fingerprint.

    `candidate_log`: if given, every signal the selector saw is appended
    as a `CandidateRecord` (bar-close knowledge only), for the selector
    study. Signals are only generated while flat, as in live operation.

    `entry_block_reason`: a GLOBAL new-entry block the caller is under
    for this whole call (kill switch not DISENGAGED, news calendar
    unavailable...). No entry is scanned or filled -- a pending entry is
    dropped with this reason -- while existing positions are still
    managed exactly as usual (directive sections 37/42/45).

    `resume_open_position`/`resume_pending_entry`/`resume_regime_tracker`/
    `resume_risk_state`/`force_close_at_range_end=False` are for an
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
    max_fill_delay = (
        config.max_entry_fill_delay_seconds if config.max_entry_fill_delay_seconds is not None
        else 2 * resolution_seconds(resolution)
    )

    active_strategies = select_active_strategies(strategy_keys, build_active_registry())
    fingerprint, _ = compute_config_fingerprint(
        config, canonical_symbol=canonical_symbol, resolutions=(resolution,),
        strategies=tuple((s.key, s.version) for s in active_strategies),
    )
    if resume_regime_tracker is not None:
        regime_tracker = RegimeTracker(
            min_confirmations=config.regime_min_confirmations, initial_regime=resume_regime_tracker.confirmed,
            initial_candidate=resume_regime_tracker.candidate,
            initial_candidate_count=resume_regime_tracker.candidate_count,
        )
    else:
        regime_tracker = RegimeTracker(min_confirmations=config.regime_min_confirmations)

    equity = config.initial_equity
    peak_equity = equity  # this call's own drawdown metric
    max_drawdown = 0.0
    start_index = config.feature_lookback + 1
    risk_state = resume_risk_state or RiskState(
        peak_equity=config.initial_equity, day_utc=bars[start_index].time // _SECONDS_PER_DAY, day_realized_pnl=0.0,
    )

    open_trade: _OpenTrade | None = (
        _OpenTrade(**resume_open_position.__dict__) if resume_open_position is not None else None
    )
    pending_entry: PendingEntryState | None = resume_pending_entry
    trades: list[SimulatedTrade] = []
    equity_curve: list[tuple[int, float]] = []
    rejections: list[EntryRejection] = []
    halted_scans = 0

    def close(
        trade: _OpenTrade, bar_time: int, exit_price: float, reason: str, *,
        exit_spread_price: float, exit_slippage_price: float, fill_reference: str, decision_time: int,
    ) -> None:
        nonlocal equity, peak_equity, max_drawdown, risk_state
        closed = _close_trade(
            trade, bar_time, exit_price, reason, regime_tracker.confirmed_regime, symbol_spec,
            exit_spread_price=exit_spread_price, exit_slippage_price=exit_slippage_price, fills=fills,
            fill_reference=fill_reference, decision_time=decision_time, origin=origin, config_fingerprint=fingerprint,
            server_time_rule=config.server_time_rule,
        )
        trades.append(closed)
        equity = equity + (closed.realized_pnl or 0.0)
        peak_equity = max(peak_equity, equity)
        max_drawdown = max(max_drawdown, peak_equity - equity)
        equity_curve.append((bar_time, equity))
        risk_state = RiskState(
            peak_equity=max(risk_state.peak_equity, equity), day_utc=risk_state.day_utc,
            day_realized_pnl=risk_state.day_realized_pnl + (closed.realized_pnl or 0.0),
        )

    def reject(pending: PendingEntryState, bar: Bar, code: str, detail: str) -> None:
        rejections.append(EntryRejection(
            signal_time_utc=pending.signal_time_utc, attempted_fill_time_utc=bar.time,
            strategy_key=pending.strategy_key, direction=pending.direction, reason_code=code, detail=detail,
        ))

    for i in range(start_index, len(bars)):
        bar = bars[i]
        day = bar.time // _SECONDS_PER_DAY
        if day != risk_state.day_utc:
            risk_state = RiskState(peak_equity=risk_state.peak_equity, day_utc=day, day_realized_pnl=0.0)

        # 1. Fill whatever was decided at the PRIOR bar's close, at THIS
        # bar's open -- the earliest causal fill.
        if open_trade is not None and open_trade.pending_exit_reason is not None:
            fill = simulate_fill(bar, _opposite(open_trade.direction), point, fills)
            close(
                open_trade, bar.time, fill.price, open_trade.pending_exit_reason,
                exit_spread_price=fill.spread_cost_price / 2.0, exit_slippage_price=fill.slippage_cost_price,
                fill_reference=FILL_NEXT_BAR_OPEN,
                decision_time=open_trade.pending_exit_decision_time_utc or bar.time,
            )
            open_trade = None
        elif pending_entry is not None and open_trade is None and entry_block_reason is not None:
            reject(pending_entry, bar, entry_block_reason, "global new-entry block in force at fill time")
        elif pending_entry is not None and open_trade is None:
            outcome = _revalidate_and_open(
                pending_entry, bar, equity=equity, risk_state=risk_state, symbol_spec=symbol_spec,
                config=config, canonical_symbol=canonical_symbol, max_fill_delay_seconds=max_fill_delay,
                external_open_positions=external_open_positions, correlation_matrix=correlation_matrix,
                config_fingerprint=fingerprint, bar_seconds=resolution_seconds(resolution),
            )
            if isinstance(outcome, _OpenTrade):
                open_trade = outcome
            else:
                reject(pending_entry, bar, *outcome)
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
                reason, exit_price, exit_spread, exit_slippage, reference = intrabar
                close(
                    open_trade, bar.time, exit_price, reason, exit_spread_price=exit_spread,
                    exit_slippage_price=exit_slippage, fill_reference=reference, decision_time=bar.time,
                )
                open_trade = None
            else:
                _review_open_trade(open_trade, bar, features, confirmed_regime, active_strategies, symbol_spec, config,
                                   bar_seconds=resolution_seconds(resolution))

        # 4. Scan for a new entry only when flat, outside any supplied
        # news-block window, and while no daily-loss/drawdown ceiling is
        # reached (directive section 45: a globally blocked state does not
        # keep evaluating strategies). A selection fills at the next open.
        if open_trade is None and entry_block_reason is None and not _in_news_window(bar.time, config.news_windows):
            if _risk_halt_reason(risk_state, equity, config.risk_limits) is not None:
                halted_scans += 1
                continue
            candidates = []
            regime_obj = raw_regime.__class__(confirmed_regime, raw_regime.confidence, raw_regime.version, raw_regime.reason)
            for strategy in active_strategies:
                signal = strategy.evaluate(features, regime_obj)
                if signal is not None:
                    candidates.append(signal)
            # Entry-suspended strategies never reach the (frozen V1) selector.
            kept, _ = partition_suspended(candidates, config.suspended_strategy_keys)
            candidates = [candidates[i] for i in kept]
            if candidates:
                bar_seconds = resolution_seconds(resolution)
                cost = _entry_cost(bar, symbol_spec, config, fill_time_utc=bar.time + bar_seconds,
                                   bar_seconds=bar_seconds)
                selection = select_proposal(
                    candidates, {canonical_symbol: cost},
                    min_net_edge_price=config.min_net_edge_price, min_raw_confidence=config.min_raw_confidence,
                    edge_evidence=edge_provider_of(config),
                )
                if candidate_log is not None:
                    candidate_log.extend(_candidate_records(selection, bar.time, cost))
                if selection.selected is not None:
                    net_edge = next(
                        (e.expected_net_edge for e in selection.candidates if e.signal is selection.selected), None,
                    )
                    pending_entry = _pending_from_signal(
                        selection.selected, numeric_feature_vector(features), bar.time,
                        canonical_symbol=canonical_symbol,
                        estimated_cost_price=cost.total_cost if cost is not None else None,
                        expected_net_edge_price=net_edge,
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
            fill = simulate_fill(last_bar, _opposite(open_trade.direction), point, fills, at="close")
            close(
                open_trade, last_bar.time, fill.price, BACKTEST_RANGE_ENDED,
                exit_spread_price=fill.spread_cost_price / 2.0, exit_slippage_price=fill.slippage_cost_price,
                fill_reference=FILL_RANGE_END_CLOSE, decision_time=last_bar.time,
            )
        else:
            open_position_state = open_trade.to_state()

    metrics = _compute_metrics(trades, config.initial_equity, equity, max_drawdown)
    dataset = build_dataset_snapshot(
        bars, canonical_symbol=canonical_symbol, resolution=resolution,
        strategies=tuple(s.key for s in active_strategies), feature_schema_version=FEATURE_SCHEMA_VERSION, now_utc=now,
    )

    final_confirmed, final_candidate, final_candidate_count = regime_tracker.state
    return BacktestResult(
        canonical_symbol=canonical_symbol, resolution=resolution, dataset_id=dataset.dataset_id,
        range_start_utc=bars[0].time, range_end_utc=bars[-1].time, trades=tuple(trades), metrics=metrics,
        equity_curve=tuple(equity_curve), origin=origin,
        news_limitation_note=None if config.news_windows else NEWS_LIMITATION_NOTE,
        config=config, open_position=open_position_state,
        final_regime_tracker_state=RegimeTrackerState(final_confirmed, final_candidate, final_candidate_count),
        pending_entry=None if force_close_at_range_end else pending_entry,
        entry_rejections=tuple(rejections), final_risk_state=risk_state, risk_halted_scans=halted_scans,
        config_fingerprint=fingerprint, fill_model_version=FILL_MODEL_VERSION, cost_provenance=fills.provenance,
    )


def _candidate_records(selection, bar_time: int, cost) -> list[CandidateRecord]:
    records = []
    for evaluation in selection.candidates:
        signal = evaluation.signal
        selected = signal is selection.selected
        reason = evaluation.rejection_reason if evaluation.rejected else (None if selected else LOST_TO_HIGHER_EDGE)
        records.append(CandidateRecord(
            bar_time_utc=bar_time, strategy_key=signal.strategy_key, direction=signal.direction,
            raw_confidence=signal.raw_confidence, stop_distance=signal.stop_distance,
            target_distance=signal.target_distance,
            estimated_cost_price=cost.total_cost if cost is not None else None,
            expected_net_edge_price=evaluation.expected_net_edge, selected=selected,
            rejected=evaluation.rejected, rejection_reason=reason, regime=signal.regime,
        ))
    return records


def _revalidate_and_open(
    pending: PendingEntryState, bar: Bar, *, equity: float, risk_state: RiskState, symbol_spec: SymbolSpec,
    config: BacktestConfig, canonical_symbol: str, max_fill_delay_seconds: int,
    external_open_positions: tuple[PositionExposure, ...],
    correlation_matrix: dict[tuple[str, str], CorrelationResult] | None, config_fingerprint: str,
    bar_seconds: int,
) -> _OpenTrade | tuple[str, str]:
    """Re-checks a pending entry against everything knowable at the fill
    bar's open (directive 0.7/0.8) and opens it, or returns
    `(reason_code, detail)`. Uses the same pure gates as DEMO final
    permission for every dimension that needs no broker call."""
    delay = bar.time - pending.signal_time_utc
    if delay > max_fill_delay_seconds:
        return REJECT_STALE_SIGNAL, f"next bar opened {delay}s after the decision (max {max_fill_delay_seconds}s)"
    if _in_news_window(bar.time, config.news_windows):
        return REJECT_NEWS, "fill bar is inside a supplied news-block window"

    signal = _signal_from_pending(pending, canonical_symbol)
    cost = _entry_cost(bar, symbol_spec, config, fill_time_utc=bar.time, bar_seconds=bar_seconds)
    # Research replay (LEGACY_V1_RAW_SCORE) is a simulation, so it is not an
    # executable gate; with edge_model "NONE" there is no evidence and the
    # entry is rejected (FLAT).
    cost_decision, edge_eval = evaluate_cost_gate(
        signal, cost, config.min_net_edge_price,
        evidence=edge_provider_of(config).for_signal(signal), executable=False,
    )
    if cost_decision != _COST_ALLOW:
        if cost_decision == _COST_BLOCK_EXPECTED_EDGE:
            return REJECT_EXPECTED_EDGE, edge_eval.reason
        if cost_decision == _COST_BLOCK_EDGE_UNVALIDATED:
            return REJECT_EDGE_UNVALIDATED, "no validated edge evidence (edge_model NONE)"
        return REJECT_COST, "cost unknown at fill"

    safe_volume = calculate_safe_volume(
        equity=equity, risk_per_trade_pct=config.risk_per_trade_pct,
        stop_distance_price=pending.stop_distance, symbol_spec=symbol_spec,
    )
    if not safe_volume.approved:
        return REJECT_SIZING, safe_volume.reason

    same_symbol = [p for p in external_open_positions if p.canonical_symbol == canonical_symbol]
    risk_decision, risk_reason = evaluate_risk_gate(RiskGateInput(
        proposed_symbol=canonical_symbol, proposed_monetary_risk=safe_volume.monetary_risk, equity=equity,
        current_total_open_risk=sum(p.monetary_risk for p in external_open_positions),
        current_total_pending_risk=0.0, current_positions_count=len(external_open_positions),
        current_positions_for_symbol=len(same_symbol), daily_realized_pnl=risk_state.day_realized_pnl,
        peak_equity=risk_state.peak_equity,
    ), config.risk_limits)
    if risk_decision != _RISK_ALLOW:
        return REJECT_RISK, risk_reason

    portfolio_decision, portfolio_reason = evaluate_portfolio_risk_gate(
        proposed_symbol=canonical_symbol, proposed_direction=pending.direction,
        proposed_monetary_risk=safe_volume.monetary_risk, equity=equity,
        open_positions=list(external_open_positions), pending_positions=[],
        correlation_matrix=correlation_matrix or {},
        limits=portfolio_risk_limits_from_risk_limits(config.risk_limits.max_total_open_risk_pct),
    )
    if portfolio_decision != _PORTFOLIO_ALLOW:
        return REJECT_PORTFOLIO_RISK, portfolio_reason

    if external_open_positions:
        corr_decision, corr_reason = evaluate_correlation_gate(
            canonical_symbol, [p.canonical_symbol for p in external_open_positions], correlation_matrix or {},
        )
        if corr_decision != _CORRELATION_ALLOW:
            return REJECT_CORRELATION, corr_reason

    fill = simulate_fill(bar, pending.direction, symbol_spec.point, config.fill_assumptions)
    sign = 1.0 if pending.direction == "BUY" else -1.0

    def money(price_distance: float) -> float:
        return money_from_price_distance(
            price_distance, safe_volume.volume,
            tick_size=symbol_spec.trade_tick_size, tick_value=symbol_spec.trade_tick_value,
        )

    entry_spread_cost = money(fill.spread_cost_price / 2.0)
    entry_slippage_cost = money(fill.slippage_cost_price)
    evidence = {
        "strategy": {
            "key": pending.strategy_key, "version": pending.strategy_version, "raw_confidence": pending.raw_confidence,
            "entry_method": pending.entry_method, "regime": pending.regime, "fingerprint": pending.fingerprint,
        },
        "decision": {
            "signal_time_utc": pending.signal_time_utc, "estimated_cost_price": pending.estimated_cost_price,
            "expected_net_edge_price": pending.expected_net_edge_price,
        },
        "fill_revalidation": {
            "fill_time_utc": bar.time, "cost_price": cost.total_cost if cost is not None else None,
            "expected_net_edge_price": edge_eval.expected_net_edge if edge_eval is not None else None,
            "risk_gate": risk_reason, "portfolio_gate": portfolio_reason,
            "external_open_positions": len(external_open_positions),
        },
        "ml_observer": NOT_CONSULTED, "rag": NOT_CONSULTED, "okf": NOT_CONSULTED,
        "fill_model_version": FILL_MODEL_VERSION, "config_fingerprint": config_fingerprint,
    }
    return _OpenTrade(
        strategy_key=pending.strategy_key, direction=pending.direction, entry_time_utc=bar.time,
        entry_price=fill.price, volume=safe_volume.volume, initial_monetary_risk=safe_volume.monetary_risk,
        entry_regime=pending.regime, stop_price=fill.price - sign * pending.stop_distance,
        target_price=fill.price + sign * pending.target_distance,
        initial_stop_distance_price=pending.stop_distance,
        total_cost=entry_spread_cost + entry_slippage_cost,
        entry_features=pending.entry_features, entry_raw_confidence=pending.raw_confidence,
        peak_r=0.0, strategy_version=pending.strategy_version, signal_time_utc=pending.signal_time_utc,
        entry_spread_cost=entry_spread_cost, entry_slippage_cost=entry_slippage_cost, entry_evidence=evidence,
    )


def _review_open_trade(
    open_trade: _OpenTrade, bar: Bar, features, confirmed_regime: str, active_strategies,
    symbol_spec: SymbolSpec, config: BacktestConfig, *, bar_seconds: int,
) -> None:
    """Bar-close review: updates peak_r/stop in place, or sets a
    `pending_exit_reason` that fills at the next bar's open. The review
    happens at the bar's CLOSE (`bar.time + bar_seconds`), so that is the
    holding time's end (BUG_BACKLOG #13: measuring to the bar's open made
    max-holding exits one bar late)."""
    mark = _mark_price(open_trade, bar, symbol_spec.point)
    unrealized_pnl = money_from_price_distance(
        (mark - open_trade.entry_price) if open_trade.direction == "BUY" else (open_trade.entry_price - mark),
        open_trade.volume, tick_size=symbol_spec.trade_tick_size, tick_value=symbol_spec.trade_tick_value,
    )
    current_r = compute_current_r(open_trade.initial_monetary_risk, unrealized_pnl)
    if current_r is None:
        return  # quarantined R -- HOLD, matching live position_management.manager's behavior
    open_trade.last_current_r = current_r
    if current_r > open_trade.peak_r:
        open_trade.peak_r = current_r
        open_trade.peak_r_time_utc = bar.time

    holding_seconds = bar.time + bar_seconds - open_trade.entry_time_utc
    setup_signal = _reevaluate_setup(active_strategies, open_trade, features, confirmed_regime)
    horizon = _max_hold_horizon(config, bar_seconds)
    cost = _remaining_exit_cost(
        symbol_spec, config, review_time_utc=bar.time + bar_seconds,
        remaining_hold_seconds=None if horizon is None else max(0, horizon - holding_seconds),
    )
    current_net_edge = (
        (open_trade.target_price - mark if open_trade.direction == "BUY" else mark - open_trade.target_price)
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
        open_trade.pending_exit_decision_time_utc = bar.time
    elif decision.action == MOVE_PROTECTIVE_STOP:
        proposed = (
            open_trade.entry_price + decision.new_stop_r * open_trade.initial_stop_distance_price
            if open_trade.direction == "BUY"
            else open_trade.entry_price - decision.new_stop_r * open_trade.initial_stop_distance_price
        )
        open_trade.stop_price = resolve_new_stop_price(open_trade.stop_price, proposed, open_trade.direction)


def _opposite(direction: str) -> str:
    return "SELL" if direction == "BUY" else "BUY"


def _max_hold_horizon(config: BacktestConfig, bar_seconds: int) -> int | None:
    """Longest possible hold: a max-holding decision is taken at a bar close
    and fills at the next bar's open, so up to one bar past the limit."""
    params = config.adaptive_exit_params
    return params.max_holding_seconds + bar_seconds if params.max_holding_enabled else None


def _swap_price(config: BacktestConfig, symbol_spec: SymbolSpec, start_utc: int, max_hold: int | None) -> float | None:
    return swap_price_for_horizon(
        server_time_rule=config.server_time_rule, start_utc=start_utc, max_hold_seconds=max_hold,
        swap_per_lot_per_day=config.fill_assumptions.swap_monetary_per_lot_per_day,
        tick_size=symbol_spec.trade_tick_size, tick_value=symbol_spec.trade_tick_value,
    )


def _entry_cost(bar: Bar, symbol_spec: SymbolSpec, config: BacktestConfig, *, fill_time_utc: int, bar_seconds: int):
    """Whole-trade cost for an entry decision (HORIZON_FULL_ROUND_TRIP): the
    same convention `runtime.demo.live_cost_estimate` uses -- per-fill
    slippage on both fills, the spread once, the round-trip commission, swap
    only if the maximum hold from `fill_time_utc` can cross a rollover."""
    return round_trip_cost_from_evidence(
        spread_price=_spread_price(bar, symbol_spec.point),
        per_fill_slippage_price=config.fill_assumptions.slippage_price,
        round_trip_commission_price=round_trip_commission_price(
            config.fill_assumptions, tick_size=symbol_spec.trade_tick_size, tick_value=symbol_spec.trade_tick_value,
        ),
        swap_price_equivalent=_swap_price(config, symbol_spec, fill_time_utc, _max_hold_horizon(config, bar_seconds)),
        uncertainty_margin_pct=config.uncertainty_margin_pct,
    )


def _remaining_exit_cost(
    symbol_spec: SymbolSpec, config: BacktestConfig, *, review_time_utc: int, remaining_hold_seconds: int | None,
):
    """Friction still payable on an open simulated trade (HORIZON_REMAINING_EXIT),
    measured from the executable mark (`_mark_price`), so no exit spread is
    added; entry costs are sunk. Same convention as the DEMO review."""
    return remaining_exit_cost_from_evidence(
        exit_spread_price=0.0, per_fill_slippage_price=config.fill_assumptions.slippage_price,
        round_trip_commission_price=round_trip_commission_price(
            config.fill_assumptions, tick_size=symbol_spec.trade_tick_size, tick_value=symbol_spec.trade_tick_value,
        ),
        swap_price_equivalent=_swap_price(config, symbol_spec, review_time_utc, remaining_hold_seconds),
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
    trade: _OpenTrade, exit_time_utc: int, exit_price: float, exit_reason: str, exit_regime: str,
    symbol_spec: SymbolSpec, *, exit_spread_price: float, exit_slippage_price: float, fills: FillAssumptions,
    fill_reference: str, decision_time: int, origin: EvidenceOrigin, config_fingerprint: str,
    server_time_rule: str = "UTC",
) -> SimulatedTrade:
    """`entry_price`/`exit_price` are execution prices (spread and
    slippage already inside them), so they are NOT deducted again;
    commission and swap are. Swap is charged per broker-server rollover
    crossed (`server_time_rule` midnight; "UTC" = UTC midnight)."""
    def money(price_distance: float) -> float:
        return money_from_price_distance(
            price_distance, trade.volume, tick_size=symbol_spec.trade_tick_size, tick_value=symbol_spec.trade_tick_value,
        )

    price_distance = (exit_price - trade.entry_price) if trade.direction == "BUY" else (trade.entry_price - exit_price)
    execution_pnl = money(price_distance)
    commission = fills.commission_monetary_per_lot * trade.volume
    rollovers = rollovers_crossed(server_time_rule, trade.entry_time_utc, exit_time_utc)
    if rollovers and fills.swap_monetary_per_lot_per_day is None:
        # The entry gate blocks any trade whose maximum hold could cross a
        # rollover while swap is unknown, so reaching here is a broken
        # invariant -- never price it as zero.
        raise RuntimeError(f"trade crossed {rollovers} rollover(s) with UNKNOWN swap; refusing to price it as zero")
    swap = (fills.swap_monetary_per_lot_per_day or 0.0) * trade.volume * rollovers
    fee = 0.0
    net_pnl = execution_pnl - commission - swap - fee
    exit_spread_cost = money(exit_spread_price)
    exit_slippage_cost = money(exit_slippage_price)
    total_cost = (
        trade.entry_spread_cost + trade.entry_slippage_cost + exit_spread_cost + exit_slippage_cost
        + commission + swap + fee
    )
    realized_r = net_pnl / trade.initial_monetary_risk if trade.initial_monetary_risk > 0 else None
    return SimulatedTrade(
        strategy_key=trade.strategy_key, direction=trade.direction, entry_time_utc=trade.entry_time_utc,
        entry_price=trade.entry_price, volume=trade.volume, initial_monetary_risk=trade.initial_monetary_risk,
        entry_regime=trade.entry_regime, exit_time_utc=exit_time_utc, exit_price=exit_price, exit_reason=exit_reason,
        exit_regime=exit_regime, realized_r=realized_r, realized_pnl=net_pnl, total_cost=total_cost,
        entry_features=trade.entry_features, entry_raw_confidence=trade.entry_raw_confidence,
        strategy_version=trade.strategy_version, signal_time_utc=trade.signal_time_utc,
        entry_fill_reference=FILL_NEXT_BAR_OPEN, exit_fill_reference=fill_reference,
        exit_decision_time_utc=decision_time,
        entry_spread_cost=trade.entry_spread_cost, entry_slippage_cost=trade.entry_slippage_cost,
        exit_spread_cost=exit_spread_cost, exit_slippage_cost=exit_slippage_cost,
        commission_cost=commission, swap_cost=swap, fee_cost=fee, gross_pnl=net_pnl + total_cost,
        peak_r=trade.peak_r, origin=origin, fill_model_version=FILL_MODEL_VERSION,
        cost_provenance=fills.provenance, config_fingerprint=config_fingerprint, entry_evidence=trade.entry_evidence,
    )


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
