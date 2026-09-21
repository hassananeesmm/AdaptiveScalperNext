"""Tests for walk-forward validation (directive section 80)."""

from __future__ import annotations

import pytest

from adaptive_scalper.backtest.types import BacktestConfig
from adaptive_scalper.backtest.walk_forward import run_walk_forward
from adaptive_scalper.gateway.types import Bar, SymbolSpec, SymbolTradeMode
from adaptive_scalper.persistence import connect, migrate
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
    bars = []
    price = start_price
    for i in range(n):
        open_price = price
        close_price = price + step
        bars.append(Bar(
            time=start_time + i * 300, open=open_price, high=close_price + 0.05, low=open_price - 0.05,
            close=close_price, tick_volume=100, spread=10, real_volume=0,
        ))
        price = close_price
    return bars


def _config(**overrides) -> BacktestConfig:
    defaults = dict(fill_assumptions=FillAssumptions(slippage_price=0.0, commission_monetary_per_lot=0.0))
    defaults.update(overrides)
    return BacktestConfig(**defaults)


@pytest.fixture()
def db(tmp_path):
    conn = connect(tmp_path / "test.sqlite3")
    migrate(conn)
    yield conn
    conn.close()


def test_run_walk_forward_produces_the_requested_number_of_folds():
    bars = _trending_bars(500)
    result = run_walk_forward(bars, CANONICAL_SYMBOL, RESOLUTION, _symbol_spec(), config=_config(), n_folds=5, now_utc=2_000_000_000)
    assert len(result.folds) == 5
    for i, fold in enumerate(result.folds):
        assert fold.fold_index == i


def test_run_walk_forward_folds_are_strictly_forward_in_time_and_non_overlapping():
    bars = _trending_bars(500)
    result = run_walk_forward(bars, CANONICAL_SYMBOL, RESOLUTION, _symbol_spec(), config=_config(), n_folds=5, now_utc=2_000_000_000)
    for prev, cur in zip(result.folds, result.folds[1:]):
        assert prev.range_end_utc < cur.range_start_utc


def test_run_walk_forward_embargo_widens_the_gap_between_folds():
    bars = _trending_bars(500)
    no_embargo = run_walk_forward(bars, CANONICAL_SYMBOL, RESOLUTION, _symbol_spec(), config=_config(), n_folds=5, embargo_bars=0, now_utc=2_000_000_000)
    with_embargo = run_walk_forward(bars, CANONICAL_SYMBOL, RESOLUTION, _symbol_spec(), config=_config(), n_folds=5, embargo_bars=10, now_utc=2_000_000_000)
    gap_no_embargo = no_embargo.folds[1].range_start_utc - no_embargo.folds[0].range_end_utc
    gap_with_embargo = with_embargo.folds[1].range_start_utc - with_embargo.folds[0].range_end_utc
    assert gap_with_embargo > gap_no_embargo


def test_run_walk_forward_aggregate_metrics_pool_every_folds_trades():
    bars = _trending_bars(500)
    result = run_walk_forward(bars, CANONICAL_SYMBOL, RESOLUTION, _symbol_spec(), config=_config(), n_folds=5, now_utc=2_000_000_000)
    total_trades_across_folds = sum(len(f.result.trades) for f in result.folds)
    assert result.aggregate_metrics.trade_count == total_trades_across_folds


def test_run_walk_forward_rejects_too_few_folds():
    bars = _trending_bars(500)
    with pytest.raises(ValueError, match="n_folds"):
        run_walk_forward(bars, CANONICAL_SYMBOL, RESOLUTION, _symbol_spec(), config=_config(), n_folds=1)


def test_run_walk_forward_rejects_negative_embargo():
    bars = _trending_bars(500)
    with pytest.raises(ValueError, match="embargo_bars"):
        run_walk_forward(bars, CANONICAL_SYMBOL, RESOLUTION, _symbol_spec(), config=_config(), n_folds=2, embargo_bars=-1)


def test_run_walk_forward_rejects_too_few_bars_for_the_requested_folds():
    bars = _trending_bars(30)  # far too few for 5 folds of feature_lookback+3 each
    with pytest.raises(ValueError, match="below the minimum"):
        run_walk_forward(bars, CANONICAL_SYMBOL, RESOLUTION, _symbol_spec(), config=_config(), n_folds=5)


def test_run_walk_forward_persists_every_fold_as_walk_forward_fold(db):
    bars = _trending_bars(500)
    run_walk_forward(
        bars, CANONICAL_SYMBOL, RESOLUTION, _symbol_spec(), config=_config(), n_folds=5,
        conn=db, run_id_prefix="wf-test", now_utc=2_000_000_000,
    )
    rows = db.execute("SELECT run_id, run_type FROM backtest_runs ORDER BY run_id").fetchall()
    assert len(rows) == 5
    for row in rows:
        assert row["run_type"] == "WALK_FORWARD_FOLD"
        assert row["run_id"].startswith("wf-test:fold")

    usage_rows = db.execute("SELECT used_for FROM dataset_usage").fetchall()
    assert len(usage_rows) == 5
    assert all(r["used_for"] == "WALK_FORWARD_FOLD" for r in usage_rows)

    trade_rows = db.execute("SELECT COUNT(*) AS n FROM backtest_trades").fetchone()
    total_trades_across_folds = sum(len(f.result.trades) for f in run_walk_forward(
        bars, CANONICAL_SYMBOL, RESOLUTION, _symbol_spec(), config=_config(), n_folds=5, now_utc=2_000_000_000,
    ).folds)
    assert trade_rows["n"] == total_trades_across_folds


def test_run_walk_forward_persistence_is_idempotent_on_run_id(db):
    bars = _trending_bars(500)
    run_walk_forward(
        bars, CANONICAL_SYMBOL, RESOLUTION, _symbol_spec(), config=_config(), n_folds=5,
        conn=db, run_id_prefix="wf-test", now_utc=2_000_000_000,
    )
    run_walk_forward(
        bars, CANONICAL_SYMBOL, RESOLUTION, _symbol_spec(), config=_config(), n_folds=5,
        conn=db, run_id_prefix="wf-test", now_utc=2_000_000_000,
    )
    rows = db.execute("SELECT COUNT(*) AS n FROM backtest_runs").fetchone()
    assert rows["n"] == 5  # not 10 -- the second call's run_ids collide with the first
