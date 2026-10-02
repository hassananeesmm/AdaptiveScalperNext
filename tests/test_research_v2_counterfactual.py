"""H4/H5 research: the reserved OOS is refused at every research layer
(including `run_backtest` itself), the same-entry counterfactual engine
reproduces the engine's own V1 exits exactly, holds entries identical
across exit variants, never widens a stop, and the marginal future cost
excludes the already-paid entry side."""

from __future__ import annotations

import pytest

from adaptive_scalper.backtest.engine import _estimate_cost, run_backtest
from adaptive_scalper.backtest.reserved_oos import (
    RESERVED_OOS_INTERVALS,
    ReservedOosOverlapError,
    assert_outside_reserved_oos,
    overlaps_reserved_oos,
)
from adaptive_scalper.backtest.types import BacktestConfig
from adaptive_scalper.research import independent
from adaptive_scalper.research.v2.counterfactual import (
    H4_POLICIES,
    H5_POLICIES,
    V1_POLICY,
    ExitPolicy,
    build_fold_context,
    cohort_from_run,
    replay_entry,
    review_feature_indices,
    same_exit,
)
from adaptive_scalper.research.v2.holding import no_thesis_exit
from adaptive_scalper.research.v2.marginal_cost import marginal_future_cost
from adaptive_scalper.research.v2.selectors import friction_filter_selector
from adaptive_scalper.strategies import build_active_registry, select_active_strategies
from sim_helpers import RES, START, SYMBOL, random_walk_bars, spec

BARS = random_walk_bars(1500, seed=7)
OOS_START, OOS_END = RESERVED_OOS_INTERVALS[0]
STRATEGIES = select_active_strategies(None, build_active_registry())
CONFIG = BacktestConfig()


# ---------------------------------------------------------------- OOS defense

def test_reserved_oos_is_one_definition_shared_by_research():
    assert independent.RESERVED_OOS_INTERVALS is RESERVED_OOS_INTERVALS
    assert independent.assert_outside_reserved_oos is assert_outside_reserved_oos
    assert independent.ReservedOosOverlapError is ReservedOosOverlapError


@pytest.mark.parametrize("start, end, overlaps", [
    (OOS_START - 10_000, OOS_START - 1, False),
    (OOS_START - 10_000, OOS_START, True),
    (OOS_START + 3_600, OOS_START + 7_200, True),
    (OOS_END - 1, OOS_END + 10_000, True),
    (OOS_END, OOS_END + 10_000, False),
])
def test_overlap_boundaries(start, end, overlaps):
    assert overlaps_reserved_oos(start, end) is overlaps


def test_calling_run_backtest_directly_with_research_hooks_over_oos_fails_closed():
    oos_bars = random_walk_bars(400, seed=3, start=OOS_START + 86_400)
    with pytest.raises(ReservedOosOverlapError):
        run_backtest(oos_bars, SYMBOL, RES, spec(), now_utc=OOS_START, research_holding_thesis=no_thesis_exit,
                     research_variant="bypass-attempt")
    selector = friction_filter_selector(3.0, cooldown_bars=6, bar_seconds=300)
    with pytest.raises(ReservedOosOverlapError):
        run_backtest(oos_bars, SYMBOL, RES, spec(), now_utc=OOS_START, research_selector=selector,
                     research_variant="bypass-attempt")


def test_a_range_that_only_starts_before_oos_is_still_refused():
    straddling = random_walk_bars(400, seed=3, start=OOS_START - 200 * 300)
    with pytest.raises(ReservedOosOverlapError):
        run_backtest(straddling, SYMBOL, RES, spec(), now_utc=OOS_START, research_holding_thesis=no_thesis_exit,
                     research_variant="bypass-attempt")


def test_the_counterfactual_engine_refuses_oos_bars():
    oos_bars = random_walk_bars(200, seed=3, start=OOS_START + 86_400)
    with pytest.raises(ReservedOosOverlapError):
        build_fold_context(oos_bars, SYMBOL, RES, spec(), CONFIG, index=0)


