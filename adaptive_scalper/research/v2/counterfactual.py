"""H4 -- same-entry counterfactual exits (research only, BACKTEST evidence).

Question: do the frozen V1 ENTRIES carry gross edge that the V1 EXIT
destroys, or is there no gross edge to destroy? Answering it without
survivor bias means replaying the IDENTICAL entry cohort (time, side,
strategy, fill price, size, initial risk, stop, target, entry costs) under
several exit rules -- never "trades that happened to last 600 s did well".

Each entry is replayed independently from its fill bar to the end of its
fold over the same bars, per-bar features and confirmed regime the engine
used (`build_fold_context` replicates `run_backtest` step 2 exactly), with
the engine's OWN primitives: `_intrabar_stop_or_target_hit` (stop assumed
first when both are in range), `simulate_fill` at the next bar's open for
bar-close decisions, range-end close, and `_close_trade` cost accounting.
The V1 policy calls `engine._review_open_trade` itself, so replaying V1
must reproduce the engine's exits (the fidelity check). Protective stop
and target stand in every policy; no policy widens a stop or adds risk.

Replayed trades of one cohort may overlap in time (a longer hold would
have skipped later entries in a flat-only engine), so a result here is a
DIAGNOSTIC; a promising exit needs a causal full-run confirmation.

Never touches a broker, a database or the reserved OOS (refused).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from adaptive_scalper.backtest.engine import (
    BACKTEST_RANGE_ENDED,
    _close_trade,
    _estimate_cost,
    _evaluate_original_strategy,
    _intrabar_stop_or_target_hit,
    _mark_price,
    _opposite,
    _OpenTrade,
    _reevaluate_setup,
    _review_open_trade,
)
from adaptive_scalper.backtest.reserved_oos import assert_outside_reserved_oos
from adaptive_scalper.backtest.types import FILL_NEXT_BAR_OPEN, FILL_RANGE_END_CLOSE
from adaptive_scalper.features.bar_features import compute_bar_features
from adaptive_scalper.position_management.adaptive_exit import (
    FULL_CLOSE,
    MOVE_PROTECTIVE_STOP,
    compute_current_r,
    evaluate_adaptive_exit,
    resolve_new_stop_price,
)
from adaptive_scalper.position_management.expectancy import ExpectancyEvidence, evaluate_position_expectancy
from adaptive_scalper.regimes.classifier import RegimeTracker, classify_regime
from adaptive_scalper.research.v2.holding import directional_invalidation
from adaptive_scalper.research.v2.marginal_cost import marginal_future_cost
from adaptive_scalper.simulation.fill_model import _spread_price, money_from_price_distance, simulate_fill
from adaptive_scalper.simulation.types import EvidenceOrigin

FIXED_HOLD_REASON = "fixed holding time reached"


# --------------------------------------------------------------------------
# Fold context: bars + causal features/regime, computed once per fold
# --------------------------------------------------------------------------

@dataclass
class FoldContext:
    index: int
    bars: list
    start_index: int
    regimes: list            # confirmed regime per bar (None before start_index)
    features: dict = field(default_factory=dict)   # bar index -> features (only where a review needs them)
    atr: list = field(default_factory=list)        # causal `atr` feature per bar (None before start_index)

    def features_at(self, i: int):
        if i not in self.features:
            raise KeyError(f"fold {self.index}: features for bar {i} were not precomputed")
        return self.features[i]


def build_fold_context(fold_bars, canonical_symbol: str, resolution: str, symbol_spec, config, *,
                       index: int, keep_features_for: set[int] | None = None) -> FoldContext:
    """Exactly `run_backtest`'s causal step 2 on a bounded fold: features
    from `bars[: i + 1]` only, a fresh `RegimeTracker` per fold."""
    assert_outside_reserved_oos(fold_bars[0].time, fold_bars[-1].time)
    start = config.feature_lookback + 1
    tracker = RegimeTracker(min_confirmations=config.regime_min_confirmations)
    regimes: list = [None] * len(fold_bars)
    atr: list = [None] * len(fold_bars)
    kept: dict = {}
    for i in range(start, len(fold_bars)):
        feats = compute_bar_features(canonical_symbol, resolution, fold_bars[: i + 1],
                                     lookback=config.feature_lookback, point_size=symbol_spec.point,
                                     now=fold_bars[i].time)
        regimes[i] = tracker.update(classify_regime(feats))
        atr[i] = feats.atr
        if keep_features_for is None or i in keep_features_for:
            kept[i] = feats
    return FoldContext(index=index, bars=list(fold_bars), start_index=start, regimes=regimes, features=kept, atr=atr)


# --------------------------------------------------------------------------
# Entry cohort
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class CohortEntry:
    cohort: str
    fold: int
    entry_index: int
    stop_distance: float
    target_distance: float
    original: object         # the engine's SimulatedTrade (V1 exit) for this entry

    @property
    def key(self) -> tuple:
        t = self.original
        return (self.fold, t.entry_time_utc, t.strategy_key, t.direction)


def cohort_from_run(cohort: str, fold: int, fold_bars, result, candidate_log) -> list[CohortEntry]:
    """Join each V1 trade to the candidate the selector chose at its signal
    bar (stop/target distances are not on SimulatedTrade). A trade with no
    unique match is an error, never guessed."""
    times = {b.time: i for i, b in enumerate(fold_bars)}
    selected: dict = {}
    for c in candidate_log:
        if c.selected:
            k = (c.bar_time_utc, c.strategy_key, c.direction)
            if k in selected:
                raise ValueError(f"two selected candidates for {k}")
            selected[k] = c
    out = []
    for t in result.trades:
        c = selected.get((t.signal_time_utc, t.strategy_key, t.direction))
        if c is None:
            raise ValueError(f"no selected candidate for trade at {t.entry_time_utc} ({t.strategy_key})")
        if t.entry_time_utc not in times:
            raise ValueError(f"entry time {t.entry_time_utc} is not a bar of fold {fold}")
        out.append(CohortEntry(cohort, fold, times[t.entry_time_utc], c.stop_distance, c.target_distance, t))
    return out


# --------------------------------------------------------------------------
# Exit policies
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class ExitPolicy:
    """`kind`: STOP_TARGET | FIXED_HOLD | REVIEW. For REVIEW, `engine=True`
    calls `engine._review_open_trade` itself (V1 and holding-thesis
    variants); otherwise `_research_review` with the requested cost model."""
    name: str
    kind: str
    hold_bars: int = 0
    thesis: object = None
    cost_model: str = "full"          # full | marginal
    engine: bool = False


V1_POLICY = ExitPolicy("V1", "REVIEW", engine=True)
H4_POLICIES = (
    ExitPolicy("ST", "STOP_TARGET"),
    ExitPolicy("FH3", "FIXED_HOLD", hold_bars=3),
    ExitPolicy("FH12", "FIXED_HOLD", hold_bars=12),
    ExitPolicy("FH36", "FIXED_HOLD", hold_bars=36),
    ExitPolicy("THESIS", "REVIEW", thesis=directional_invalidation, engine=True),
    ExitPolicy("MEV", "REVIEW", thesis=directional_invalidation, cost_model="marginal"),
)
H5_POLICIES = (ExitPolicy("V1-MC", "REVIEW", cost_model="marginal"),)


def _research_review(trade, bar, features, confirmed_regime, strategies, spec, config, *, bar_seconds: int,
                     thesis, cost_model: str) -> None:
    """`engine._review_open_trade` with the remaining-edge cost made
    selectable. With cost_model='full' and thesis=None it is the engine's
    logic line for line (proved by tests against the engine itself)."""
    mark = _mark_price(trade, bar, spec.point)
    unrealized = money_from_price_distance(
        (mark - trade.entry_price) if trade.direction == "BUY" else (trade.entry_price - mark),
        trade.volume, tick_size=spec.trade_tick_size, tick_value=spec.trade_tick_value)
    current_r = compute_current_r(trade.initial_monetary_risk, unrealized)
    if current_r is None:
        return
    trade.last_current_r = current_r
    if current_r > trade.peak_r:
        trade.peak_r = current_r
        trade.peak_r_time_utc = bar.time
    holding_seconds = bar.time + bar_seconds - trade.entry_time_utc
    if thesis is None:
        still_valid = _reevaluate_setup(strategies, trade, features, confirmed_regime) is not None
    else:
        still_valid = bool(thesis(direction=trade.direction, entry_regime=trade.entry_regime,
                                  confirmed_regime=confirmed_regime,
                                  fresh_signal=_evaluate_original_strategy(strategies, trade, features,
                                                                           confirmed_regime)))
    if cost_model == "full":
        cost = _estimate_cost(bar, spec, config)
        cost_total = cost.total_cost if cost is not None else None
    elif cost_model == "marginal":
        cost_total = marginal_future_cost(bar, spec, config).total_cost
    else:
        raise ValueError(f"unknown cost_model {cost_model!r}")
    remaining = (trade.target_price - bar.close) if trade.direction == "BUY" else (bar.close - trade.target_price)
    net_edge = remaining - cost_total if cost_total is not None else None
    expectancy = evaluate_position_expectancy(ExpectancyEvidence(
        entry_regime=trade.entry_regime, current_regime=confirmed_regime, strategy_setup_still_valid=still_valid,
        current_net_edge_price=net_edge, min_required_edge_price=config.min_net_edge_price,
        holding_seconds=holding_seconds, current_r=current_r, peak_r=trade.peak_r))
    decision = evaluate_adaptive_exit(current_r=current_r, peak_r=trade.peak_r, holding_seconds=holding_seconds,
                                      thesis_valid=expectancy.thesis_valid,
                                      regime_reversed=expectancy.regime_reversed,
                                      params=config.adaptive_exit_params)
    if decision.action == FULL_CLOSE:
        trade.pending_exit_reason = decision.reason
        trade.pending_exit_decision_time_utc = bar.time
    elif decision.action == MOVE_PROTECTIVE_STOP:
        sign = 1.0 if trade.direction == "BUY" else -1.0
        proposed = trade.entry_price + sign * decision.new_stop_r * trade.initial_stop_distance_price
        trade.stop_price = resolve_new_stop_price(trade.stop_price, proposed, trade.direction)


# --------------------------------------------------------------------------
# Replay
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class ReplayedTrade:
    cohort: str
    variant: str
    fold: int
    entry_key: tuple
    trade: object            # SimulatedTrade
    mae_r: float             # max adverse excursion, executable side, in initial-stop units
    mfe_r: float             # max favorable excursion, executable side
    exec_r: float            # realized execution-price move in initial-stop units (before commission/swap)
    bars_held: int
    stop_distance: float


def open_trade_for(entry: CohortEntry) -> _OpenTrade:
    t = entry.original
    sign = 1.0 if t.direction == "BUY" else -1.0
    return _OpenTrade(
        strategy_key=t.strategy_key, direction=t.direction, entry_time_utc=t.entry_time_utc,
        entry_price=t.entry_price, volume=t.volume, initial_monetary_risk=t.initial_monetary_risk,
        entry_regime=t.entry_regime, stop_price=t.entry_price - sign * entry.stop_distance,
        target_price=t.entry_price + sign * entry.target_distance, initial_stop_distance_price=entry.stop_distance,
        total_cost=t.entry_spread_cost + t.entry_slippage_cost, entry_features=t.entry_features,
        entry_raw_confidence=t.entry_raw_confidence, peak_r=0.0, strategy_version=t.strategy_version,
        signal_time_utc=t.signal_time_utc, entry_spread_cost=t.entry_spread_cost,
        entry_slippage_cost=t.entry_slippage_cost, entry_evidence=t.entry_evidence,
    )


def replay_entry(entry: CohortEntry, ctx: FoldContext, policy: ExitPolicy, *, symbol_spec, config, strategies,
                 bar_seconds: int, fingerprint: str) -> ReplayedTrade:
    trade = open_trade_for(entry)
    fills = config.fill_assumptions
    point = symbol_spec.point
    bars = ctx.bars
    sign = 1.0 if trade.direction == "BUY" else -1.0
    stop_distance = entry.stop_distance
    mae = mfe = 0.0

    def excursion(price: float) -> None:
        nonlocal mae, mfe
        move = sign * (price - trade.entry_price) / stop_distance
        mfe, mae = max(mfe, move), max(mae, -move)

    def finish(i, exit_price, reason, *, spread, slippage, reference, decision_time, regime):
        closed = _close_trade(trade, bars[i].time, exit_price, reason, regime, symbol_spec,
                              exit_spread_price=spread, exit_slippage_price=slippage, fills=fills,
                              fill_reference=reference, decision_time=decision_time,
                              origin=EvidenceOrigin.BACKTEST, config_fingerprint=fingerprint)
        excursion(exit_price)   # the exit bar counts only up to the exit price (order within a bar is unknown)
        exec_r = sign * (exit_price - trade.entry_price) / stop_distance
        return ReplayedTrade(entry.cohort, policy.name, entry.fold, entry.key, closed, mae, mfe, exec_r,
                             i - entry.entry_index + 1, stop_distance)

    for i in range(entry.entry_index, len(bars)):
        bar = bars[i]
        if trade.pending_exit_reason is not None:
            fill = simulate_fill(bar, _opposite(trade.direction), point, fills)
            return finish(i, fill.price, trade.pending_exit_reason, spread=fill.spread_cost_price / 2.0,
                          slippage=fill.slippage_cost_price, reference=FILL_NEXT_BAR_OPEN,
                          decision_time=trade.pending_exit_decision_time_utc or bar.time,
                          regime=ctx.regimes[i - 1])
        hit = _intrabar_stop_or_target_hit(trade, bar, point, fills.slippage_price)
        if hit is not None:
            reason, price, spread, slippage, reference = hit
            return finish(i, price, reason, spread=spread, slippage=slippage, reference=reference,
                          decision_time=bar.time, regime=ctx.regimes[i])
        half = _spread_price(bar, point) / 2.0
        if trade.direction == "BUY":
            excursion(bar.high - half)
            excursion(bar.low - half)
        else:
            excursion(bar.low + half)
            excursion(bar.high + half)
        if policy.kind == "FIXED_HOLD":
            if i - entry.entry_index + 1 >= policy.hold_bars:
                trade.pending_exit_reason = f"{FIXED_HOLD_REASON} ({policy.hold_bars} bars)"
                trade.pending_exit_decision_time_utc = bar.time
        elif policy.kind == "REVIEW":
            features = ctx.features_at(i)
            if policy.engine:
                _review_open_trade(trade, bar, features, ctx.regimes[i], strategies, symbol_spec, config,
                                   bar_seconds=bar_seconds, holding_thesis=policy.thesis)
            else:
                _research_review(trade, bar, features, ctx.regimes[i], strategies, symbol_spec, config,
                                 bar_seconds=bar_seconds, thesis=policy.thesis, cost_model=policy.cost_model)
        elif policy.kind != "STOP_TARGET":
            raise ValueError(f"unknown policy kind {policy.kind!r}")

    last = len(bars) - 1
    fill = simulate_fill(bars[last], _opposite(trade.direction), point, fills, at="close")
    return finish(last, fill.price, BACKTEST_RANGE_ENDED, spread=fill.spread_cost_price / 2.0,
                  slippage=fill.slippage_cost_price, reference=FILL_RANGE_END_CLOSE,
                  decision_time=bars[last].time, regime=ctx.regimes[last])


def review_feature_indices(entries: list[CohortEntry], config, bar_seconds: int) -> set[int]:
    """Bars a REVIEW policy can reach: every review policy keeps V1's max
    holding time, so a position is closed by the bar after it expires.
    A policy reaching further fails loudly in `FoldContext.features_at`."""
    params = config.adaptive_exit_params
    horizon = (math.ceil(params.max_holding_seconds / bar_seconds) + 1) if params.max_holding_enabled else 10_000
    return {e.entry_index + k for e in entries for k in range(horizon + 1)}


def same_exit(a, b, *, tol: float = 1e-6) -> bool:
    return (a.exit_time_utc == b.exit_time_utc and a.exit_reason == b.exit_reason
            and abs((a.realized_pnl or 0.0) - (b.realized_pnl or 0.0)) <= tol)
