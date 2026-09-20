"""Tests for position_management.expectancy (execution-safety review
round 2 finding #8)."""

from __future__ import annotations

from adaptive_scalper.position_management.expectancy import ExpectancyEvidence, evaluate_position_expectancy


def _evidence(**overrides) -> ExpectancyEvidence:
    defaults = dict(
        entry_regime="TRENDING_UP", current_regime="TRENDING_UP", strategy_setup_still_valid=True,
        current_net_edge_price=0.5, min_required_edge_price=0.1,
    )
    defaults.update(overrides)
    return ExpectancyEvidence(**defaults)


def test_healthy_position_thesis_remains_valid():
    result = evaluate_position_expectancy(_evidence())
    assert result.thesis_valid is True
    assert result.regime_reversed is False


def test_regime_reversal_detected():
    result = evaluate_position_expectancy(_evidence(current_regime="TRENDING_DOWN"))
    assert result.regime_reversed is True
    assert "reversed" in result.reasons[0]


def test_non_reversal_regime_change_not_flagged():
    result = evaluate_position_expectancy(_evidence(entry_regime="TRENDING_UP", current_regime="RANGE"))
    assert result.regime_reversed is False


def test_strategy_setup_no_longer_valid_invalidates_thesis():
    result = evaluate_position_expectancy(_evidence(strategy_setup_still_valid=False))
    assert result.thesis_valid is False
    assert any("setup condition" in r for r in result.reasons)


def test_unknown_cost_edge_fails_closed():
    result = evaluate_position_expectancy(_evidence(current_net_edge_price=None))
    assert result.thesis_valid is False
    assert any("unknown" in r for r in result.reasons)


def test_insufficient_edge_invalidates_thesis():
    result = evaluate_position_expectancy(_evidence(current_net_edge_price=0.05, min_required_edge_price=0.1))
    assert result.thesis_valid is False
    assert any("below the minimum" in r for r in result.reasons)


def test_sufficient_edge_at_exact_boundary_is_valid():
    result = evaluate_position_expectancy(_evidence(current_net_edge_price=0.1, min_required_edge_price=0.1))
    assert result.thesis_valid is True


def test_rag_negative_alone_never_invalidates_thesis():
    # RAG is advisory-only -- must never be sufficient on its own to flip
    # thesis_valid when everything measurable still supports it.
    result = evaluate_position_expectancy(_evidence(rag_advisory_negative=True))
    assert result.thesis_valid is True
    assert any("RAG" in r for r in result.reasons)


def test_model_negative_alone_never_invalidates_thesis():
    result = evaluate_position_expectancy(_evidence(model_advisory_negative=True))
    assert result.thesis_valid is True
    assert any("ML observer" in r for r in result.reasons)


def test_rag_and_model_negative_together_still_do_not_override_valid_measurable_evidence():
    result = evaluate_position_expectancy(
        _evidence(rag_advisory_negative=True, model_advisory_negative=True)
    )
    assert result.thesis_valid is True


def test_measurable_invalidation_combined_with_advisory_negatives_still_invalidates():
    result = evaluate_position_expectancy(
        _evidence(strategy_setup_still_valid=False, rag_advisory_negative=True, model_advisory_negative=True)
    )
    assert result.thesis_valid is False
    assert len(result.reasons) >= 3


def test_reasons_default_message_when_everything_healthy():
    result = evaluate_position_expectancy(_evidence())
    assert result.reasons == ("current evidence still supports the original thesis",)
