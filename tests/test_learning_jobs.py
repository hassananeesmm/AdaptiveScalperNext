"""Model walk-forward and the real training job (completion directive
Phase 6).

Proves: folds only ever train on the past and purge label overlap; the
out-of-fold evidence (skill vs base rate, calibration, subgroups) is
computed honestly; the job never trains on untouched-OOS runs or on rows
overlapping a reserved OOS range, never pools PAPER with BACKTEST,
registers BASELINE (never CURRENT), records a trial, reports the
promotion gate without acting on it, and carries rollback evidence.
"""

from __future__ import annotations

import json
import random

import pytest

from adaptive_scalper.backtest.engine import run_backtest
from adaptive_scalper.backtest.persistence import record_backtest_run
from adaptive_scalper.features.bar_features import NUMERIC_FEATURE_FIELDS
from adaptive_scalper.learning.dataset import FEATURE_COLUMNS, TrainingRow, build_training_rows_from_records
from adaptive_scalper.learning.jobs import SOURCE_PAPER, TrainingJobError, run_training_job
from adaptive_scalper.learning.lifecycle import ModelLifecycleState
from adaptive_scalper.learning.model_walk_forward import calibration_bins, run_model_walk_forward
from adaptive_scalper.learning.observer import SCORED, EntryObserver
from adaptive_scalper.learning.promotion import ALLOW
from adaptive_scalper.persistence import connect, migrate
from adaptive_scalper.research.ledger import list_trials

T0 = 1_700_000_000
STRATEGIES = ("momentum_continuation", "range_breakout")
REGIMES = ("TRENDING_UP", "RANGE")


def _features(rng: random.Random, signal: float) -> dict:
    values = {name: rng.gauss(0, 1) for name in NUMERIC_FEATURE_FIELDS}
    values["momentum"] = signal + rng.gauss(0, 0.5)
    values["hour_of_day_utc"] = float(rng.randrange(24))
    return values


