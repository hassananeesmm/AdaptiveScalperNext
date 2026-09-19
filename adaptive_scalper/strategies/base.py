"""Common Strategy interface (directive sections 9, 90).

A strategy consumes a `FeatureSnapshot` + `RegimeClassification` and may
produce a `StrategySignal` hypothesis, or `None` — FLAT is always a valid
outcome (directive section 10), not a failure.

A strategy must NEVER decide money risk, volume, account safety, news
permission, portfolio risk, kill switch, or final execution permission.
`StrategySignal` structurally enforces this: it has no monetary/volume
field at all, and `stop_distance`/`target_distance` are PRICE distances
(e.g. ATR multiples), not money — converting a price distance into an
actual position size is the risk governor's job alone (directive section
32), a module this one does not import or call.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from adaptive_scalper.features.bar_features import FeatureSnapshot
from adaptive_scalper.regimes.classifier import RegimeClassification

DIRECTIONS = frozenset({"BUY", "SELL"})


@dataclass(frozen=True)
class StrategySignal:
    strategy_key: str
    strategy_version: int
    canonical_symbol: str
    direction: str              # "BUY" or "SELL" — never a monetary/volume field
    raw_confidence: float       # 0..1, this strategy's own unadjusted confidence
    stop_distance: float        # PRICE distance (e.g. ATR multiple), not money
    target_distance: float      # PRICE distance, not money
    expected_duration_seconds: int
    entry_method: str
    regime: str
    rationale: str
    feature_schema_version: int
    data_timestamp: int

    def __post_init__(self) -> None:
        if self.direction not in DIRECTIONS:
            raise ValueError(f"direction must be one of {sorted(DIRECTIONS)}, got {self.direction!r}")
        if not (0.0 <= self.raw_confidence <= 1.0):
            raise ValueError(f"raw_confidence must be in [0, 1], got {self.raw_confidence!r}")
        if self.stop_distance <= 0:
            raise ValueError(f"stop_distance must be positive, got {self.stop_distance!r}")
        if self.target_distance <= 0:
            raise ValueError(f"target_distance must be positive, got {self.target_distance!r}")


class Strategy(Protocol):
    key: str
    version: int

    def evaluate(self, features: FeatureSnapshot, regime: RegimeClassification) -> StrategySignal | None: ...
