"""Explicit edge-evidence fixtures for tests (issue #6; audit section 8).

Production code has no verified expected-edge evidence, so every executable
gate is FLAT by default. Tests that exercise downstream mechanics need a
proposal to get past the edge gate, and must say so explicitly:

- `v1_replay_config(...)`: a BacktestConfig replaying the frozen V1 formula
  (`edge_model="LEGACY_V1_RAW_SCORE"`) -- for tests of fills, exits,
  pending entries, restart and accounting, whose expected numbers were
  derived under V1. Research replay only, never executable.
- `fixture_certificate(...)` / `fixture_validated_evidence(...)`: a
  ValidationCertificate SEALED with `TEST_CERTIFICATE_KEY` that satisfies
  every protocol threshold, bound to one strategy/version/symbol, and the
  VALIDATED EdgeEvidence built from it. It only verifies against
  `TEST_CERTIFICATE_KEY` and `TEST_PROTOCOL` -- the production protocol with
  the evidence-start date moved to 1970, because the runtime fakes run on
  2023 clocks. Production loads its key from a file outside Git and always
  uses CURRENT_PROTOCOL.
- `FixtureValidatedProvider`: builds that evidence per signal and carries the
  test key/protocol so `runtime_helpers.build_engine` can hand them to the
  runtime.

Imported as `from edge_fixtures import ...`, like runtime_helpers.
"""

from __future__ import annotations

import dataclasses

from adaptive_scalper.backtest.types import BacktestConfig
from adaptive_scalper.costs.edge_evidence import (
    EDGE_MODEL_LEGACY_V1_RAW_SCORE,
    CalibratedWinProbability,
    EdgeEvidence,
)
from adaptive_scalper.shadow.lifecycle import lifecycle_for
from adaptive_scalper.simulation.fill_model import COST_BROKER_DEMO_CONFIRMED, FILL_MODEL_VERSION
from adaptive_scalper.validation.certificate import (
    CERTIFICATE_SCHEMA,
    CURRENT_PROTOCOL,
    DIRECTION_ANY,
    PROTOCOL_VERSION,
    ValidationCertificate,
    seal_certificate,
)

LEGACY_V1 = EDGE_MODEL_LEGACY_V1_RAW_SCORE
TEST_CERTIFICATE_KEY = b"tests-only-edge-certificate-key-0123456789abcdef"
TEST_PROTOCOL = dataclasses.replace(CURRENT_PROTOCOL, min_evidence_start_utc=0)
TEST_MODEL = ("TEST_FIXTURE_ONLY", "1")


def v1_replay_config(**overrides) -> BacktestConfig:
    overrides.setdefault("edge_model", LEGACY_V1)
    return BacktestConfig(**overrides)


def _ensure_test_stub_lifecycle(strategy_key: str) -> None:
    """Runtime tests use stub strategies (e.g. `multi_position_stub`) that a
    real certificate could never cover. Declare a clearly labelled TEST_STUB
    lifecycle for such keys only -- never for a real strategy key, and never
    from production code (production has no registration hook)."""
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


def fixture_certificate(strategy_key="momentum_continuation", strategy_version=1, symbol="XAUUSD",
                        direction=DIRECTION_ANY, *, avg_win_r=10.0, avg_loss_r=1.0, key=TEST_CERTIFICATE_KEY,
                        **overrides) -> ValidationCertificate:
    _ensure_test_stub_lifecycle(strategy_key)
    lifecycle = lifecycle_for(strategy_key)
    fields = dict(
        schema=CERTIFICATE_SCHEMA, protocol_version=PROTOCOL_VERSION, strategy_key=strategy_key,
        strategy_version=strategy_version, canonical_symbol=symbol, direction=direction,
        lifecycle_id=lifecycle.strategy_key, lifecycle_version=lifecycle.lifecycle_version,
        horizon_seconds=lifecycle.horizon_seconds, model_id=TEST_MODEL[0], model_version=TEST_MODEL[1],
        calibration_method="platt", training_start_utc=1_000, training_end_utc=2_000,
        calibration_start_utc=2_000, calibration_end_utc=3_000, validation_start_utc=3_000,
        validation_end_utc=4_000, as_of_utc=4_000, expires_utc=4_000_000_000, raw_observations=900,
        effective_observations=400, chronological_blocks=8, positive_blocks=7, brier=0.2, log_loss=0.6, ece=0.02,
        calibration_passed=True, mean_gross_r=0.3, mean_net_r=0.2, net_r_lower_bound_95=0.05,
        avg_realized_win_r=avg_win_r, avg_realized_loss_r=avg_loss_r, max_single_trade_share=0.02,
        cost_model_version=FILL_MODEL_VERSION, cost_provenance=COST_BROKER_DEMO_CONFIRMED, cost_stress_net_r=0.1,
        ledger_trial_count=300, psr=0.99, dsr=0.97, pbo=0.1, safety_validation_passed=True,
        forbidden_data_access=False,
    )
    fields.update(overrides)
    return seal_certificate(ValidationCertificate(**fields), key)


def fixture_validated_evidence(p: float = 0.6, avg_win: float = 10.0, avg_loss: float = 1.0, *,
                               strategy_key="momentum_continuation", symbol="XAUUSD", stop_distance=1.0,
                               certificate: ValidationCertificate | None = None, **cert_overrides) -> EdgeEvidence:
    cert = certificate or fixture_certificate(strategy_key, symbol=symbol, avg_win_r=avg_win, avg_loss_r=avg_loss,
                                              **cert_overrides)
    probability = CalibratedWinProbability(p, f"{cert.model_id}:{cert.model_version}", "platt", 400)
    return EdgeEvidence.from_certificate(cert, probability, stop_distance_price=stop_distance)


class FixtureValidatedProvider:
    """Sealed, protocol-satisfying evidence for EVERY signal (tests only)."""

    model_id = TEST_MODEL[0]
    certificate_key = TEST_CERTIFICATE_KEY
    validation_protocol = TEST_PROTOCOL

    def __init__(self, p: float = 0.6, avg_win: float = 10.0, avg_loss: float = 1.0) -> None:
        self.p, self.avg_win, self.avg_loss = p, avg_win, avg_loss

    def for_signal(self, signal):
        cert = fixture_certificate(signal.strategy_key, signal.strategy_version, signal.canonical_symbol,
                                   avg_win_r=self.avg_win, avg_loss_r=self.avg_loss)
        return fixture_validated_evidence(self.p, certificate=cert, stop_distance=signal.stop_distance)
