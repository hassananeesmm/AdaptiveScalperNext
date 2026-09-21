"""Tests for causal training-dataset assembly (directive sections 62/64/65)."""

from __future__ import annotations

from adaptive_scalper.backtest.types import SimulatedTrade
from adaptive_scalper.features.bar_features import NUMERIC_FEATURE_FIELDS
from adaptive_scalper.learning.dataset import build_training_rows


def _closed_trade(**overrides) -> SimulatedTrade:
    defaults = dict(
        strategy_key="momentum_continuation", direction="BUY", entry_time_utc=1000, entry_price=2000.0,
        volume=0.1, initial_monetary_risk=20.0, entry_regime="TRENDING_UP", exit_time_utc=1100,
        exit_price=2005.0, exit_reason="TAKE_PROFIT_HIT", exit_regime="TRENDING_UP", realized_r=1.5,
        realized_pnl=30.0, total_cost=0.5,
        entry_features={name: 0.5 for name in NUMERIC_FEATURE_FIELDS}, entry_raw_confidence=0.7,
    )
    defaults.update(overrides)
    return SimulatedTrade(**defaults)


def test_build_training_rows_includes_a_fully_evidenced_closed_trade():
    rows, excluded = build_training_rows([_closed_trade()])
    assert len(rows) == 1
    assert excluded == 0
    assert rows[0].label == 1  # realized_pnl=30.0 > 0
    assert len(rows[0].features) == len(NUMERIC_FEATURE_FIELDS)


def test_build_training_rows_labels_a_loss_as_zero():
    rows, excluded = build_training_rows([_closed_trade(realized_pnl=-15.0)])
    assert rows[0].label == 0


def test_build_training_rows_excludes_an_unclosed_trade():
    rows, excluded = build_training_rows([_closed_trade(exit_time_utc=None, exit_price=None, realized_pnl=None, realized_r=None)])
    assert rows == []
    assert excluded == 1


def test_build_training_rows_excludes_a_trade_with_no_captured_features():
    rows, excluded = build_training_rows([_closed_trade(entry_features=None)])
    assert rows == []
    assert excluded == 1


def test_build_training_rows_excludes_a_trade_with_any_missing_feature_value():
    partial = {name: 0.5 for name in NUMERIC_FEATURE_FIELDS}
    partial[NUMERIC_FEATURE_FIELDS[0]] = None
    rows, excluded = build_training_rows([_closed_trade(entry_features=partial)])
    assert rows == []
    assert excluded == 1


def test_build_training_rows_mixed_batch_counts_correctly():
    trades = [
        _closed_trade(entry_time_utc=1000, realized_pnl=10.0),
        _closed_trade(entry_time_utc=1100, entry_features=None),
        _closed_trade(entry_time_utc=1200, realized_pnl=-5.0),
        _closed_trade(entry_time_utc=1300, exit_time_utc=None, exit_price=None, realized_pnl=None, realized_r=None),
    ]
    rows, excluded = build_training_rows(trades)
    assert len(rows) == 2
    assert excluded == 2
    assert [r.label for r in rows] == [1, 0]
