"""Portfolio exposure tracking (directive section 35): open/pending
monetary risk, per-symbol exposure, currency-direction exposure,
USD-related exposure, and correlated-cluster exposure.

`PositionExposure` is a minimal, LOCAL stand-in for "a position/pending
order with a known monetary risk and direction" — the real execution/
position-management layer (not yet built) will supply these values from
actual broker state; this module only aggregates whatever it's given.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from adaptive_scalper.config.constants import ALLOWED_CANONICAL_SYMBOLS
from adaptive_scalper.portfolio.correlation import CorrelationResult

# The CONCEPTUAL currency pair used for portfolio accounting — distinct
# from gateway/symbol_validation.py's EXPECTED_IDENTITY, which verifies
# broker-REPORTED metadata (and deliberately does NOT claim BTCUSD's
# broker currency_base is "BTC", since brokers report that field
# inconsistently for crypto CFDs — see that module's docstring). Here we
# want the idealized long/short currency exposure a human would reason
# about (buying BTCUSD is "long BTC, short USD" regardless of what the
# broker's currency_base field happens to say), which is a different
# question from "does this broker symbol's metadata prove it's really
# BTCUSD."
CANONICAL_CURRENCY_PAIR: dict[str, tuple[str, str]] = {
    "XAUUSD": ("XAU", "USD"),
    "GBPJPY": ("GBP", "JPY"),
    "BTCUSD": ("BTC", "USD"),
}
assert frozenset(CANONICAL_CURRENCY_PAIR) == ALLOWED_CANONICAL_SYMBOLS


@dataclass(frozen=True)
class PositionExposure:
    canonical_symbol: str
    direction: str              # "BUY" or "SELL"
    monetary_risk: float        # initial monetary risk, always >= 0

    def __post_init__(self) -> None:
        if self.canonical_symbol not in ALLOWED_CANONICAL_SYMBOLS:
            raise ValueError(f"{self.canonical_symbol!r} is not an allowed canonical symbol")
        if self.direction not in ("BUY", "SELL"):
            raise ValueError(f"direction must be 'BUY' or 'SELL', got {self.direction!r}")
        if self.monetary_risk < 0:
            raise ValueError(f"monetary_risk must be non-negative, got {self.monetary_risk!r}")


@dataclass(frozen=True)
class PortfolioExposure:
    total_open_risk: float
    total_pending_risk: float
    symbol_exposure: dict[str, float] = field(default_factory=dict)
    currency_direction_exposure: dict[str, float] = field(default_factory=dict)  # currency -> signed net (+long/-short)
    usd_related_exposure: float = 0.0
    positions_per_symbol: dict[str, int] = field(default_factory=dict)


def _apply_currency_direction(exposure: dict[str, float], symbol: str, direction: str, risk: float) -> None:
    base, profit = CANONICAL_CURRENCY_PAIR[symbol]
    sign = 1.0 if direction == "BUY" else -1.0
    exposure[base] = exposure.get(base, 0.0) + sign * risk
    exposure[profit] = exposure.get(profit, 0.0) - sign * risk


def compute_exposure(
    open_positions: list[PositionExposure], pending_positions: list[PositionExposure] = ()
) -> PortfolioExposure:
    symbol_exposure: dict[str, float] = {}
    currency_direction: dict[str, float] = {}
    positions_per_symbol: dict[str, int] = {}
    usd_related = 0.0

    for pos in open_positions:
        symbol_exposure[pos.canonical_symbol] = symbol_exposure.get(pos.canonical_symbol, 0.0) + pos.monetary_risk
        positions_per_symbol[pos.canonical_symbol] = positions_per_symbol.get(pos.canonical_symbol, 0) + 1
        _apply_currency_direction(currency_direction, pos.canonical_symbol, pos.direction, pos.monetary_risk)
        if "USD" in CANONICAL_CURRENCY_PAIR[pos.canonical_symbol]:
            usd_related += pos.monetary_risk

    total_open_risk = sum(p.monetary_risk for p in open_positions)
    total_pending_risk = sum(p.monetary_risk for p in pending_positions)

    return PortfolioExposure(
        total_open_risk=total_open_risk,
        total_pending_risk=total_pending_risk,
        symbol_exposure=symbol_exposure,
        currency_direction_exposure=currency_direction,
        usd_related_exposure=usd_related,
        positions_per_symbol=positions_per_symbol,
    )


def correlated_cluster_exposure(
    exposure: PortfolioExposure,
    correlation_matrix: dict[tuple[str, str], CorrelationResult],
    high_correlation_threshold: float = 0.7,
) -> dict[frozenset[str], float]:
    """Combined open-risk exposure for every pair of symbols BOTH
    currently open AND measurably highly correlated (|corr| >= threshold,
    N/A pairs excluded — never assumed correlated or uncorrelated)."""
    open_symbols = [s for s, risk in exposure.symbol_exposure.items() if risk > 0]
    clusters: dict[frozenset[str], float] = {}
    for i, a in enumerate(open_symbols):
        for b in open_symbols[i + 1:]:
            result = correlation_matrix.get((a, b), CorrelationResult(None, 0))
            if result.correlation is None or abs(result.correlation) < high_correlation_threshold:
                continue
            key = frozenset({a, b})
            clusters[key] = exposure.symbol_exposure[a] + exposure.symbol_exposure[b]
    return clusters
