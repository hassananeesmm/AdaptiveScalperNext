"""STAGE 1 MODEL OBSERVER (directive section 61) for the live pipeline.

Scores a proposal's causal feature vector with the newest registered entry
model for that symbol (BASELINE/CHALLENGER/CURRENT/PREVIOUS_STABLE) and
returns the probability as EVIDENCE ONLY. Nothing in the runtime passes
this score to the selector, the cost/edge gate, the risk governor or final
permission -- the runtime journals it (`MODEL_USED`) and moves on. Stage 2
bounded influence is deliberately NOT implemented.

Every failure -- no model, insufficient-data model, checksum mismatch,
unloadable artifact, a feature the snapshot couldn't compute -- is a
status, never an exception: an unavailable observer can't affect trading.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from adaptive_scalper.learning.dataset import FEATURE_COLUMNS
from adaptive_scalper.learning.lifecycle import ModelLifecycleState
from adaptive_scalper.learning.registry import ModelRecord
from adaptive_scalper.learning.training import load_model_artifact

SCORED = "SCORED"
NO_MODEL = "NO_MODEL"
NOT_SCORABLE = "NOT_SCORABLE"
DEGRADED = "DEGRADED"

_SCORING_STATES = (
    ModelLifecycleState.CURRENT, ModelLifecycleState.CHALLENGER, ModelLifecycleState.BASELINE,
    ModelLifecycleState.PREVIOUS_STABLE,
)


@dataclass(frozen=True)
class ObserverScore:
    status: str
    model_key: str
    detail: str
    version: int | None = None
    lifecycle_state: str | None = None
    probability: float | None = None
    influence: str = "NONE (STAGE 1 OBSERVER)"


def entry_model_key(canonical_symbol: str) -> str:
    return f"entry_model:{canonical_symbol}"


def _latest_scoring_model(conn: sqlite3.Connection, model_key: str) -> ModelRecord | None:
    from adaptive_scalper.learning.registry import _row_to_model

    marks = ", ".join("?" * len(_SCORING_STATES))
    row = conn.execute(
        f"SELECT * FROM models WHERE model_key = ? AND lifecycle_state IN ({marks}) "
        "AND artifact_path IS NOT NULL ORDER BY version DESC LIMIT 1",
        (model_key, *(s.value for s in _SCORING_STATES)),
    ).fetchone()
    return _row_to_model(row) if row is not None else None


class EntryObserver:
    def __init__(self) -> None:
        self._cache: dict[tuple[str, int], object] = {}

    def score(self, conn: sqlite3.Connection, canonical_symbol: str, features: dict[str, float | None]) -> ObserverScore:
        key = entry_model_key(canonical_symbol)
        try:
            record = _latest_scoring_model(conn, key)
            if record is None:
                return ObserverScore(NO_MODEL, key, "no trained entry model registered for this symbol")
            values = [features.get(name) for name in FEATURE_COLUMNS]
            if any(v is None for v in values):
                missing = [n for n, v in zip(FEATURE_COLUMNS, values) if v is None]
                return ObserverScore(NOT_SCORABLE, key, f"features not computable: {missing}", record.version,
                                     record.lifecycle_state.value)
            cache_key = (key, record.version)
            if cache_key not in self._cache:
                self._cache[cache_key] = load_model_artifact(record.artifact_path, expected_checksum=record.artifact_checksum)
            model = self._cache[cache_key]
            probability = float(model.predict_proba([values])[0][1])
            return ObserverScore(SCORED, key, "observer score (no influence)", record.version,
                                 record.lifecycle_state.value, probability)
        except Exception as exc:
            return ObserverScore(DEGRADED, key, f"{type(exc).__name__}: {exc}")
