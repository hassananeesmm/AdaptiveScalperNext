"""Integration tests for the causal backtest engine (directive section 80).

Exercises `run_backtest()` end to end against the REAL production decision
cores (strategies, regime classifier, expectancy, adaptive exit, risk
governor) on synthetic bars -- not isolated unit tests of those cores
(which already exist elsewhere), but a wiring/integration check that the
engine assembles them correctly and produces internally-consistent
results.
"""

from __future__ import annotations

import math
from dataclasses import replace

import pytest

from adaptive_scalper.backtest.engine import run_backtest
from adaptive_scalper.backtest.types import BacktestConfig
from adaptive_scalper.gateway.types import Bar, SymbolSpec, SymbolTradeMode
from adaptive_scalper.simulation.fill_model import FillAssumptions
from edge_fixtures import v1_replay_config

CANONICAL_SYMBOL = "XAUUSD"
RESOLUTION = "M5"


def _symbol_spec(**overrides) -> SymbolSpec:
    defaults = dict(
        name="XAUUSDm", description="Gold vs US Dollar", currency_base="XAU", currency_profit="USD",
        currency_margin="XAU", digits=2, point=0.01, trade_contract_size=100.0,
        volume_min=0.01, volume_max=100.0, volume_step=0.01, trade_tick_size=0.01,
        trade_tick_value=1.0, spread=10, visible=True, trade_mode=SymbolTradeMode.FULL,
    )
    defaults.update(overrides)
    return SymbolSpec(**defaults)


def _trending_bars(n: int, *, start_price: float = 2000.0, step: float = 0.5, start_time: int = 1_700_000_000) -> list[Bar]:
    """A clean, low-noise uptrend -- efficiency_ratio near 1.0, so
    `classify_regime()` reliably confirms TRENDING_UP and
    `momentum_continuation` reliably fires. Deterministic (no RNG), so
    this test never flakes."""
    bars = []
    price = start_price
    for i in range(n):
        open_price = price
        close_price = price + step
        high = close_price + 0.05
        low = open_price - 0.05
        bars.append(Bar(
            time=start_time + i * 300, open=open_price, high=high, low=low, close=close_price,
            tick_volume=100, spread=10, real_volume=0,
        ))
        price = close_price
    return bars


def _flat_bars(n: int, *, price: float = 2000.0, start_time: int = 1_700_000_000) -> list[Bar]:
    """A perfectly flat series -- no regime ever confirms TRENDING/
    BREAKOUT/VOLATILITY_EXPANSION, so no strategy should ever fire.
    Used to prove the engine runs cleanly end to end with zero trades."""
    return [
        Bar(time=start_time + i * 300, open=price, high=price + 0.01, low=price - 0.01, close=price,
            tick_volume=100, spread=10, real_volume=0)
        for i in range(n)
    ]


def _config(**overrides) -> BacktestConfig:
    defaults = dict(
        fill_assumptions=FillAssumptions(slippage_price=0.0, commission_monetary_per_lot=0.0),
    )
    defaults.update(overrides)
    return v1_replay_config(**defaults)


def test_run_backtest_on_a_trending_series_produces_at_least_one_trade():
    bars = _trending_bars(200)
    result = run_backtest(bars, CANONICAL_SYMBOL, RESOLUTION, _symbol_spec(), config=_config(), now_utc=2_000_000_000)

    assert result.canonical_symbol == CANONICAL_SYMBOL
    assert result.resolution == RESOLUTION
    assert result.range_start_utc == bars[0].time
    assert result.range_end_utc == bars[-1].time
    assert result.dataset_id
    assert len(result.trades) >= 1

    for trade in result.trades:
        assert trade.direction == "BUY"  # a clean uptrend should only ever produce BUY entries
        assert trade.volume > 0
        assert trade.initial_monetary_risk > 0
        assert trade.entry_price > 0
        # Every trade in a completed run must be closed -- the engine
        # force-closes anything still open at the final bar.
        assert trade.is_closed
        assert trade.exit_price is not None and trade.exit_price > 0
        assert trade.exit_reason is not None


