"""Strategy lifecycle contract (audit section 8; adaptive_scalper/shadow/lifecycle.py)."""

from __future__ import annotations

import pytest

from adaptive_scalper.position_management.expectancy import ExpectancyEvidence, evaluate_position_expectancy
from adaptive_scalper.shadow.lifecycle import (
    HORIZON_PREREGISTERED,
    THESIS_COUPLED_TO_ENTRY_TRIGGER,
    THESIS_EXPLICIT,
    StrategyLifecycle,
    lifecycle_for,
    trigger_loss_invalidates_thesis,
)
from adaptive_scalper.strategies import build_active_registry


def _explicit(**kw):
    base = dict(strategy_key="candidate_v2", strategy_version=1, eligibility="x", entry_trigger="event",
                initial_risk="1 ATR", payoff_and_horizon="fixed 900 s", horizon_seconds=900,
                horizon_status=HORIZON_PREREGISTERED, thesis_mode=THESIS_EXPLICIT,
                thesis_valid_condition="price above the breakout level",
                thesis_invalidated_condition="close back inside the range", profit_management=None)
    base.update(kw)
    return StrategyLifecycle(**base)


def test_every_active_strategy_declares_a_lifecycle_matching_its_version():
    for strategy in build_active_registry().all_active():
        assert lifecycle_for(strategy.key).strategy_version == strategy.version


def test_v1_strategies_are_recorded_as_thesis_coupled_to_their_entry_trigger():
    for strategy in build_active_registry().all_active():
        lc = lifecycle_for(strategy.key)
        assert lc.thesis_mode == THESIS_COUPLED_TO_ENTRY_TRIGGER and lc.horizon_seconds == 600
        assert lc.thesis_valid_condition is None and lc.thesis_invalidated_condition is None


def test_an_entry_trigger_disappearing_does_not_invalidate_an_explicit_thesis():
    assert trigger_loss_invalidates_thesis(_explicit()) is False
    assert trigger_loss_invalidates_thesis(lifecycle_for("microstructure_acceleration")) is True


def test_an_explicit_thesis_must_define_both_conditions():
    with pytest.raises(ValueError):
        _explicit(thesis_invalidated_condition=None)
    with pytest.raises(ValueError):
        _explicit(thesis_valid_condition="")


def test_characterization_v1_executable_review_still_treats_trigger_loss_as_invalidation():
    """Known, documented defect (audit D5), deliberately NOT changed in the
    executable path by this branch: a vanished entry trigger alone makes the
    V1 review's thesis invalid. This test pins the current behaviour so any
    future change to it is a visible, reviewed decision."""
    result = evaluate_position_expectancy(ExpectancyEvidence(
        entry_regime="RANGE", current_regime="RANGE", strategy_setup_still_valid=False,
        current_net_edge_price=5.0, min_required_edge_price=0.0,
    ))
    assert result.thesis_valid is False
