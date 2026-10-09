"""Forward-evidence evaluator (ASN-031 step 1): docs/audits/FORWARD_EVIDENCE_PROTOCOL.md.

Read-only. It applies the PREREGISTERED protocol (thresholds come only from
`validation.certificate.PREREGISTERED_PROTOCOL`; nothing here can be passed
a lower one) to the DEMO shadow observer's lifecycle outcomes
(`shadow_lifecycle_outcomes` joined to `shadow_candidates`, migration 0032)
and returns one of the protocol's outcome labels per strategy x symbol.

It NEVER issues, signs or stores a certificate, never trains a model and
never changes what the runtime may do: a passing report is the input to a
separate, human-reviewed issuance step (ASN-031), and the protocol's
promotion ladder then starts at forward PAPER, not DEMO.

Fail-closed choices (each documented in the report it produces):
- Independence: observations of one strategy x symbol are counted as
  independent only when they do not overlap in time (entry at/after the
  previous kept observation's exit), the sequence a single-position book
  could actually have traded. Raw candidate counts are reported separately.
- No interim peeking: below the preregistered sample size or block count the
  report carries counts only, never performance figures (optional stopping).
- A criterion the stored data cannot measure is NOT_EVALUATED, which can never
  produce an eligible label: the cost stress (slippage x1.5, spread at p75)
  needs the per-trade cost components, which lifecycle outcomes do not
  record; DSR needs the research ledger's cross-trial Sharpe variance; the
  safety / forbidden-data criterion needs a human attestation.
"""

from __future__ import annotations

import math
import random
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field

from adaptive_scalper.research.stats import deflated_sharpe_ratio, probabilistic_sharpe_ratio, return_moments
from adaptive_scalper.simulation.fill_model import COST_BROKER_DEMO_CONFIRMED
from adaptive_scalper.validation.certificate import PREREGISTERED_PROTOCOL, ProtocolThresholds

LABEL_INSUFFICIENT = "FORWARD EVIDENCE INSUFFICIENT — SYSTEM REMAINS FLAT"
LABEL_REJECTED = "FORWARD EDGE REJECTED — SYSTEM REMAINS FLAT"
LABEL_ELIGIBLE = "FORWARD POSITIVE NET EDGE VALIDATED — PAPER ELIGIBLE — HUMAN APPROVAL REQUIRED"

PASS, FAIL, NOT_EVALUATED, NOT_APPLICABLE = "PASS", "FAIL", "NOT_EVALUATED", "NOT_APPLICABLE"

BOOTSTRAP_RESAMPLES = 10_000
BOOTSTRAP_SEED = 20261003          # fixed: the same evidence always gives the same bound
CONFIDENCE = 0.95


@dataclass(frozen=True)
class ForwardObservation:
    entry_time_utc: int
    exit_time_utc: int
    direction: str
    gross_r: float
    cost_r: float
    net_r: float
    cost_provenance: str | None


@dataclass(frozen=True)
class Criterion:
    number: int
    name: str
    status: str
    detail: str


@dataclass(frozen=True)
class ForwardEvidenceReport:
    group: dict
    raw_observations: int
    effective_observations: int
    chronological_blocks: int
    label: str
    criteria: tuple[Criterion, ...] = ()
    statistics: dict = field(default_factory=dict)   # empty unless the sample thresholds were met


def effective_sequence(observations: list[ForwardObservation]) -> list[ForwardObservation]:
    """Chronological, non-overlapping subsequence (one position at a time)."""
    kept: list[ForwardObservation] = []
    for obs in sorted(observations, key=lambda o: (o.entry_time_utc, o.exit_time_utc)):
        if not kept or obs.entry_time_utc >= kept[-1].exit_time_utc:
            kept.append(obs)
    return kept


def chronological_blocks(sequence: list, n_blocks: int) -> list[list]:
    """`n_blocks` contiguous, non-overlapping blocks of (near-)equal count."""
    if n_blocks < 1 or len(sequence) < n_blocks:
        return []
    base, extra = divmod(len(sequence), n_blocks)
    blocks, cursor = [], 0
    for b in range(n_blocks):
        size = base + (1 if b < extra else 0)
        blocks.append(sequence[cursor:cursor + size])
        cursor += size
    return blocks


