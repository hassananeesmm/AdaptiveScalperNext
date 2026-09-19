"""statistical_reversion: fade an extreme within a RANGE/COMPRESSION
regime, expecting reversion toward the middle of the recent range
(directive section 9, item 4).

Uses `recent_high`/`recent_low` from the feature engine's lookback
window to locate price within its recent range; a close within
`proximity_threshold` of either extreme is treated as an overextension
worth fading. Only fires in RANGE/COMPRESSION — a reversion bet inside a
TRENDING or BREAKOUT regime is exactly the kind of mistake this
distinction exists to prevent (directive section 13's whole point is
that different regimes call for different logic).
"""

from __future__ import annotations

from adaptive_scalper.features.bar_features import FeatureSnapshot
from adaptive_scalper.regimes.classifier import COMPRESSION, RANGE, RegimeClassification
from adaptive_scalper.strategies.base import StrategySignal

STRATEGY_KEY = "statistical_reversion"
STRATEGY_VERSION = 1


class StatisticalReversionStrategy:
    key = STRATEGY_KEY
    version = STRATEGY_VERSION

    def __init__(
        self,
        proximity_threshold: float = 0.15,
        stop_atr_multiple: float = 1.0,
        target_atr_multiple: float = 1.5,
        min_confidence: float = 0.15,
        expected_duration_seconds: int = 300,
    ) -> None:
        self.proximity_threshold = proximity_threshold
        self.stop_atr_multiple = stop_atr_multiple
        self.target_atr_multiple = target_atr_multiple
        self.min_confidence = min_confidence
        self.expected_duration_seconds = expected_duration_seconds

    def evaluate(self, features: FeatureSnapshot, regime: RegimeClassification) -> StrategySignal | None:
        if regime.regime not in (RANGE, COMPRESSION):
            return None
        if not features.atr or features.recent_high is None or features.recent_low is None:
            return None

        span = features.recent_high - features.recent_low
        if span <= 0:
            return None

        proximity_to_high = (features.recent_high - features.close) / span
        proximity_to_low = (features.close - features.recent_low) / span

        if proximity_to_high <= self.proximity_threshold:
            direction = "SELL"
            proximity = proximity_to_high
        elif proximity_to_low <= self.proximity_threshold:
            direction = "BUY"
            proximity = proximity_to_low
        else:
            return None

        raw_confidence = max(0.0, min(1.0, regime.confidence * (1 - proximity / self.proximity_threshold)))
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
                f"{regime.regime} regime, close within {proximity:.2%} of recent range extreme "
                f"(recent_high={features.recent_high}, recent_low={features.recent_low})"
            ),
            feature_schema_version=features.feature_schema_version,
            data_timestamp=features.data_timestamp,
        )
