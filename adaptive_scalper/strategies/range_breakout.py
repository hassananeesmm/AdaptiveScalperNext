"""range_breakout: trade the decisive move that triggered a BREAKOUT
regime classification (directive section 9, item 3).

Direction follows the sign of the latest single-bar return — the same
signal the regime classifier itself used to distinguish BREAKOUT from
VOLATILITY_EXPANSION (decisive direction vs. none). Wider stop/target
than momentum_continuation since a breakout bar is, by definition, an
unusually wide one (directive section 13's BREAKOUT regime is only
reached via `range_expansion_ratio` above threshold).
"""

from __future__ import annotations

from adaptive_scalper.features.bar_features import FeatureSnapshot
from adaptive_scalper.regimes.classifier import BREAKOUT, RegimeClassification
from adaptive_scalper.strategies.base import StrategySignal

STRATEGY_KEY = "range_breakout"
STRATEGY_VERSION = 1


class RangeBreakoutStrategy:
    key = STRATEGY_KEY
    version = STRATEGY_VERSION

    def __init__(
        self,
        stop_atr_multiple: float = 2.0,
        target_atr_multiple: float = 3.0,
        min_confidence: float = 0.15,
        expected_duration_seconds: int = 600,
    ) -> None:
        self.stop_atr_multiple = stop_atr_multiple
        self.target_atr_multiple = target_atr_multiple
        self.min_confidence = min_confidence
        self.expected_duration_seconds = expected_duration_seconds

    def evaluate(self, features: FeatureSnapshot, regime: RegimeClassification) -> StrategySignal | None:
        if regime.regime != BREAKOUT:
            return None
        if not features.atr or features.return_1 is None or features.return_1 == 0:
            return None

        direction = "BUY" if features.return_1 > 0 else "SELL"
        raw_confidence = max(0.0, min(1.0, regime.confidence))
        if raw_confidence < self.min_confidence:
            return None

        return StrategySignal(
            strategy_key=self.key,
            strategy_version=self.version,
            canonical_symbol=features.canonical_symbol,
            direction=direction,
            raw_confidence=raw_confidence,
            stop_distance=features.atr * self.stop_atr_multiple,
            target_distance=features.atr * self.target_atr_multiple,
            expected_duration_seconds=self.expected_duration_seconds,
            entry_method="MARKET",
            regime=regime.regime,
            rationale=(
                f"BREAKOUT regime (confidence={regime.confidence:.2f}), "
                f"decisive return_1={features.return_1:.4f}, "
                f"range_expansion_ratio={features.range_expansion_ratio}"
            ),
            feature_schema_version=features.feature_schema_version,
            data_timestamp=features.data_timestamp,
        )