def block_bootstrap_lower_bound(blocks: list[list[float]], *, resamples: int = BOOTSTRAP_RESAMPLES,
                                seed: int = BOOTSTRAP_SEED, confidence: float = CONFIDENCE) -> float:
    """One-sided lower confidence bound of the mean, resampling whole
    chronological blocks with replacement (serial dependence stays inside)."""
    rng = random.Random(seed)
    means = []
    for _ in range(resamples):
        drawn = [rng.choice(blocks) for _ in blocks]
        values = [v for block in drawn for v in block]
        means.append(sum(values) / len(values))
    means.sort()
    return means[int(math.floor((1.0 - confidence) * resamples))]


def evaluate_group(
    group: dict, observations: list[ForwardObservation], *, ledger_trial_count: int | None,
    trial_sharpe_variance: float | None, safety_attested: bool | None = None,
    protocol: ProtocolThresholds = PREREGISTERED_PROTOCOL,
) -> ForwardEvidenceReport:
    if protocol is not PREREGISTERED_PROTOCOL:
        raise ValueError("forward evidence is evaluated against the preregistered protocol only")
    effective = effective_sequence(observations)
    blocks = chronological_blocks(effective, protocol.min_blocks)
    if len(effective) < protocol.min_effective_observations or len(blocks) < protocol.min_blocks:
        return ForwardEvidenceReport(
            group, len(observations), len(effective), len(blocks), LABEL_INSUFFICIENT,
            (Criterion(1, "sample size", FAIL,
                       f"{len(effective)} independent observations (need >= {protocol.min_effective_observations}) "
                       f"in {len(blocks)} chronological blocks (need >= {protocol.min_blocks}); performance is "
                       f"not reported before the preregistered sample exists"),),
        )

    net = [o.net_r for o in effective]
    gross = [o.gross_r for o in effective]
    mean_net, mean_gross = sum(net) / len(net), sum(gross) / len(gross)
    block_net = [[o.net_r for o in b] for b in blocks]
    block_means = [sum(b) / len(b) for b in block_net]
    positive_blocks = sum(1 for m in block_means if m > 0)
    lower_bound = block_bootstrap_lower_bound(block_net)
    total_net = sum(net)
    top_share = (max(net) / total_net) if total_net > 0 else math.inf
    stats: dict = {"mean_gross_r": mean_gross, "mean_net_r": mean_net, "net_r_lower_bound_95": lower_bound,
                   "block_mean_net_r": block_means, "positive_blocks": positive_blocks,
                   "max_single_trade_share": top_share}
    criteria = [
        Criterion(1, "sample size", PASS, f"{len(effective)} independent observations in {len(blocks)} blocks"),
        Criterion(2, "mean gross and net R > 0", PASS if mean_gross > 0 and mean_net > 0 else FAIL,
                  f"mean gross {mean_gross:.4f} R, mean net {mean_net:.4f} R"),
        Criterion(3, "one-sided 95% block-bootstrap lower bound of mean net R > 0",
                  PASS if lower_bound > 0 else FAIL, f"lower bound {lower_bound:.4f} R ({BOOTSTRAP_RESAMPLES} "
                                                     f"resamples, seed {BOOTSTRAP_SEED})"),
        Criterion(4, "block consistency and concentration",
                  PASS if positive_blocks >= protocol.min_positive_blocks
                  and top_share <= protocol.max_single_trade_share else FAIL,
                  f"{positive_blocks}/{len(blocks)} positive blocks (need >= {protocol.min_positive_blocks}); "
                  f"largest single trade {top_share:.1%} of total net R (max {protocol.max_single_trade_share:.0%})"),
        Criterion(5, "cost stress (slippage x1.5, spread at p75)", NOT_EVALUATED,
                  "lifecycle outcomes store the total cost_r only, not its slippage and spread components"),
        Criterion(6, "calibration", NOT_APPLICABLE,
                  "no probability is used by this evaluation; an executable certificate additionally needs a "
                  "calibrated model and calibrator (ECE <= 0.05), which do not exist"),
    ]
    try:
        moments = return_moments(net)
        psr = probabilistic_sharpe_ratio(moments.sharpe, 0.0, moments.n, moments.skew, moments.kurtosis)
        stats["psr"] = psr
        if ledger_trial_count is None or trial_sharpe_variance is None:
            criteria.append(Criterion(7, "PSR(0) and DSR >= 0.95", NOT_EVALUATED,
                                      f"PSR {psr:.3f}; DSR needs the research ledger's trial count and "
                                      f"cross-trial Sharpe variance"))
        elif ledger_trial_count < protocol.min_ledger_trials:
            criteria.append(Criterion(7, "PSR(0) and DSR >= 0.95", FAIL,
                                      f"ledger trial count {ledger_trial_count} < {protocol.min_ledger_trials}"))
        else:
            dsr = deflated_sharpe_ratio(moments.sharpe, moments.n, n_trials=ledger_trial_count,
                                        trial_sharpe_variance=trial_sharpe_variance, skew=moments.skew,
                                        kurtosis=moments.kurtosis)
            stats["dsr"] = dsr
            criteria.append(Criterion(7, "PSR(0) and DSR >= 0.95",
                                      PASS if psr >= protocol.min_psr and dsr >= protocol.min_dsr else FAIL,
                                      f"PSR {psr:.3f}, DSR {dsr:.3f} over {ledger_trial_count} ledger trials"))
    except ValueError as exc:
        criteria.append(Criterion(7, "PSR(0) and DSR >= 0.95", FAIL, f"not computable: {exc}"))
    criteria.append(Criterion(8, "PBO <= 0.2", NOT_APPLICABLE, "a single preregistered variant per group"))
    unconfirmed = sum(1 for o in effective if o.cost_provenance != COST_BROKER_DEMO_CONFIRMED)
    if unconfirmed:
        criteria.append(Criterion(9, "cost provenance, safety, data access", FAIL,
                                  f"{unconfirmed} observations without {COST_BROKER_DEMO_CONFIRMED} cost provenance"))
    elif safety_attested is None:
        criteria.append(Criterion(9, "cost provenance, safety, data access", NOT_EVALUATED,
                                  "cost provenance complete; zero safety violations and no forbidden data access "
                                  "need a human attestation"))
    else:
        criteria.append(Criterion(9, "cost provenance, safety, data access", PASS if safety_attested else FAIL,
                                  "human attestation recorded"))

    statuses = {c.status for c in criteria}
    if FAIL in statuses:
        label = LABEL_REJECTED
    elif NOT_EVALUATED in statuses:
        label = LABEL_INSUFFICIENT
    else:
        label = LABEL_ELIGIBLE
    return ForwardEvidenceReport(group, len(observations), len(effective), len(blocks), label, tuple(criteria), stats)