def test_frozen_v1_path_is_not_changed_by_the_guard():
    # A plain V1 run (no research hook) keeps working on any range: PAPER and
    # the one-shot `oos` command take this path, never the research hooks.
    oos_bars = random_walk_bars(300, seed=3, start=OOS_START + 86_400)
    run_backtest(oos_bars, SYMBOL, RES, spec(), now_utc=OOS_START)


# ------------------------------------------------------- counterfactual engine

def _cohort(strategy_keys=None):
    log = []
    result = run_backtest(BARS, SYMBOL, RES, spec(), now_utc=START, strategy_keys=strategy_keys, candidate_log=log)
    entries = cohort_from_run("test", 0, BARS, result, log)
    keep = review_feature_indices(entries, CONFIG, 300)
    ctx = build_fold_context(BARS, SYMBOL, RES, spec(), CONFIG, index=0, keep_features_for=keep)
    return result, entries, ctx


def _replay(entries, ctx, policy):
    return [replay_entry(e, ctx, policy, symbol_spec=spec(), config=CONFIG, strategies=STRATEGIES,
                         bar_seconds=300, fingerprint="test") for e in entries]


@pytest.fixture(scope="module")
def selector_cohort():
    return _cohort()


def test_cohort_joins_every_trade_to_its_selected_candidate(selector_cohort):
    result, entries, _ = selector_cohort
    assert len(entries) == len(result.trades) >= 10
    for e in entries:
        assert e.stop_distance > 0 and e.target_distance > 0
        assert BARS[e.entry_index].time == e.original.entry_time_utc


def test_replaying_v1_reproduces_the_engines_own_exits(selector_cohort):
    _, entries, ctx = selector_cohort
    replayed = _replay(entries, ctx, V1_POLICY)
    assert all(same_exit(r.trade, e.original) for r, e in zip(replayed, entries))


def test_research_review_with_full_cost_is_the_engine_review(selector_cohort):
    _, entries, ctx = selector_cohort
    replica = ExitPolicy("V1-replica", "REVIEW", cost_model="full")
    for a, b in zip(_replay(entries, ctx, replica), _replay(entries, ctx, V1_POLICY)):
        assert same_exit(a.trade, b.trade)


def test_single_strategy_cohort_fidelity():
    _, entries, ctx = _cohort(("momentum_continuation",))
    replayed = _replay(entries, ctx, V1_POLICY)
    assert entries and all(same_exit(r.trade, e.original) for r, e in zip(replayed, entries))


@pytest.mark.parametrize("policy", H4_POLICIES + H5_POLICIES, ids=lambda p: p.name)
def test_entries_are_identical_across_exit_variants_and_stops_never_widen(selector_cohort, policy):
    _, entries, ctx = selector_cohort
    for r, e in zip(_replay(entries, ctx, policy), entries):
        t, o = r.trade, e.original
        assert (t.entry_time_utc, t.entry_price, t.volume, t.initial_monetary_risk, t.direction, t.strategy_key) == \
            (o.entry_time_utc, o.entry_price, o.volume, o.initial_monetary_risk, o.direction, o.strategy_key)
        assert t.entry_spread_cost == o.entry_spread_cost and t.entry_slippage_cost == o.entry_slippage_cost
        assert t.exit_time_utc >= t.entry_time_utc
        # a loss can never exceed the initial stop by more than the gap/slippage allowance
        assert r.exec_r >= -r.mae_r - 1e-9
        assert r.mfe_r >= 0 and r.mae_r >= 0


def test_fixed_hold_exits_no_later_than_its_horizon(selector_cohort):
    _, entries, ctx = selector_cohort
    fh3 = next(p for p in H4_POLICIES if p.name == "FH3")
    for r in _replay(entries, ctx, fh3):
        assert r.bars_held <= 4   # decided at the 3rd bar's close, filled at the 4th bar's open