def test_run_backtest_metrics_are_internally_consistent():
    bars = _trending_bars(200)
    result = run_backtest(bars, CANONICAL_SYMBOL, RESOLUTION, _symbol_spec(), config=_config(), now_utc=2_000_000_000)

    m = result.metrics
    assert m.trade_count == len(result.trades)
    assert m.closed_trade_count == len([t for t in result.trades if t.is_closed])
    # final_equity must equal initial_equity plus the sum of every closed
    # trade's realized P/L -- never independently drifting from the trade log.
    expected_final_equity = _config().initial_equity + sum(t.realized_pnl or 0.0 for t in result.trades)
    assert math.isclose(m.final_equity, expected_final_equity, rel_tol=1e-9, abs_tol=1e-6)
    assert m.net_pnl == m.gross_pnl - m.total_cost
    assert m.max_drawdown >= 0.0
    if m.closed_trade_count > 0:
        assert 0.0 <= (m.win_rate or 0.0) <= 1.0


def test_run_backtest_equity_curve_is_monotonically_increasing_in_time():
    bars = _trending_bars(200)
    result = run_backtest(bars, CANONICAL_SYMBOL, RESOLUTION, _symbol_spec(), config=_config(), now_utc=2_000_000_000)
    times = [t for t, _ in result.equity_curve]
    assert times == sorted(times)
    assert len(result.equity_curve) == len([t for t in result.trades if t.is_closed])


def test_run_backtest_on_flat_bars_produces_no_trades_and_never_crashes():
    bars = _flat_bars(100)
    result = run_backtest(bars, CANONICAL_SYMBOL, RESOLUTION, _symbol_spec(), config=_config(), now_utc=2_000_000_000)
    assert result.trades == ()
    assert result.metrics.trade_count == 0
    assert result.metrics.final_equity == _config().initial_equity
    assert result.metrics.win_rate is None
    assert result.metrics.profit_factor is None


def test_run_backtest_default_news_limitation_note_is_set_when_no_windows_given():
    bars = _flat_bars(100)
    result = run_backtest(bars, CANONICAL_SYMBOL, RESOLUTION, _symbol_spec(), config=_config(), now_utc=2_000_000_000)
    assert result.news_limitation_note is not None
    assert "news" in result.news_limitation_note.lower()


def test_run_backtest_news_limitation_note_absent_when_windows_supplied():
    bars = _flat_bars(100)
    config = _config(news_windows=((bars[0].time, bars[0].time + 1),))
    result = run_backtest(bars, CANONICAL_SYMBOL, RESOLUTION, _symbol_spec(), config=config, now_utc=2_000_000_000)
    assert result.news_limitation_note is None


def test_run_backtest_rejects_too_few_bars():
    bars = _flat_bars(5)
    with pytest.raises(ValueError, match="need at least"):
        run_backtest(bars, CANONICAL_SYMBOL, RESOLUTION, _symbol_spec(), config=_config())


def test_run_backtest_is_deterministic_same_input_same_output():
    bars = _trending_bars(200)
    r1 = run_backtest(bars, CANONICAL_SYMBOL, RESOLUTION, _symbol_spec(), config=_config(), now_utc=2_000_000_000)
    r2 = run_backtest(bars, CANONICAL_SYMBOL, RESOLUTION, _symbol_spec(), config=_config(), now_utc=2_000_000_000)
    assert r1.trades == r2.trades
    assert r1.metrics == r2.metrics


def test_run_backtest_downtrend_produces_only_sell_entries():
    bars = _trending_bars(200, step=-0.5)
    result = run_backtest(bars, CANONICAL_SYMBOL, RESOLUTION, _symbol_spec(), config=_config(), now_utc=2_000_000_000)
    assert len(result.trades) >= 1
    for trade in result.trades:
        assert trade.direction == "SELL"


