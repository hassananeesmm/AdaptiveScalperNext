"""Portfolio exposure tracking (directive section 35): open/pending
monetary risk, per-symbol exposure, currency-direction exposure,
USD-related exposure, and correlated-cluster exposure.

`PositionExposure` is a minimal, LOCAL stand-in for "a position/pending
order with a known monetary risk and direction" — the real execution/
position-management layer (not yet built) will supply these values from
actual broker state; this module only aggregates whatever it's given.

`evaluate_portfolio_risk_gate()` (execution-safety review round 2 finding
#6) is the deterministic PORTFOLIO HEAT policy `core.final_permission`'s
`BLOCK_PORTFOLIO_RISK` integrates — distinct from `risk.governor
.evaluate_risk_gate()`'s aggregate open+pending+proposed ceiling and from
`portfolio.correlation.evaluate_correlation_gate()`'s pairwise N/A-fails-
closed check: this gate looks at what adding the PROPOSED position would
do to per-symbol exposure, net currency-direction exposure, and
correlated-cluster exposure, each independently bounded by a
`PortfolioRiskLimits` ceiling that — per directive — must never exceed
the existing global `max_total_open_risk_pct` and is never raisable by
ML/RAG (this module imports neither).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from adaptive_scalper.config.constants import ALLOWED_CANONICAL_SYMBOLS
from adaptive_scalper.portfolio.correlation import CorrelationResult

ALLOW = "ALLOW"
BLOCK_PORTFOLIO_RISK = "BLOCK_PORTFOLIO_RISK"

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
    """A RESTING/PLACED order is still potential broker exposure (directive
    section 30/external review finding #10) — until broker truth confirms
    filled/cancelled/expired/rejected, it must participate in every heat
    dimension exactly like an OPEN position: total risk, per-symbol heat,
    currency-direction heat, USD heat, and (via `symbol_exposure`)
    correlated-cluster heat. `total_open_risk`/`total_pending_risk` stay
    separately reported (dashboard/observability needs to distinguish
    filled from resting exposure), but every OTHER field below is the
    combined OPEN + PENDING view — external review finding #11: a prior
    version only ever folded `pending_positions` into the total-risk sum,
    leaving per-symbol/currency/USD/cluster heat blind to resting orders."""
    symbol_exposure: dict[str, float] = {}
    currency_direction: dict[str, float] = {}
    positions_per_symbol: dict[str, int] = {}
    usd_related = 0.0

    for pos in (*open_positions, *pending_positions):
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
    """Combined exposure for every pair of symbols BOTH carrying OPEN OR
    PENDING risk (`exposure.symbol_exposure` is the combined view — see
    `compute_exposure()`) AND measurably highly correlated (|corr| >=
    threshold, N/A pairs excluded — never assumed correlated or
    uncorrelated)."""
    symbols_with_exposure = [s for s, risk in exposure.symbol_exposure.items() if risk > 0]
    clusters: dict[frozenset[str], float] = {}
    for i, a in enumerate(symbols_with_exposure):
        for b in symbols_with_exposure[i + 1:]:
            result = correlation_matrix.get((a, b), CorrelationResult(None, 0))
            if result.correlation is None or abs(result.correlation) < high_correlation_threshold:
                continue
            key = frozenset({a, b})
            clusters[key] = exposure.symbol_exposure[a] + exposure.symbol_exposure[b]
    return clusters


@dataclass(frozen=True)
class PortfolioRiskLimits:
    max_total_open_risk_pct: float
    max_symbol_risk_pct: float
    max_currency_direction_risk_pct: float
    max_correlated_cluster_risk_pct: float


def portfolio_risk_limits_from_risk_limits(max_total_open_risk_pct: float) -> PortfolioRiskLimits:
    """Conservative default per directive: every portfolio-heat
    sub-ceiling equals the SAME global `max_total_open_risk_pct` already
    enforced by `risk.governor.evaluate_risk_gate()` — never
    independently higher, and not an arbitrary invented number. A
    deployment wanting STRICTER per-symbol/currency/cluster ceilings
    constructs `PortfolioRiskLimits` directly instead of using this
    convenience constructor."""
    return PortfolioRiskLimits(
        max_total_open_risk_pct=max_total_open_risk_pct,
        max_symbol_risk_pct=max_total_open_risk_pct,
        max_currency_direction_risk_pct=max_total_open_risk_pct,
        max_correlated_cluster_risk_pct=max_total_open_risk_pct,
    )


def evaluate_portfolio_risk_gate(
    *,
    proposed_symbol: str,
    proposed_direction: str,
    proposed_monetary_risk: float,
    equity: float,
    open_positions: list[PositionExposure],
    pending_positions: list[PositionExposure],
    correlation_matrix: dict[tuple[str, str], CorrelationResult],
    limits: PortfolioRiskLimits,
    high_correlation_threshold: float = 0.7,
) -> tuple[str, str]:
    """Deterministic portfolio-heat policy (execution-safety review round
    2 finding #6). Simulates adding the PROPOSED position to current
    open/pending exposure, then checks total/per-symbol/net-currency-
    direction/correlated-cluster risk against `limits`, each
    independently. Checked in a fixed order; returns the FIRST breached
    ceiling. Fail-closed on invalid equity/risk inputs, exactly like
    `risk.governor.evaluate_risk_gate()`."""
    if equity <= 0 or not math.isfinite(equity):
        return BLOCK_PORTFOLIO_RISK, f"equity must be a positive, finite number, got {equity!r}"
    if proposed_monetary_risk <= 0 or not math.isfinite(proposed_monetary_risk):
        return BLOCK_PORTFOLIO_RISK, f"proposed_monetary_risk must be a positive, finite number, got {proposed_monetary_risk!r}"

    proposed = PositionExposure(proposed_symbol, proposed_direction, proposed_monetary_risk)
    exposure_after = compute_exposure([*open_positions, proposed], pending_positions)

    max_total = equity * (limits.max_total_open_risk_pct / 100.0)
    total_after = exposure_after.total_open_risk + exposure_after.total_pending_risk
    if total_after > max_total:
        return BLOCK_PORTFOLIO_RISK, (
            f"total portfolio risk after this trade ({total_after:.2f}) would exceed "
            f"{max_total:.2f} ({limits.max_total_open_risk_pct}% of equity)"
        )

    max_symbol = equity * (limits.max_symbol_risk_pct / 100.0)
    symbol_risk_after = exposure_after.symbol_exposure.get(proposed_symbol, 0.0)
    if symbol_risk_after > max_symbol:
        return BLOCK_PORTFOLIO_RISK, (
            f"per-symbol risk for {proposed_symbol} after this trade ({symbol_risk_after:.2f}) would "
            f"exceed {max_symbol:.2f} ({limits.max_symbol_risk_pct}% of equity)"
        )

    max_currency = equity * (limits.max_currency_direction_risk_pct / 100.0)
    for currency in CANONICAL_CURRENCY_PAIR[proposed_symbol]:
        net = exposure_after.currency_direction_exposure.get(currency, 0.0)
        if abs(net) > max_currency:
            return BLOCK_PORTFOLIO_RISK, (
                f"net {currency} exposure after this trade ({net:.2f}) would exceed the {max_currency:.2f} "
                f"({limits.max_currency_direction_risk_pct}% of equity) ceiling"
            )

    max_cluster = equity * (limits.max_correlated_cluster_risk_pct / 100.0)
    clusters = correlated_cluster_exposure(exposure_after, correlation_matrix, high_correlation_threshold)
    for pair, combined in sorted(clusters.items(), key=lambda kv: sorted(kv[0])):
        if proposed_symbol in pair and combined > max_cluster:
            return BLOCK_PORTFOLIO_RISK, (
                f"correlated-cluster risk for {sorted(pair)} after this trade ({combined:.2f}) would "
                f"exceed {max_cluster:.2f} ({limits.max_correlated_cluster_risk_pct}% of equity)"
            )

    return ALLOW, "within all portfolio heat limits"
