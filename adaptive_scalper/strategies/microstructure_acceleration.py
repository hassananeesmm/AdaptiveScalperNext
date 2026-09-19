"""microstructure_acceleration: very-short-horizon signal on aligned,
meaningful velocity + acceleration (directive section 9, item 6).

Unlike the other five, this one is not regime-restricted to a single
state — short-horizon acceleration bursts can occur inside a trend,
range, or breakout alike — but it deliberately excludes COMPRESSION,
ERRATIC, and UNKNOWN, where a short-horizon acceleration reading is
either meaningless (compression: everything is small by definition) or
untrustworthy (erratic/unknown). Fires only when velocity and
acceleration agree in sign AND the acceleration is large relative to
ATR — a small, noisy wiggle should not trigger a signal.
"""

from __future__ import annotations

from adaptive_scalper.features.bar_features import FeatureSnapshot
from adaptive_scalper.regimes.classifier import COMPRESSION, ERRATIC, UNKNOWN, RegimeClassification
from adaptive_scalper.strategies.base import StrategySignal

STRATEGY_KEY = "microstructure_acceleration"
STRATEGY_VERSION = 1

_EXCLUDED_REGIMES = frozenset({COMPRESSION, ERRATIC, UNKNOWN})


class MicrostructureAccelerationStrategy:
    key = STRATEGY_KEY
    version = STRATEGY_VERSION

    def __init__(
        self,
        min_acceleration_to_atr_ratio: float = 0.05,
        stop_atr_multiple: float = 1.0,
        target_atr_multiple: float = 1.5,
        min_confidence: float = 0.15,
        expected_duration_seconds: int = 180,
    ) -> None:
        self.min_acceleration_to_atr_ratio = min_acceleration_to_atr_ratio
        self.stop_atr_multiple = stop_atr_multiple
        self.target_atr_multiple = target_atr_multiple
        self.min_confidence = min_confidence
        self.expected_duration_seconds = expected_duration_seconds

    def evaluate(self, features: FeatureSnapshot, regime: RegimeClassification) -> StrategySignal | None:
        if regime.regime in _EXCLUDED_REGIMES:
            return None
        if not features.atr or features.velocity is None or features.acceleration is None:
            return None
        if features.velocity == 0 or features.acceleration == 0:
            return None
        if (features.velocity > 0) != (features.acceleration > 0):
            return None

        accel_ratio = abs(features.acceleration) / features.atr
        if accel_ratio < self.min_acceleration_to_atr_ratio:
            return None

        direction = "BUY" if features.velocity > 0 else "SELL"
        raw_confidence = max(0.0, min(1.0, accel_ratio * 2))
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
                f"velocity={features.velocity:.4f} and acceleration={features.acceleration:.4f} aligned, "
                f"accel/atr={accel_ratio:.3f} (regime={regime.regime})"
            ),
            feature_schema_version=features.feature_schema_version,
            data_timestamp=features.data_timestamp,
        )
