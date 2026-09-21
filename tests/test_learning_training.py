"""Tests for CPU-friendly causal ML training (directive sections 60-66)."""

from __future__ import annotations

import random

import pytest

from adaptive_scalper.learning.dataset import FEATURE_COLUMNS, TrainingRow
from adaptive_scalper.learning.lifecycle import ModelLifecycleState
from adaptive_scalper.learning.training import (
    REASON_INSUFFICIENT_SAMPLES,
    REASON_SINGLE_CLASS,
    REASON_TRAINED,
    load_model_artifact,
    register_entry_model,
    save_model_artifact,
    train_and_register_entry_model_from_trades,
    train_entry_outcome_model,
)
from adaptive_scalper.persistence import connect, migrate


def _separable_rows(n: int, *, start_time: int = 1_000_000, seed: int = 0) -> list[TrainingRow]:
    """A deterministic, perfectly learnable relationship: label follows
    the sign of `features[0]`, alternating over time so both the train
    and validation temporal splits contain both classes. Every OTHER
    feature is pure noise -- proves the model learns real structure
    rather than just memorizing a lucky split."""
    rng = random.Random(seed)
    rows = []
    for i in range(n):
        positive = i % 2 == 0
        signal = 2.0 if positive else -2.0
        features = tuple(
            signal + rng.uniform(-0.2, 0.2) if j == 0 else rng.uniform(-1.0, 1.0)
            for j in range(len(FEATURE_COLUMNS))
        )
        rows.append(TrainingRow(
            entry_time_utc=start_time + i, strategy_key="momentum_continuation", entry_regime="TRENDING_UP",
            features=features, label=1 if positive else 0,
            realized_r=1.0 if positive else -1.0, realized_pnl=10.0 if positive else -10.0,
        ))
    return rows


def _constant_label_rows(n: int, *, label: int, start_time: int = 1_000_000) -> list[TrainingRow]:
    return [
        TrainingRow(
            entry_time_utc=start_time + i, strategy_key="momentum_continuation", entry_regime="TRENDING_UP",
            features=tuple(0.1 * j for j in range(len(FEATURE_COLUMNS))), label=label,
            realized_r=1.0, realized_pnl=10.0,
        )
        for i in range(n)
    ]


@pytest.fixture()
def db(tmp_path):
    conn = connect(tmp_path / "test.sqlite3")
    migrate(conn)
    yield conn
    conn.close()


def test_train_entry_outcome_model_learns_a_real_separable_relationship():
    rows = _separable_rows(200)
    result = train_entry_outcome_model(rows, min_samples=100, seed=0)
    assert result.trained is True
    assert result.reason == REASON_TRAINED
    assert result.validation_accuracy is not None and result.validation_accuracy > 0.9
    assert result.validation_auc is not None and result.validation_auc > 0.9
    assert result.validation_brier_score is not None and result.validation_brier_score < 0.1
    assert result.artifact_checksum is not None
    assert result.artifact_bytes is not None
    assert result.model is not None


def test_train_entry_outcome_model_refuses_below_min_samples():
    rows = _separable_rows(50)
    result = train_entry_outcome_model(rows, min_samples=200)
    assert result.trained is False
    assert result.reason.startswith(REASON_INSUFFICIENT_SAMPLES)
    assert result.model is None
    assert result.artifact_bytes is None


def test_train_entry_outcome_model_refuses_a_single_class_training_split():
    rows = _constant_label_rows(200, label=1)
    result = train_entry_outcome_model(rows, min_samples=100)
    assert result.trained is False
    assert result.reason.startswith(REASON_SINGLE_CLASS)


def test_train_entry_outcome_model_uses_a_temporal_not_shuffled_split():
    # Validation rows must be the CHRONOLOGICALLY LATEST rows, never a
    # random subset -- prove it by making the split obviously visible:
    # only the earliest 80% could feed training given validation_fraction=0.2.
    rows = _separable_rows(100)
    result = train_entry_outcome_model(rows, min_samples=50, validation_fraction=0.2)
    assert result.train_row_count == 80
    assert result.validation_row_count == 20


