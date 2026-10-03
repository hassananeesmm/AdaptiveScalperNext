"""Strategy selector.

Ranks candidate `StrategySignal`s NOT by raw confidence alone, but by
cost-adjusted expected net edge (`adaptive_scalper.costs.edge`) — a
strategy with lower raw confidence but a wider risk/reward ratio can
have a better expected outcome after real transaction costs than one
with higher raw confidence and a poor ratio. FLAT (no candidate
selected) is a valid, deliberate output, never treated as a failure to
find something (directive section 10).

Retired strategy keys are structurally impossible to reach this
function via `strategies.registry.StrategyRegistry` in the first place
— but this module checks independently too anyway, consistent with
every other safety boundary in this codebase using defense-in-depth
rather than one layer alone.

Does NOT: size money, clear the kill switch, override news, override
final permission, or submit orders — all strictly outside this module's
scope; its only output is "here is the best candidate" or "FLAT," never
a decision to act on it.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass

from adaptive_scalper.config.constants import RETIRED_STRATEGY_KEYS
from adaptive_scalper.costs.edge_evidence import NO_VALIDATED_EDGE_EVIDENCE, EdgeEvidenceProvider
from adaptive_scalper.costs.model import HORIZON_FULL_ROUND_TRIP, CostEstimate, require_horizon
from adaptive_scalper.journal.events import append_event
from adaptive_scalper.strategies.base import StrategySignal

REJECTED_RETIRED = "retired_strategy_key"
REJECTED_LOW_CONFIDENCE = "raw_confidence_below_minimum"
REJECTED_UNKNOWN_COST = "cost_unknown_for_this_symbol"
REJECTED_INSUFFICIENT_EDGE = "expected_net_edge_below_minimum"
REJECTED_EDGE_UNVALIDATED = "no_validated_edge_evidence"


@dataclass(frozen=True)
class CandidateEvaluation:
    signal: StrategySignal
    expected_net_edge: float | None
    rejected: bool
    rejection_reason: str | None


@dataclass(frozen=True)
class SelectionResult:
    selected: StrategySignal | None   # None means FLAT
    reason: str
    candidates: tuple[CandidateEvaluation, ...]


def select_proposal(
    candidates: list[StrategySignal],
    cost_estimates: dict[str, CostEstimate | None],
    *,
    min_net_edge_price: float = 0.0,
    min_raw_confidence: float = 0.0,
    edge_evidence: EdgeEvidenceProvider = NO_VALIDATED_EDGE_EVIDENCE,
) -> SelectionResult:
    """`cost_estimates` is keyed by `canonical_symbol` — candidates may
    span more than one of the three symbols in a single evaluation
    cycle, and cost genuinely differs by symbol, so a single shared
    estimate would be wrong. A symbol missing from `cost_estimates` (or
    mapped to `None`) rejects every candidate for that symbol with
    `REJECTED_UNKNOWN_COST` rather than guessing.

    Expected edge comes only from `edge_evidence` (costs/edge_evidence.py),
    never from `raw_confidence` read as a probability. The default provider
    has no validated evidence, so every candidate is rejected with
    `REJECTED_EDGE_UNVALIDATED` and the result is FLAT. `min_raw_confidence`
    is a raw-SCORE floor, not a probability threshold.
    """
    evaluations: list[CandidateEvaluation] = []
    best: StrategySignal | None = None
    best_edge: float | None = None

    for signal in candidates:
        if signal.strategy_key in RETIRED_STRATEGY_KEYS:
            evaluations.append(CandidateEvaluation(signal, None, True, REJECTED_RETIRED))
            continue

        if signal.raw_confidence < min_raw_confidence:
            evaluations.append(CandidateEvaluation(signal, None, True, REJECTED_LOW_CONFIDENCE))
            continue

        cost = cost_estimates.get(signal.canonical_symbol)
        require_horizon(cost, HORIZON_FULL_ROUND_TRIP, "selector")
        if cost is None:
            evaluations.append(CandidateEvaluation(signal, None, True, REJECTED_UNKNOWN_COST))
            continue

        evidence = edge_evidence.for_signal(signal)
        if evidence is None:
            evaluations.append(CandidateEvaluation(signal, None, True, REJECTED_EDGE_UNVALIDATED))
            continue
        net = evidence.expected_gross_edge_price - cost.total_cost
        if net <= min_net_edge_price:
            evaluations.append(CandidateEvaluation(signal, net, True, REJECTED_INSUFFICIENT_EDGE))
            continue

        evaluations.append(CandidateEvaluation(signal, net, False, None))
        if best_edge is None or net > best_edge:
            best, best_edge = signal, net

    if best is None:
        return SelectionResult(
            None, "no candidate cleared the retirement/confidence/cost/edge filters — FLAT", tuple(evaluations)
        )

    qualifying = sum(1 for e in evaluations if not e.rejected)
    return SelectionResult(
        best,
        f"selected {best.strategy_key} ({best.canonical_symbol} {best.direction}) with the highest "
        f"expected net edge ({best_edge:.5f}) among {qualifying} qualifying candidate(s)",
        tuple(evaluations),
    )


def select_and_journal_proposal(
    conn: sqlite3.Connection,
    candidates: list[StrategySignal],
    chain_keys: list[str],
    cost_estimates: dict[str, CostEstimate | None],
    *,
    min_net_edge_price: float = 0.0,
    min_raw_confidence: float = 0.0,
    now_utc: int | None = None,
    edge_evidence: EdgeEvidenceProvider = NO_VALIDATED_EDGE_EVIDENCE,
) -> SelectionResult:
    """Same as `select_proposal()`, plus journaling every candidate's
    outcome (directive: "Persist/journal: candidates, rejections,
    selected strategy, reason, evidence"):

    - A candidate that failed the retirement/confidence/cost/edge filter
      -> `SIGNAL_REJECTED`, with the specific rejection reason.
    - A candidate that qualified but lost to a higher-edge candidate ->
      `PROPOSAL_REJECTED`.
    - The winning candidate (if any) -> `PROPOSAL_CREATED`.

    `chain_keys` must have exactly one entry per candidate, in the same
    order — each candidate is its own decision lineage and gets its own
    journal chain, even when several candidates share a symbol or cycle.
    """
    if len(chain_keys) != len(candidates):
        raise ValueError("chain_keys must have exactly one entry per candidate, in the same order")

    result = select_proposal(
        candidates, cost_estimates, min_net_edge_price=min_net_edge_price, min_raw_confidence=min_raw_confidence,
        edge_evidence=edge_evidence,
    )
    now = now_utc if now_utc is not None else int(time.time())

    for chain_key, evaluation in zip(chain_keys, result.candidates):
        signal = evaluation.signal
        payload = {
            "strategy_key": signal.strategy_key, "direction": signal.direction,
            # Persisted name kept for compatibility; it is a heuristic raw
            # score, not a probability (issue #6).
            "raw_confidence": signal.raw_confidence, "raw_score_is_probability": False,
            "edge_model": edge_evidence.model_id, "expected_net_edge": evaluation.expected_net_edge,
        }
        if evaluation.rejected:
            event_type, payload["reason"] = "SIGNAL_REJECTED", evaluation.rejection_reason
        elif signal is result.selected:
            event_type, payload["reason"] = "PROPOSAL_CREATED", "highest expected net edge among qualifying candidates"
        else:
            event_type, payload["reason"] = "PROPOSAL_REJECTED", "another candidate had a higher expected net edge"

        append_event(
            conn, chain_key, event_type, now, signal.canonical_symbol, payload, strategy_key=signal.strategy_key
        )

    return result
