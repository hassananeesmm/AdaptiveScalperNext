"""Edge-validation certificate (adaptive_scalper/validation/certificate.py; audit section 8).

A bare VALIDATED status must never authorize exposure. These tests forge,
tamper with, expire, mis-bind and under-power certificates and check that
every one fails closed -- at the certificate, at the executable edge gate
and at final permission.
"""

from __future__ import annotations

import ast
import dataclasses
from pathlib import Path

import pytest

from adaptive_scalper.costs.edge import BLOCK_EDGE_UNVALIDATED, executable_edge_check
from adaptive_scalper.costs.edge_evidence import EVIDENCE_VALIDATED, CalibratedWinProbability, EdgeEvidence
from adaptive_scalper.strategies.base import StrategySignal
from adaptive_scalper.validation.certificate import (
    CURRENT_PROTOCOL,
    RESEARCH_FREEZE_UTC,
    load_certificate_key,
    seal_certificate,
    verify_certificate,
)
from edge_fixtures import TEST_CERTIFICATE_KEY, TEST_PROTOCOL, fixture_certificate, fixture_validated_evidence

ROOT = Path(__file__).resolve().parents[1] / "adaptive_scalper"
NOW = 10_000


def _verify(cert, *, key=TEST_CERTIFICATE_KEY, now=NOW, strategy="momentum_continuation", version=1,
            symbol="XAUUSD", direction="BUY", protocol=TEST_PROTOCOL):
    return verify_certificate(cert, key=key, now_utc=now, strategy_key=strategy, strategy_version=version,
                              canonical_symbol=symbol, direction=direction, protocol=protocol)


def _signal(**kw) -> StrategySignal:
    base = dict(strategy_key="momentum_continuation", strategy_version=1, canonical_symbol="XAUUSD",
                direction="BUY", raw_confidence=0.99, stop_distance=1.0, target_distance=3.0,
                expected_duration_seconds=600, entry_method="MARKET", regime="TRENDING_UP", rationale="t",
                feature_schema_version=1, data_timestamp=1000)
    base.update(kw)
    return StrategySignal(**base)


def test_a_sound_certificate_verifies():
    assert _verify(fixture_certificate()).ok


# --- forged / tampered ---------------------------------------------------------------

def test_unsealed_certificate_is_refused():
    cert = dataclasses.replace(fixture_certificate(), seal="")
    assert _verify(cert).reason == "BAD_SEAL"


def test_certificate_sealed_with_another_key_is_refused():
    forged = fixture_certificate(key=b"an-attacker-key-that-is-long-enough-000000")
    assert _verify(forged).reason == "BAD_SEAL"


def test_tampering_with_any_field_after_sealing_is_refused():
    cert = fixture_certificate()
    for change in ({"effective_observations": 5000}, {"canonical_symbol": "BTCUSD"}, {"expires_utc": 9_999_999_999},
                   {"net_r_lower_bound_95": 1.0}, {"cost_provenance": "BROKER_DEMO_CONFIRMED "}):
        assert _verify(dataclasses.replace(cert, **change)).reason == "BAD_SEAL", change


def test_no_key_means_nothing_verifies():
    assert _verify(fixture_certificate(), key=None).reason == "NO_VERIFICATION_KEY"


def test_a_bare_validated_status_cannot_be_constructed_without_a_certificate():
    with pytest.raises(ValueError):
        EdgeEvidence(1.0, EVIDENCE_VALIDATED, "forged")


# --- binding ------------------------------------------------------------------------

@pytest.mark.parametrize("kwargs, reason", [
    ({"strategy": "range_breakout"}, "WRONG_STRATEGY"),
    ({"version": 2}, "WRONG_STRATEGY_VERSION"),
    ({"symbol": "BTCUSD"}, "WRONG_SYMBOL"),
])
def test_wrong_strategy_version_or_symbol_is_refused(kwargs, reason):
    assert _verify(fixture_certificate(), **kwargs).reason == reason


def test_direction_specific_certificate_refuses_the_other_direction():
    assert _verify(fixture_certificate(direction="SELL"), direction="BUY").reason == "WRONG_DIRECTION"


