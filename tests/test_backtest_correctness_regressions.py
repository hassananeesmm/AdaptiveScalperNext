"""Regression tests for PAPER/backtest correctness defects (checkpoint
after commit 8e67f77).

Each test pins one defect with exact, hand-computed prices, driven by a
SCRIPTED stub strategy monkeypatched into `backtest.engine` so a signal /
thesis invalidation happens at an exact bar -- the real strategies are
exercised by `test_backtest_engine.py` and the incremental-equivalence
property test at the bottom of this file.

Price conventions under test (see `backtest/engine.py` docstring): bars
are MID prices; `Bar.spread` 10 points * point 0.01 = 0.10 price, so the
half spread is 0.05. A BUY position is opened at the ask and closed at
the bid.
"""

from __future__ import annotations

import random
from dataclasses import replace

import pytest

import adaptive_scalper.backtest.engine as engine_module
from adaptive_scalper.backtest.dataset import build_dataset_snapshot, record_dataset, record_dataset_usage
from adaptive_scalper.backtest.engine import STOP_LOSS_HIT, run_backtest
from adaptive_scalper.backtest.oos import DatasetContaminatedError, run_untouched_oos
from adaptive_scalper.backtest.types import BacktestConfig
from adaptive_scalper.gateway.types import Bar, SymbolSpec, SymbolTradeMode
from adaptive_scalper.paper.engine import run_paper_cycle
from adaptive_scalper.paper.state import get_paper_trades, get_session
from adaptive_scalper.persistence import connect, migrate
from adaptive_scalper.position_management.adaptive_exit import AdaptiveExitParams
from adaptive_scalper.simulation.fill_model import FillAssumptions
from adaptive_scalper.simulation.types import EvidenceOrigin
from adaptive_scalper.strategies.base import StrategySignal

SYMBOL = "XAUUSD"
RES = "M5"
STEP = 300
START = 1_700_000_000
HALF_SPREAD = 0.05
STOP_DISTANCE = 2.0
TARGET_DISTANCE = 5.0
FIRE_INDEX = 30  # signal decided at bar 30's close -> earliest fill is bar 31's open


def _spec() -> SymbolSpec:
    return SymbolSpec(
        name="XAUUSDm", description="Gold vs US Dollar", currency_base="XAU", currency_profit="USD",
        currency_margin="XAU", digits=2, point=0.01, trade_contract_size=100.0,
        volume_min=0.01, volume_max=100.0, volume_step=0.01, trade_tick_size=0.01,
        trade_tick_value=1.0, spread=10, visible=True, trade_mode=SymbolTradeMode.FULL,
    )


def _bars(n: int = 40, overrides: dict[int, tuple[float, float, float, float]] | None = None, *, start: int = START) -> list[Bar]:
    """Flat 2000.00 mid bars; `overrides[i] = (open, high, low, close)`."""
    overrides = overrides or {}
    out = []
    for i in range(n):
        o, h, l, c = overrides.get(i, (2000.0, 2000.01, 1999.99, 2000.0))
        out.append(Bar(time=start + i * STEP, open=o, high=h, low=l, close=c, tick_volume=100, spread=10, real_volume=0))
    return out


# Every adaptive-exit rule that could fire on its own is disabled unless a
# test turns it back on, so each test isolates exactly one mechanism.
_QUIET_EXIT = AdaptiveExitParams(
    max_holding_enabled=False, early_take_profit_r=1e9, breakeven_enabled=False,
    profit_protection_trigger_r=1e9, regime_reversal_exit=False,
)


def _config(**overrides) -> BacktestConfig:
    defaults = dict(
        fill_assumptions=FillAssumptions(slippage_price=0.0, commission_monetary_per_lot=0.0),
        adaptive_exit_params=_QUIET_EXIT,
    )
    defaults.update(overrides)
    return BacktestConfig(**defaults)


