"""Continuous position expectancy (directive section 17, execution-safety
review round 2 finding #8).

For every open position, the question is never "was the original entry
right" — it is "if I were flat right now, with EVERYTHING known now,
would I still open roughly this exposure at the current price after
current costs?" `evaluate_position_expectancy()` answers that from
current evidence only and produces the typed `ExpectancyResult`
(`thesis_valid`, `regime_reversed`) that
`position_management.adaptive_exit.evaluate_adaptive_exit()` consumes —
`adaptive_exit` no longer has to accept fabricated booleans from a
caller; THIS module is what computes them, from a caller-supplied
evidence bundle it evaluates deterministically.

Pure decision core, no I/O — exactly like every other gate in this
codebase. The caller gathers current regime, current strategy-condition
validity, current cost/edge, and RAG/ML advisory signals immediately
beforehand and passes them in.

RAG/ML evidence is DELIBERATELY advisory-only here too, consistent with
`rag/service.py`'s and `learning/`'s own established contracts (RAG
cannot execute/raise risk/bypass permission; ML is observer-only, "not
authoritative"): `rag_advisory_negative`/`model_advisory_negative` are
recorded in `ExpectancyResult.reasons` for transparency, but neither one
can, by itself, flip `thesis_valid` to `False` — only measurable,
current evidence (strategy setup validity, current net edge) does that.
Making RAG/ML independently sufficient to force a close would make them
de facto authoritative over a live position, which both subsystems are
explicitly built never to be.
"""

from __future__ import annotations

from dataclasses import dataclass

# Regime pairs that count as a genuine reversal against a held position —
# TRENDING_UP <-> TRENDING_DOWN is the only unambiguous case; RANGE/
# COMPRESSION/VOLATILITY_EXPANSION/BREAKOUT/ERRATIC/UNKNOWN transitions
# are not treated as a reversal on their own (they don't contradict a
# directional thesis the way a full trend flip does).
_REVERSAL_PAIRS = frozenset({frozenset({"TRENDING_UP", "TRENDING_DOWN"})})


@dataclass(frozen=True)
class ExpectancyEvidence:
    entry_regime: str
    current_regime: str
    strategy_setup_still_valid: bool        # does the strategy's own entry condition still hold now?
    current_net_edge_price: float | None    # None = cost/edge currently unknown -> fails closed
    min_required_edge_price: float
    holding_seconds: int = 0
    current_r: float | None = None
    peak_r: float | None = None
    rag_advisory_negative: bool = False     # RAG precedent is advisory only -- see module docstring
    model_advisory_negative: bool = False   # ML observer signal is advisory only -- see module docstring


@dataclass(frozen=True)
class ExpectancyResult:
    thesis_valid: bool
    regime_reversed: bool
    reasons: tuple[str, ...]


def evaluate_position_expectancy(evidence: ExpectancyEvidence) -> ExpectancyResult:
    reasons: list[str] = []

    regime_reversed = frozenset({evidence.entry_regime, evidence.current_regime}) in _REVERSAL_PAIRS
    if regime_reversed:
        reasons.append(f"regime reversed from {evidence.entry_regime} to {evidence.current_regime}")

    thesis_valid = True

    if not evidence.strategy_setup_still_valid:
        thesis_valid = False
        reasons.append("strategy setup condition no longer holds at current price/features")

    if evidence.current_net_edge_price is None:
        thesis_valid = False
        reasons.append("current cost/edge is unknown -- cannot prove the setup remains worth holding, fails closed")
    elif evidence.current_net_edge_price < evidence.min_required_edge_price:
        thesis_valid = False
        reasons.append(
            f"current net edge ({evidence.current_net_edge_price:.5f}) is below the minimum required "
            f"({evidence.min_required_edge_price:.5f}) at the current price/cost"
        )

    if evidence.rag_advisory_negative:
        reasons.append("RAG surfaced a materially negative precedent (advisory only, not sole reason)")
    if evidence.model_advisory_negative:
        reasons.append("ML observer model surfaced a materially negative signal (advisory only, not sole reason)")

    if not reasons:
        reasons.append("current evidence still supports the original thesis")

    return ExpectancyResult(thesis_valid=thesis_valid, regime_reversed=regime_reversed, reasons=tuple(reasons))
