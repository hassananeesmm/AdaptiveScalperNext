"""pullback_continuation: enter a confirmed trend on a short-term
counter-move, rather than chasing the trend at its extreme (directive
section 9, item 2).

Distinct from momentum_continuation (directive section 18: entry logic
concepts must stay distinct, not one identical score for everything).
Requires: trend regime AND the LATEST single bar moved against the
trend (`return_1` opposite sign) AND the longer `momentum` window still
confirms the trend direction — i.e. a genuine pullback within an intact
trend, not a reversal. Slightly discounted confidence vs. pure
continuation, since entering into an immediate counter-move.
"""

from __future__ import annotations

from adaptive_scalper.features.bar_features import FeatureSnapshot
from adaptive_scalper.regimes.classifier import TRENDING_DOWN, TRENDING_UP, RegimeClassification
from adaptive_scalper.strategies.base import StrategySignal

STRATEGY_KEY = "pullback_continuation"
STRATEGY_VERSION = 1


class PullbackContinuationStrategy:
    key = STRATEGY_KEY
    version = STRATEGY_VERSION

    def __init__(
        self,
        stop_atr_multiple: float = 1.2,
        target_atr_multiple: float = 2.0,
        min_confidence: float = 0.15,
        confidence_discount: float = 0.8,
        expected_duration_seconds: int = 480,
    ) -> None:
        self.stop_atr_multiple = stop_atr_multiple
        self.target_atr_multiple = target_atr_multiple
        self.min_confidence = min_confidence
        self.confidence_discount = confidence_discount
        self.expected_duration_seconds = expected_duration_seconds

    def evaluate(self, features: FeatureSnapshot, regime: RegimeClassification) -> StrategySignal | None:
        if regime.regime not in (TRENDING_UP, TRENDING_DOWN):
            return None
        if features.return_1 is None or features.momentum is None or not features.atr:
            return None

        trend_up = regime.regime == TRENDING_UP
        pullback_present = features.return_1 < 0 if trend_up else features.return_1 > 0
        momentum_confirms = features.momentum > 0 if trend_up else features.momentum < 0
        if not (pullback_present and momentum_confirms):
            return None

        direction = "BUY" if trend_up else "SELL"
        raw_confidence = max(0.0, min(1.0, regime.confidence * self.confidence_discount))
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
                f"trend regime={regime.regime}, return_1={features.return_1:.4f} against trend, "
                f"momentum={features.momentum:.4f} confirms trend intact"
            ),
            feature_schema_version=features.feature_schema_version,
            data_timestamp=features.data_timestamp,
        )
