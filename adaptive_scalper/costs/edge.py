"""Expected net edge gate (directive section 34).

expected_net_edge = expected_gross_edge - total_cost

Stage 0 (directive section 61: no model/RAG influence yet — deterministic
strategies only): `expected_gross_edge` comes directly from the
strategy's OWN hypothesis (`StrategySignal.raw_confidence`,
`target_distance`, `stop_distance`) via a standard expected-value
calculation, not from a trained model. Once a model/RAG/selector exist,
this is exactly the seam where their bounded adjustments (directive
section 71) would plug in — this function's signature does not need to
change; only what's passed as the confidence would.
"""

from __future__ import annotations

from dataclasses import dataclass

from adaptive_scalper.costs.model import CostEstimate
from adaptive_scalper.strategies.base import StrategySignal

ALLOW = "ALLOW"
BLOCK_COST = "BLOCK_COST"
BLOCK_EXPECTED_EDGE = "BLOCK_EXPECTED_EDGE"


@dataclass(frozen=True)
class EdgeEvaluation:
    expected_gross_edge: float
    cost: CostEstimate
    expected_net_edge: float
    sufficient: bool
    reason: str


def expected_gross_edge_price(signal: StrategySignal) -> float:
    """Expected value in PRICE units, from the strategy's own
    stop/target/confidence alone:

        EV = p * target_distance - (1 - p) * stop_distance

    where p = raw_confidence. This assumes the strategy's implied
    probability is taken at face value (Stage 0) — no calibration
    adjustment happens here."""
    p = signal.raw_confidence
    return p * signal.target_distance - (1 - p) * signal.stop_distance


def evaluate_expected_edge(
    signal: StrategySignal, cost: CostEstimate, min_net_edge_price: float = 0.0
) -> EdgeEvaluation:
    gross = expected_gross_edge_price(signal)
    net = gross - cost.total_cost
    sufficient = net > min_net_edge_price
    reason = (
        f"gross_edge={gross:.5f} total_cost={cost.total_cost:.5f} "
        f"net_edge={net:.5f} min_required={min_net_edge_price:.5f}"
    )
    return EdgeEvaluation(gross, cost, net, sufficient, reason)


def evaluate_cost_gate(
    signal: StrategySignal, cost: CostEstimate | None, min_net_edge_price: float = 0.0
) -> tuple[str, EdgeEvaluation | None]:
    """The gate a composed final permission check calls. `cost=None`
    means costs could not be determined at all (e.g. no live quote/
    contract spec) — that's `BLOCK_COST`, distinct from `BLOCK_EXPECTED_EDGE`
    (costs ARE known, but the net edge doesn't clear the bar)."""
    if cost is None:
        return BLOCK_COST, None
    evaluation = evaluate_expected_edge(signal, cost, min_net_edge_price)
    if not evaluation.sufficient:
        return BLOCK_EXPECTED_EDGE, evaluation
    return ALLOW, evaluation
