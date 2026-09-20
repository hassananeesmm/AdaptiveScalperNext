"""Persisted model registry (directive: model lifecycle, retired
strategies excluded at training AND promotion).

Mirrors `execution.store`'s design: `register_model()` inserts the
initial lifecycle-state transition in the same transaction;
`transition_model_state()` is the ONLY way a model's state changes, and
always validates through `learning.lifecycle.apply_transition()` first.

Retired strategy keys (`config.constants.RETIRED_STRATEGY_KEYS`) are
refused HERE independently of any other layer (directive section 8's
defense-in-depth pattern, reused) — `register_model()` raises
`RetiredStrategyModelError` unconditionally, and `transition_model_state()`
refuses to promote (`-> CURRENT`) a model already tied to a retired key,
in case a key is retired AFTER a model was registered for it.
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass

from adaptive_scalper.config.constants import RETIRED_STRATEGY_KEYS
from adaptive_scalper.learning.lifecycle import ModelLifecycleState, apply_transition


class RetiredStrategyModelError(ValueError):
    """Raised when a model registration or promotion is attempted for a
    permanently retired strategy key. Never caught and routed around."""


@dataclass(frozen=True)
class ModelRecord:
    id: int
    model_key: str
    version: int
    strategy_key: str | None
    lifecycle_state: ModelLifecycleState
    artifact_path: str | None
    artifact_checksum: str | None
    training_sample_count: int
    metrics: dict | None
    created_at_utc: int
    updated_at_utc: int


def _row_to_model(row: sqlite3.Row) -> ModelRecord:
    return ModelRecord(
        id=row["id"], model_key=row["model_key"], version=row["version"], strategy_key=row["strategy_key"],
        lifecycle_state=ModelLifecycleState(row["lifecycle_state"]), artifact_path=row["artifact_path"],
        artifact_checksum=row["artifact_checksum"], training_sample_count=row["training_sample_count"],
        metrics=json.loads(row["metrics_json"]) if row["metrics_json"] else None,
        created_at_utc=row["created_at_utc"], updated_at_utc=row["updated_at_utc"],
    )


def get_model(conn: sqlite3.Connection, model_key: str, version: int) -> ModelRecord | None:
    row = conn.execute(
        "SELECT * FROM models WHERE model_key = ? AND version = ?", (model_key, version)
    ).fetchone()
    return _row_to_model(row) if row else None


def get_latest_version(conn: sqlite3.Connection, model_key: str) -> int:
    row = conn.execute(
        "SELECT COALESCE(MAX(version), 0) AS v FROM models WHERE model_key = ?", (model_key,)
    ).fetchone()
    return row["v"]


def get_current_model(conn: sqlite3.Connection, model_key: str) -> ModelRecord | None:
    row = conn.execute(
        "SELECT * FROM models WHERE model_key = ? AND lifecycle_state = ? ORDER BY version DESC LIMIT 1",
        (model_key, ModelLifecycleState.CURRENT.value),
    ).fetchone()
    return _row_to_model(row) if row else None


def register_model(
    conn: sqlite3.Connection,
    model_key: str,
    *,
    strategy_key: str | None = None,
    initial_state: ModelLifecycleState = ModelLifecycleState.BASELINE,
    artifact_path: str | None = None,
    artifact_checksum: str | None = None,
    training_sample_count: int = 0,
    metrics: dict | None = None,
    now_utc: int | None = None,
) -> ModelRecord:
    """Registers a NEW version for `model_key` (auto-incremented, never
    caller-supplied — avoids version collisions/races). Unconditionally
    refuses a retired `strategy_key`."""
    if strategy_key is not None and strategy_key in RETIRED_STRATEGY_KEYS:
        raise RetiredStrategyModelError(
            f"{strategy_key!r} is permanently retired (directive section 8) — no model may be registered for it"
        )

    now = now_utc if now_utc is not None else int(time.time())
    version = get_latest_version(conn, model_key) + 1
    conn.execute("BEGIN IMMEDIATE")
    try:
        cursor = conn.execute(
            """
            INSERT INTO models
                (model_key, version, strategy_key, lifecycle_state, artifact_path, artifact_checksum,
                 training_sample_count, metrics_json, created_at_utc, updated_at_utc)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                model_key, version, strategy_key, initial_state.value, artifact_path, artifact_checksum,
                training_sample_count, json.dumps(metrics, default=str) if metrics is not None else None, now, now,
            ),
        )
        model_id = cursor.lastrowid
        conn.execute(
            "INSERT INTO model_lifecycle_transitions (model_id, from_state, to_state, occurred_at_utc, detail) "
            "VALUES (?, NULL, ?, ?, ?)",
            (model_id, initial_state.value, now, "model registered"),
        )
        conn.execute("COMMIT")
    except sqlite3.Error:
        conn.execute("ROLLBACK")
        raise
    return get_model(conn, model_key, version)  # type: ignore[return-value]


def transition_model_state(
    conn: sqlite3.Connection,
    model_id: int,
    to_state: ModelLifecycleState,
    *,
    detail: str | None = None,
    metrics: dict | None = None,
    now_utc: int | None = None,
) -> ModelRecord:
    row = conn.execute("SELECT * FROM models WHERE id = ?", (model_id,)).fetchone()
    if row is None:
        raise ValueError(f"no model with id={model_id}")
    from_state = ModelLifecycleState(row["lifecycle_state"])

    if (
        to_state == ModelLifecycleState.CURRENT
        and row["strategy_key"] is not None
        and row["strategy_key"] in RETIRED_STRATEGY_KEYS
    ):
        raise RetiredStrategyModelError(
            f"{row['strategy_key']!r} is permanently retired — refusing to promote model id={model_id} to CURRENT"
        )

    apply_transition(from_state, to_state)  # raises InvalidLifecycleTransitionError if illegal

    now = now_utc if now_utc is not None else int(time.time())
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(
            "INSERT INTO model_lifecycle_transitions (model_id, from_state, to_state, occurred_at_utc, detail) "
            "VALUES (?, ?, ?, ?, ?)",
            (model_id, from_state.value, to_state.value, now, detail),
        )
        conn.execute(
            "UPDATE models SET lifecycle_state = ?, updated_at_utc = ?, "
            "metrics_json = COALESCE(?, metrics_json) WHERE id = ?",
            (to_state.value, now, json.dumps(metrics, default=str) if metrics is not None else None, model_id),
        )
        conn.execute("COMMIT")
    except sqlite3.Error:
        conn.execute("ROLLBACK")
        raise
    return get_model(conn, row["model_key"], row["version"])  # type: ignore[return-value]


def get_model_lifecycle_history(conn: sqlite3.Connection, model_id: int) -> list[tuple[str | None, str, int]]:
    rows = conn.execute(
        "SELECT from_state, to_state, occurred_at_utc FROM model_lifecycle_transitions "
        "WHERE model_id = ? ORDER BY occurred_at_utc, id",
        (model_id,),
    ).fetchall()
    return [(r["from_state"], r["to_state"], r["occurred_at_utc"]) for r in rows]