class _ScriptedStrategy:
    """Fires `fire[t]` when SCANNING at bar time t; while RE-EVALUATING an
    open position (the engine's `_reevaluate_setup`, identified by its
    `"re-evaluation"` regime reason) reports the setup as still valid
    until `invalidate_from` (inclusive)."""

    key = "scripted"
    version = 1

    def __init__(self, fire: dict[int, str], invalidate_from: int | None = None) -> None:
        self.fire = fire
        self.invalidate_from = invalidate_from
        self._held_direction = next(iter(fire.values()))

    def _signal(self, t: int, direction: str) -> StrategySignal:
        return StrategySignal(
            strategy_key=self.key, strategy_version=1, canonical_symbol=SYMBOL, direction=direction,
            raw_confidence=0.9, stop_distance=STOP_DISTANCE, target_distance=TARGET_DISTANCE,
            expected_duration_seconds=600, entry_method="market", regime="RANGE", rationale="scripted",
            feature_schema_version=1, data_timestamp=t,
        )

    def evaluate(self, features, regime):
        t = features.data_timestamp
        if regime.reason == "re-evaluation":
            if self.invalidate_from is not None and t >= self.invalidate_from:
                return None
            return self._signal(t, self._held_direction)
        direction = self.fire.get(t)
        return self._signal(t, direction) if direction else None


class _StubRegistry:
    def __init__(self, *strategies) -> None:
        self._strategies = tuple(strategies)

    def all_active(self):
        return self._strategies


@pytest.fixture()
def script(monkeypatch):
    def install(fire_index: int = FIRE_INDEX, direction: str = "BUY", invalidate_index: int | None = None, *, start: int = START):
        strategy = _ScriptedStrategy(
            {start + fire_index * STEP: direction},
            invalidate_from=None if invalidate_index is None else start + invalidate_index * STEP,
        )
        monkeypatch.setattr(engine_module, "build_active_registry", lambda: _StubRegistry(strategy))
        return strategy
    return install


@pytest.fixture()
def db(tmp_path):
    conn = connect(tmp_path / "test.sqlite3")
    migrate(conn)
    yield conn
    conn.close()


def _only_trade(result):
    assert len(result.trades) == 1, result.trades
    return result.trades[0]


# ---------------------------------------------------------------------------
# Causal fill timing
# ---------------------------------------------------------------------------

def test_adaptive_full_close_fills_at_the_next_bar_open_never_the_decision_bars_own_open(script):
    # Thesis invalidated at bar 33's CLOSE. The decision cannot be known
    # before that close, so the earliest causal exit is bar 34's OPEN.
    # Before the fix the exit filled at bar 33's own open (2000.00) --
    # a price from BEFORE the information that triggered the exit existed.
    script(invalidate_index=33)
    bars = _bars(overrides={33: (2000.0, 2001.01, 1999.99, 2001.0), 34: (2001.0, 2001.01, 2000.99, 2001.0)})
    for i in range(35, 40):
        bars[i] = replace(bars[i], open=2001.0, high=2001.01, low=2000.99, close=2001.0)

    trade = _only_trade(run_backtest(bars, SYMBOL, RES, _spec(), config=_config(), now_utc=START))
    assert trade.entry_time_utc == bars[FIRE_INDEX + 1].time
    assert trade.exit_time_utc == bars[34].time
    assert trade.exit_price == pytest.approx(2001.0 - HALF_SPREAD)
    assert "thesis" in trade.exit_reason


def test_range_end_force_close_uses_the_last_bars_close_not_its_open(script):
    script()
    bars = _bars(overrides={39: (2000.0, 2001.01, 1999.99, 2001.0)})
    trade = _only_trade(run_backtest(bars, SYMBOL, RES, _spec(), config=_config(), now_utc=START))
    assert trade.exit_reason == "BACKTEST_RANGE_ENDED"
    assert trade.exit_price == pytest.approx(2001.0 - HALF_SPREAD)


# ---------------------------------------------------------------------------
# Entry-bar SL/TP handling and stop gaps
# ---------------------------------------------------------------------------

