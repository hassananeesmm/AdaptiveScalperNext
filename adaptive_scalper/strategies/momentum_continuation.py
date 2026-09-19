"""momentum_continuation: ride an already-confirmed trend (directive
section 9's active strategy family list, item 1).

Fires only in a TRENDING_UP/TRENDING_DOWN regime; direction follows the
regime's direction. Confidence scales with regime confidence and the
feature engine's efficiency ratio (a clean, efficient trend is a
stronger hypothesis than a noisy one that happened to classify as
trending). Deliberately conservative: below `min_confidence`, returns
None (FLAT) rather than a weak signal — directive section 10, FLAT is a
valid decision, not a failure to find something.
"""

from __future__ import annotations

from adaptive_scalper.features.bar_features import FeatureSnapshot
from adaptive_scalper.regimes.classifier import TRENDING_DOWN, TRENDING_UP, RegimeClassification
from adaptive_scalper.strategies.base import StrategySignal

STRATEGY_KEY = "momentum_continuation"
STRATEGY_VERSION = 1


class MomentumContinuationStrategy:
    key = STRATEGY_KEY
    version = STRATEGY_VERSION

    def __init__(
        self,
        stop_atr_multiple: float = 1.5,
        target_atr_multiple: float = 2.5,
        min_confidence: float = 0.15,
        expected_duration_seconds: int = 600,
    ) -> None:
        self.stop_atr_multiple = stop_atr_multiple
        self.target_atr_multiple = target_atr_multiple
        self.min_confidence = min_confidence
        self.expected_duration_seconds = expected_duration_seconds

    def evaluate(self, features: FeatureSnapshot, regime: RegimeClassification) -> StrategySignal | None:
        if regime.regime not in (TRENDING_UP, TRENDING_DOWN):
            return None
        if features.atr is None or features.atr <= 0 or features.efficiency_ratio is None:
            return None

        direction = "BUY" if regime.regime == TRENDING_UP else "SELL"
        raw_confidence = max(0.0, min(1.0, regime.confidence * features.efficiency_ratio))
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
                f"regime={regime.regime} (confidence={regime.confidence:.2f}), "
                f"efficiency_ratio={features.efficiency_ratio:.2f}"
            ),
            feature_schema_version=features.feature_schema_version,
            data_timestamp=features.data_timestamp,
        )