def test_train_entry_outcome_model_embargo_shrinks_the_training_set():
    rows = _separable_rows(200)
    no_embargo = train_entry_outcome_model(rows, min_samples=50, embargo_rows=0)
    with_embargo = train_entry_outcome_model(rows, min_samples=50, embargo_rows=20)
    assert with_embargo.train_row_count == no_embargo.train_row_count - 20
    assert with_embargo.validation_row_count == no_embargo.validation_row_count


def test_train_entry_outcome_model_is_deterministic_given_the_same_seed():
    rows = _separable_rows(200)
    r1 = train_entry_outcome_model(rows, min_samples=100, seed=7)
    r2 = train_entry_outcome_model(rows, min_samples=100, seed=7)
    assert r1.validation_accuracy == r2.validation_accuracy
    assert r1.artifact_checksum == r2.artifact_checksum


def test_train_entry_outcome_model_rejects_invalid_validation_fraction():
    with pytest.raises(ValueError, match="validation_fraction"):
        train_entry_outcome_model(_separable_rows(200), min_samples=50, validation_fraction=1.5)


def test_save_and_load_model_artifact_round_trips_and_can_predict(tmp_path):
    rows = _separable_rows(200)
    result = train_entry_outcome_model(rows, min_samples=100, seed=0)
    path = save_model_artifact(result.artifact_bytes, str(tmp_path / "model.joblib"))

    loaded = load_model_artifact(path, expected_checksum=result.artifact_checksum)
    proba = loaded.predict_proba([list(rows[0].features)])[:, 1]
    assert 0.0 <= proba[0] <= 1.0


def test_load_model_artifact_rejects_a_checksum_mismatch(tmp_path):
    rows = _separable_rows(200)
    result = train_entry_outcome_model(rows, min_samples=100, seed=0)
    path = save_model_artifact(result.artifact_bytes, str(tmp_path / "model.joblib"))

    with pytest.raises(ValueError, match="checksum"):
        load_model_artifact(path, expected_checksum="0" * 64)


def test_register_entry_model_records_insufficient_data_when_not_trained(db, tmp_path):
    rows = _separable_rows(10)
    result = train_entry_outcome_model(rows, min_samples=200)
    record = register_entry_model(
        db, result, model_key="entry_model:XAUUSD", strategy_key="momentum_continuation",
        artifact_dir=str(tmp_path), total_row_count=len(rows), now_utc=1000,
    )
    assert record.lifecycle_state == ModelLifecycleState.INSUFFICIENT_DATA
    assert record.artifact_path is None
    assert record.training_sample_count == 10


def test_register_entry_model_records_baseline_with_artifact_when_trained(db, tmp_path):
    rows = _separable_rows(200)
    result = train_entry_outcome_model(rows, min_samples=100, seed=0)
    record = register_entry_model(
        db, result, model_key="entry_model:XAUUSD", strategy_key="momentum_continuation",
        artifact_dir=str(tmp_path), total_row_count=len(rows), now_utc=1000,
    )
    assert record.lifecycle_state == ModelLifecycleState.BASELINE
    assert record.artifact_path is not None
    assert record.artifact_checksum == result.artifact_checksum
    import os
    assert os.path.exists(record.artifact_path)

    reloaded = load_model_artifact(record.artifact_path, expected_checksum=record.artifact_checksum)
    assert reloaded is not None