def test_wrong_lifecycle_version_or_horizon_is_refused():
    assert _verify(fixture_certificate(lifecycle_version="V2_EXPLICIT/1")).reason == "WRONG_LIFECYCLE_VERSION"
    assert _verify(fixture_certificate(horizon_seconds=900)).reason == "WRONG_HORIZON"


# --- time -------------------------------------------------------------------------

def test_expired_certificate_is_refused():
    assert _verify(fixture_certificate(expires_utc=NOW)).reason == "EXPIRED"


def test_certificate_from_the_future_is_refused():
    assert _verify(fixture_certificate(as_of_utc=NOW + 1, validation_end_utc=NOW + 1)).reason == "AS_OF_IN_FUTURE"


def test_non_chronological_intervals_are_refused():
    assert _verify(fixture_certificate(calibration_start_utc=1_500)).reason == "INTERVALS_NOT_CHRONOLOGICAL"


def test_production_protocol_refuses_evidence_older_than_the_research_freeze():
    assert _verify(fixture_certificate(), protocol=CURRENT_PROTOCOL).reason == "EVIDENCE_PREDATES_RESEARCH_FREEZE"
    assert CURRENT_PROTOCOL.min_evidence_start_utc == RESEARCH_FREEZE_UTC


def test_evidence_overlapping_the_sealed_oos_interval_is_refused():
    oos_start, oos_end = 1782864000, 1789776000
    cert = fixture_certificate(training_start_utc=oos_start - 10, training_end_utc=oos_start + 10,
                               calibration_start_utc=oos_end + 10, calibration_end_utc=oos_end + 20,
                               validation_start_utc=oos_end + 20, validation_end_utc=oos_end + 30,
                               as_of_utc=oos_end + 30, expires_utc=4_000_000_000)
    assert _verify(cert, now=oos_end + 40).reason == "SEALED_OOS_OVERLAP"


# --- protocol -------------------------------------------------------------------------

@pytest.mark.parametrize("change, reason", [
    ({"effective_observations": 299}, "INSUFFICIENT_SAMPLE"),
    ({"raw_observations": 10}, "INSUFFICIENT_SAMPLE"),
    ({"chronological_blocks": 7}, "INSUFFICIENT_BLOCKS"),
    ({"positive_blocks": 5}, "INSUFFICIENT_BLOCKS"),
    ({"net_r_lower_bound_95": 0.0}, "NO_POSITIVE_NET_LOWER_BOUND"),
    ({"mean_net_r": -0.01}, "NO_POSITIVE_NET_LOWER_BOUND"),
    ({"mean_gross_r": 0.0}, "NO_POSITIVE_NET_LOWER_BOUND"),
    ({"cost_stress_net_r": -0.01}, "FAILS_COST_STRESS"),
    ({"max_single_trade_share": 0.11}, "SINGLE_TRADE_DEPENDENCE"),
    ({"cost_provenance": "UNVERIFIED_ASSUMPTION"}, "UNVERIFIED_COST_EVIDENCE"),
    ({"cost_provenance": "BROKER_SPEC_ESTIMATE"}, "UNVERIFIED_COST_EVIDENCE"),
    ({"cost_model_version": "fill_model/v2"}, "WRONG_COST_MODEL_VERSION"),
    ({"calibration_passed": False}, "CALIBRATION_FAILED"),
    ({"ece": 0.06}, "CALIBRATION_FAILED"),
    ({"psr": 0.94}, "PSR_DSR_BELOW_THRESHOLD"),
    ({"dsr": 0.5}, "PSR_DSR_BELOW_THRESHOLD"),
    ({"pbo": 0.21}, "PBO_ABOVE_THRESHOLD"),
    ({"ledger_trial_count": 10}, "TRIAL_COUNT_UNDERSTATED"),
    ({"safety_validation_passed": False}, "SAFETY_VALIDATION_FAILED"),
    ({"forbidden_data_access": True}, "SAFETY_VALIDATION_FAILED"),
    ({"protocol_version": "forward_evidence_protocol/older"}, "WRONG_PROTOCOL_VERSION"),
])
def test_every_protocol_failure_fails_closed(change, reason):
    assert _verify(fixture_certificate(**change)).reason == reason


