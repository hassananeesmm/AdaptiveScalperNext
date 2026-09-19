"""Strategy registry with a structural retirement firewall (directive
sections 8, 121).

`failed_breakout_fade` and `support_resistance_reaction` must NEVER
register, signal, rank, train actively, promote, or execute — through
restart, config reload, migration, model registry restore, RAG, or
packaging. This module is the "cannot register" half of that guarantee:
`register()` checks every incoming key against
`RETIRED_STRATEGY_KEYS` (the single hard-coded source of truth in
`adaptive_scalper.config.constants`) and raises `RetiredStrategyError`
rather than ever accepting one — there is no configuration flag, no
migration path, no restore mechanism that can add a retired key to an
active registry, because the check happens unconditionally on every call,
not as a one-time gate that state could later bypass.
"""

from __future__ import annotations

from adaptive_scalper.config.constants import RETIRED_STRATEGY_KEYS
from adaptive_scalper.strategies.base import Strategy


class RetiredStrategyError(ValueError):
    """Raised when code attempts to register a permanently retired
    strategy key. This must never be caught-and-ignored to "work around"
    — a retired key reaching this exception means something upstream
    (config, a restore path, a copy-paste) tried to reactivate a strategy
    that directive section 8 permanently forbids."""


class DuplicateStrategyError(ValueError):
    """Raised when two different Strategy instances claim the same key."""


class StrategyRegistry:
    def __init__(self) -> None:
        self._strategies: dict[str, Strategy] = {}

    def register(self, strategy: Strategy) -> None:
        if strategy.key in RETIRED_STRATEGY_KEYS:
            raise RetiredStrategyError(
                f"{strategy.key!r} is permanently retired (directive section 8) and can "
                f"never be registered as an active strategy, regardless of caller, "
                f"configuration, or restore path"
            )
        if strategy.key in self._strategies:
            raise DuplicateStrategyError(f"a strategy is already registered under key {strategy.key!r}")
        self._strategies[strategy.key] = strategy

    def get(self, key: str) -> Strategy | None:
        return self._strategies.get(key)

    def all_active(self) -> tuple[Strategy, ...]:
        return tuple(self._strategies.values())

    def active_keys(self) -> frozenset[str]:
        return frozenset(self._strategies.keys())

    @staticmethod
    def is_retired(key: str) -> bool:
        return key in RETIRED_STRATEGY_KEYS
