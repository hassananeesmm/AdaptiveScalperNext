"""Research-only selectors for `run_backtest(research_selector=...)`.

Both return the live selector's `SelectionResult` shape so the engine's
candidate log and fill path are unchanged; FLAT (`selected=None`) is a
normal, deliberate outcome. The live `selector.select_proposal` is never
modified -- H2 wraps it, H3 replaces it inside a research backtest only.
"""

from __future__ import annotations

from adaptive_scalper.research.v2.calibration import StrategyCalibration
from adaptive_scalper.selector.selector import (
    REJECTED_UNKNOWN_COST,
    CandidateEvaluation,
    SelectionResult,
    select_proposal,
)

REJECTED_FRICTION = "research_v2:target_below_friction_multiple"
REJECTED_COOLDOWN = "research_v2:cooldown_after_exit"
REJECTED_UNCALIBRATED = "research_v2:calibration_unknown_abstain"
REJECTED_NO_CALIBRATED_EDGE = "research_v2:calibrated_edge_not_demonstrated"

HIGH_FREQUENCY_STRATEGIES = frozenset({"microstructure_acceleration", "statistical_reversion"})


def friction_filter_selector(k: float, *, cooldown_bars: int, bar_seconds: int,
                             cooldown_keys=HIGH_FREQUENCY_STRATEGIES):
    """H2: a candidate is eligible only if its target distance is at least
    `k` x the estimated round-trip cost at the signal bar, and (for the
    high-frequency strategies) at least `cooldown_bars` bars have passed
    since the session's last exit. Survivors go to the unchanged V1 ranking."""

    def select(candidates, cost_estimates, *, min_net_edge_price, min_raw_confidence, bar_time_utc,
               last_exit_time_utc):
        evaluations, eligible = [], []
        for signal in candidates:
            cost = cost_estimates.get(signal.canonical_symbol)
            if cost is None:
                evaluations.append(CandidateEvaluation(signal, None, True, REJECTED_UNKNOWN_COST))
            elif signal.target_distance < k * cost.total_cost:
                evaluations.append(CandidateEvaluation(signal, None, True, REJECTED_FRICTION))
            elif (signal.strategy_key in cooldown_keys and last_exit_time_utc is not None
                  and bar_time_utc - last_exit_time_utc < cooldown_bars * bar_seconds):
                evaluations.append(CandidateEvaluation(signal, None, True, REJECTED_COOLDOWN))
            else:
                eligible.append(signal)
        inner = select_proposal(eligible, cost_estimates, min_net_edge_price=min_net_edge_price,
                                min_raw_confidence=min_raw_confidence)
        return SelectionResult(inner.selected, f"[H2 k={k}] {inner.reason}", tuple(evaluations) + inner.candidates)

    return select


def calibrated_selector(calibrations: dict[str, StrategyCalibration | None], *, margin: float):
    """H3: ranks on OUT-OF-FOLD calibrated evidence, never raw_confidence.
    score = (mean gross R - 1 SE) - margin x mean cost R, from the strategy's
    own earlier trades in the same raw_confidence bin. UNKNOWN calibration
    or score <= 0 -> the candidate abstains; no candidate -> FLAT.
    `expected_net_edge` in the evaluations is this score IN R (not price)."""

    def select(candidates, cost_estimates, *, min_net_edge_price, min_raw_confidence, bar_time_utc,
               last_exit_time_utc):
        evaluations, best, best_score = [], None, None
        for signal in candidates:
            if cost_estimates.get(signal.canonical_symbol) is None:
                evaluations.append(CandidateEvaluation(signal, None, True, REJECTED_UNKNOWN_COST))
                continue
            calibration = calibrations.get(signal.strategy_key)
            if calibration is None:
                evaluations.append(CandidateEvaluation(signal, None, True, REJECTED_UNCALIBRATED))
                continue
            est = calibration.lookup(signal.raw_confidence)
            score = (est.mean_gross_r - est.se_gross_r) - margin * est.mean_cost_r
            if score <= 0:
                evaluations.append(CandidateEvaluation(signal, score, True, REJECTED_NO_CALIBRATED_EDGE))
                continue
            evaluations.append(CandidateEvaluation(signal, score, False, None))
            if best_score is None or score > best_score:
                best, best_score = signal, score
        if best is None:
            return SelectionResult(None, f"[H3 m={margin}] no calibrated edge demonstrated -- FLAT", tuple(evaluations))
        return SelectionResult(best, f"[H3 m={margin}] {best.strategy_key} calibrated score {best_score:.3f}R",
                               tuple(evaluations))

    return select