def test_run_backtest_captures_entry_features_for_ml_training():
    bars = _trending_bars(200)
    result = run_backtest(bars, CANONICAL_SYMBOL, RESOLUTION, _symbol_spec(), config=_config(), now_utc=2_000_000_000)
    assert len(result.trades) >= 1
    for trade in result.trades:
        assert trade.entry_features is not None
        assert trade.entry_raw_confidence is not None
        assert 0.0 <= trade.entry_raw_confidence <= 1.0
        # A clean trend should yield a real (non-None) efficiency_ratio --
        # proves the captured vector is the REAL features snapshot, not
        # an empty/placeholder dict.
        assert trade.entry_features["efficiency_ratio"] is not None


def test_run_backtest_force_close_false_leaves_a_still_open_trade_unclosed():
    # 201 bars: the range must END while a trade is open (entries recur every
    # 3 bars on this trend, so 200 bars ends on a pending entry instead since
    # max-holding exits moved to the correct bar-close time, BUG_BACKLOG #13).
    bars = _trending_bars(201)
    result = run_backtest(
        bars, CANONICAL_SYMBOL, RESOLUTION, _symbol_spec(), config=_config(), now_utc=2_000_000_000,
        force_close_at_range_end=False,
    )
    assert result.open_position is not None
    assert all(t.exit_reason != "BACKTEST_RANGE_ENDED" for t in result.trades)
    # The open position must NOT also appear as a closed trade.
    assert result.open_position.entry_time_utc not in {t.entry_time_utc for t in result.trades if t.is_closed}


def test_run_backtest_force_close_true_is_the_default_and_always_closes():
    bars = _trending_bars(200)
    result = run_backtest(bars, CANONICAL_SYMBOL, RESOLUTION, _symbol_spec(), config=_config(), now_utc=2_000_000_000)
    assert result.open_position is None
    assert all(t.is_closed for t in result.trades)


def test_run_backtest_resume_open_position_continues_the_same_trade_into_a_later_window():
    # A resuming (PAPER-style) caller passes a window of [feature_lookback
    # bars of trailing CONTEXT] + [only the genuinely NEW bars] -- never
    # the full history again, which would re-decide already-processed bars.
    bars = _trending_bars(220)  # split at 201: the first window must end with a trade OPEN
    lookback = _config().feature_lookback
    first = run_backtest(
        bars[:201], CANONICAL_SYMBOL, RESOLUTION, _symbol_spec(), config=_config(), now_utc=2_000_000_000,
        force_close_at_range_end=False,
    )
    assert first.open_position is not None

    resume_window = bars[201 - lookback - 1:220]
    second = run_backtest(
        resume_window, CANONICAL_SYMBOL, RESOLUTION, _symbol_spec(), config=_config(), now_utc=2_000_000_000,
        resume_open_position=first.open_position, force_close_at_range_end=False,
    )
    # The resumed position's entry details are UNCHANGED from the first call.
    resumed_trade = next(t for t in second.trades if t.entry_time_utc == first.open_position.entry_time_utc)
    assert resumed_trade.entry_price == first.open_position.entry_price
    assert resumed_trade.volume == first.open_position.volume
    assert resumed_trade.entry_features == first.open_position.entry_features


def test_run_backtest_resume_open_position_never_reopens_a_new_entry_before_managing_the_resumed_one():
    bars = _trending_bars(220)  # split at 201: the first window must end with a trade OPEN
    lookback = _config().feature_lookback
    first = run_backtest(
        bars[:201], CANONICAL_SYMBOL, RESOLUTION, _symbol_spec(), config=_config(), now_utc=2_000_000_000,
        force_close_at_range_end=False,
    )
    resume_window = bars[201 - lookback - 1:220]
    second = run_backtest(
        resume_window, CANONICAL_SYMBOL, RESOLUTION, _symbol_spec(), config=_config(), now_utc=2_000_000_000,
        resume_open_position=first.open_position, force_close_at_range_end=False,
    )
    # No trade in the resumed run can have opened chronologically BEFORE
    # the resumed position closed (or is still the same open position) --
    # the engine must manage the resumed trade first, never scan for a
    # fresh entry while one is already (resumed) open.
    entry_times = sorted(t.entry_time_utc for t in second.trades)
    if entry_times:
        assert entry_times[0] == first.open_position.entry_time_utc


