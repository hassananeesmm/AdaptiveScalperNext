"""Tests for the persisted model registry (learning/registry.py)."""

from __future__ import annotations

import pytest

from adaptive_scalper.learning.lifecycle import InvalidLifecycleTransitionError, ModelLifecycleState
from adaptive_scalper.learning.registry import (
    RetiredStrategyModelError,
    get_current_model,
    get_model,
    get_model_lifecycle_history,
    register_model,
    transition_model_state,
)
from adaptive_scalper.persistence import connect, migrate


@pytest.fixture()
def db(tmp_path):
    conn = connect(tmp_path / "test.sqlite3")
    migrate(conn)
    yield conn
    conn.close()


def test_register_model_starts_baseline(db):
    model = register_model(db, "xauusd_exit_quality", now_utc=1000)
    assert model.lifecycle_state == ModelLifecycleState.BASELINE
    assert model.version == 1


def test_register_model_auto_increments_version(db):
    m1 = register_model(db, "xauusd_exit_quality", now_utc=1000)
    m2 = register_model(db, "xauusd_exit_quality", now_utc=2000)
    assert m1.version == 1
    assert m2.version == 2


def test_register_model_refuses_retired_strategy_key(db):
    with pytest.raises(RetiredStrategyModelError):
        register_model(db, "some_model", strategy_key="failed_breakout_fade")
    with pytest.raises(RetiredStrategyModelError):
        register_model(db, "some_model", strategy_key="support_resistance_reaction")


def test_register_model_allows_active_strategy_key(db):
    model = register_model(db, "some_model", strategy_key="momentum_continuation")
    assert model.strategy_key == "momentum_continuation"


def test_transition_model_state_moves_through_lifecycle(db):
    model = register_model(db, "m1", now_utc=1000)
    model = transition_model_state(db, model.id, ModelLifecycleState.CHALLENGER, now_utc=1001)
    assert model.lifecycle_state == ModelLifecycleState.CHALLENGER
    model = transition_model_state(db, model.id, ModelLifecycleState.CURRENT, now_utc=1002)
    assert model.lifecycle_state == ModelLifecycleState.CURRENT


def test_transition_model_state_rejects_illegal_jump(db):
    model = register_model(db, "m1")
    with pytest.raises(InvalidLifecycleTransitionError):
        transition_model_state(db, model.id, ModelLifecycleState.DEGRADED)


def test_transition_to_current_refuses_retired_strategy_even_if_retired_after_registration(db):
    # Simulate: model registered while strategy was active, but the key
    # is now (hypothetically) retired -- promotion must still refuse.
    model = register_model(db, "m1", strategy_key="momentum_continuation")
    db.execute("UPDATE models SET strategy_key = 'failed_breakout_fade' WHERE id = ?", (model.id,))
    db.commit()
    with pytest.raises(RetiredStrategyModelError):
        transition_model_state(db, model.id, ModelLifecycleState.CURRENT)


def test_get_current_model_returns_none_when_no_current(db):
    register_model(db, "m1")
    assert get_current_model(db, "m1") is None


def test_get_current_model_finds_the_promoted_version(db):
    m1 = register_model(db, "m1", now_utc=1000)
    transition_model_state(db, m1.id, ModelLifecycleState.CHALLENGER, now_utc=1001)
    transition_model_state(db, m1.id, ModelLifecycleState.CURRENT, now_utc=1002)
    m2 = register_model(db, "m1", now_utc=2000)  # a new challenger, not yet promoted

    current = get_current_model(db, "m1")
    assert current.version == 1
    assert m2.lifecycle_state == ModelLifecycleState.BASELINE


def test_get_model_lifecycle_history_records_full_path(db):
    model = register_model(db, "m1", now_utc=1000)
    transition_model_state(db, model.id, ModelLifecycleState.CHALLENGER, now_utc=1001)
    transition_model_state(db, model.id, ModelLifecycleState.CURRENT, now_utc=1002)
    transition_model_state(db, model.id, ModelLifecycleState.PREVIOUS_STABLE, now_utc=1003)

    history = get_model_lifecycle_history(db, model.id)
    assert history == [
        (None, "BASELINE", 1000),
        ("BASELINE", "CHALLENGER", 1001),
        ("CHALLENGER", "CURRENT", 1002),
        ("CURRENT", "PREVIOUS_STABLE", 1003),
    ]


def test_get_model_returns_none_for_unknown(db):
    assert get_model(db, "no-such-key", 1) is None


def test_metrics_persisted_as_json(db):
    model = register_model(db, "m1", metrics={"accuracy": 0.6})
    assert model.metrics == {"accuracy": 0.6}


def test_metrics_update_on_transition(db):
    model = register_model(db, "m1")
    updated = transition_model_state(db, model.id, ModelLifecycleState.CHALLENGER, metrics={"auc": 0.7})
    assert updated.metrics == {"auc": 0.7}
