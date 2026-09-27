"""Strategy interface, registry, and the six active strategy families
(directive sections 9, 90). See `registry.py` for the retirement
firewall — `failed_breakout_fade`/`support_resistance_reaction` can
never be registered here, structurally, not by omission.
"""

from __future__ import annotations

from adaptive_scalper.strategies.microstructure_acceleration import MicrostructureAccelerationStrategy
from adaptive_scalper.strategies.momentum_continuation import MomentumContinuationStrategy
from adaptive_scalper.strategies.pullback_continuation import PullbackContinuationStrategy
from adaptive_scalper.strategies.range_breakout import RangeBreakoutStrategy
from adaptive_scalper.strategies.registry import StrategyRegistry
from adaptive_scalper.strategies.statistical_reversion import StatisticalReversionStrategy
from adaptive_scalper.strategies.volatility_expansion import VolatilityExpansionStrategy


def build_active_registry() -> StrategyRegistry:
    """The single source of truth for "which strategies are active right
    now": exactly the six directive-mandated families, nothing else.
    Adding a strategy here means adding it to this function; there is no
    other path (config, plugin discovery, etc.) that can activate one."""
    registry = StrategyRegistry()
    for strategy in (
        MomentumContinuationStrategy(),
        PullbackContinuationStrategy(),
        RangeBreakoutStrategy(),
        StatisticalReversionStrategy(),
        VolatilityExpansionStrategy(),
        MicrostructureAccelerationStrategy(),
    ):
        registry.register(strategy)
    return registry


def select_active_strategies(
    strategy_keys: tuple[str, ...] | None = None, registry: StrategyRegistry | None = None,
) -> tuple:
    """The active strategies, optionally restricted to `strategy_keys`
    (an independent research session evaluates ONE strategy on its own).
    A subset can only ever narrow the active set: a retired or unknown key
    raises instead of being silently ignored, so a research run can never
    claim to have evaluated a strategy it did not. `registry` defaults to
    `build_active_registry()`; callers pass their own module's reference so
    test doubles substituted there still apply."""
    active = (registry if registry is not None else build_active_registry()).all_active()
    if strategy_keys is None:
        return active
    if not strategy_keys:
        raise ValueError("strategy_keys must name at least one active strategy")
    by_key = {s.key: s for s in active}
    for key in strategy_keys:
        if StrategyRegistry.is_retired(key):
            raise ValueError(f"{key!r} is permanently retired and can never be evaluated")
        if key not in by_key:
            raise ValueError(f"{key!r} is not an active strategy (active: {sorted(by_key)})")
    if len(set(strategy_keys)) != len(strategy_keys):
        raise ValueError(f"duplicate strategy key in {strategy_keys!r}")
    return tuple(s for s in active if s.key in strategy_keys)
