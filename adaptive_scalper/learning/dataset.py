"""Causal training-dataset assembly from REAL trade evidence (directive
sections 62/64/65).

Never a synthetic/fabricated label: every row's label is derived directly
from a `SimulatedTrade`'s actual realized outcome (from a real
`run_backtest()`/`run_walk_forward()` run over real historical bars), and
every row's feature vector is the EXACT causal snapshot
(`backtest.engine`'s `numeric_feature_vector()` capture, wired at entry
time) the strategy actually used to decide that entry — never
recomputed after the fact from data the strategy couldn't have seen.

A trade missing its captured features, or with ANY `None` value in its
feature vector, is EXCLUDED from the dataset rather than imputed
(directive's "insufficient evidence is honest N/A" pattern) — `
build_training_rows()` returns the excluded count alongside the rows so
that count is never silently lost (feeds `DatasetSnapshot
.excluded_row_count`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from adaptive_scalper.backtest.types import SimulatedTrade
from adaptive_scalper.features.bar_features import NUMERIC_FEATURE_FIELDS

FEATURE_COLUMNS: tuple[str, ...] = NUMERIC_FEATURE_FIELDS

LABEL_POSITIVE_NET_OUTCOME = "positive_net_outcome"


@dataclass(frozen=True)
class TrainingRow:
    entry_time_utc: int
    strategy_key: str
    entry_regime: str
    features: tuple[float, ...]  # positionally aligned with FEATURE_COLUMNS
    label: int  # 1 if realized_pnl (after costs) > 0, else 0
    realized_r: float | None
    realized_pnl: float


def build_training_rows(
    trades: Iterable[SimulatedTrade], *, feature_columns: tuple[str, ...] = FEATURE_COLUMNS,
) -> tuple[list[TrainingRow], int]:
    """Returns `(rows, excluded_row_count)`. A trade is excluded when it
    never closed, has no `realized_pnl`, has no captured `entry_features`
    at all, or is missing even one of `feature_columns` (a `None` value
    for a field that engine `numeric_feature_vector()` genuinely couldn't
    compute at that point)."""
    rows: list[TrainingRow] = []
    excluded = 0
    for trade in trades:
        if not trade.is_closed or trade.realized_pnl is None or trade.entry_features is None:
            excluded += 1
            continue
        values = [trade.entry_features.get(name) for name in feature_columns]
        if any(v is None for v in values):
            excluded += 1
            continue
        rows.append(TrainingRow(
            entry_time_utc=trade.entry_time_utc, strategy_key=trade.strategy_key,
            entry_regime=trade.entry_regime, features=tuple(values),
            label=1 if trade.realized_pnl > 0 else 0,
            realized_r=trade.realized_r, realized_pnl=trade.realized_pnl,
        ))
    return rows, excluded