def test_stop_hit_within_the_entry_bar_itself_closes_the_trade_on_that_bar(script):
    # Filled at bar 31's open (ask 2000.05, stop 1998.05); bar 31's low
    # then trades through the stop. Before the fix the entry bar was
    # skipped entirely ("continue"), so this stop-out was never seen.
    script()
    bars = _bars(overrides={31: (2000.0, 2000.5, 1995.0, 2000.0)})
    trade = _only_trade(run_backtest(bars, SYMBOL, RES, _spec(), config=_config(), now_utc=START))
    assert trade.entry_price == pytest.approx(2000.0 + HALF_SPREAD)
    assert trade.exit_reason == STOP_LOSS_HIT
    assert trade.exit_time_utc == bars[31].time
    assert trade.exit_price == pytest.approx(2000.0 + HALF_SPREAD - STOP_DISTANCE)


def test_target_hit_within_the_entry_bar_itself_closes_the_trade_on_that_bar(script):
    script()
    bars = _bars(overrides={31: (2000.0, 2006.0, 1999.9, 2000.0)})
    trade = _only_trade(run_backtest(bars, SYMBOL, RES, _spec(), config=_config(), now_utc=START))
    assert trade.exit_reason == "TAKE_PROFIT_HIT"
    assert trade.exit_time_utc == bars[31].time
    assert trade.exit_price == pytest.approx(2000.0 + HALF_SPREAD + TARGET_DISTANCE)


def test_stop_gapped_through_at_the_open_fills_at_the_open_not_at_the_stop(script):
    # Stop 1998.05; bar 32 OPENS at 1990 (gap). A stop is a market order
    # once triggered -- it fills at the first available bid (1989.95) less
    # slippage, never at the stop price that was never actually tradeable.
    script()
    slip = 0.02
    bars = _bars(overrides={32: (1990.0, 1990.5, 1989.0, 1990.0)})
    config = _config(fill_assumptions=FillAssumptions(slippage_price=slip, commission_monetary_per_lot=0.0))
    trade = _only_trade(run_backtest(bars, SYMBOL, RES, _spec(), config=config, now_utc=START))
    assert trade.exit_reason == STOP_LOSS_HIT
    assert trade.exit_price == pytest.approx(1990.0 - HALF_SPREAD - slip)


def test_a_buy_stop_triggers_on_the_bid_not_the_mid(script):
    # Stop 1998.05. Bar 32's mid low 1998.08 stays above it, but the BID
    # low (1998.03) trades through -- a long position's stop is triggered
    # by the bid, so it IS hit.
    script()
    bars = _bars(overrides={32: (2000.0, 2000.01, 1998.08, 2000.0)})
    trade = _only_trade(run_backtest(bars, SYMBOL, RES, _spec(), config=_config(), now_utc=START))
    assert trade.exit_reason == STOP_LOSS_HIT
    assert trade.exit_time_utc == bars[32].time


# ---------------------------------------------------------------------------
# Persistent peak R
# ---------------------------------------------------------------------------

# volume = floor(25 / (2.0 * 100) / 0.01) * 0.01 = 0.12 lots; risk = $24;
# 1R = 2.0 price. Marked at the bid: r = (close - 0.05 - 2000.05) / 2.0.
_PEAK_BARS = {
    32: (2000.0, 2001.71, 1999.99, 2001.70),   # r = 0.80 -> new peak
    33: (2001.70, 2001.71, 2001.19, 2001.20),  # r = 0.55 -> giveback 0.25R
    34: (2001.20, 2001.21, 2001.19, 2001.20),
}
_GIVEBACK_EXIT = AdaptiveExitParams(
    max_holding_enabled=False, early_take_profit_r=1e9, breakeven_enabled=False, regime_reversal_exit=False,
    profit_protection_trigger_r=0.60, max_profit_retracement_r=0.20,
)


