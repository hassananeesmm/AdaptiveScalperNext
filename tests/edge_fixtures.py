"""Explicit edge-evidence fixtures for tests (issue #6; release hardening).

Production code has no verified expected-edge evidence, so every executable
gate is FLAT by default. Tests that exercise downstream mechanics need a
proposal to get past the edge gate, and must say so explicitly:

- `v1_replay_config(...)`: a BacktestConfig replaying the frozen V1 formula
  (`edge_model="LEGACY_V1_RAW_SCORE"`) -- research replay only, never executable.
- `fixture_certificate(...)`: a ValidationCertificate SIGNED with the test-only
  Ed25519 seed `TEST_SIGNING_SEED` (deterministic, published here, worthless
  outside tests), binding the fixture model/calibrator ARTIFACTS by SHA-256.
  It verifies only against `TEST_PUBLIC_KEY`; production loads its public key
  from a file outside Git.
- `fixture_validated_evidence(...)`: EdgeEvidence whose probability is produced
  by `certified_probability` from those artifacts (the same recomputation the
  executable gate performs).
- `FixtureValidatedProvider`: that evidence per signal; it carries
  `certificate_public_key` so `runtime_helpers.build_engine` can hand the
  runtime the TEST public key.

The fixture certificates' evidence intervals are in 1970 because the runtime
fakes run on 2023 clocks; tests/conftest.py relaxes ONLY the evidence-start
(research-freeze) date of `CURRENT_PROTOCOL` for the test session. Every other
threshold is the preregistered one, and test_validation_certificate.py proves
in a clean interpreter that production's CURRENT_PROTOCOL is the preregistered
protocol.

Imported as `from edge_fixtures import ...`, like runtime_helpers.
"""

from __future__ import annotations

import dataclasses
import json
import math

from adaptive_scalper.backtest.types import BacktestConfig
from adaptive_scalper.costs.edge_evidence import (
    EDGE_MODEL_LEGACY_V1_RAW_SCORE,
    CalibratedWinProbability,
    EdgeEvidence,
)
from adaptive_scalper.features.bar_features import FEATURE_SCHEMA_VERSION
from adaptive_scalper.shadow.lifecycle import lifecycle_for
from adaptive_scalper.simulation.fill_model import COST_BROKER_DEMO_CONFIRMED, FILL_MODEL_VERSION
from adaptive_scalper.validation import ed25519
from adaptive_scalper.validation.certificate import (
    CALIBRATOR_ARTIFACT_SCHEMA,
    CERTIFICATE_SCHEMA,
    DIRECTION_ANY,
    FEATURE_TRANSFORM_VERSION,
    MODEL_ARTIFACT_SCHEMA,
    PREREGISTERED_PROTOCOL,
    PROTOCOL_VERSION,
    SIGNATURE_SCHEME,
    ValidationCertificate,
    artifact_sha256,
    certified_probability,
    issue_signature,
)

LEGACY_V1 = EDGE_MODEL_LEGACY_V1_RAW_SCORE
TEST_SIGNING_SEED = bytes(range(32))                       # tests only -- published, worthless elsewhere
TEST_PUBLIC_KEY = ed25519.public_key(TEST_SIGNING_SEED)
TEST_PROTOCOL = dataclasses.replace(PREREGISTERED_PROTOCOL, min_evidence_start_utc=0)
TEST_MODEL = ("TEST_FIXTURE_ONLY", "1")


def v1_replay_config(**overrides) -> BacktestConfig:
    overrides.setdefault("edge_model", LEGACY_V1)
    return BacktestConfig(**overrides)


def _ensure_test_stub_lifecycle(strategy_key: str) -> None:
    """Stub strategies (e.g. `multi_position_stub`) get a clearly labelled
    TEST_STUB lifecycle -- never a real strategy key, never from production."""
    from adaptive_scalper.shadow import lifecycle as lc
    from adaptive_scalper.strategies import build_active_registry

    if strategy_key in lc.V1_LIFECYCLES:
        return
    if strategy_key in build_active_registry().active_keys():
        raise AssertionError(f"real strategy {strategy_key} must declare its own lifecycle")
    lc.V1_LIFECYCLES[strategy_key] = lc.StrategyLifecycle(
        strategy_key=strategy_key, strategy_version=1, eligibility="test stub", entry_trigger="test stub",
        initial_risk="test stub", payoff_and_horizon="test stub", horizon_seconds=600,
        horizon_status=lc.HORIZON_UNVALIDATED_DEFAULT, thesis_mode=lc.THESIS_COUPLED_TO_ENTRY_TRIGGER,
        thesis_valid_condition=None, thesis_invalidated_condition=None, profit_management=None,
        lifecycle_version="TEST_STUB/1",
    )


