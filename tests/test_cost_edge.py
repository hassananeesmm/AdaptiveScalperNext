"""Tests for the expected-net-edge gate (directive section 34; issue #6).

Expected edge comes only from EdgeEvidence. The raw strategy score is never
read as a probability by an executable gate: without VALIDATED evidence the
gate is BLOCK_EDGE_UNVALIDATED (FLAT). The frozen V1 formula survives only as
the labelled LEGACY_V1_RAW_SCORE research-replay provider.
"""

from __future__ import annotations

import pytest

from adaptive_scalper.costs.edge import (
    ALLOW,
    BLOCK_COST,
    BLOCK_EDGE_UNVALIDATED,
    BLOCK_EXPECTED_EDGE,
    evaluate_cost_gate,
    evaluate_expected_edge,
)
from adaptive_scalper.costs.edge_evidence import (
    EDGE_MODEL_LEGACY_V1_RAW_SCORE,
    EVIDENCE_LEGACY_UNCALIBRATED,
    EVIDENCE_TEST_FIXTURE,
    EVIDENCE_VALIDATED,
    LEGACY_V1_RAW_SCORE_EVIDENCE,
    NO_VALIDATED_EDGE_EVIDENCE,
    CalibratedWinProbability,
    EdgeEvidence,
    LifecyclePayoff,
    executable_evidence,
    provider_for_model,
    require_executable_provider,
)
from adaptive_scalper.costs.model import estimate_cost
from adaptive_scalper.strategies.base import StrategySignal


def _signal(**overrides) -> StrategySignal:
    defaults = dict(
        strategy_key="momentum_continuation", strategy_version=1, canonical_symbol="XAUUSD",
        direction="BUY", raw_confidence=0.6, stop_distance=1.0, target_distance=2.0,
        expected_duration_seconds=300, entry_method="MARKET", regime="TRENDING_UP",
        rationale="test", feature_schema_version=1, data_timestamp=1000,
    )
    defaults.update(overrides)
    return StrategySignal(**defaults)


def _cost(spread=0.1):
    return estimate_cost(spread_price=spread, commission_price_equivalent=0.0, expected_slippage_price=0.0,
                         swap_price_equivalent=0.0, uncertainty_margin_pct=0.0)


def _validated(p=0.55, win=2.0, loss=1.0, status=EVIDENCE_VALIDATED) -> EdgeEvidence:
    return EdgeEvidence.from_calibration(
        CalibratedWinProbability(p, "cal-test", "platt", 1200),
        LifecyclePayoff(win, loss, 1200, "forward-test"), model_id="test-model", status=status,
    )


def _legacy(signal):
    return LEGACY_V1_RAW_SCORE_EVIDENCE.for_signal(signal)


# --- frozen V1 formula, research replay only ---------------------------------------

def test_legacy_v1_replay_reproduces_the_frozen_formula():
    # EV = 0.6*2.0 - 0.4*1.0 = 0.8
    evidence = _legacy(_signal(raw_confidence=0.6, stop_distance=1.0, target_distance=2.0))
    assert evidence.expected_gross_edge_price == pytest.approx(0.8)
    assert evidence.status == EVIDENCE_LEGACY_UNCALIBRATED
    assert evidence.probability is None  # a raw score never becomes a CalibratedWinProbability


def test_legacy_v1_replay_negative_for_a_poor_setup():
    evidence = _legacy(_signal(raw_confidence=0.3, stop_distance=2.0, target_distance=1.0))
    assert evidence.expected_gross_edge_price == pytest.approx(-1.1)


def test_research_replay_gate_uses_legacy_evidence_when_not_executable():
    signal = _signal(raw_confidence=0.6, stop_distance=1.0, target_distance=2.0)
    decision, evaluation = evaluate_cost_gate(signal, _cost(), evidence=_legacy(signal), executable=False)
    assert decision == ALLOW
    assert evaluation.expected_net_edge == pytest.approx(0.7)


# --- executable gate fails closed ------------------------------------------------

def test_no_evidence_is_block_edge_unvalidated_even_with_a_perfect_raw_score():
    signal = _signal(raw_confidence=1.0, stop_distance=1.0, target_distance=10.0)
    assert evaluate_cost_gate(signal, _cost()) == (BLOCK_EDGE_UNVALIDATED, None)