def test_profit_giveback_protection_uses_the_running_peak_r_not_the_current_r(script):
    # Before the fix `peak_r=current_r` was passed on every bar, so
    # giveback was always 0 and profit protection could never fire.
    script()
    bars = _bars(overrides=_PEAK_BARS)
    config = _config(adaptive_exit_params=_GIVEBACK_EXIT)
    trade = _only_trade(run_backtest(bars, SYMBOL, RES, _spec(), config=config, now_utc=START))
    assert "giveback" in trade.exit_reason
    assert trade.exit_time_utc == bars[34].time  # decided at 33's close, filled at 34's open


def test_peak_r_is_carried_in_the_resumable_open_position_state(script):
    script()
    bars = _bars(overrides=_PEAK_BARS)
    config = _config(adaptive_exit_params=_GIVEBACK_EXIT)
    result = run_backtest(bars[:33], SYMBOL, RES, _spec(), config=config, now_utc=START, force_close_at_range_end=False)
    assert result.open_position is not None
    assert result.open_position.peak_r == pytest.approx(0.80)


def test_peak_r_survives_a_paper_cycle_boundary(script, db):
    # Cycle 1 ends right at the peak; cycle 2 sees only the retracement.
    # Without a persisted peak, cycle 2 restarts the peak at the retraced
    # current_r and the giveback exit is silently lost.
    script()
    bars = _bars(overrides=_PEAK_BARS)
    config = _config(adaptive_exit_params=_GIVEBACK_EXIT)
    run_paper_cycle(db, bars[:33], SYMBOL, RES, _spec(), config=config, now_utc=START)
    assert get_session(db, f"PAPER:{SYMBOL}:{RES}").open_position.peak_r == pytest.approx(0.80)

    second = run_paper_cycle(db, bars, SYMBOL, RES, _spec(), config=config, now_utc=START)
    assert len(second.new_trades) == 1
    trade = second.new_trades[0]
    assert "giveback" in trade.exit_reason
    assert trade.exit_time_utc == bars[34].time


def test_peak_r_starts_at_zero_like_the_live_state_store(script):
    # Live position_management_state.peak_r defaults to 0.0 and is only
    # ever max()-ed upward; a trade that never goes green keeps peak 0.0.
    script()
    bars = _bars(overrides={32: (2000.0, 2000.01, 1999.49, 1999.5)})
    result = run_backtest(bars[:33], SYMBOL, RES, _spec(), config=_config(), now_utc=START, force_close_at_range_end=False)
    assert result.open_position.peak_r == 0.0


# ---------------------------------------------------------------------------
# Pending entries / exits across cycles
# ---------------------------------------------------------------------------

def test_a_signal_on_the_last_bar_of_a_window_is_returned_as_a_pending_entry(script):
    script()
    result = run_backtest(_bars()[:FIRE_INDEX + 1], SYMBOL, RES, _spec(), config=_config(), now_utc=START,
                          force_close_at_range_end=False)
    assert result.trades == ()
    assert result.open_position is None
    assert result.pending_entry is not None
    assert result.pending_entry.direction == "BUY"
    assert result.pending_entry.signal_time_utc == START + FIRE_INDEX * STEP


def test_a_resumed_pending_entry_fills_at_the_first_new_bars_open(script):
    script()
    bars = _bars()
    lookback = _config().feature_lookback
    first = run_backtest(bars[:FIRE_INDEX + 1], SYMBOL, RES, _spec(), config=_config(), now_utc=START,
                         force_close_at_range_end=False)
    window = bars[FIRE_INDEX + 1 - lookback - 1:]
    second = run_backtest(window, SYMBOL, RES, _spec(), config=_config(), now_utc=START,
                          resume_pending_entry=first.pending_entry, resume_regime_tracker=first.final_regime_tracker_state,
                          force_close_at_range_end=False)
    assert second.open_position is not None
    assert second.open_position.entry_time_utc == bars[FIRE_INDEX + 1].time
    assert second.open_position.entry_price == pytest.approx(2000.0 + HALF_SPREAD)