_QUERY = """
SELECT c.mode, c.strategy_key, c.strategy_version, c.canonical_symbol, c.lifecycle_version, c.direction,
       o.evaluator_version, o.entry_time_utc, o.exit_time_utc, o.gross_r, o.cost_r, o.net_r, o.cost_provenance
FROM shadow_lifecycle_outcomes o JOIN shadow_candidates c ON c.id = o.candidate_id
WHERE o.status = 'RESOLVED' AND c.observed_at_utc >= ?
  AND o.entry_time_utc IS NOT NULL AND o.exit_time_utc IS NOT NULL AND o.net_r IS NOT NULL
"""


def load_forward_observations(conn: sqlite3.Connection, *,
                              protocol: ProtocolThresholds = PREREGISTERED_PROTOCOL) -> dict[tuple, list]:
    """Resolved lifecycle outcomes observed after the research freeze, grouped
    by (mode, strategy, version, symbol, lifecycle version, evaluator version)."""
    groups: dict[tuple, list] = defaultdict(list)
    for r in conn.execute(_QUERY, (protocol.min_evidence_start_utc,)):
        key = (r["mode"], r["strategy_key"], r["strategy_version"], r["canonical_symbol"], r["lifecycle_version"],
               r["evaluator_version"])
        groups[key].append(ForwardObservation(r["entry_time_utc"], r["exit_time_utc"], r["direction"],
                                              r["gross_r"], r["cost_r"] or 0.0, r["net_r"], r["cost_provenance"]))
    return dict(groups)


def evaluate_database(conn: sqlite3.Connection, *, ledger_trial_count: int | None,
                      trial_sharpe_variance: float | None) -> list[ForwardEvidenceReport]:
    names = ("mode", "strategy_key", "strategy_version", "canonical_symbol", "lifecycle_version",
             "evaluator_version")
    return [evaluate_group(dict(zip(names, key)), obs, ledger_trial_count=ledger_trial_count,
                           trial_sharpe_variance=trial_sharpe_variance)
            for key, obs in sorted(load_forward_observations(conn).items())]
