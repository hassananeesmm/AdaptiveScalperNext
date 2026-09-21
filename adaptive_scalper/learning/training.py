"""CPU-friendly, causal, temporal-split ML training (directive sections
60-66).

Trains a `LogisticRegression` "probability of a positive net outcome
after costs" entry model (directive section 64's suggested CPU-friendly,
auditable model family) on `learning.dataset.TrainingRow` evidence built
from REAL backtest/walk-forward trade outcomes — never synthetic labels.

Evaluation uses a strictly TEMPORAL split: rows sorted by
`entry_time_utc`, the earliest `1 - validation_fraction` fraction trains,
the latest `validation_fraction` fraction validates, with an optional
`embargo_rows` purge gap dropped between them. Never a random/shuffled
split, which would leak future rows into the training set for a
time-ordered problem (directive section 65's "temporal/purged split").

STAGE 1 MODEL OBSERVER ONLY (directive section 61): this module produces
a trained artifact and honest validation metrics, and
`register_entry_model()` records it in the model registry at
`INSUFFICIENT_DATA` or `BASELINE` — NEVER `CURRENT`. It does not wire the
model into any live/backtest decision path and grants it zero selector or
risk influence. Promoting a model to `CURRENT` requires an independent,
later `learning.promotion.evaluate_promotion_gate()` pass over a formal
challenger validation (directive section 61's STAGE 3) — training success
alone is never sufficient for promotion, and this module does not attempt
it.
"""

from __future__ import annotations

import hashlib
import io
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from adaptive_scalper.backtest.types import SimulatedTrade
from adaptive_scalper.learning.dataset import FEATURE_COLUMNS, TrainingRow, build_training_rows
from adaptive_scalper.learning.lifecycle import ModelLifecycleState
from adaptive_scalper.learning.registry import ModelRecord, get_latest_version, register_model

# Directive section 62: "Do not hard-code an unrealistically tiny
# 'learning complete' sample" — 200 REAL closed trades is a conservative
# floor for a two-class logistic model with ~17 numeric features, not a
# claim that this is sufficient for a validated, promotable model (that
# additionally requires the full promotion gate).
DEFAULT_MIN_TRAINING_SAMPLES = 200
DEFAULT_VALIDATION_FRACTION = 0.2

REASON_INSUFFICIENT_SAMPLES = "insufficient_samples"
REASON_SINGLE_CLASS = "single_outcome_class_in_split"
REASON_TRAINED = "trained"


@dataclass(frozen=True)
class EntryModelTrainingResult:
    trained: bool
    reason: str
    feature_columns: tuple[str, ...]
    train_row_count: int
    validation_row_count: int
    validation_accuracy: float | None = None
    validation_auc: float | None = None
    validation_brier_score: float | None = None
    artifact_checksum: str | None = None
    artifact_bytes: bytes | None = None
    model: object = None  # fitted sklearn estimator, or None if not trained


def train_entry_outcome_model(
    rows: list[TrainingRow],
    *,
    feature_columns: tuple[str, ...] = FEATURE_COLUMNS,
    min_samples: int = DEFAULT_MIN_TRAINING_SAMPLES,
    validation_fraction: float = DEFAULT_VALIDATION_FRACTION,
    embargo_rows: int = 0,
    seed: int = 0,
) -> EntryModelTrainingResult:
    if not (0.0 < validation_fraction < 1.0):
        raise ValueError(f"validation_fraction must be in (0, 1), got {validation_fraction!r}")
    if embargo_rows < 0:
        raise ValueError(f"embargo_rows must be >= 0, got {embargo_rows!r}")

    if len(rows) < min_samples:
        return EntryModelTrainingResult(
            trained=False, reason=(
                f"{REASON_INSUFFICIENT_SAMPLES}: {len(rows)} rows < min_samples={min_samples}"
            ),
            feature_columns=feature_columns, train_row_count=0, validation_row_count=0,
        )

    ordered = sorted(rows, key=lambda r: r.entry_time_utc)
    n_val = max(1, round(len(ordered) * validation_fraction))
    val_rows = ordered[len(ordered) - n_val:]
    train_rows = ordered[: max(0, len(ordered) - n_val - embargo_rows)]

    if len(train_rows) < min_samples - n_val or not train_rows or not val_rows:
        return EntryModelTrainingResult(
            trained=False, reason=(
                f"{REASON_INSUFFICIENT_SAMPLES}: temporal split leaves only "
                f"{len(train_rows)} train / {len(val_rows)} validation rows"
            ),
            feature_columns=feature_columns, train_row_count=len(train_rows), validation_row_count=len(val_rows),
        )

    x_train = [list(r.features) for r in train_rows]
    y_train = [r.label for r in train_rows]
    x_val = [list(r.features) for r in val_rows]
    y_val = [r.label for r in val_rows]

    if len(set(y_train)) < 2:
        return EntryModelTrainingResult(
            trained=False, reason=f"{REASON_SINGLE_CLASS}: training split has only one outcome class",
            feature_columns=feature_columns, train_row_count=len(train_rows), validation_row_count=len(val_rows),
        )

    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import accuracy_score, brier_score_loss, roc_auc_score

    model = LogisticRegression(max_iter=1000, random_state=seed)
    model.fit(x_train, y_train)

    proba = model.predict_proba(x_val)[:, 1]
    preds = model.predict(x_val)
    accuracy = accuracy_score(y_val, preds)
    brier = brier_score_loss(y_val, proba)
    auc = roc_auc_score(y_val, proba) if len(set(y_val)) > 1 else None

    buf = io.BytesIO()
    import joblib
    joblib.dump(model, buf)
    artifact_bytes = buf.getvalue()
    checksum = hashlib.sha256(artifact_bytes).hexdigest()

    return EntryModelTrainingResult(
        trained=True, reason=REASON_TRAINED, feature_columns=feature_columns,
        train_row_count=len(train_rows), validation_row_count=len(val_rows),
        validation_accuracy=accuracy, validation_auc=auc, validation_brier_score=brier,
        artifact_checksum=checksum, artifact_bytes=artifact_bytes, model=model,
    )


