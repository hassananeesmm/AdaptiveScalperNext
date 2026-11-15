"""Forward-evidence evaluator (validation/forward_evidence.py; ASN-031 step 1).

Every series below is SYNTHETIC test input that exists only inside this
file; nothing here is evidence about any strategy."""

from __future__ import annotations

import ast
import dataclasses
import random
from pathlib import Path

import pytest

from adaptive_scalper.simulation.fill_model import COST_BROKER_DEMO_CONFIRMED
from adaptive_scalper.validation import forward_evidence as fe
from adaptive_scalper.validation.certificate import PREREGISTERED_PROTOCOL

GROUP = {"strategy_key": "synthetic", "canonical_symbol": "XAUUSD"}


def _series(net_values, *, gap=600, hold=300, provenance=COST_BROKER_DEMO_CONFIRMED, cost=0.1):
    return [fe.ForwardObservation(1_791_000_000 + i * gap, 1_791_000_000 + i * gap + hold, "BUY",
                                  v + cost, cost, v, provenance) for i, v in enumerate(net_values)]


def _profitable(n=400, seed=7):
    rng = random.Random(seed)
    return [0.25 + rng.gauss(0, 0.6) for _ in range(n)]


def _evaluate(obs, **kw):
    kw.setdefault("ledger_trial_count", 262)
    kw.setdefault("trial_sharpe_variance", 0.0004)
    return fe.evaluate_group(GROUP, obs, **kw)


def _status(report, n):
    return next(c.status for c in report.criteria if c.number == n)


def test_below_the_preregistered_sample_only_counts_are_reported():
    report = _evaluate(_series(_profitable(n=299)))
    assert report.label == fe.LABEL_INSUFFICIENT
    assert report.effective_observations == 299 and report.statistics == {}
    assert [c.number for c in report.criteria] == [1]


def test_overlapping_observations_are_not_independent():
    overlapping = _series([0.1] * 10, gap=60, hold=300)   # each trade still open when the next starts
    kept = fe.effective_sequence(overlapping)
    assert len(kept) == 2                                  # t=0 and t=300 (entry at/after previous exit)
    assert all(b.entry_time_utc >= a.exit_time_utc for a, b in zip(kept, kept[1:]))


def test_chronological_blocks_are_contiguous_and_cover_everything():
    blocks = fe.chronological_blocks(list(range(301)), 8)
    assert len(blocks) == 8 and sum(len(b) for b in blocks) == 301
    assert [x for b in blocks for x in b] == list(range(301))


def test_a_strong_synthetic_series_still_cannot_be_eligible_without_cost_components():
    report = _evaluate(_series(_profitable()))
    assert [_status(report, n) for n in (1, 2, 3, 4)] == ["PASS"] * 4
    assert _status(report, 5) == fe.NOT_EVALUATED       # slippage/spread components are not recorded
    assert report.label == fe.LABEL_INSUFFICIENT
    assert report.label != fe.LABEL_ELIGIBLE


def test_a_losing_series_with_enough_sample_is_rejected():
    rng = random.Random(3)
    report = _evaluate(_series([-0.05 + rng.gauss(0, 0.6) for _ in range(400)]))
    assert report.label == fe.LABEL_REJECTED


def test_one_dominant_trade_fails_the_concentration_criterion():
    values = [0.0] * 399 + [50.0]
    report = _evaluate(_series(values))
    assert _status(report, 4) == fe.FAIL and report.label == fe.LABEL_REJECTED


def test_unconfirmed_cost_provenance_fails():
    report = _evaluate(_series(_profitable(), provenance="BROKER_SPEC_ESTIMATE"))
    assert _status(report, 9) == fe.FAIL and report.label == fe.LABEL_REJECTED


def test_an_understated_trial_count_fails_and_a_missing_ledger_is_not_evaluated():
    assert _status(_evaluate(_series(_profitable()), ledger_trial_count=10), 7) == fe.FAIL
    missing = _evaluate(_series(_profitable()), ledger_trial_count=None, trial_sharpe_variance=None)
    assert _status(missing, 7) == fe.NOT_EVALUATED


def test_only_the_preregistered_protocol_is_accepted():
    weaker = dataclasses.replace(PREREGISTERED_PROTOCOL, min_effective_observations=10)
    with pytest.raises(ValueError, match="preregistered protocol only"):
        _evaluate(_series(_profitable()), protocol=weaker)


def test_the_bootstrap_bound_is_deterministic():
    blocks = fe.chronological_blocks(_profitable(), 8)
    assert fe.block_bootstrap_lower_bound(blocks) == fe.block_bootstrap_lower_bound(blocks)


def test_the_evaluator_cannot_write_sign_or_reach_the_order_path():
    package = Path(fe.__file__).resolve().parents[1]
    source = Path(fe.__file__).read_text(encoding="utf-8")
    for forbidden in ("INSERT", "UPDATE ", "DELETE", "issue_signature", "order_send", "put_state"):
        assert forbidden not in source, forbidden
    importers = []
    for path in package.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "adaptive_scalper.validation.forward_evidence":
                importers.append(path.relative_to(package).as_posix())
    assert importers == ["cli/research.py"]
