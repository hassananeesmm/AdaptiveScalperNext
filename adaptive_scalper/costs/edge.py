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

import math

from adaptive_scalper.costs.edge_evidence import EVIDENCE_VALIDATED, EdgeEvidence
from adaptive_scalper.validation.certificate import (
    CURRENT_PROTOCOL,
    CertificateCheck,
    ProtocolThresholds,
    verify_certificate,
)
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


def executable_edge_check(
    signal: StrategySignal, evidence: EdgeEvidence | None, *, certificate_key: bytes | None, now_utc: int | None,
    protocol: ProtocolThresholds = CURRENT_PROTOCOL,
) -> CertificateCheck:
    """May this evidence authorize a broker order for THIS proposal now?
    Status VALIDATED is necessary, never sufficient: the certificate's seal,
    binding, freshness and the current protocol are re-verified here."""
    if evidence is None:
        return CertificateCheck(False, "NO_EDGE_EVIDENCE")
    if evidence.status != EVIDENCE_VALIDATED:
        return CertificateCheck(False, f"EVIDENCE_{evidence.status}")
    if now_utc is None:
        return CertificateCheck(False, "NO_CLOCK")
    if evidence.stop_distance_price is None or not math.isclose(evidence.stop_distance_price, signal.stop_distance,
                                                                rel_tol=1e-9, abs_tol=1e-12):
        return CertificateCheck(False, "EVIDENCE_FOR_ANOTHER_PROPOSAL")
    return verify_certificate(
        evidence.certificate, key=certificate_key, now_utc=now_utc, strategy_key=signal.strategy_key,
        strategy_version=signal.strategy_version, canonical_symbol=signal.canonical_symbol,
        direction=signal.direction, protocol=protocol,
    )


def evaluate_cost_gate(
    signal: StrategySignal, cost: CostEstimate | None, min_net_edge_price: float = 0.0, *,
    evidence: EdgeEvidence | None = None, executable: bool = True, certificate_key: bytes | None = None,
    now_utc: int | None = None, protocol: ProtocolThresholds = CURRENT_PROTOCOL,
) -> tuple[str, EdgeEvaluation | None]:
    """The gate a composed final permission check calls. `cost=None` means
    costs could not be determined (BLOCK_COST); no usable evidence means the
    expected edge is unknowable (BLOCK_EDGE_UNVALIDATED); both block.
    `executable=True` additionally demands a verified certificate."""
    if cost is None:
        return BLOCK_COST, None
    require_horizon(cost, HORIZON_FULL_ROUND_TRIP, "entry cost gate")
    if evidence is None:
        return BLOCK_EDGE_UNVALIDATED, None
    if executable and not executable_edge_check(signal, evidence, certificate_key=certificate_key, now_utc=now_utc,
                                                protocol=protocol).ok:
        return BLOCK_EDGE_UNVALIDATED, None
    evaluation = evaluate_expected_edge(signal, cost, evidence, min_net_edge_price)
    if not evaluation.sufficient:
        return BLOCK_EXPECTED_EDGE, evaluation
    return ALLOW, evaluation