def test_stop_target_only_holds_at_least_as_long_as_v1(selector_cohort):
    _, entries, ctx = selector_cohort
    st = next(p for p in H4_POLICIES if p.name == "ST")
    for a, b in zip(_replay(entries, ctx, st), _replay(entries, ctx, V1_POLICY)):
        assert a.trade.exit_time_utc >= b.trade.exit_time_utc


def test_review_features_outside_the_precomputed_window_fail_loudly(selector_cohort):
    _, entries, ctx = selector_cohort
    with pytest.raises(KeyError):
        ctx.features_at(10 ** 9)


# --------------------------------------------------------------------- metrics

def test_trial_metrics_cover_the_required_list(selector_cohort):
    from adaptive_scalper.research.v2.metrics import development_verdict, exit_category, trial_metrics

    _, entries, ctx = selector_cohort
    v1 = _replay(entries, ctx, V1_POLICY)
    base = {r.entry_key: (r.trade.realized_pnl + r.trade.total_cost) / r.trade.initial_monetary_risk for r in v1}
    st = _replay(entries, ctx, next(p for p in H4_POLICIES if p.name == "ST"))
    m = trial_metrics(st, symbol=SYMBOL, symbol_spec=spec(), n_folds=1, spread_percentile_prices={90: 0.5},
                      baseline_gross_r=base)
    for key in ("trades", "gross_pnl", "costs", "net_pnl", "gross_r", "cost_r", "net_r", "win_rate", "loss_rate",
                "profit_factor", "avg_win_r", "avg_loss_r", "expectancy_r", "max_drawdown_r", "max_consecutive_wins",
                "max_consecutive_losses", "avg_holding_seconds", "median_holding_seconds", "mae_r", "mfe_r",
                "peak_r", "realized_exec_r", "giveback_r", "exit_shares", "profit_capture_ratio", "fold_net_r",
                "positive_folds", "statistics", "stress_net_r", "paired_vs_v1_gross_r", "segments"):
        assert key in m, key
    assert m["trades"] == len(entries)
    assert m["net_pnl"] == pytest.approx(m["gross_pnl"] - m["costs"])
    assert sum(m["exit_shares"].values()) == pytest.approx(1.0)
    # higher cost can only lower net R
    s = m["stress_net_r"]
    assert s["cost_x1.5"] <= s["cost_x1.2"] <= s["cost_x1.1"] <= m["net_r"] + 1e-12
    assert s["slippage_x2"] <= m["net_r"] + 1e-12
    assert exit_category("TAKE_PROFIT_HIT") == "target"
    assert exit_category("max holding time reached (600s >= 600s)") == "timeout"
    verdict = development_verdict(m, dsr=None, pbo=None)
    assert verdict["passes"] is False and verdict["checks"]["dsr_ge_0_95"] is False


# --------------------------------------------------------------- marginal cost

def test_marginal_cost_excludes_the_paid_entry_side():
    bar = BARS[500]
    full = _estimate_cost(bar, spec(), CONFIG)
    marginal = marginal_future_cost(bar, spec(), CONFIG)
    half_spread = full.spread_cost / 2.0
    assert marginal.exit_spread == pytest.approx(half_spread)
    assert marginal.exit_commission == 0.0 and marginal.swap == 0.0
    assert marginal.total_cost < full.total_cost
    assert marginal.total_cost == pytest.approx(
        (half_spread + CONFIG.fill_assumptions.slippage_price) * (1 + CONFIG.uncertainty_margin_pct))


def test_marginal_cost_audit_switches():
    bar = BARS[500]
    with_commission = marginal_future_cost(bar, spec(), CONFIG, include_exit_commission=True)
    assert with_commission.exit_commission >= 0.0
    with pytest.raises(ValueError):
        marginal_future_cost(bar, spec(), CONFIG, rollovers_ahead=-1)
