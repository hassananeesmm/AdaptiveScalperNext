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

from adaptive_scalper.backtest.engine import run_backtest
from adaptive_scalper.backtest.types import BacktestConfig
from adaptive_scalper.gateway.types import Bar, SymbolSpec, SymbolTradeMode
from adaptive_scalper.simulation.fill_model import FillAssumptions

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
    return BacktestConfig(**defaults)


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
    import pytest

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


def test_run_backtest_never_lets_a_trade_close_before_it_opens():
    bars = _trending_bars(200)
    result = run_backtest(bars, CANONICAL_SYMBOL, RESOLUTION, _symbol_spec(), config=_config(), now_utc=2_000_000_000)
    for trade in result.trades:
        assert trade.exit_time_utc >= trade.entry_time_utc
