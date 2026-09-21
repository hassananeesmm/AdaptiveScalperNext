"""Tests for untouched-OOS validation (directive section 65)."""

from __future__ import annotations

import pytest

from adaptive_scalper.backtest.dataset import build_dataset_snapshot, record_dataset, record_dataset_usage
from adaptive_scalper.backtest.oos import DatasetContaminatedError, run_untouched_oos
from adaptive_scalper.backtest.types import BacktestConfig
from adaptive_scalper.gateway.types import Bar, SymbolSpec, SymbolTradeMode
from adaptive_scalper.persistence import connect, migrate
from adaptive_scalper.simulation.fill_model import FillAssumptions
from adaptive_scalper.simulation.types import EvidenceOrigin
from adaptive_scalper.strategies import build_active_registry

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


def test_run_untouched_oos_succeeds_on_a_genuinely_fresh_dataset(db):
    bars = _trending_bars(200)
    result = run_untouched_oos(
        bars, CANONICAL_SYMBOL, RESOLUTION, _symbol_spec(), conn=db, run_id="oos-1",
        config=_config(), now_utc=2_000_000_000,
    )
    assert result.canonical_symbol == CANONICAL_SYMBOL
    row = db.execute("SELECT run_type FROM backtest_runs WHERE run_id = 'oos-1'").fetchone()
    assert row["run_type"] == "OOS"


def test_run_untouched_oos_refuses_a_dataset_already_used_for_training(db):
    bars = _trending_bars(200)
    strategies = tuple(s.key for s in build_active_registry().all_active())
    snapshot = build_dataset_snapshot(
        bars, canonical_symbol=CANONICAL_SYMBOL, resolution=RESOLUTION, strategies=strategies,
        feature_schema_version=1, origin=EvidenceOrigin.BACKTEST, now_utc=2_000_000_000,
    )
    record_dataset(db, snapshot)
    record_dataset_usage(db, snapshot.dataset_id, "some-training-run", "TRAINING", now_utc=2_000_000_000)

    with pytest.raises(DatasetContaminatedError, match="TRAINING"):
        run_untouched_oos(
            bars, CANONICAL_SYMBOL, RESOLUTION, _symbol_spec(), conn=db, run_id="oos-2",
            config=_config(), now_utc=2_000_000_000,
        )


def test_run_untouched_oos_refuses_a_dataset_already_used_as_walk_forward_fold(db):
    bars = _trending_bars(200)
    strategies = tuple(s.key for s in build_active_registry().all_active())
    snapshot = build_dataset_snapshot(
        bars, canonical_symbol=CANONICAL_SYMBOL, resolution=RESOLUTION, strategies=strategies,
        feature_schema_version=1, origin=EvidenceOrigin.BACKTEST, now_utc=2_000_000_000,
    )
    record_dataset(db, snapshot)
    record_dataset_usage(db, snapshot.dataset_id, "some-wf-run", "WALK_FORWARD_FOLD", now_utc=2_000_000_000)

    with pytest.raises(DatasetContaminatedError, match="WALK_FORWARD_FOLD"):
        run_untouched_oos(
            bars, CANONICAL_SYMBOL, RESOLUTION, _symbol_spec(), conn=db, run_id="oos-3",
            config=_config(), now_utc=2_000_000_000,
        )


def test_run_untouched_oos_refuses_to_be_run_twice_on_the_same_dataset(db):
    bars = _trending_bars(200)
    run_untouched_oos(
        bars, CANONICAL_SYMBOL, RESOLUTION, _symbol_spec(), conn=db, run_id="oos-4a",
        config=_config(), now_utc=2_000_000_000,
    )
    with pytest.raises(DatasetContaminatedError, match="already run as OOS"):
        run_untouched_oos(
            bars, CANONICAL_SYMBOL, RESOLUTION, _symbol_spec(), conn=db, run_id="oos-4b",
            config=_config(), now_utc=2_000_001_000,
        )


def test_run_untouched_oos_allows_explicit_reuse_when_flagged(db):
    bars = _trending_bars(200)
    run_untouched_oos(
        bars, CANONICAL_SYMBOL, RESOLUTION, _symbol_spec(), conn=db, run_id="oos-5a",
        config=_config(), now_utc=2_000_000_000,
    )
    # Should NOT raise.
    result = run_untouched_oos(
        bars, CANONICAL_SYMBOL, RESOLUTION, _symbol_spec(), conn=db, run_id="oos-5b",
        config=_config(), allow_oos_reuse=True, now_utc=2_000_001_000,
    )
    assert result.canonical_symbol == CANONICAL_SYMBOL


def test_run_untouched_oos_does_not_block_on_an_unrelated_dataset(db):
    # A DIFFERENT bar range (different content -> different checksum ->
    # different dataset_id) used for TRAINING must not block THIS range.
    training_bars = _trending_bars(200, start_price=1000.0)
    strategies = tuple(s.key for s in build_active_registry().all_active())
    training_snapshot = build_dataset_snapshot(
        training_bars, canonical_symbol=CANONICAL_SYMBOL, resolution=RESOLUTION, strategies=strategies,
        feature_schema_version=1, origin=EvidenceOrigin.BACKTEST, now_utc=2_000_000_000,
    )
    record_dataset(db, training_snapshot)
    record_dataset_usage(db, training_snapshot.dataset_id, "some-training-run", "TRAINING", now_utc=2_000_000_000)

    oos_bars = _trending_bars(200, start_price=2000.0)
    result = run_untouched_oos(
        oos_bars, CANONICAL_SYMBOL, RESOLUTION, _symbol_spec(), conn=db, run_id="oos-6",
        config=_config(), now_utc=2_000_000_000,
    )
    assert result.canonical_symbol == CANONICAL_SYMBOL