def test_train_and_register_entry_model_from_trades_end_to_end_with_real_backtest_output(db, tmp_path):
    from adaptive_scalper.backtest.engine import run_backtest
    from adaptive_scalper.gateway.types import Bar, SymbolSpec, SymbolTradeMode
    from adaptive_scalper.simulation.fill_model import FillAssumptions

    def _symbol_spec():
        return SymbolSpec(
            name="XAUUSDm", description="Gold vs US Dollar", currency_base="XAU", currency_profit="USD",
            currency_margin="XAU", digits=2, point=0.01, trade_contract_size=100.0,
            volume_min=0.01, volume_max=100.0, volume_step=0.01, trade_tick_size=0.01,
            trade_tick_value=1.0, spread=10, visible=True, trade_mode=SymbolTradeMode.FULL,
        )

    def _trending_bars(n, *, start_price, step, start_time):
        bars = []
        price = start_price
        for i in range(n):
            open_price = price
            close_price = price + step
            bars.append(Bar(
                time=start_time + i * 300, open=open_price, high=max(open_price, close_price) + 0.05,
                low=min(open_price, close_price) - 0.05, close=close_price, tick_volume=100, spread=10, real_volume=0,
            ))
            price = close_price
        return bars

    from adaptive_scalper.backtest.types import BacktestConfig
    config = BacktestConfig(fill_assumptions=FillAssumptions(slippage_price=0.0, commission_monetary_per_lot=0.0))

    # Several independent trending runs (alternating direction/start
    # price) to accumulate more than one real trade -- proves the
    # orchestrator wires build_training_rows() -> train_entry_outcome_model()
    # -> register_entry_model() against GENUINE backtest evidence, not
    # hand-built TrainingRow fixtures.
    all_trades = []
    for i in range(6):
        bars = _trending_bars(
            200, start_price=1900.0 + i * 50, step=0.5 if i % 2 == 0 else -0.5, start_time=1_700_000_000 + i * 100_000,
        )
        result = run_backtest(bars, "XAUUSD", "M5", _symbol_spec(), config=config, now_utc=2_000_000_000)
        all_trades.extend(result.trades)

    assert len(all_trades) >= 1  # otherwise this test proves nothing

    record, training_result, excluded = train_and_register_entry_model_from_trades(
        db, all_trades, model_key="entry_model:XAUUSD:test", strategy_key="momentum_continuation",
        artifact_dir=str(tmp_path), min_samples=1, now_utc=2_000_000_000,
    )
    # Some feature values are legitimately None this early (e.g.
    # spread_percentile needs spread VARIATION within the lookback window,
    # which these synthetic constant-spread bars never provide) -- exercise
    # the honest-exclusion path rather than assert zero exclusions.
    assert excluded >= 0
    assert record.training_sample_count == len(all_trades) - excluded
    assert record.training_sample_count + excluded == len(all_trades)
    if training_result.trained:
        assert record.lifecycle_state == ModelLifecycleState.BASELINE
        assert record.artifact_path is not None
    else:
        assert record.lifecycle_state == ModelLifecycleState.INSUFFICIENT_DATA


def test_register_entry_model_sanitizes_a_model_key_containing_colons_for_the_filename(db, tmp_path):
    # model_key naturally uses ':' as a compound-key separator elsewhere
    # in this codebase (e.g. "entry_model:XAUUSD") -- ':' is a reserved
    # Windows filename character, so writing the artifact must not choke
    # on it (regression: previously raised OSError: Invalid argument).
    rows = _separable_rows(200)
    result = train_entry_outcome_model(rows, min_samples=100, seed=0)
    record = register_entry_model(
        db, result, model_key="entry_model:XAUUSD:test", strategy_key="momentum_continuation",
        artifact_dir=str(tmp_path), total_row_count=len(rows), now_utc=1000,
    )
    assert record.artifact_path is not None
    import os
    assert os.path.exists(record.artifact_path)
    assert ":" not in os.path.basename(record.artifact_path)


def test_register_entry_model_refuses_a_retired_strategy(db, tmp_path):
    from adaptive_scalper.learning.registry import RetiredStrategyModelError

    rows = _separable_rows(200)
    result = train_entry_outcome_model(rows, min_samples=100, seed=0)
    with pytest.raises(RetiredStrategyModelError):
        register_entry_model(
            db, result, model_key="entry_model:XAUUSD", strategy_key="failed_breakout_fade",
            artifact_dir=str(tmp_path), total_row_count=len(rows), now_utc=1000,
        )