def test_run_backtest_rejects_resuming_both_an_open_position_and_a_pending_entry(script):
    script()
    bars = _bars()
    first = run_backtest(bars[:FIRE_INDEX + 1], SYMBOL, RES, _spec(), config=_config(), now_utc=START,
                         force_close_at_range_end=False)
    opened = run_backtest(bars[:FIRE_INDEX + 3], SYMBOL, RES, _spec(), config=_config(), now_utc=START,
                          force_close_at_range_end=False)
    with pytest.raises(ValueError, match="pending entry"):
        run_backtest(bars, SYMBOL, RES, _spec(), config=_config(), now_utc=START,
                     resume_open_position=opened.open_position, resume_pending_entry=first.pending_entry,
                     force_close_at_range_end=False)


def test_paper_pending_entry_survives_the_cycle_boundary(script, db):
    # Before the fix the pending entry was a local variable of
    # run_backtest() and was silently dropped at the end of every cycle:
    # a signal on a cycle's last bar never traded at all.
    script()
    bars = _bars()
    first = run_paper_cycle(db, bars[:FIRE_INDEX + 1], SYMBOL, RES, _spec(), config=_config(), now_utc=START)
    assert first.ran and first.new_trades == () and first.open_position is None
    assert get_session(db, first.session_key).pending_entry is not None

    second = run_paper_cycle(db, bars, SYMBOL, RES, _spec(), config=_config(), now_utc=START)
    assert second.open_position is not None
    assert second.open_position.entry_time_utc == bars[FIRE_INDEX + 1].time
    assert get_session(db, first.session_key).pending_entry is None


def test_paper_pending_exit_survives_the_cycle_boundary(script, db):
    # FULL_CLOSE decided on cycle 1's last bar (33) must fill at cycle 2's
    # first new bar (34) open -- not be forgotten, not fill retroactively.
    script(invalidate_index=33)
    bars = _bars(overrides={34: (2001.0, 2001.01, 2000.99, 2001.0)})
    first = run_paper_cycle(db, bars[:34], SYMBOL, RES, _spec(), config=_config(), now_utc=START)
    assert first.open_position is not None
    assert first.open_position.pending_exit_reason is not None

    second = run_paper_cycle(db, bars, SYMBOL, RES, _spec(), config=_config(), now_utc=START)
    assert len(second.new_trades) == 1
    trade = second.new_trades[0]
    assert trade.exit_time_utc == bars[34].time
    assert trade.exit_price == pytest.approx(2001.0 - HALF_SPREAD)
    assert get_paper_trades(db, second.session_key)[0]["exit_time_utc"] == bars[34].time


# ---------------------------------------------------------------------------
# Cost accounting
# ---------------------------------------------------------------------------

def test_costs_are_charged_exactly_once_and_total_cost_reports_every_component(script):
    # Entry ask = 2000 + 0.05 + 0.02 slip = 2000.07; exit (thesis
    # invalidated at 33, fill at 34 open 2001) bid = 2001 - 0.05 - 0.02 =
    # 2000.93. 0.12 lots, $12 per 1.0 price.
    #   execution P/L    = (2000.93 - 2000.07) * 12 = 10.32
    #   commission       = 5.00 * 0.12              =  0.60
    #   realized (net)   = 10.32 - 0.60             =  9.72
    #   entry friction   = 0.07 * 12 = 0.84; exit friction = 0.84
    #   total_cost       = 0.84 + 0.84 + 0.60       =  2.28
    #   gross = net + total_cost = 12.00 = mid-to-mid (2001 - 2000) * 12
    # Before the fix: entry friction was subtracted AGAIN on top of an
    # entry price that already contained it, exit friction and commission
    # never appeared in total_cost, and commission was never charged.
    script(invalidate_index=33)
    bars = _bars(overrides={34: (2001.0, 2001.01, 2000.99, 2001.0)})
    for i in range(35, 40):
        bars[i] = replace(bars[i], open=2001.0, high=2001.01, low=2000.99, close=2001.0)
    config = _config(fill_assumptions=FillAssumptions(slippage_price=0.02, commission_monetary_per_lot=5.0))
    result = run_backtest(bars, SYMBOL, RES, _spec(), config=config, now_utc=START)
    trade = _only_trade(result)

    assert trade.volume == pytest.approx(0.12)
    assert trade.entry_price == pytest.approx(2000.07)
    assert trade.exit_price == pytest.approx(2000.93)
    assert trade.realized_pnl == pytest.approx(9.72)
    assert trade.total_cost == pytest.approx(2.28)
    assert result.metrics.gross_pnl == pytest.approx(12.0)
    assert result.metrics.net_pnl == pytest.approx(9.72)
    assert result.metrics.final_equity == pytest.approx(config.initial_equity + 9.72)


