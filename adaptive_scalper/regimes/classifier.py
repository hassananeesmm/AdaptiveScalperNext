"""Deterministic market regime classification (directive section 13).

`classify_regime()` is a pure, stateless function of one `FeatureSnapshot`
— it makes no claim about persistence across calls. `RegimeTracker` adds
the hysteresis directive section 13 explicitly requires ("do not close a
position solely because one noisy observation briefly flips regime"): it
only flips its CONFIRMED regime after `min_confirmations` consecutive raw
classifications agree, so a single noisy bar can't whipsaw a position
manager that reads `confirmed_regime`.

Thresholds are initial research defaults (documented, not claimed
optimal), consistent with directive section 20's framing for the exit
parameters — these are starting points for real validation, not proven
values.
"""

from __future__ import annotations

from dataclasses import dataclass

from adaptive_scalper.features.bar_features import FeatureSnapshot

REGIME_VERSION = 1

TRENDING_UP = "TRENDING_UP"
TRENDING_DOWN = "TRENDING_DOWN"
RANGE = "RANGE"
COMPRESSION = "COMPRESSION"
VOLATILITY_EXPANSION = "VOLATILITY_EXPANSION"
BREAKOUT = "BREAKOUT"
ERRATIC = "ERRATIC"
UNKNOWN = "UNKNOWN"

REGIME_STATES = frozenset({
    TRENDING_UP, TRENDING_DOWN, RANGE, COMPRESSION,
    VOLATILITY_EXPANSION, BREAKOUT, ERRATIC, UNKNOWN,
})

DEFAULT_TREND_EFFICIENCY_THRESHOLD = 0.5
DEFAULT_TREND_PERSISTENCE_THRESHOLD = 0.6
DEFAULT_RANGE_EFFICIENCY_THRESHOLD = 0.3
DEFAULT_COMPRESSION_RATIO_THRESHOLD = 0.6
DEFAULT_EXPANSION_RATIO_THRESHOLD = 1.8
DEFAULT_ERRATIC_EFFICIENCY_THRESHOLD = 0.25


@dataclass(frozen=True)
class RegimeClassification:
    regime: str
    confidence: float   # heuristic 0..1, NOT a calibrated probability
    version: int
    reason: str


def _clip01(x: float) -> float:
    return max(0.0, min(1.0, x))


