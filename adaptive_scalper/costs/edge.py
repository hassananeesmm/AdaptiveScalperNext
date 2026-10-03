"""Expected net edge gate (directive section 34; GitHub issue #6).

expected_net_edge = expected_gross_edge - total_cost

`expected_gross_edge` comes ONLY from an `EdgeEvidence` object
(costs/edge_evidence.py): a calibrated win probability and the realized
lifecycle payoff. `StrategySignal.raw_confidence` is a heuristic score and
never enters this formula directly. No evidence -> BLOCK_EDGE_UNVALIDATED
(FLAT), distinct from BLOCK_COST (cost unknown) and BLOCK_EXPECTED_EDGE
(evidence and cost known, net edge too small).

`executable=True` (DEMO final permission) additionally refuses any evidence
that is not VALIDATED, so research-replay evidence (LEGACY_UNCALIBRATED) or a
test fixture can never authorize a broker order.
"""

from __future__ import annotations

from dataclasses import dataclass

from adaptive_scalper.costs.edge_evidence import EVIDENCE_VALIDATED, EdgeEvidence
from adaptive_scalper.costs.model import HORIZON_FULL_ROUND_TRIP, CostEstimate, require_horizon
from adaptive_scalper.strategies.base import StrategySignal

ALLOW = "ALLOW"
BLOCK_COST = "BLOCK_COST"
BLOCK_EXPECTED_EDGE = "BLOCK_EXPECTED_EDGE"
BLOCK_EDGE_UNVALIDATED = "BLOCK_EDGE_UNVALIDATED"


@dataclass(frozen=True)
class EdgeEvaluation:
    expected_gross_edge: float
    cost: CostEstimate
    expected_net_edge: float
    sufficient: bool
    reason: str
    evidence_status: str = ""
    edge_model: str = ""


def evaluate_expected_edge(
    signal: StrategySignal, cost: CostEstimate, evidence: EdgeEvidence, min_net_edge_price: float = 0.0
) -> EdgeEvaluation:
    require_horizon(cost, HORIZON_FULL_ROUND_TRIP, "entry expected-edge gate")
    gross = evidence.expected_gross_edge_price
    net = gross - cost.total_cost
    sufficient = net > min_net_edge_price
    reason = (
        f"gross_edge={gross:.5f} total_cost={cost.total_cost:.5f} "
        f"net_edge={net:.5f} min_required={min_net_edge_price:.5f} "
        f"edge_model={evidence.model_id} evidence={evidence.status}"
    )
    return EdgeEvaluation(gross, cost, net, sufficient, reason, evidence.status, evidence.model_id)


def evaluate_cost_gate(
    signal: StrategySignal, cost: CostEstimate | None, min_net_edge_price: float = 0.0, *,
    evidence: EdgeEvidence | None = None, executable: bool = True,
) -> tuple[str, EdgeEvaluation | None]:
    """The gate a composed final permission check calls. `cost=None` means
    costs could not be determined (BLOCK_COST); no usable evidence means the
    expected edge is unknowable (BLOCK_EDGE_UNVALIDATED); both block."""
    if cost is None:
        return BLOCK_COST, None
    require_horizon(cost, HORIZON_FULL_ROUND_TRIP, "entry cost gate")
    if evidence is None or (executable and evidence.status != EVIDENCE_VALIDATED):
        return BLOCK_EDGE_UNVALIDATED, None
    evaluation = evaluate_expected_edge(signal, cost, evidence, min_net_edge_price)
    if not evaluation.sufficient:
        return BLOCK_EXPECTED_EDGE, evaluation
    return ALLOW, evaluation
