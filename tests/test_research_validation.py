"""Research validation layer: purging/embargo, purged K-fold, CPCV,
PSR/DSR, PBO, trial ledger -- each checked against a hand-constructed
case whose right answer is known independently of the implementation."""

from __future__ import annotations

import ast
import math
import sqlite3
from pathlib import Path

import numpy as np
import pytest

from adaptive_scalper.persistence import connect, migrate
from adaptive_scalper.research.ledger import (
    family_sharpe_variance,
    family_trial_count,
    list_trials,
    record_trial,
)
from adaptive_scalper.research.splits import (
    LabelInterval,
    contiguous_groups,
    cpcv_path_count,
    cpcv_paths,
    cpcv_splits,
    purged_kfold,
)
from adaptive_scalper.research.stats import (
    deflated_sharpe_ratio,
    expected_max_sharpe,
    probabilistic_sharpe_ratio,
    probability_of_backtest_overfitting,
    return_moments,
)

PACKAGE = Path(__file__).resolve().parents[1] / "adaptive_scalper"


def _overlapping_intervals(n: int) -> list[LabelInterval]:
    # sample i's label spans [10i, 10i + 15]: each overlaps its successor.
    return [LabelInterval(10 * i, 10 * i + 15) for i in range(n)]


# ---------------------------------------------------------------------------
# purging / embargo / purged K-fold
# ---------------------------------------------------------------------------

def test_purged_kfold_drops_overlapping_neighbours_of_the_test_fold():
    # groups (0,1) (2,3) (4,5); fold 1 tests samples 2,3 spanning [20, 45].
    # Sample 1 [10,25] and sample 4 [40,55] overlap it -> purged.
    splits = purged_kfold(_overlapping_intervals(6), 3)
    fold = splits[1]
    assert fold.test == (2, 3)
    assert fold.purged == (1, 4)
    assert fold.train == (0, 5)
    assert fold.embargoed == ()


def test_embargo_drops_samples_starting_just_after_the_test_fold():
    # With a 10s embargo, sample 5 (starts 50, within (45, 55]) is dropped
    # too; sample 0 (before the fold) is never embargoed.
    fold = purged_kfold(_overlapping_intervals(6), 3, embargo_seconds=10)[1]
    assert fold.embargoed == (5,)
    assert fold.train == (0,)


def test_no_training_sample_ever_overlaps_a_test_sample():
    intervals = _overlapping_intervals(40)
    for split in purged_kfold(intervals, 5, embargo_seconds=5) + cpcv_splits(intervals, 6, 2, embargo_seconds=5):
        test = [intervals[i] for i in split.test]
        for i in split.train:
            assert all(intervals[i].end_utc < t.start_utc or intervals[i].start_utc > t.end_utc for t in test)
        assert not set(split.train) & set(split.test)


def test_splits_refuse_unsorted_samples():
    with pytest.raises(ValueError, match="sorted"):
        purged_kfold([LabelInterval(10, 20), LabelInterval(0, 5), LabelInterval(30, 40)], 2)


def test_contiguous_groups_cover_every_sample_once_in_order():
    groups = contiguous_groups(10, 3)
    assert groups == [(0, 1, 2, 3), (4, 5, 6), (7, 8, 9)]


# ---------------------------------------------------------------------------
# CPCV
# ---------------------------------------------------------------------------

def test_cpcv_split_and_path_counts_match_the_combinatorics():
    splits = cpcv_splits(_overlapping_intervals(60), n_groups=6, n_test_groups=2)
    assert len(splits) == math.comb(6, 2) == 15
    assert cpcv_path_count(6, 2) == 5
    tested = [g for s in splits for g in s.test_groups]
    assert all(tested.count(g) == 5 for g in range(6))


def test_cpcv_paths_use_each_group_once_from_a_split_that_tested_it():
    splits = cpcv_splits(_overlapping_intervals(60), 6, 2)
    paths = cpcv_paths(6, 2)
    assert len(paths) == 5
    used = set()
    for path in paths:
        assert [g for g, _ in path] == list(range(6))
        for group, split_index in path:
            assert group in splits[split_index].test_groups
            used.add((group, split_index))
    assert len(used) == 30  # every (group, test-split) pairing used exactly once


# ---------------------------------------------------------------------------
# PSR / DSR
# ---------------------------------------------------------------------------