def _rows(n: int, *, seed: int = 1, informative: bool = True, hold_seconds: int = 600) -> list[TrainingRow]:
    rng = random.Random(seed)
    out = []
    for i in range(n):
        label = rng.random() < 0.5
        signal = (1.0 if label else -1.0) if informative else 0.0
        f = _features(rng, signal)
        if not informative:
            f = {name: 0.0 for name in NUMERIC_FEATURE_FIELDS}
        out.append(TrainingRow(
            entry_time_utc=T0 + i * 300, strategy_key=STRATEGIES[i % 2], entry_regime=REGIMES[(i // 2) % 2],
            features=tuple(f[name] for name in FEATURE_COLUMNS), label=int(label), realized_r=1.0 if label else -1.0,
            realized_pnl=10.0 if label else -10.0, exit_time_utc=T0 + i * 300 + hold_seconds,
        ))
    return out


# ---------------------------------------------------------------------------
# model walk-forward
# ---------------------------------------------------------------------------

def test_an_informative_feature_beats_the_base_rate_out_of_fold():
    result = run_model_walk_forward(_rows(600), n_folds=4, min_train_rows=50)
    assert all(f.trained for f in result.folds) and len(result.folds) == 4
    assert result.oos_rows == 600 - 120  # the first block is never tested
    assert result.beats_base_rate and result.brier_skill > 0.3
    assert result.oos_auc > 0.8
    assert result.promotion_ready is False  # evidence only, always


def test_folds_train_only_on_the_past_and_purge_overlapping_labels():
    rows = _rows(300, hold_seconds=3 * 300 + 10)  # every label spans the next three entries
    result = run_model_walk_forward(rows, n_folds=2, min_train_rows=20)
    for fold in result.folds:
        assert fold.purged_rows == 3  # the three trades still open when the test block starts
        assert fold.train_rows + fold.purged_rows == 100 * fold.fold
    gapped = run_model_walk_forward(rows, n_folds=2, gap_seconds=3600, min_train_rows=20)
    assert all(g.purged_rows > f.purged_rows for g, f in zip(gapped.folds, result.folds))


def test_no_skill_is_reported_as_no_skill():
    result = run_model_walk_forward(_rows(400, informative=False), n_folds=3, min_train_rows=50)
    assert result.oos_rows > 0
    assert not result.beats_base_rate and result.brier_skill <= 0.0


def test_calibration_bins_and_ece():
    bins, ece = calibration_bins([1, 0, 1, 1], [0.95, 0.05, 0.9, 1.0])
    assert sum(b.count for b in bins) == 4 and ece < 0.1
    _, bad = calibration_bins([0, 0, 0, 0], [0.9, 0.9, 0.9, 0.9])
    assert bad == pytest.approx(0.9)


def test_subgroups_cover_strategy_regime_and_session():
    result = run_model_walk_forward(_rows(600), n_folds=4, min_train_rows=50)
    assert set(result.subgroups) == {"strategy", "regime", "session"}
    assert set(result.subgroups["strategy"]) == set(STRATEGIES)
    assert sum(m.rows for m in result.subgroups["regime"].values()) == result.oos_rows
    assert result.subgroups_stable


def test_too_few_rows_is_an_honest_non_result():
    result = run_model_walk_forward(_rows(3), n_folds=5)
    assert result.oos_rows == 0 and not result.folds and "cannot form" in result.detail


# ---------------------------------------------------------------------------
# persisted features
# ---------------------------------------------------------------------------

def test_backtest_trades_persist_their_decision_time_features(tmp_path):
    from sim_helpers import config, random_walk_bars, spec

    conn = connect(tmp_path / "db.sqlite3")
    migrate(conn)
    bars = random_walk_bars(600, seed=3)
    result = run_backtest(bars, "XAUUSD", "M5", spec(), config=config(), now_utc=2_000_000_000)
    assert result.trades
    record_backtest_run(conn, result, bars, run_id="r", run_type="BACKTEST", used_for="VALIDATION",
                        strategies=STRATEGIES, feature_schema_version=1, now_utc=2_000_000_000)
    stored = conn.execute("SELECT * FROM backtest_trades WHERE run_id = 'r'").fetchall()
    assert all(json.loads(r["entry_features_json"]) == t.entry_features for r, t in zip(stored, result.trades))
    rows, excluded = build_training_rows_from_records(stored)
    assert len(rows) + excluded == len(stored)


# ---------------------------------------------------------------------------
# the training job
# ---------------------------------------------------------------------------

@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "db.sqlite3")
    migrate(c)
    return c


def _seed_backtest(conn, rows: list[TrainingRow], *, run_id: str = "bt-1", run_type: str = "BACKTEST",
                   symbol: str = "XAUUSD", provenance: str = "UNVERIFIED_ASSUMPTION") -> None:
    conn.execute(
        "INSERT OR IGNORE INTO datasets (dataset_id, created_at_utc, canonical_symbol, resolution, strategies_json, "
        "origin, feature_schema_version, row_count, range_start_utc, range_end_utc, checksum) "
        "VALUES ('ds', ?, ?, 'M5', '[]', 'BACKTEST', 1, 1, ?, ?, 'x')", (T0, symbol, T0, T0 + 1),
    )
    conn.execute(
        "INSERT INTO backtest_runs (run_id, created_at_utc, canonical_symbol, resolution, dataset_id, run_type, "
        "range_start_utc, range_end_utc, config_json, trade_count, gross_pnl, net_pnl, total_cost, metrics_json, "
        "origin) VALUES (?, ?, ?, 'M5', 'ds', ?, ?, ?, '{}', ?, 0, 0, 0, '{}', 'BACKTEST')",
        (run_id, T0, symbol, run_type, rows[0].entry_time_utc, rows[-1].label_end_utc, len(rows)),
    )
    for r in rows:
        conn.execute(
            "INSERT INTO backtest_trades (run_id, canonical_symbol, strategy_key, direction, entry_time_utc, "
            "entry_price, exit_time_utc, volume, initial_monetary_risk, realized_r, realized_pnl, total_cost, "
            "entry_regime, strategy_version, cost_provenance, entry_features_json) "
            "VALUES (?, ?, ?, 'BUY', ?, 100, ?, 0.1, 10, ?, ?, 0.5, ?, 1, ?, ?)",
            (run_id, symbol, r.strategy_key, r.entry_time_utc, r.exit_time_utc, r.realized_r, r.realized_pnl,
             r.entry_regime, provenance, json.dumps(dict(zip(FEATURE_COLUMNS, r.features)))),
        )
    conn.commit()


def _reserve_oos(conn, symbol: str, start: int, end: int) -> None:
    conn.execute(
        "INSERT INTO datasets (dataset_id, created_at_utc, canonical_symbol, resolution, strategies_json, origin, "
        "feature_schema_version, row_count, range_start_utc, range_end_utc, checksum) "
        "VALUES ('oos-ds', ?, ?, 'M5', '[]', 'BACKTEST', 1, 10, ?, ?, 'x')", (T0, symbol, start, end),
    )
    conn.execute("INSERT INTO dataset_usage (dataset_id, used_by_run_id, used_for, used_at_utc) "
                 "VALUES ('oos-ds', 'oos-run', 'OOS', ?)", (T0,))
    conn.commit()


def test_the_job_trains_registers_baseline_and_never_promotes(conn, tmp_path):
    _seed_backtest(conn, _rows(600))
    report = run_training_job(conn, canonical_symbol="XAUUSD", artifact_dir=str(tmp_path / "models"),
                              n_folds=4, min_samples=200, now_utc=T0 + 10**6)
    assert report.training.trained and report.record.lifecycle_state == ModelLifecycleState.BASELINE
    assert report.walk_forward.beats_base_rate
    assert report.promotion_decision != ALLOW  # e.g. untouched OOS not evaluated, costs unverified
    summary = report.summary()
    assert summary["promotion_gate"]["action_taken"].startswith("NONE")
    assert conn.execute("SELECT COUNT(*) FROM models WHERE lifecycle_state = 'CURRENT'").fetchone()[0] == 0
    (trial,) = list_trials(conn, family="entry_model:XAUUSD")
    assert trial.kind == "MODEL_WALK_FORWARD" and trial.status == "COMPLETED"
    metrics = json.loads(conn.execute("SELECT metrics_json FROM models").fetchone()[0])
    assert metrics["walk_forward"]["brier_skill"] == report.walk_forward.brier_skill


def test_the_observer_scores_the_trained_model_with_no_influence(conn, tmp_path):
    rows = _rows(600)
    _seed_backtest(conn, rows)
    run_training_job(conn, canonical_symbol="XAUUSD", artifact_dir=str(tmp_path / "models"), n_folds=3,
                     now_utc=T0 + 10**6)
    score = EntryObserver().score(conn, "XAUUSD", dict(zip(FEATURE_COLUMNS, rows[0].features)))
    assert score.status == SCORED and score.influence.startswith("NONE")


def test_untouched_oos_runs_and_reserved_ranges_are_never_training_data(conn, tmp_path):
    rows = _rows(600)
    _seed_backtest(conn, rows[:400])
    _seed_backtest(conn, rows[400:], run_id="oos-run", run_type="OOS")
    _reserve_oos(conn, "XAUUSD", rows[300].entry_time_utc, rows[399].entry_time_utc)
    report = run_training_job(conn, canonical_symbol="XAUUSD", artifact_dir=str(tmp_path / "m"), n_folds=3,
                              min_samples=100, now_utc=T0 + 10**6)
    assert report.candidate_rows == 400                  # the OOS run was never read
    # 300..399, plus 298/299 whose labels reach the range start (closed intervals)
    assert report.oos_protected_rows == 102
    assert report.rows == 298


def test_paper_and_backtest_evidence_are_never_pooled(conn, tmp_path):
    _seed_backtest(conn, _rows(300))
    report = run_training_job(conn, canonical_symbol="XAUUSD", artifact_dir=str(tmp_path / "m"), source=SOURCE_PAPER,
                              now_utc=T0 + 10**6)
    assert report.candidate_rows == 0 and report.rows == 0
    assert report.record.lifecycle_state == ModelLifecycleState.INSUFFICIENT_DATA


def test_insufficient_data_registers_insufficient_data(conn, tmp_path):
    _seed_backtest(conn, _rows(50))
    report = run_training_job(conn, canonical_symbol="XAUUSD", artifact_dir=str(tmp_path / "m"), now_utc=T0 + 10**6)
    assert not report.training.trained
    assert report.record.lifecycle_state == ModelLifecycleState.INSUFFICIENT_DATA
    assert report.record.artifact_path is None


@pytest.mark.parametrize("kwargs", [{"canonical_symbol": "EURUSD"}, {"canonical_symbol": "XAUUSD", "source": "MIXED"}])
def test_the_job_refuses_other_symbols_and_pooled_sources(conn, tmp_path, kwargs):
    with pytest.raises(TrainingJobError):
        run_training_job(conn, artifact_dir=str(tmp_path), **kwargs)


def test_retired_strategy_rows_are_dropped(conn, tmp_path):
    rows = _rows(300)
    rows = [TrainingRow(**{**r.__dict__, "strategy_key": "failed_breakout_fade"}) if i % 10 == 0 else r
            for i, r in enumerate(rows)]
    _seed_backtest(conn, rows)
    report = run_training_job(conn, canonical_symbol="XAUUSD", artifact_dir=str(tmp_path / "m"), n_folds=3,
                              min_samples=100, now_utc=T0 + 10**6)
    assert report.retired_rows == 30 and report.rows == 270


def test_a_second_run_carries_rollback_evidence(conn, tmp_path):
    _seed_backtest(conn, _rows(600))
    first = run_training_job(conn, canonical_symbol="XAUUSD", artifact_dir=str(tmp_path / "m"), n_folds=3,
                             now_utc=T0 + 10**6)
    second = run_training_job(conn, canonical_symbol="XAUUSD", artifact_dir=str(tmp_path / "m"), n_folds=4,
                              now_utc=T0 + 2 * 10**6)
    assert second.record.version == first.record.version + 1
    assert second.rollback["previous_version"] == first.record.version
    assert second.rollback["previous_brier_skill"] == first.walk_forward.brier_skill
    assert second.rollback["rollback_target_available"] is False  # nothing was ever CURRENT


def test_costs_are_only_realistic_when_broker_confirmed(conn, tmp_path):
    from adaptive_scalper.learning.promotion import BLOCK_COSTS_NOT_REALISTIC, BLOCK_OOS_NOT_UNTOUCHED

    _seed_backtest(conn, _rows(600), provenance="UNVERIFIED_ASSUMPTION")
    report = run_training_job(conn, canonical_symbol="XAUUSD", artifact_dir=str(tmp_path / "m"), n_folds=4,
                              now_utc=T0 + 10**6)
    # the gate stops at the first unmet requirement; untouched OOS precedes costs
    assert report.promotion_decision in (BLOCK_OOS_NOT_UNTOUCHED, BLOCK_COSTS_NOT_REALISTIC)
