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