def test_stop_exit_pays_slippage_and_reports_exit_friction(script):
    # Stop 1998.07 (entry ask 2000.07); bar 32 bid low trades through.
    # Stop fill = 1998.07 - 0.02 slip = 1998.05.
    #   execution P/L = (1998.05 - 2000.07) * 12 = -24.24; commission 0.
    #   total_cost = entry 0.84 + exit (0.05 + 0.02) * 12 = 0.84 -> 1.68
    script()
    bars = _bars(overrides={32: (2000.0, 2000.01, 1990.0, 1995.0)})
    config = _config(fill_assumptions=FillAssumptions(slippage_price=0.02, commission_monetary_per_lot=0.0))
    trade = _only_trade(run_backtest(bars, SYMBOL, RES, _spec(), config=config, now_utc=START))
    assert trade.exit_reason == STOP_LOSS_HIT
    assert trade.exit_price == pytest.approx(1998.05)
    assert trade.realized_pnl == pytest.approx(-24.24)
    assert trade.total_cost == pytest.approx(1.68)
    assert trade.realized_r == pytest.approx(-24.24 / 24.0)


def test_swap_is_charged_per_utc_rollover_crossed(script):
    # Bars aligned so bar 31 (entry) is 23:55 UTC and bar 32 is 00:00 UTC.
    # Held until the range-end close at bar 39: exactly one rollover.
    start = (START // 86400 + 1) * 86400 - 32 * STEP
    script(start=start)
    bars = _bars(start=start)
    config = _config(fill_assumptions=FillAssumptions(
        slippage_price=0.0, commission_monetary_per_lot=0.0, swap_monetary_per_lot_per_day=2.0,
    ))
    no_swap = _only_trade(run_backtest(bars, SYMBOL, RES, _spec(), config=_config(), now_utc=start))
    with_swap = _only_trade(run_backtest(bars, SYMBOL, RES, _spec(), config=config, now_utc=start))
    assert with_swap.realized_pnl == pytest.approx(no_swap.realized_pnl - 2.0 * 0.12)
    assert with_swap.total_cost == pytest.approx(no_swap.total_cost + 2.0 * 0.12)


def test_metrics_gross_minus_cost_equals_net_on_real_strategies():
    bars = _random_walk_bars(600, seed=7)
    config = BacktestConfig(fill_assumptions=FillAssumptions(slippage_price=0.03, commission_monetary_per_lot=7.0))
    result = run_backtest(bars, SYMBOL, RES, _spec(), config=config, now_utc=START)
    assert result.metrics.closed_trade_count > 0
    m = result.metrics
    assert m.net_pnl == pytest.approx(m.gross_pnl - m.total_cost)
    assert m.final_equity == pytest.approx(config.initial_equity + m.net_pnl)
    for t in result.trades:
        assert t.total_cost > 0


# ---------------------------------------------------------------------------
# Overlapping OOS protection
# ---------------------------------------------------------------------------

def _record_usage(db, bars, used_for, *, symbol=SYMBOL, resolution=RES, run_id="prior-run"):
    snapshot = build_dataset_snapshot(
        bars, canonical_symbol=symbol, resolution=resolution, strategies=("x",),
        feature_schema_version=1, origin=EvidenceOrigin.BACKTEST, now_utc=START,
    )
    record_dataset(db, snapshot)
    record_dataset_usage(db, snapshot.dataset_id, run_id, used_for, now_utc=START)


def _oos(db, bars, run_id="oos", **kwargs):
    return run_untouched_oos(bars, SYMBOL, RES, _spec(), conn=db, run_id=run_id, config=BacktestConfig(),
                             now_utc=START, **kwargs)


def test_oos_refuses_a_range_that_partially_overlaps_a_training_range(db):
    # Before the fix only the EXACT content checksum was checked, so
    # shifting the OOS window by even one bar sailed past the guard.
    bars = _random_walk_bars(400, seed=1)
    _record_usage(db, bars[:200], "TRAINING")
    with pytest.raises(DatasetContaminatedError, match="TRAINING"):
        _oos(db, bars[150:350])


def test_oos_refuses_a_range_nested_inside_a_walk_forward_fold(db):
    bars = _random_walk_bars(400, seed=2)
    _record_usage(db, bars[:300], "WALK_FORWARD_FOLD")
    with pytest.raises(DatasetContaminatedError, match="WALK_FORWARD_FOLD"):
        _oos(db, bars[100:200])


def test_oos_refuses_overlap_with_a_different_resolution_of_the_same_symbol(db):
    # M1 training over the same calendar period IS the same market data.
    bars = _random_walk_bars(400, seed=3)
    _record_usage(db, bars[:250], "TRAINING", resolution="M1")
    with pytest.raises(DatasetContaminatedError):
        _oos(db, bars[200:400])


def test_oos_refuses_a_range_overlapping_a_previously_spent_oos_range(db):
    bars = _random_walk_bars(400, seed=4)
    _oos(db, bars[:200], run_id="oos-a")
    with pytest.raises(DatasetContaminatedError, match="already run as OOS"):
        _oos(db, bars[100:300], run_id="oos-b")
    _oos(db, bars[100:300], run_id="oos-c", allow_oos_reuse=True)


def test_oos_allows_a_strictly_later_non_overlapping_range(db):
    bars = _random_walk_bars(400, seed=5)
    _record_usage(db, bars[:200], "TRAINING")
    result = _oos(db, bars[200:400])
    assert result.range_start_utc > bars[199].time


def test_oos_refuses_same_period_training_data_with_different_content(db):
    # A corrected/re-downloaded copy of the same calendar period has a
    # different checksum but is still the data the design already saw.
    bars = _random_walk_bars(300, seed=8)
    other_copy = [replace(b, close=b.close + 0.01) for b in bars]
    _record_usage(db, other_copy, "TRAINING")
    with pytest.raises(DatasetContaminatedError, match="TRAINING"):
        _oos(db, bars)


def test_oos_is_not_blocked_by_another_symbols_usage_of_the_same_period(db):
    bars = _random_walk_bars(400, seed=6)
    _record_usage(db, bars[:300], "TRAINING", symbol="GBPJPY")
    _oos(db, bars[:300])


# ---------------------------------------------------------------------------
# Incremental PAPER cycling == one continuous run, with real strategies
# ---------------------------------------------------------------------------

def _random_walk_bars(n: int, *, seed: int, start: int = START) -> list[Bar]:
    """Deterministic drifting random walk with alternating trend phases,
    so the real strategies/regime classifier actually trade."""
    rng = random.Random(seed)
    price = 2000.0
    out = []
    for i in range(n):
        drift = 0.35 if (i // 60) % 2 == 0 else -0.35
        o = price
        c = o + drift + rng.gauss(0.0, 0.6)
        h = max(o, c) + abs(rng.gauss(0.0, 0.3))
        l = min(o, c) - abs(rng.gauss(0.0, 0.3))
        out.append(Bar(time=start + i * STEP, open=o, high=h, low=l, close=c, tick_volume=100,
                       spread=rng.choice((8, 10, 12)), real_volume=0))
        price = c
    return out


def _trade_key(t):
    return (t["entry_time_utc"], t["direction"], t["exit_time_utc"], t["exit_reason"], round(t["realized_pnl"], 6))


@pytest.mark.parametrize("seed", [11, 12, 13])
@pytest.mark.parametrize("chunk", [2, 3, 7, 25])
def test_incremental_paper_cycles_match_a_single_continuous_run(db, seed, chunk):
    bars = _random_walk_bars(500, seed=seed)
    config = BacktestConfig(fill_assumptions=FillAssumptions(slippage_price=0.02, commission_monetary_per_lot=7.0))
    reference = run_backtest(bars, SYMBOL, RES, _spec(), config=config, now_utc=START, force_close_at_range_end=False)
    assert len(reference.trades) >= 3  # the property is vacuous without real trading activity

    key = f"prop:{seed}:{chunk}"
    cutoff = config.feature_lookback + 3
    last = None
    while True:
        last = run_paper_cycle(db, bars[:cutoff], SYMBOL, RES, _spec(), config=config, session_key=key, now_utc=START)
        if cutoff >= len(bars):
            break
        cutoff = min(len(bars), cutoff + chunk)

    session = get_session(db, key)
    assert session.last_processed_bar_time_utc == bars[-1].time
    assert session.equity == pytest.approx(reference.metrics.final_equity)
    recorded = sorted(_trade_key(r) for r in get_paper_trades(db, key))
    expected = sorted(_trade_key({
        "entry_time_utc": t.entry_time_utc, "direction": t.direction, "exit_time_utc": t.exit_time_utc,
        "exit_reason": t.exit_reason, "realized_pnl": t.realized_pnl,
    }) for t in reference.trades)
    assert recorded == expected
    assert session.open_position == reference.open_position
    assert session.pending_entry == reference.pending_entry


# ---------------------------------------------------------------------------
# PAPER windowing edge cases
# ---------------------------------------------------------------------------

def test_paper_cycle_processes_a_single_new_bar(db):
    # Live closed bars arrive one at a time. Before the fix a window of
    # [context] + [1 new bar] was below the engine's minimum, so every
    # single-bar cycle was a no-op and PAPER ran permanently a bar behind.
    bars = _random_walk_bars(120, seed=21)
    run_paper_cycle(db, bars[:100], SYMBOL, RES, _spec(), config=BacktestConfig(), now_utc=START)
    for n in range(101, 121):
        result = run_paper_cycle(db, bars[:n], SYMBOL, RES, _spec(), config=BacktestConfig(), now_utc=START)
        assert result.ran is True
        assert result.last_processed_bar_time_utc == bars[n - 1].time


def test_paper_cycle_refuses_a_history_too_short_to_give_the_new_bars_context(db):
    # Supplying only the most recent few bars would make the engine start
    # deciding PAST the new bars while the cursor jumps over them.
    bars = _random_walk_bars(120, seed=22)
    run_paper_cycle(db, bars[:100], SYMBOL, RES, _spec(), config=BacktestConfig(), now_utc=START)
    with pytest.raises(ValueError, match="already-processed"):
        run_paper_cycle(db, bars[95:110], SYMBOL, RES, _spec(), config=BacktestConfig(), now_utc=START)
    assert get_session(db, f"PAPER:{SYMBOL}:{RES}").last_processed_bar_time_utc == bars[99].time


def test_open_position_json_persisted_before_this_fix_still_loads(db, script):
    # Rows written by 8e67f77 lack peak_r / pending_exit_reason; they load
    # with the live-state-store defaults (0.0 / none pending).
    import json

    script()
    run_paper_cycle(db, _bars()[:FIRE_INDEX + 3], SYMBOL, RES, _spec(), config=_config(), now_utc=START)
    key = f"PAPER:{SYMBOL}:{RES}"
    raw = json.loads(db.execute("SELECT open_position_json FROM paper_session_state WHERE session_key = ?", (key,)).fetchone()[0])
    raw.pop("peak_r")
    raw.pop("pending_exit_reason")
    db.execute("UPDATE paper_session_state SET open_position_json = ? WHERE session_key = ?", (json.dumps(raw), key))
    db.commit()
    restored = get_session(db, key).open_position
    assert restored.peak_r == 0.0
    assert restored.pending_exit_reason is None