def fixture_artifacts(strategy_key="momentum_continuation", strategy_version=1, symbol="XAUUSD", *, p=0.6,
                      features=(), weights=(), model=TEST_MODEL, method="platt") -> tuple[bytes, bytes]:
    """Canonical-JSON model + calibrator artifacts. With no features the
    model is a constant score chosen so the calibrated p equals `p`."""
    logit = math.log(p / (1 - p))
    model_bytes = json.dumps({
        "schema": MODEL_ARTIFACT_SCHEMA, "model_id": model[0], "model_version": model[1],
        "strategy_key": strategy_key, "strategy_version": strategy_version, "canonical_symbol": symbol,
        "feature_schema_version": FEATURE_SCHEMA_VERSION, "feature_transform_version": FEATURE_TRANSFORM_VERSION,
        "features": list(features), "weights": list(weights), "intercept": logit,
    }, sort_keys=True, separators=(",", ":")).encode()
    calibrator_bytes = json.dumps({
        "schema": CALIBRATOR_ARTIFACT_SCHEMA, "model_artifact_sha256": artifact_sha256(model_bytes),
        "method": method, "a": 1.0, "b": 0.0,
    }, sort_keys=True, separators=(",", ":")).encode()
    return model_bytes, calibrator_bytes


def fixture_certificate(strategy_key="momentum_continuation", strategy_version=1, symbol="XAUUSD",
                        direction=DIRECTION_ANY, *, avg_win_r=10.0, avg_loss_r=1.0, seed=TEST_SIGNING_SEED,
                        artifacts: tuple[bytes, bytes] | None = None, **overrides) -> ValidationCertificate:
    _ensure_test_stub_lifecycle(strategy_key)
    lifecycle = lifecycle_for(strategy_key)
    model_bytes, calibrator_bytes = artifacts or fixture_artifacts(strategy_key, strategy_version, symbol)
    fields = dict(
        schema=CERTIFICATE_SCHEMA, signature_scheme=SIGNATURE_SCHEME, issuer_key_id="",
        protocol_version=PROTOCOL_VERSION, strategy_key=strategy_key, strategy_version=strategy_version,
        canonical_symbol=symbol, direction=direction, lifecycle_id=lifecycle.strategy_key,
        lifecycle_version=lifecycle.lifecycle_version, horizon_seconds=lifecycle.horizon_seconds,
        model_id=TEST_MODEL[0], model_version=TEST_MODEL[1], model_artifact_sha256=artifact_sha256(model_bytes),
        calibrator_artifact_sha256=artifact_sha256(calibrator_bytes), calibration_method="platt",
        feature_schema_version=FEATURE_SCHEMA_VERSION, feature_transform_version=FEATURE_TRANSFORM_VERSION,
        training_start_utc=1_000, training_end_utc=2_000, calibration_start_utc=2_000, calibration_end_utc=3_000,
        validation_start_utc=3_000, validation_end_utc=4_000, as_of_utc=4_000, expires_utc=4_000_000_000,
        raw_observations=900, effective_observations=400, chronological_blocks=8, positive_blocks=7, brier=0.2,
        log_loss=0.6, ece=0.02, calibration_passed=True, mean_gross_r=0.3, mean_net_r=0.2,
        net_r_lower_bound_95=0.05, avg_realized_win_r=avg_win_r, avg_realized_loss_r=avg_loss_r,
        max_single_trade_share=0.02, cost_model_version=FILL_MODEL_VERSION,
        cost_provenance=COST_BROKER_DEMO_CONFIRMED, cost_stress_net_r=0.1, ledger_trial_count=300, psr=0.99,
        dsr=0.97, pbo=0.1, safety_validation_passed=True, forbidden_data_access=False,
    )
    fields.update(overrides)
    return issue_signature(ValidationCertificate(**fields), seed)


def fixture_validated_evidence(p: float = 0.6, avg_win: float = 10.0, avg_loss: float = 1.0, *,
                               strategy_key="momentum_continuation", strategy_version=1, symbol="XAUUSD",
                               stop_distance=1.0, features: dict | None = None,
                               certificate: ValidationCertificate | None = None, **cert_overrides) -> EdgeEvidence:
    artifacts = fixture_artifacts(strategy_key, strategy_version, symbol, p=p)
    cert = certificate or fixture_certificate(strategy_key, strategy_version, symbol, avg_win_r=avg_win,
                                              avg_loss_r=avg_loss, artifacts=artifacts, **cert_overrides)
    prediction = certified_probability(cert, *artifacts, features if features is not None else {})
    value = prediction.probability if prediction.ok else p      # a deliberately broken fixture keeps the claim
    probability = CalibratedWinProbability(value, f"{cert.model_id}:{cert.model_version}", "platt", 400)
    return EdgeEvidence.from_certificate(cert, probability, stop_distance_price=stop_distance,
                                         model_artifact=artifacts[0], calibrator_artifact=artifacts[1])


class FixtureValidatedProvider:
    """Signed, protocol-satisfying evidence for EVERY signal (tests only)."""

    model_id = TEST_MODEL[0]
    certificate_public_key = TEST_PUBLIC_KEY

    def __init__(self, p: float = 0.6, avg_win: float = 10.0, avg_loss: float = 1.0) -> None:
        self.p, self.avg_win, self.avg_loss = p, avg_win, avg_loss

    def for_signal(self, signal):
        return fixture_validated_evidence(self.p, self.avg_win, self.avg_loss, strategy_key=signal.strategy_key,
                                          strategy_version=signal.strategy_version, symbol=signal.canonical_symbol,
                                          stop_distance=signal.stop_distance)
