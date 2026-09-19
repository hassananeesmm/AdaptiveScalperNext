"""Retirement firewall regression tests (directive sections 8, 121):
failed_breakout_fade and support_resistance_reaction must never be
registerable, through any path this codebase currently has."""

from __future__ import annotations

import pytest

from adaptive_scalper.config.constants import RETIRED_STRATEGY_KEYS
from adaptive_scalper.strategies import build_active_registry
from adaptive_scalper.strategies.microstructure_acceleration import MicrostructureAccelerationStrategy
from adaptive_scalper.strategies.momentum_continuation import MomentumContinuationStrategy
from adaptive_scalper.strategies.pullback_continuation import PullbackContinuationStrategy
from adaptive_scalper.strategies.range_breakout import RangeBreakoutStrategy
from adaptive_scalper.strategies.registry import (
    DuplicateStrategyError,
    RetiredStrategyError,
    StrategyRegistry,
)
from adaptive_scalper.strategies.statistical_reversion import StatisticalReversionStrategy
from adaptive_scalper.strategies.volatility_expansion import VolatilityExpansionStrategy

EXPECTED_ACTIVE_KEYS = frozenset({
    "momentum_continuation",
    "pullback_continuation",
    "range_breakout",
    "statistical_reversion",
    "volatility_expansion",
    "microstructure_acceleration",
})


class _FakeRetiredStrategy:
    """A minimal stand-in for the Strategy protocol, used only to prove
    the registry rejects a retired key regardless of what implementation
    is behind it — not just the two real classes that happen to exist."""

    def __init__(self, key: str) -> None:
        self.key = key
        self.version = 1

    def evaluate(self, features, regime):
        return None


def test_retired_keys_are_exactly_the_two_directive_names():
    assert RETIRED_STRATEGY_KEYS == frozenset({"failed_breakout_fade", "support_resistance_reaction"})


@pytest.mark.parametrize("retired_key", sorted(RETIRED_STRATEGY_KEYS))
def test_registering_a_retired_key_always_raises(retired_key):
    registry = StrategyRegistry()
    with pytest.raises(RetiredStrategyError):
        registry.register(_FakeRetiredStrategy(retired_key))


@pytest.mark.parametrize("retired_key", sorted(RETIRED_STRATEGY_KEYS))
def test_retired_key_never_appears_in_registry_after_failed_registration(retired_key):
    registry = StrategyRegistry()
    with pytest.raises(RetiredStrategyError):
        registry.register(_FakeRetiredStrategy(retired_key))
    assert retired_key not in registry.active_keys()
    assert registry.get(retired_key) is None


@pytest.mark.parametrize("retired_key", sorted(RETIRED_STRATEGY_KEYS))
def test_is_retired_identifies_both_retired_keys(retired_key):
    assert StrategyRegistry.is_retired(retired_key) is True


def test_is_retired_is_false_for_an_active_key():
    assert StrategyRegistry.is_retired("momentum_continuation") is False


def test_duplicate_key_registration_raises():
    registry = StrategyRegistry()
    registry.register(MomentumContinuationStrategy())
    with pytest.raises(DuplicateStrategyError):
        registry.register(MomentumContinuationStrategy())


def test_registering_a_retired_key_does_not_corrupt_subsequent_valid_registrations():
    """A retired-key rejection must not leave the registry in a broken
    state — the very next, legitimate registration must still work."""
    registry = StrategyRegistry()
    with pytest.raises(RetiredStrategyError):
        registry.register(_FakeRetiredStrategy("failed_breakout_fade"))
    registry.register(MomentumContinuationStrategy())
    assert registry.get("momentum_continuation") is not None


def test_build_active_registry_contains_exactly_the_six_directive_strategies():
    registry = build_active_registry()
    assert registry.active_keys() == EXPECTED_ACTIVE_KEYS


def test_build_active_registry_contains_no_retired_key():
    registry = build_active_registry()
    assert registry.active_keys().isdisjoint(RETIRED_STRATEGY_KEYS)


def test_build_active_registry_is_freshly_constructed_each_call():
    # No shared mutable module-level registry state that a "restart"
    # (a fresh process, or just a fresh call in this test) could leak
    # a previously-rejected retired key through.
    registry_a = build_active_registry()
    registry_b = build_active_registry()
    assert registry_a is not registry_b
    assert registry_a.active_keys() == registry_b.active_keys() == EXPECTED_ACTIVE_KEYS


@pytest.mark.parametrize("strategy_cls,expected_key", [
    (MomentumContinuationStrategy, "momentum_continuation"),
    (PullbackContinuationStrategy, "pullback_continuation"),
    (RangeBreakoutStrategy, "range_breakout"),
    (StatisticalReversionStrategy, "statistical_reversion"),
    (VolatilityExpansionStrategy, "volatility_expansion"),
    (MicrostructureAccelerationStrategy, "microstructure_acceleration"),
])
def test_each_active_strategy_class_reports_its_own_documented_key(strategy_cls, expected_key):
    instance = strategy_cls()
    assert instance.key == expected_key
    assert isinstance(instance.version, int) and instance.version >= 1