def classify_regime(
    snap: FeatureSnapshot,
    *,
    trend_efficiency_threshold: float = DEFAULT_TREND_EFFICIENCY_THRESHOLD,
    trend_persistence_threshold: float = DEFAULT_TREND_PERSISTENCE_THRESHOLD,
    range_efficiency_threshold: float = DEFAULT_RANGE_EFFICIENCY_THRESHOLD,
    compression_ratio_threshold: float = DEFAULT_COMPRESSION_RATIO_THRESHOLD,
    expansion_ratio_threshold: float = DEFAULT_EXPANSION_RATIO_THRESHOLD,
    erratic_efficiency_threshold: float = DEFAULT_ERRATIC_EFFICIENCY_THRESHOLD,
) -> RegimeClassification:
    """Classify one FeatureSnapshot. Deterministic: same input always
    yields the same output — no hidden state here (see RegimeTracker for
    the stateful hysteresis layer).

    Priority order (first match wins), each requiring the fields it
    reads to be non-None — anything with insufficient underlying data
    resolves to UNKNOWN rather than guessing:

    1. missing efficiency_ratio or range_expansion_ratio -> UNKNOWN
    2. wide current bar (range_expansion_ratio > expansion threshold):
       - decisive direction (high persistence + efficiency) -> BREAKOUT
       - no direction, very choppy -> ERRATIC
       - no direction, otherwise -> VOLATILITY_EXPANSION
    3. narrow current bar (range_expansion_ratio < compression threshold)
       -> COMPRESSION
    4. high efficiency + high persistence -> TRENDING_UP/TRENDING_DOWN
       (direction from return_1's sign)
    5. low efficiency -> RANGE
    6. otherwise (ambiguous middle ground) -> RANGE (the safe default for
       "not clearly trending, not clearly ranging")
    """
    if snap.efficiency_ratio is None or snap.range_expansion_ratio is None:
        return RegimeClassification(UNKNOWN, 0.0, REGIME_VERSION, "insufficient_data")

    efficiency = snap.efficiency_ratio
    persistence = snap.directional_persistence
    expansion = snap.range_expansion_ratio

    if expansion > expansion_ratio_threshold:
        decisive = (
            persistence is not None
            and persistence >= trend_persistence_threshold
            and efficiency >= range_efficiency_threshold
        )
        if decisive:
            confidence = _clip01(0.5 + (expansion - expansion_ratio_threshold) / expansion_ratio_threshold * 0.5)
            return RegimeClassification(BREAKOUT, confidence, REGIME_VERSION, "wide_bar_with_decisive_direction")
        if efficiency < erratic_efficiency_threshold:
            confidence = _clip01(0.5 + (expansion_ratio_threshold - efficiency) * 0.5)
            return RegimeClassification(ERRATIC, confidence, REGIME_VERSION, "wide_bar_choppy_no_direction")
        confidence = _clip01(0.4 + (expansion - expansion_ratio_threshold) / expansion_ratio_threshold * 0.4)
        return RegimeClassification(VOLATILITY_EXPANSION, confidence, REGIME_VERSION, "wide_bar_no_clear_direction")

    if expansion < compression_ratio_threshold:
        confidence = _clip01(0.5 + (compression_ratio_threshold - expansion) / compression_ratio_threshold * 0.5)
        return RegimeClassification(COMPRESSION, confidence, REGIME_VERSION, "narrow_bar_relative_to_recent_range")

    if (
        efficiency >= trend_efficiency_threshold
        and persistence is not None
        and persistence >= trend_persistence_threshold
        and snap.return_1 is not None
        and snap.return_1 != 0
    ):
        direction = TRENDING_UP if snap.return_1 > 0 else TRENDING_DOWN
        confidence = _clip01(
            0.5
            + (efficiency - trend_efficiency_threshold) / (1 - trend_efficiency_threshold) * 0.25
            + (persistence - trend_persistence_threshold) / (1 - trend_persistence_threshold) * 0.25
        )
        return RegimeClassification(direction, confidence, REGIME_VERSION, "high_efficiency_persistent_direction")

    if efficiency < range_efficiency_threshold:
        confidence = _clip01(0.5 + (range_efficiency_threshold - efficiency) / range_efficiency_threshold * 0.5)
        return RegimeClassification(RANGE, confidence, REGIME_VERSION, "low_efficiency")

    return RegimeClassification(RANGE, 0.4, REGIME_VERSION, "ambiguous_default")


class RegimeTracker:
    """Stateful hysteresis layer. Feed it consecutive raw
    `RegimeClassification`s via `update()`; `confirmed_regime` only
    changes after `min_confirmations` consecutive raw classifications
    agree on the SAME new regime, so a single noisy bar can't flip it."""

    def __init__(
        self, min_confirmations: int = 2, initial_regime: str = UNKNOWN, *,
        initial_candidate: str | None = None, initial_candidate_count: int = 0,
    ) -> None:
        if min_confirmations < 1:
            raise ValueError("min_confirmations must be >= 1")
        self.min_confirmations = min_confirmations
        self._confirmed = initial_regime
        # `initial_candidate`/`initial_candidate_count` let a caller
        # RESUME an ongoing tracker's in-progress hysteresis exactly
        # (directive section 13's "do not flip on one noisy bar" must
        # hold across process restarts/incremental cycles too, e.g.
        # `adaptive_scalper.paper.engine` -- not just within one
        # continuous run). Never set independently of `state`/
        # `confirmed_regime`, which report both together.
        self._candidate: str | None = initial_candidate
        self._candidate_count = initial_candidate_count

    @property
    def confirmed_regime(self) -> str:
        return self._confirmed

    @property
    def state(self) -> tuple[str, str | None, int]:
        """`(confirmed_regime, candidate, candidate_count)` -- everything
        needed to construct an equivalent `RegimeTracker` later via
        `RegimeTracker(min_confirmations, confirmed, initial_candidate=
        candidate, initial_candidate_count=candidate_count)`."""
        return self._confirmed, self._candidate, self._candidate_count

    def update(self, raw: RegimeClassification) -> str:
        if raw.regime == self._confirmed:
            self._candidate = None
            self._candidate_count = 0
            return self._confirmed

        if raw.regime == self._candidate:
            self._candidate_count += 1
        else:
            self._candidate = raw.regime
            self._candidate_count = 1

        if self._candidate_count >= self.min_confirmations:
            self._confirmed = self._candidate
            self._candidate = None
            self._candidate_count = 0

        return self._confirmed

    @property
    def confirmed_regime(self) -> str:
        return self._confirmed
