"""volatility_expansion: trade wick-rejection direction during a
VOLATILITY_EXPANSION regime — a wide bar without the decisive
directional persistence that would instead classify it as BREAKOUT
(directive section 9, item 5; section 13 distinguishes the two regimes
on exactly that basis).

Direction comes from candle wick asymmetry: a long lower wick (price
rejected from the bar's low) suggests upward continuation; a long upper
wick suggests downward continuation. This is standard causal price-action
reasoning — both wicks are fully determined by the CURRENT bar's own
OHLC, no future information involved.
"""

from __future__ import annotations

from adaptive_scalper.features.bar_features import FeatureSnapshot
from adaptive_scalper.regimes.classifier import VOLATILITY_EXPANSION, RegimeClassification
from adaptive_scalper.strategies.base import StrategySignal

STRATEGY_KEY = "volatility_expansion"
STRATEGY_VERSION = 1


class VolatilityExpansionStrategy:
    key = STRATEGY_KEY
    version = STRATEGY_VERSION

    def __init__(
        self,
        wick_asymmetry_threshold: float = 0.15,
        stop_atr_multiple: float = 2.0,
        target_atr_multiple: float = 2.5,
        min_confidence: float = 0.15,
        expected_duration_seconds: int = 420,
    ) -> None:
        self.wick_asymmetry_threshold = wick_asymmetry_threshold
        self.stop_atr_multiple = stop_atr_multiple
        self.target_atr_multiple = target_atr_multiple
        self.min_confidence = min_confidence
        self.expected_duration_seconds = expected_duration_seconds

    def evaluate(self, features: FeatureSnapshot, regime: RegimeClassification) -> StrategySignal | None:
        if regime.regime != VOLATILITY_EXPANSION:
            return None
        if not features.atr or features.upper_wick_ratio is None or features.lower_wick_ratio is None:
            return None

        wick_diff = features.lower_wick_ratio - features.upper_wick_ratio
        if wick_diff > self.wick_asymmetry_threshold:
            direction = "BUY"
        elif wick_diff < -self.wick_asymmetry_threshold:
            direction = "SELL"
        else:
            return None

        raw_confidence = max(0.0, min(1.0, regime.confidence * min(abs(wick_diff) * 2, 1.0)))
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
                f"VOLATILITY_EXPANSION regime, wick asymmetry={wick_diff:.2f} "
                f"(upper={features.upper_wick_ratio:.2f}, lower={features.lower_wick_ratio:.2f})"
            ),
            feature_schema_version=features.feature_schema_version,
            data_timestamp=features.data_timestamp,
        )