def test_raw_score_cannot_enter_the_executable_ev_gate():
    """Negative control: the legacy raw-score evidence would ALLOW this
    trade, but an executable gate must refuse it."""
    signal = _signal(raw_confidence=0.95, stop_distance=1.0, target_distance=3.0)
    assert evaluate_cost_gate(signal, _cost(), evidence=_legacy(signal), executable=False)[0] == ALLOW
    assert evaluate_cost_gate(signal, _cost(), evidence=_legacy(signal), executable=True)[0] == BLOCK_EDGE_UNVALIDATED


def test_test_fixture_evidence_is_never_executable():
    fixture = _validated(status=EVIDENCE_TEST_FIXTURE)
    assert evaluate_cost_gate(_signal(), _cost(), evidence=fixture)[0] == BLOCK_EDGE_UNVALIDATED
    assert executable_evidence(fixture) is None


def test_validated_evidence_uses_realized_payoff_not_configured_target():
    # Configured target 10.0 would give a huge EV; the realized lifecycle
    # payoff (win 0.5, loss 1.0) at p=0.55 is negative: 0.275 - 0.45 = -0.175.
    signal = _signal(raw_confidence=0.99, target_distance=10.0, stop_distance=1.0)
    evidence = _validated(p=0.55, win=0.5, loss=1.0)
    decision, evaluation = evaluate_cost_gate(signal, _cost(0.0), evidence=evidence)
    assert decision == BLOCK_EXPECTED_EDGE
    assert evaluation.expected_gross_edge == pytest.approx(-0.175)


def test_validated_positive_evidence_allows_and_reports_its_model():
    decision, evaluation = evaluate_cost_gate(_signal(), _cost(), evidence=_validated(p=0.55, win=2.0, loss=1.0))
    assert decision == ALLOW
    assert evaluation.expected_net_edge == pytest.approx(0.55 * 2.0 - 0.45 * 1.0 - 0.1)
    assert evaluation.evidence_status == EVIDENCE_VALIDATED and evaluation.edge_model == "test-model"


def test_cost_unknown_still_blocks_first():
    assert evaluate_cost_gate(_signal(), None, evidence=_validated()) == (BLOCK_COST, None)


def test_min_net_edge_threshold_still_applies():
    evidence = _validated(p=0.55, win=2.0, loss=1.0)          # gross 0.65, net 0.55
    assert evaluate_expected_edge(_signal(), _cost(), evidence, min_net_edge_price=0.5).sufficient is True
    assert evaluate_expected_edge(_signal(), _cost(), evidence, min_net_edge_price=0.6).sufficient is False


def test_reason_string_names_costs_and_evidence():
    result = evaluate_expected_edge(_signal(), _cost(), _validated())
    for token in ("gross_edge=", "total_cost=", "net_edge=", "edge_model=test-model", "evidence=VALIDATED"):
        assert token in result.reason


# --- evidence types ------------------------------------------------------------------

def test_validated_evidence_requires_probability_and_payoff():
    with pytest.raises(ValueError):
        EdgeEvidence(0.1, EVIDENCE_VALIDATED, "m")
    with pytest.raises(ValueError):  # stated EV inconsistent with p/payoff
        EdgeEvidence(5.0, EVIDENCE_VALIDATED, "m", CalibratedWinProbability(0.5, "c", "platt", 10),
                     LifecyclePayoff(1.0, 1.0, 10, "s"))


@pytest.mark.parametrize("value", [0.0, 1.0, -0.1, 1.5, float("nan")])
def test_calibrated_probability_rejects_degenerate_values(value):
    with pytest.raises(ValueError):
        CalibratedWinProbability(value, "c", "platt", 10)


def test_default_provider_has_no_evidence():
    assert NO_VALIDATED_EDGE_EVIDENCE.for_signal(_signal(raw_confidence=1.0)) is None
    assert provider_for_model("NONE") is NO_VALIDATED_EDGE_EVIDENCE


def test_executable_runtimes_refuse_the_legacy_provider():
    with pytest.raises(ValueError, match=EDGE_MODEL_LEGACY_V1_RAW_SCORE):
        require_executable_provider(LEGACY_V1_RAW_SCORE_EVIDENCE, "DEMO runtime")
    assert require_executable_provider(NO_VALIDATED_EDGE_EVIDENCE, "DEMO runtime") is NO_VALIDATED_EDGE_EVIDENCE


def test_unknown_edge_model_is_refused():
    with pytest.raises(ValueError):
        provider_for_model("RAW_CONFIDENCE_AS_P")
