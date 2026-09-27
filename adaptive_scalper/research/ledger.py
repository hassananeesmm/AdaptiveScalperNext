"""Research trial ledger (migration `0021_research_trials`).

`record_trial()` is append-only (enforced by triggers). `family_trial_count()`
and `family_sharpe_variance()` feed `stats.deflated_sharpe_ratio()` so the
deflation uses every trial that competed for the same selection, not just
the one being reported.
"""

from __future__ import annotations

import json
import sqlite3
import statistics
import time
from dataclasses import dataclass

TRIAL_STATUSES = frozenset({"COMPLETED", "FAILED", "ABANDONED"})


@dataclass(frozen=True)
class TrialRecord:
    trial_id: str
    family: str
    kind: str
    status: str
    sharpe: float | None
    n_observations: int | None
    created_at_utc: int


def record_trial(
    conn: sqlite3.Connection, *, trial_id: str, family: str, kind: str, strategy_versions: dict[str, int],
    params: dict, status: str, feature_set: str | None = None, model: str | None = None,
    dataset_id: str | None = None, split_config: dict | None = None, cost_model: dict | None = None,
    sharpe: float | None = None, n_observations: int | None = None, result: dict | None = None,
    notes: str | None = None, now_utc: int | None = None,
) -> None:
    if status not in TRIAL_STATUSES:
        raise ValueError(f"status must be one of {sorted(TRIAL_STATUSES)}, got {status!r}")
    now = now_utc if now_utc is not None else int(time.time())

    def dump(value):
        return json.dumps(value, sort_keys=True) if value is not None else None

    conn.execute(
        "INSERT INTO research_trials (trial_id, family, created_at_utc, kind, strategy_versions_json, feature_set, "
        "model, params_json, dataset_id, split_config_json, cost_model_json, status, sharpe, n_observations, "
        "result_json, notes) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            trial_id, family, now, kind, dump(strategy_versions), feature_set, model, dump(params), dataset_id,
            dump(split_config), dump(cost_model), status, sharpe, n_observations, dump(result), notes,
        ),
    )
    conn.commit()


def list_trials(conn: sqlite3.Connection, family: str | None = None) -> list[TrialRecord]:
    sql = "SELECT * FROM research_trials" + (" WHERE family = ?" if family else "") + " ORDER BY created_at_utc, id"
    rows = conn.execute(sql, (family,) if family else ()).fetchall()
    return [
        TrialRecord(r["trial_id"], r["family"], r["kind"], r["status"], r["sharpe"], r["n_observations"],
                    r["created_at_utc"])
        for r in rows
    ]


def family_trial_count(conn: sqlite3.Connection, family: str) -> int:
    """Every trial in the family, FAILED and ABANDONED included."""
    return conn.execute("SELECT COUNT(*) FROM research_trials WHERE family = ?", (family,)).fetchone()[0]


def family_sharpe_variance(conn: sqlite3.Connection, family: str, *, min_observations: int = 0) -> float | None:
    """Variance of the Sharpe ratios of the family's completed trials;
    None with fewer than two (the cross-trial variance is unknowable).

    `min_observations` excludes trials whose Sharpe rests on fewer
    observations from the VARIANCE estimate only (a 3-trade Sharpe is noise
    and would inflate it); the trial COUNT used by DSR is unaffected."""
    values = [
        r[0] for r in conn.execute(
            "SELECT sharpe FROM research_trials WHERE family = ? AND status = 'COMPLETED' AND sharpe IS NOT NULL "
            "AND COALESCE(n_observations, 0) >= ?",
            (family, min_observations),
        )
    ]
    return statistics.variance(values) if len(values) >= 2 else None
