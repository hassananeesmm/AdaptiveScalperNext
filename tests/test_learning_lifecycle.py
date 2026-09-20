"""Tests for the model lifecycle state machine (learning/lifecycle.py)."""

from __future__ import annotations

import pytest

from adaptive_scalper.learning.lifecycle import (
    ALLOWED_TRANSITIONS,
    ModelLifecycleState,
    InvalidLifecycleTransitionError,
    apply_transition,
    is_terminal,
    validate_transition,
)


def test_baseline_can_reach_current():
    assert validate_transition(ModelLifecycleState.BASELINE, ModelLifecycleState.CURRENT)


def test_challenger_can_be_rejected():
    assert validate_transition(ModelLifecycleState.CHALLENGER, ModelLifecycleState.REJECTED)


def test_current_can_degrade():
    assert validate_transition(ModelLifecycleState.CURRENT, ModelLifecycleState.DEGRADED)


def test_current_cannot_jump_straight_to_rejected():
    assert not validate_transition(ModelLifecycleState.CURRENT, ModelLifecycleState.REJECTED)


def test_degraded_can_roll_back():
    assert validate_transition(ModelLifecycleState.DEGRADED, ModelLifecycleState.ROLLED_BACK)


def test_rolled_back_can_become_current_again():
    assert validate_transition(ModelLifecycleState.ROLLED_BACK, ModelLifecycleState.CURRENT)


def test_rejected_is_terminal():
    assert is_terminal(ModelLifecycleState.REJECTED)
    assert ALLOWED_TRANSITIONS[ModelLifecycleState.REJECTED] == frozenset()


def test_insufficient_data_can_become_challenger():
    assert validate_transition(ModelLifecycleState.INSUFFICIENT_DATA, ModelLifecycleState.CHALLENGER)


def test_apply_transition_raises_on_illegal_jump():
    with pytest.raises(InvalidLifecycleTransitionError):
        apply_transition(ModelLifecycleState.BASELINE, ModelLifecycleState.DEGRADED)


def test_apply_transition_returns_to_state_on_success():
    assert apply_transition(ModelLifecycleState.CHALLENGER, ModelLifecycleState.CURRENT) == ModelLifecycleState.CURRENT


def test_no_state_can_transition_back_to_baseline():
    for state in ModelLifecycleState:
        assert ModelLifecycleState.BASELINE not in ALLOWED_TRANSITIONS.get(state, frozenset())


def test_every_state_present_in_transition_table():
    for state in ModelLifecycleState:
        assert state in ALLOWED_TRANSITIONS