def test_non_finite_statistics_cannot_even_be_sealed():
    with pytest.raises(ValueError):
        fixture_certificate(brier=float("nan"))


# --- executable gate / final permission -------------------------------------------------

def test_executable_gate_refuses_evidence_for_another_proposal():
    evidence = fixture_validated_evidence(stop_distance=1.0)
    check = executable_edge_check(_signal(stop_distance=2.5), evidence, certificate_key=TEST_CERTIFICATE_KEY,
                                  now_utc=NOW, protocol=TEST_PROTOCOL)
    assert check.reason == "EVIDENCE_FOR_ANOTHER_PROPOSAL"


@pytest.mark.parametrize("overrides", [
    {"expires_utc": NOW}, {"effective_observations": 10}, {"cost_provenance": "UNVERIFIED_ASSUMPTION"},
    {"strategy_version": 2},
])
def test_final_permission_blocks_unverifiable_evidence_end_to_end(overrides):
    from test_final_permission import _full_allow_input

    from adaptive_scalper.core.final_permission import evaluate_final_permission

    good = evaluate_final_permission(_full_allow_input())
    assert good.decision == "ALLOW"
    cert = fixture_certificate(**overrides)
    probability = CalibratedWinProbability(0.6, f"{cert.model_id}:{cert.model_version}", "platt", 400)
    bad = EdgeEvidence.from_certificate(cert, probability, stop_distance_price=1.0)
    result = evaluate_final_permission(_full_allow_input(edge_evidence=bad))
    assert result.decision == BLOCK_EDGE_UNVALIDATED


def test_final_permission_blocks_when_the_runtime_has_no_key():
    from test_final_permission import _full_allow_input

    from adaptive_scalper.core.final_permission import evaluate_final_permission

    result = evaluate_final_permission(_full_allow_input(certificate_key=None))
    assert result.decision == BLOCK_EDGE_UNVALIDATED and "NO_VERIFICATION_KEY" in result.reason


# --- key handling & structure -------------------------------------------------------------

def test_key_file_must_exist_and_be_long_enough(tmp_path, monkeypatch):
    monkeypatch.delenv("ASN_EDGE_CERTIFICATE_KEY_FILE", raising=False)
    assert load_certificate_key() is None
    short = tmp_path / "short.key"
    short.write_bytes(b"too-short")
    assert load_certificate_key(str(short)) is None
    good = tmp_path / "good.key"
    good.write_bytes(b"x" * 40)
    monkeypatch.setenv("ASN_EDGE_CERTIFICATE_KEY_FILE", str(good))
    assert load_certificate_key() == b"x" * 40
    with pytest.raises(ValueError):
        seal_certificate(fixture_certificate(), b"")


def _calls(tree, name):
    return [n for n in ast.walk(tree) if isinstance(n, ast.Call)
            and getattr(n.func, "id", getattr(n.func, "attr", None)) == name]


def test_only_the_validation_package_may_seal_and_only_certificate_py_sets_thresholds():
    sealers, thresholds = [], []
    for path in ROOT.rglob("*.py"):
        rel = path.relative_to(ROOT).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if _calls(tree, "seal_certificate") and not rel.startswith("validation/"):
            sealers.append(rel)
        if _calls(tree, "ProtocolThresholds") and rel != "validation/certificate.py":
            thresholds.append(rel)
    assert sealers == [] and thresholds == []


def test_validation_package_cannot_reach_risk_kill_switch_gateway_or_execution():
    for path in (ROOT / "validation").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        modules = {n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
        modules |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        forbidden = ("adaptive_scalper.risk", "adaptive_scalper.core", "adaptive_scalper.gateway",
                     "adaptive_scalper.execution", "adaptive_scalper.config", "MetaTrader5")
        assert not [m for m in modules if m.startswith(forbidden)], (path.name, modules)
