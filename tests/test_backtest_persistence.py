"""Tests for durable backtest-run persistence (migration 0014_backtest)."""

from __future__ import annotations

import pytest

from adaptive_scalper.backtest.engine import run_backtest
from adaptive_scalper.backtest.persistence import record_backtest_run
from adaptive_scalper.backtest.types import BacktestConfig
from adaptive_scalper.gateway.types import Bar, SymbolSpec, SymbolTradeMode
from adaptive_scalper.persistence import connect, migrate
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
    return v1_replay_config(**defaults)


@pytest.fixture()
def db(tmp_path):
    conn = connect(tmp_path / "test.sqlite3")
    migrate(conn)
    yield conn
    conn.close()


def test_record_backtest_run_persists_dataset_run_and_trades(db):
    bars = _trending_bars(200)
    result = run_backtest(bars, CANONICAL_SYMBOL, RESOLUTION, _symbol_spec(), config=_config(), now_utc=2_000_000_000)
    assert len(result.trades) >= 1  # otherwise this test proves nothing about trade persistence

    record_backtest_run(
        db, result, bars, run_id="run-1", run_type="BACKTEST", used_for="VALIDATION",
        strategies=("momentum_continuation",), feature_schema_version=1, now_utc=2_000_000_000,
    )

    dataset_row = db.execute("SELECT dataset_id FROM datasets WHERE dataset_id = ?", (result.dataset_id,)).fetchone()
    assert dataset_row is not None

    usage_row = db.execute(
        "SELECT used_for FROM dataset_usage WHERE dataset_id = ? AND used_by_run_id = 'run-1'", (result.dataset_id,)
    ).fetchone()
    assert usage_row["used_for"] == "VALIDATION"

    run_row = db.execute("SELECT trade_count, net_pnl, origin FROM backtest_runs WHERE run_id = 'run-1'").fetchone()
    assert run_row["trade_count"] == len(result.trades)
    assert run_row["net_pnl"] == pytest.approx(result.metrics.net_pnl)
    assert run_row["origin"] == result.origin.value

    trade_rows = db.execute("SELECT COUNT(*) AS n FROM backtest_trades WHERE run_id = 'run-1'").fetchone()
    assert trade_rows["n"] == len(result.trades)


def test_record_backtest_run_is_idempotent_on_run_id(db):
    bars = _trending_bars(200)
    result = run_backtest(bars, CANONICAL_SYMBOL, RESOLUTION, _symbol_spec(), config=_config(), now_utc=2_000_000_000)

    record_backtest_run(
        db, result, bars, run_id="run-2", run_type="BACKTEST", used_for="VALIDATION",
        strategies=("momentum_continuation",), feature_schema_version=1, now_utc=2_000_000_000,
    )
    record_backtest_run(
        db, result, bars, run_id="run-2", run_type="BACKTEST", used_for="VALIDATION",
        strategies=("momentum_continuation",), feature_schema_version=1, now_utc=2_000_000_000,
    )

    run_rows = db.execute("SELECT COUNT(*) AS n FROM backtest_runs WHERE run_id = 'run-2'").fetchone()
    assert run_rows["n"] == 1
    trade_rows = db.execute("SELECT COUNT(*) AS n FROM backtest_trades WHERE run_id = 'run-2'").fetchone()
    assert trade_rows["n"] == len(result.trades)


def test_record_backtest_run_reuses_an_existing_dataset_row_for_the_same_bars(db):
    bars = _trending_bars(200)
    result_a = run_backtest(bars, CANONICAL_SYMBOL, RESOLUTION, _symbol_spec(), config=_config(), now_utc=2_000_000_000)
    result_b = run_backtest(bars, CANONICAL_SYMBOL, RESOLUTION, _symbol_spec(), config=_config(), now_utc=2_000_001_000)
    assert result_a.dataset_id == result_b.dataset_id  # same bars -> same content checksum

    record_backtest_run(
        db, result_a, bars, run_id="run-3a", run_type="BACKTEST", used_for="VALIDATION",
        strategies=("momentum_continuation",), feature_schema_version=1, now_utc=2_000_000_000,
    )
    record_backtest_run(
        db, result_b, bars, run_id="run-3b", run_type="BACKTEST", used_for="TRAINING",
        strategies=("momentum_continuation",), feature_schema_version=1, now_utc=2_000_001_000,
    )

    dataset_rows = db.execute("SELECT COUNT(*) AS n FROM datasets WHERE dataset_id = ?", (result_a.dataset_id,)).fetchone()
    assert dataset_rows["n"] == 1  # one dataset row, not two

    usage_rows = db.execute("SELECT used_for FROM dataset_usage WHERE dataset_id = ? ORDER BY used_for", (result_a.dataset_id,)).fetchall()
    assert [r["used_for"] for r in usage_rows] == ["TRAINING", "VALIDATION"]  # both usages recorded independently