def _phi(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def test_psr_is_one_half_when_observed_equals_benchmark():
    assert probabilistic_sharpe_ratio(0.2, 0.2, 100, skew=-0.5, kurtosis=6.0) == pytest.approx(0.5)


def test_psr_matches_an_independent_closed_form_evaluation():
    sr, n, skew, kurt = 0.1, 100, -0.3, 5.0
    z = sr * math.sqrt(n - 1) / math.sqrt(1 - skew * sr + (kurt - 1) / 4 * sr ** 2)
    assert probabilistic_sharpe_ratio(sr, 0.0, n, skew=skew, kurtosis=kurt) == pytest.approx(_phi(z), abs=1e-9)


def test_negative_skew_and_fat_tails_reduce_confidence():
    normal = probabilistic_sharpe_ratio(0.1, 0.0, 250)
    ugly = probabilistic_sharpe_ratio(0.1, 0.0, 250, skew=-1.5, kurtosis=12.0)
    assert ugly < normal


def test_expected_max_sharpe_matches_the_simulated_maximum_of_null_trials():
    rng = np.random.default_rng(7)
    simulated = rng.standard_normal((4000, 1000)).max(axis=1).mean()  # E[max of 1000 N(0,1)] ~ 3.24
    assert expected_max_sharpe(1000, 1.0) == pytest.approx(simulated, abs=0.05)


def test_deflated_sharpe_ratio_falls_as_trials_increase():
    one = deflated_sharpe_ratio(0.15, 500, n_trials=1, trial_sharpe_variance=0.01)
    many = deflated_sharpe_ratio(0.15, 500, n_trials=200, trial_sharpe_variance=0.01)
    assert one == pytest.approx(probabilistic_sharpe_ratio(0.15, 0.0, 500))
    assert many < one


def test_return_moments_of_a_known_sample():
    m = return_moments([1.0, -1.0, 1.0, -1.0])
    assert m.mean == 0.0 and m.std == 1.0 and m.skew == 0.0 and m.kurtosis == 1.0
    with pytest.raises(ValueError, match="zero variance"):
        return_moments([1.0, 1.0, 1.0])


# ---------------------------------------------------------------------------
# PBO
# ---------------------------------------------------------------------------

def test_pbo_is_zero_when_one_configuration_dominates_every_block():
    perf = [[1.0 + 0.01 * t, 0.0, -0.5, 0.2] for t in range(8)]
    result = probability_of_backtest_overfitting(perf, n_groups=4)
    assert result.computable and result.pbo == 0.0 and result.n_combinations == 6


def test_pbo_is_one_when_the_in_sample_winner_is_always_the_out_of_sample_loser():
    # Trial A's block results sum to zero and B = -A, so whichever wins any
    # half of the groups must lose the other half.
    a = [3.0, -1.0, -1.0, -1.0]
    perf = [[x, -x] for x in a]
    result = probability_of_backtest_overfitting(perf, n_groups=4)
    assert result.pbo == 1.0


@pytest.mark.parametrize("perf,groups", [([[1.0], [2.0], [3.0], [4.0]], 4), ([[1.0, 2.0]] * 6, 5)])
def test_pbo_is_reported_as_not_computable_rather_than_zero(perf, groups):
    result = probability_of_backtest_overfitting(perf, n_groups=groups)
    assert result.computable is False and result.pbo is None


# ---------------------------------------------------------------------------
# trial ledger
# ---------------------------------------------------------------------------

@pytest.fixture()
def db(tmp_path):
    conn = connect(tmp_path / "t.sqlite3")
    migrate(conn)
    yield conn
    conn.close()


def _trial(db, trial_id, status="COMPLETED", sharpe=0.1):
    record_trial(db, trial_id=trial_id, family="xau-momentum", kind="BACKTEST",
                 strategy_versions={"momentum_continuation": 1}, params={"lookback": 20}, status=status,
                 sharpe=sharpe, n_observations=300, now_utc=1)


def test_failed_and_abandoned_trials_count_towards_deflation(db):
    _trial(db, "t1", sharpe=0.1)
    _trial(db, "t2", sharpe=0.3)
    _trial(db, "t3", status="FAILED", sharpe=None)
    _trial(db, "t4", status="ABANDONED", sharpe=None)
    assert family_trial_count(db, "xau-momentum") == 4
    assert family_sharpe_variance(db, "xau-momentum") == pytest.approx(0.02)
    assert [t.trial_id for t in list_trials(db, "xau-momentum")] == ["t1", "t2", "t3", "t4"]


def test_the_ledger_is_append_only(db):
    _trial(db, "t1")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        db.execute("UPDATE research_trials SET sharpe = 9.9")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        db.execute("DELETE FROM research_trials")


def test_sharpe_variance_is_unknown_with_fewer_than_two_completed_trials(db):
    _trial(db, "t1")
    assert family_sharpe_variance(db, "xau-momentum") is None


# ---------------------------------------------------------------------------
# structural isolation
# ---------------------------------------------------------------------------

def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
        elif isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
    return names


def test_research_cannot_reach_brokers_kill_switch_or_sizing():
    forbidden = ("adaptive_scalper.gateway", "adaptive_scalper.execution", "adaptive_scalper.core",
                 "adaptive_scalper.risk", "MetaTrader5")
    for path in (PACKAGE / "research").glob("*.py"):
        assert not [m for m in _imports(path) if m.startswith(forbidden)], path


def test_execution_critical_code_does_not_depend_on_research():
    critical = ("gateway", "execution", "core", "risk", "portfolio", "costs", "strategies", "selector",
                "position_management", "news")
    for package in critical:
        for path in (PACKAGE / package).rglob("*.py"):
            assert not [m for m in _imports(path) if m.startswith("adaptive_scalper.research")], path