def test_run_backtest_resume_regime_tracker_reproduces_continuous_processing():
    # Regression: without resuming the regime tracker's hysteresis state,
    # a resumed/incremental call restarts it from UNKNOWN every time,
    # genuinely diverging from what a continuously-running tracker would
    # have decided. Proof: cycling through the SAME range in two chunks
    # WITH resume_regime_tracker must match one continuous call exactly;
    # WITHOUT it, the two are not guaranteed to (and empirically don't).
    # Boundary 202 is deliberately chosen against this fixture's 3-bar
    # trade cycle (it was 5 bars until max-holding exits moved to the
    # correct bar-close time, BUG_BACKLOG #13): it must end on an OPEN
    # trade, and at e.g. 201 an unresumed tracker happens to re-confirm
    # TRENDING_UP before it matters, so the negative half below would be
    # vacuous there. The positive half holds at every boundary 60-214.
    bars = _trending_bars(220)
    lookback = _config().feature_lookback
    boundary = 202
    first = run_backtest(
        bars[:boundary], CANONICAL_SYMBOL, RESOLUTION, _symbol_spec(), config=_config(), now_utc=2_000_000_000,
        force_close_at_range_end=False,
    )
    assert first.open_position is not None  # otherwise this test proves nothing about resuming
    boundary_entry_time = first.open_position.entry_time_utc

    reference = run_backtest(
        bars, CANONICAL_SYMBOL, RESOLUTION, _symbol_spec(), config=_config(), now_utc=2_000_000_000,
        force_close_at_range_end=False,
    )
    reference_new = sorted(
        (t.entry_time_utc, t.direction, round(t.realized_pnl, 6))
        for t in reference.trades if t.entry_time_utc >= boundary_entry_time
    )

    resume_window = bars[boundary - lookback - 1:220]
    # A resuming (PAPER-style) caller must also carry forward the REAL
    # accumulated equity `first`'s own run ended at -- never restart
    # sizing from the static config default, which would under/over-size
    # every subsequent trade relative to the continuous reference run.
    resume_config = replace(_config(), initial_equity=first.metrics.final_equity)

    with_resume = run_backtest(
        resume_window, CANONICAL_SYMBOL, RESOLUTION, _symbol_spec(), config=resume_config, now_utc=2_000_000_000,
        resume_open_position=first.open_position, resume_regime_tracker=first.final_regime_tracker_state,
        force_close_at_range_end=False,
    )
    with_resume_trades = sorted(
        (t.entry_time_utc, t.direction, round(t.realized_pnl, 6)) for t in with_resume.trades
    )
    assert with_resume_trades == reference_new
    if with_resume.open_position is not None:
        assert reference.open_position is not None
        assert with_resume.open_position.entry_time_utc == reference.open_position.entry_time_utc
    else:
        assert reference.open_position is None

    without_resume = run_backtest(
        resume_window, CANONICAL_SYMBOL, RESOLUTION, _symbol_spec(), config=resume_config, now_utc=2_000_000_000,
        resume_open_position=first.open_position, force_close_at_range_end=False,  # no resume_regime_tracker
    )
    without_resume_trades = sorted(
        (t.entry_time_utc, t.direction, round(t.realized_pnl, 6)) for t in without_resume.trades
    )
    # Document the actual divergence this fixes -- not just "close enough".
    assert without_resume_trades != reference_new


def test_run_backtest_never_lets_a_trade_close_before_it_opens():
    bars = _trending_bars(200)
    result = run_backtest(bars, CANONICAL_SYMBOL, RESOLUTION, _symbol_spec(), config=_config(), now_utc=2_000_000_000)
    for trade in result.trades:
        assert trade.exit_time_utc >= trade.entry_time_utc
