"""Side-effect-free market analysis for the operator's `scan`/`analyse`
commands: features, the RAW regime classification and every active
strategy's would-be signal on the newest closed bar.

Nothing here journals, persists regime-tracker state, sizes, gates or
submits anything -- it answers "what does the system see right now?"
without becoming a decision. The runtime's confirmed regime (which needs
several consecutive classifications) is read separately from
`runtime_state` when a runtime is running.
"""

from __future__ import annotations

from adaptive_scalper.features.bar_features import compute_bar_features, numeric_feature_vector
from adaptive_scalper.gateway.types import Bar, SymbolSpec
from adaptive_scalper.regimes.classifier import classify_regime
from adaptive_scalper.strategies import build_active_registry

FEATURE_LOOKBACK = 20


def analyze_bars(canonical_symbol: str, resolution: str, bars: list[Bar], spec: SymbolSpec) -> dict:
    if len(bars) < FEATURE_LOOKBACK + 2:
        return {"symbol": canonical_symbol, "status": "INSUFFICIENT_BARS",
                "detail": f"{len(bars)} closed bars; need {FEATURE_LOOKBACK + 2}"}
    features = compute_bar_features(canonical_symbol, resolution, bars, lookback=FEATURE_LOOKBACK,
                                    point_size=spec.point, now=bars[-1].time)
    regime = classify_regime(features)
    signals = []
    for strategy in build_active_registry().all_active():
        signal = strategy.evaluate(features, regime)
        signals.append({
            "strategy": strategy.key, "version": strategy.version,
            "signal": None if signal is None else {
                "direction": signal.direction, "raw_confidence": round(signal.raw_confidence, 4),
                "stop_distance": signal.stop_distance, "target_distance": signal.target_distance,
                "rationale": signal.rationale,
            },
        })
    return {
        "symbol": canonical_symbol, "status": "OK", "bar_time_utc": bars[-1].time, "close": bars[-1].close,
        "raw_regime": {"regime": regime.regime, "confidence": round(regime.confidence, 4), "reason": regime.reason},
        "features": numeric_feature_vector(features), "signals": signals,
        "note": "analysis only: no journal entry, no sizing, no permission check, no order",
    }