_UNSAFE_FILENAME_CHARS = re.compile(r'[:<>"/\\|?*\x00-\x1f]')


def _safe_filename_component(value: str) -> str:
    """`model_key` may legitimately contain `:` (this codebase's usual
    compound-key separator, e.g. `entry_model:XAUUSD`) — none of the
    other reserved Windows filename characters are expected, but this
    replaces the full reserved set defensively, not just `:`, so a
    future model_key convention can't silently reproduce this bug on
    Windows (`OSError: [Errno 22] Invalid argument` for `:` alone)."""
    return _UNSAFE_FILENAME_CHARS.sub("_", value)


def save_model_artifact(artifact_bytes: bytes, path: str) -> str:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(artifact_bytes)
    return str(p)


def load_model_artifact(path: str, *, expected_checksum: str | None = None) -> object:
    """`joblib.load()` deserializes via pickle, which can execute
    arbitrary code for an attacker-controlled file -- safe HERE only
    because this loads exclusively from `artifact_dir`, a path this same
    process (or a prior run of this same codebase) wrote via
    `save_model_artifact()`, never an externally-supplied or
    user-uploaded file. `expected_checksum` (from the model registry, set
    at training time) is checked BEFORE deserializing, so even a locally
    corrupted/tampered artifact is rejected before `joblib.load()` ever
    runs on it."""
    data = Path(path).read_bytes()
    if expected_checksum is not None:
        actual = hashlib.sha256(data).hexdigest()
        if actual != expected_checksum:
            raise ValueError(f"artifact at {path!r} checksum {actual} does not match expected {expected_checksum}")
    import joblib
    return joblib.load(io.BytesIO(data))


def register_entry_model(
    conn: sqlite3.Connection,
    result: EntryModelTrainingResult,
    *,
    model_key: str,
    strategy_key: str | None,
    artifact_dir: str,
    total_row_count: int,
    now_utc: int | None = None,
) -> ModelRecord:
    """Registers a NEW version for `model_key` — `INSUFFICIENT_DATA` if
    `result.trained` is False, otherwise `BASELINE` (never `CURRENT`; see
    module docstring). `artifact_dir/<model_key>_v<version>.joblib` is
    only written when a real model was trained."""
    metrics = {
        "reason": result.reason,
        "train_row_count": result.train_row_count,
        "validation_row_count": result.validation_row_count,
        "validation_accuracy": result.validation_accuracy,
        "validation_auc": result.validation_auc,
        "validation_brier_score": result.validation_brier_score,
        "feature_columns": list(result.feature_columns),
    }

    if not result.trained:
        return register_model(
            conn, model_key, strategy_key=strategy_key, initial_state=ModelLifecycleState.INSUFFICIENT_DATA,
            training_sample_count=total_row_count, metrics=metrics, now_utc=now_utc,
        )

    next_version = get_latest_version(conn, model_key) + 1
    artifact_path = save_model_artifact(
        result.artifact_bytes,
        str(Path(artifact_dir) / f"{_safe_filename_component(model_key)}_v{next_version}.joblib"),
    )
    return register_model(
        conn, model_key, strategy_key=strategy_key, initial_state=ModelLifecycleState.BASELINE,
        artifact_path=artifact_path, artifact_checksum=result.artifact_checksum,
        training_sample_count=total_row_count, metrics=metrics, now_utc=now_utc,
    )


def train_and_register_entry_model_from_trades(
    conn: sqlite3.Connection,
    trades: list[SimulatedTrade],
    *,
    model_key: str,
    strategy_key: str | None,
    artifact_dir: str,
    min_samples: int = DEFAULT_MIN_TRAINING_SAMPLES,
    validation_fraction: float = DEFAULT_VALIDATION_FRACTION,
    embargo_rows: int = 0,
    seed: int = 0,
    now_utc: int | None = None,
) -> tuple[ModelRecord, EntryModelTrainingResult, int]:
    """End-to-end convenience: real backtest/walk-forward
    `SimulatedTrade`s in, a registered `ModelRecord` out. Returns
    `(record, training_result, excluded_row_count)` so a caller (CLI
    command, scheduled job) can report exactly how much evidence was
    excluded and why training did or didn't succeed, never just the
    final registry row in isolation."""
    rows, excluded_row_count = build_training_rows(trades)
    result = train_entry_outcome_model(
        rows, min_samples=min_samples, validation_fraction=validation_fraction,
        embargo_rows=embargo_rows, seed=seed,
    )
    record = register_entry_model(
        conn, result, model_key=model_key, strategy_key=strategy_key, artifact_dir=artifact_dir,
        total_row_count=len(rows), now_utc=now_utc,
    )
    return record, result, excluded_row_count
