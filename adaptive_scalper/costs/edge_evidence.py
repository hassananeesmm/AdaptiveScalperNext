"""Evidence an executable expected-value gate may use (GitHub issue #6).

`StrategySignal.raw_confidence` is a heuristic RAW SCORE (each strategy builds
it differently, e.g. microstructure uses 2*|acceleration|/ATR capped at 1). It
is not P(win): the independent research (docs/research/
INDEPENDENT_STRATEGY_RESEARCH_2026-09-27.md section 5) measured a flat
realized win rate across every raw-score bucket. The configured target/stop
are not the realized payoff either: the adaptive exits close most trades well
before either is reached (docs/audits/PROFITABILITY_ROOT_CAUSE_FINAL.md).

An executable EV therefore needs two pieces of independent evidence:

- `CalibratedWinProbability`: P(realized win) for THIS proposal, from a
  calibration fitted and evaluated on data disjoint from anything used to
  design the strategy, with its sample size and method recorded;
- a sealed `validation.certificate.ValidationCertificate` for the
  calibrator and the lifecycle the runtime actually follows: binding,
  freshness, the preregistered protocol statistics, and the average realized
  win and loss (R units) of that lifecycle.

    EV_gross = stop * (p * avg_realized_win_r - (1 - p) * avg_realized_loss_r)

Until such evidence exists the provider is `NoValidatedEdgeEvidence`, which
returns None for every signal, and every executable gate fails closed (FLAT).
There is deliberately no function here that turns a raw score into a
probability.

`LegacyV1RawScoreEvidence` reproduces the frozen V1 formula
(p = raw_confidence, payoff = configured target/stop) so the H1-H9 research
and the single preregistered correctness replay stay reproducible. Its
evidence is labelled LEGACY_UNCALIBRATED, carries no probability object, and
the DEMO and PAPER runtimes refuse the provider (`require_executable_provider`).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Protocol

from adaptive_scalper.strategies.base import StrategySignal
from adaptive_scalper.validation.certificate import ValidationCertificate

EDGE_MODEL_NONE = "NONE"
EDGE_MODEL_LEGACY_V1_RAW_SCORE = "LEGACY_V1_RAW_SCORE"
EDGE_MODELS = (EDGE_MODEL_NONE, EDGE_MODEL_LEGACY_V1_RAW_SCORE)

EVIDENCE_VALIDATED = "VALIDATED"                      # passed a preregistered, independent calibration protocol
EVIDENCE_LEGACY_UNCALIBRATED = "LEGACY_UNCALIBRATED"  # V1 research replay only, never executable
EVIDENCE_TEST_FIXTURE = "EXPLICIT_TEST_FIXTURE"       # tests only, never executable
EVIDENCE_STATUSES = (EVIDENCE_VALIDATED, EVIDENCE_LEGACY_UNCALIBRATED, EVIDENCE_TEST_FIXTURE)


@dataclass(frozen=True)
class CalibratedWinProbability:
    value: float
    calibration_id: str
    method: str
    n_calibration: int

    def __post_init__(self) -> None:
        if not math.isfinite(self.value) or not (0.0 < self.value < 1.0):
            raise ValueError(f"calibrated probability must be in (0, 1), got {self.value!r}")
        if self.n_calibration < 1:
            raise ValueError("a calibrated probability needs at least one calibration observation")
        if not self.calibration_id or not self.method:
            raise ValueError("calibration_id and method are required")


@dataclass(frozen=True)
class EdgeEvidence:
    """VALIDATED evidence = a calibrated probability for THIS proposal + the
    sealed `ValidationCertificate` of the calibrator/lifecycle it came from +
    the proposal's stop distance. The realized payoff is the certificate's
    (R units):

        EV_gross_price = stop * (p * avg_realized_win_r - (1 - p) * avg_realized_loss_r)

    The status string alone authorizes nothing: every executable gate re-runs
    `validation.certificate.verify_certificate` (Ed25519 signature, binding, freshness,
    current protocol) at the moment of use."""

    expected_gross_edge_price: float
    status: str
    model_id: str
    probability: CalibratedWinProbability | None = None
    certificate: ValidationCertificate | None = None
    stop_distance_price: float | None = None
    # The exact certified artifacts (their SHA-256 are signed in the
    # certificate). The executable gate RECOMPUTES p from these and the
    # proposal's causal features; `probability` is only the provider's
    # claim and must equal the recomputation.
    model_artifact: bytes | None = None
    calibrator_artifact: bytes | None = None

    def __post_init__(self) -> None:
        if self.status not in EVIDENCE_STATUSES:
            raise ValueError(f"status must be one of {EVIDENCE_STATUSES}, got {self.status!r}")
        if not math.isfinite(self.expected_gross_edge_price):
            raise ValueError("expected_gross_edge_price must be finite")
        if self.status == EVIDENCE_VALIDATED:
            if self.probability is None or not isinstance(self.certificate, ValidationCertificate):
                raise ValueError("VALIDATED evidence needs a calibrated probability and a ValidationCertificate")
            if not isinstance(self.model_artifact, bytes) or not isinstance(self.calibrator_artifact, bytes):
                raise ValueError("VALIDATED evidence needs the certified model and calibrator artifacts")
            if self.stop_distance_price is None or not self.stop_distance_price > 0:
                raise ValueError("VALIDATED evidence needs the proposal's positive stop distance")
            cert = self.certificate
            if self.probability.calibration_id != f"{cert.model_id}:{cert.model_version}":
                raise ValueError("the probability must come from the certificate's model and version")
            if self.model_id != cert.model_id:
                raise ValueError("model_id must match the certificate")
            if not math.isclose(_ev_price(self.probability.value, cert, self.stop_distance_price),
                                self.expected_gross_edge_price, rel_tol=1e-12, abs_tol=1e-12):
                raise ValueError("VALIDATED expected edge must equal stop*(p*avg_win_r - (1-p)*avg_loss_r)")

    @classmethod
    def from_certificate(
        cls, certificate: ValidationCertificate, probability: CalibratedWinProbability, *, stop_distance_price: float,
        model_artifact: bytes, calibrator_artifact: bytes,
    ) -> "EdgeEvidence":
        return cls(_ev_price(probability.value, certificate, stop_distance_price), EVIDENCE_VALIDATED,
                   certificate.model_id, probability, certificate, stop_distance_price, model_artifact,
                   calibrator_artifact)


def _ev_price(p: float, cert: ValidationCertificate, stop_distance_price: float) -> float:
    return stop_distance_price * (p * cert.avg_realized_win_r - (1 - p) * cert.avg_realized_loss_r)


class EdgeEvidenceProvider(Protocol):
    model_id: str

    def for_signal(self, signal: StrategySignal) -> EdgeEvidence | None: ...


class NoValidatedEdgeEvidence:
    """The production default: no calibrated evidence exists, so every
    executable EV question is unanswerable and the gates return FLAT."""

    model_id = EDGE_MODEL_NONE

    def for_signal(self, signal: StrategySignal) -> EdgeEvidence | None:
        return None


class LegacyV1RawScoreEvidence:
    """Frozen V1 semantics for research replay ONLY: p = raw_confidence and
    the configured target/stop as the payoff, labelled LEGACY_UNCALIBRATED
    (no CalibratedWinProbability is ever built from a raw score)."""

    model_id = EDGE_MODEL_LEGACY_V1_RAW_SCORE

    def for_signal(self, signal: StrategySignal) -> EdgeEvidence | None:
        score = signal.raw_confidence
        return EdgeEvidence(
            score * signal.target_distance - (1 - score) * signal.stop_distance,
            EVIDENCE_LEGACY_UNCALIBRATED, self.model_id,
        )


NO_VALIDATED_EDGE_EVIDENCE = NoValidatedEdgeEvidence()
LEGACY_V1_RAW_SCORE_EVIDENCE = LegacyV1RawScoreEvidence()


def provider_for_model(model_id: str) -> EdgeEvidenceProvider:
    if model_id == EDGE_MODEL_NONE:
        return NO_VALIDATED_EDGE_EVIDENCE
    if model_id == EDGE_MODEL_LEGACY_V1_RAW_SCORE:
        return LEGACY_V1_RAW_SCORE_EVIDENCE
    raise ValueError(f"unknown edge model {model_id!r}; known: {EDGE_MODELS}")


def edge_provider_of(config) -> EdgeEvidenceProvider:
    """The provider a BacktestConfig asks for: its `edge_provider` object if
    one is set, otherwise the named `edge_model`."""
    provider = getattr(config, "edge_provider", None)
    return provider if provider is not None else provider_for_model(config.edge_model)


def edge_model_id_of(config) -> str:
    return edge_provider_of(config).model_id


def require_executable_provider(provider: EdgeEvidenceProvider, consumer: str) -> EdgeEvidenceProvider:
    """DEMO/PAPER refuse the legacy research-replay provider outright."""
    if provider.model_id == EDGE_MODEL_LEGACY_V1_RAW_SCORE or isinstance(provider, LegacyV1RawScoreEvidence):
        raise ValueError(f"{consumer} refuses the {EDGE_MODEL_LEGACY_V1_RAW_SCORE} research-replay edge model")
    return provider


def executable_evidence(evidence: EdgeEvidence | None) -> EdgeEvidence | None:
    """What an EXECUTABLE gate may act on: VALIDATED evidence only."""
    return evidence if evidence is not None and evidence.status == EVIDENCE_VALIDATED else None
