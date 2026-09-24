"""The real entry-model training job (completion directive Phase 6),
behind `adaptive-scalper learning train`.

    persisted trades (ONE origin: BACKTEST or PAPER, never pooled)
      -> causal feature rows (rows without captured features excluded)
      -> drop rows overlapping any reserved OOS range (untouched OOS stays untouched)
      -> model walk-forward (train -> purge/gap -> validate, forward in time)
      -> final retrain on the temporal training split
      -> register as BASELINE (or INSUFFICIENT_DATA) -- NEVER CURRENT
      -> append-only research trial (kind MODEL_WALK_FORWARD)
      -> promotion gate evaluated on the TRUE facts and REPORTED only

Training never implies promotion: nothing here transitions a model's
lifecycle state after registration, and the observer that scores
registered models has zero influence (Stage 2 is not implemented). The
report carries rollback evidence -- the previous version's walk-forward
skill next to this one's -- for the human who decides.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass

from adaptive_scalper.config.constants import ALLOWED_CANONICAL_SYMBOLS, RETIRED_STRATEGY_KEYS
from adaptive_scalper.learning.dataset import TrainingRow, build_training_rows_from_records
from adaptive_scalper.learning.lifecycle import ModelLifecycleState
from adaptive_scalper.learning.model_walk_forward import (
    DEFAULT_FOLDS,
    ModelWalkForwardResult,
    result_to_dict,
    run_model_walk_forward,
)
from adaptive_scalper.learning.observer import entry_model_key
from adaptive_scalper.learning.promotion import PromotionEvidence, evaluate_promotion_gate
from adaptive_scalper.learning.registry import ModelRecord
from adaptive_scalper.learning.training import (
    DEFAULT_MIN_TRAINING_SAMPLES,
    EntryModelTrainingResult,
    register_entry_model,
    train_entry_outcome_model,
)
from adaptive_scalper.research.ledger import record_trial
from adaptive_scalper.simulation.fill_model import COST_BROKER_DEMO_CONFIRMED

SOURCE_BACKTEST = "BACKTEST"
SOURCE_PAPER = "PAPER"
_SOURCE_QUERIES = {
    # untouched-OOS runs are never training data
    SOURCE_BACKTEST: ("SELECT t.* FROM backtest_trades t JOIN backtest_runs r ON r.run_id = t.run_id "
                      "WHERE t.canonical_symbol = ? AND r.run_type != 'OOS' ORDER BY t.entry_time_utc"),
    SOURCE_PAPER: "SELECT * FROM paper_trades WHERE canonical_symbol = ? ORDER BY entry_time_utc",
}
_OOS_PURPOSES = ("OOS", "OOS_ANALYSIS_REUSE")


class TrainingJobError(ValueError):
    """The job was asked for something it must refuse (symbol/source)."""


@dataclass(frozen=True)
class TrainingJobReport:
    model_key: str
    source: str
    candidate_rows: int
    excluded_rows: int
    oos_protected_rows: int
    retired_rows: int
    rows: int
    walk_forward: ModelWalkForwardResult
    training: EntryModelTrainingResult
    record: ModelRecord
    trial_id: str
    promotion_decision: str
    promotion_reason: str
    rollback: dict

    def summary(self) -> dict:
        wf = self.walk_forward
        return {
            "model_key": self.model_key, "source": self.source, "version": self.record.version,
            "lifecycle_state": self.record.lifecycle_state.value, "rows": self.rows,
            "excluded_rows": self.excluded_rows, "oos_protected_rows": self.oos_protected_rows,
            "retired_rows": self.retired_rows, "trained": self.training.trained, "reason": self.training.reason,
            "walk_forward": {"oos_rows": wf.oos_rows, "auc": wf.oos_auc, "brier": wf.oos_brier,
                             "brier_skill": wf.brier_skill, "ece": wf.ece, "beats_base_rate": wf.beats_base_rate,
                             "calibrated": wf.calibrated, "subgroups_stable": wf.subgroups_stable,
                             "detail": wf.detail},
            "trial_id": self.trial_id,
            "promotion_gate": {"decision": self.promotion_decision, "reason": self.promotion_reason,
                               "action_taken": "NONE (training never promotes; human review required)"},
            "rollback": self.rollback, "influence": "NONE (STAGE 1 OBSERVER)",
        }


def _oos_ranges(conn: sqlite3.Connection, canonical_symbol: str, start: int, end: int) -> list[tuple[int, int]]:
    from adaptive_scalper.backtest.dataset import find_overlapping_usage

    ranges = []
    for purpose in _OOS_PURPOSES:
        ranges += [(r["range_start_utc"], r["range_end_utc"])
                   for r in find_overlapping_usage(conn, canonical_symbol, start, end, purpose)]
    return ranges


def _previous_walk_forward(conn: sqlite3.Connection, model_key: str, below_version: int) -> dict:
    import json

    row = conn.execute(
        "SELECT version, lifecycle_state, metrics_json FROM models WHERE model_key = ? AND version < ? "
        "ORDER BY version DESC LIMIT 1", (model_key, below_version),
    ).fetchone()
    if row is None:
        return {"previous_version": None}
    metrics = json.loads(row["metrics_json"] or "{}")
    return {"previous_version": row["version"], "previous_state": row["lifecycle_state"],
            "previous_brier_skill": (metrics.get("walk_forward") or {}).get("brier_skill")}


def run_training_job(
    conn: sqlite3.Connection, *, canonical_symbol: str, artifact_dir: str, source: str = SOURCE_BACKTEST,
    n_folds: int = DEFAULT_FOLDS, gap_seconds: int = 0, min_samples: int = DEFAULT_MIN_TRAINING_SAMPLES,
    seed: int = 0, now_utc: int | None = None,
) -> TrainingJobReport:
    if canonical_symbol not in ALLOWED_CANONICAL_SYMBOLS:
        raise TrainingJobError(f"{canonical_symbol!r} is not an allowed canonical symbol")
    if source not in _SOURCE_QUERIES:
        raise TrainingJobError(f"source must be one of {sorted(_SOURCE_QUERIES)} (origins are never pooled)")
    now = now_utc if now_utc is not None else int(time.time())
    model_key = entry_model_key(canonical_symbol)

    records = conn.execute(_SOURCE_QUERIES[source], (canonical_symbol,)).fetchall()
    rows, excluded = build_training_rows_from_records(records)

    retired = [r for r in rows if r.strategy_key in RETIRED_STRATEGY_KEYS]
    rows = [r for r in rows if r.strategy_key not in RETIRED_STRATEGY_KEYS]

    protected = 0
    if rows:
        ranges = _oos_ranges(conn, canonical_symbol, min(r.entry_time_utc for r in rows),
                             max(r.label_end_utc for r in rows))
        kept: list[TrainingRow] = []
        for row in rows:
            if any(row.entry_time_utc <= end and row.label_end_utc >= start for start, end in ranges):
                protected += 1
            else:
                kept.append(row)
        rows = kept

    wf = run_model_walk_forward(rows, n_folds=n_folds, gap_seconds=gap_seconds,
                                min_train_rows=max(1, min_samples // 2), seed=seed)
    training = train_entry_outcome_model(rows, min_samples=min_samples, seed=seed)
    wf_metrics = {k: v for k, v in result_to_dict(wf).items() if k not in ("calibration", "subgroups")}
    record = register_entry_model(
        conn, training, model_key=model_key, strategy_key=None, artifact_dir=artifact_dir, total_row_count=len(rows),
        now_utc=now, extra_metrics={"source": source, "walk_forward": wf_metrics, "oos_protected_rows": protected},
    )
    if record.lifecycle_state not in (ModelLifecycleState.BASELINE, ModelLifecycleState.INSUFFICIENT_DATA):
        raise RuntimeError(f"training registered {record.lifecycle_state.value}; training must never promote")

    versions: dict[str, int] = {}
    for rec in records:
        if rec["strategy_version"] is not None and rec["strategy_key"] not in RETIRED_STRATEGY_KEYS:
            versions[rec["strategy_key"]] = max(versions.get(rec["strategy_key"], 0), rec["strategy_version"])
    trial_id = f"model-wf:{model_key}:v{record.version}:{now}"
    record_trial(
        conn, trial_id=trial_id, family=model_key, kind="MODEL_WALK_FORWARD", strategy_versions=versions,
        params={"source": source, "n_folds": n_folds, "gap_seconds": gap_seconds, "min_samples": min_samples,
                "seed": seed},
        status="COMPLETED" if wf.oos_rows > 0 else "FAILED", model="LogisticRegression",
        feature_set="NUMERIC_FEATURE_FIELDS", n_observations=wf.oos_rows, result=result_to_dict(wf),
        notes=wf.detail, now_utc=now,
    )

    rollback_target = conn.execute(
        "SELECT 1 FROM models WHERE model_key = ? AND lifecycle_state IN ('CURRENT', 'PREVIOUS_STABLE') LIMIT 1",
        (model_key,),
    ).fetchone() is not None
    evidence = PromotionEvidence(
        strategy_key=None, training_sample_count=len(rows), min_required_samples=min_samples,
        uses_causal_features=True,   # engine-captured decision-time snapshots only
        temporal_purged_split_verified=any(f.trained for f in wf.folds),
        walk_forward_verified=wf.oos_rows > 0 and wf.beats_base_rate,
        oos_untouched=False,         # this job never evaluates the reserved OOS; that is a separate, one-shot step
        realistic_costs_applied=bool(rows) and all(r.cost_provenance == COST_BROKER_DEMO_CONFIRMED for r in rows),
        calibration_checked=wf.calibrated, subgroup_stability_checked=wf.subgroups_stable,
        artifact_checksum=record.artifact_checksum, rollback_target_available=rollback_target,
    )
    decision, reason = evaluate_promotion_gate(evidence)
    rollback = _previous_walk_forward(conn, model_key, record.version)
    rollback.update({"this_brier_skill": wf.brier_skill, "rollback_target_available": rollback_target})
    return TrainingJobReport(
        model_key=model_key, source=source, candidate_rows=len(records), excluded_rows=excluded,
        oos_protected_rows=protected, retired_rows=len(retired), rows=len(rows), walk_forward=wf,
        training=training, record=record, trial_id=trial_id, promotion_decision=decision,
        promotion_reason=reason, rollback=rollback,
    )
