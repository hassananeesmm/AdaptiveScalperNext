"""Edge-validation certificates (validation/certificate.py; FLAT/SHADOW release hardening).

A bare VALIDATED status must never authorize exposure. These tests forge,
replay, substitute, mutate, expire, mis-bind and under-power certificates
and artifacts and check that every one fails closed -- at the certificate,
at the executable edge gate and at final permission. They also pin the
trust architecture: Ed25519, public key only in the runtime, no HMAC, no
injectable protocol, and a probability that can only come from the exact
certified model.
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
import json
import subprocess
import sys
from pathlib import Path

import pytest

from adaptive_scalper.costs.edge import BLOCK_EDGE_UNVALIDATED, evaluate_cost_gate, executable_edge_check
from adaptive_scalper.costs.edge_evidence import EVIDENCE_VALIDATED, CalibratedWinProbability, EdgeEvidence
from adaptive_scalper.strategies.base import StrategySignal
from adaptive_scalper.validation import certificate as certificate_module
from adaptive_scalper.validation import ed25519
from adaptive_scalper.validation.certificate import (
    CERTIFICATE_SCHEMA,
    PREREGISTERED_PROTOCOL,
    PRIVATE_KEY_PREFIX,
    PUBLIC_KEY_ENV,
    PUBLIC_KEY_PREFIX,
    RESEARCH_FREEZE_UTC,
    ProtocolThresholds,
    ValidationCertificate,
    _verify_against,
    artifact_sha256,
    certified_probability,
    issue_signature,
    key_id,
    load_certificate_public_key,
    verify_certificate,
)
from edge_fixtures import (
    TEST_PUBLIC_KEY,
    TEST_SIGNING_SEED,
    fixture_artifacts,
    fixture_certificate,
    fixture_validated_evidence,
)

REPO = Path(__file__).resolve().parents[1]
ROOT = REPO / "adaptive_scalper"
NOW = 10_000
ATTACKER_SEED = bytes(range(100, 132))


def _verify(cert, *, key=TEST_PUBLIC_KEY, now=NOW, strategy="momentum_continuation", version=1,
            symbol="XAUUSD", direction="BUY"):
    return verify_certificate(cert, public_key=key, now_utc=now, strategy_key=strategy, strategy_version=version,
                              canonical_symbol=symbol, direction=direction)


def _signal(**kw) -> StrategySignal:
    base = dict(strategy_key="momentum_continuation", strategy_version=1, canonical_symbol="XAUUSD",
                direction="BUY", raw_confidence=0.99, stop_distance=1.0, target_distance=3.0,
                expected_duration_seconds=600, entry_method="MARKET", regime="TRENDING_UP", rationale="t",
                feature_schema_version=1, data_timestamp=1000)
    base.update(kw)
    return StrategySignal(**base)


def _gate(evidence, signal=None, *, key=TEST_PUBLIC_KEY, features=None):
    return executable_edge_check(signal or _signal(), evidence, public_key=key, now_utc=NOW,
                                 proposal_features={} if features is None else features)


def _resign(cert, seed=TEST_SIGNING_SEED, **changes):
    """Re-sign a modified certificate with the given seed (an ISSUER act)."""
    return issue_signature(dataclasses.replace(cert, **changes), seed)


def test_a_sound_certificate_verifies():
    assert _verify(fixture_certificate()).ok
    assert _gate(fixture_validated_evidence()).ok


# --- Ed25519 primitive ----------------------------------------------------------------

def test_ed25519_matches_rfc8032_test_vectors():
    vectors = [  # RFC 8032 section 7.1, TEST 1-3: (secret, public, message, signature)
        ("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60",
         "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a", "",
         "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bacc61e39701cf9b46b"
         "d25bf5f0595bbe24655141438e7a100b"),
        ("4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb",
         "3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c", "72",
         "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da085ac1e43e15996e458f3613d0f11d8c"
         "387b2eaeb4302aeeb00d291612bb0c00"),
        ("c5aa8df43f9f837bedb7442f31dcb7b166d38535076f094b85ce3a2e0b4458f7",
         "fc51cd8e6218a1a38da47ed00230f0580816ed13ba3303ac5deb911548908025", "af82",
         "6291d657deec24024827e69c3abe01a30ce548a284743a445e3680d7db5ac3ac18ff9b538d16f290ae67f760984dc659"
         "4a7c15e9716ed28dc027beceea1ec40a"),
    ]
    for secret, public, message, signature in vectors:
        sk, pk, msg, sig = (bytes.fromhex(x) for x in (secret, public, message, signature))
        assert ed25519.public_key(sk) == pk
        assert ed25519.sign(sk, msg) == sig
        assert ed25519.verify(pk, msg, sig)
        assert not ed25519.verify(pk, msg + b"x", sig)


@pytest.mark.parametrize("public, signature", [
    (b"", b"\x00" * 64), (b"\x00" * 31, b"\x00" * 64), (None, b"\x00" * 64),
    ("TEST", b"\x00" * 64), (b"\xff" * 32, b"\x00" * 64), (b"\x00" * 32, b"\x00" * 63), (b"\x00" * 32, None),
])
def test_ed25519_verify_never_raises_on_garbage(public, signature):
    assert ed25519.verify(public, b"m", signature) is False


def test_ed25519_rejects_a_malleated_signature():
    """s + L encodes the same scalar mod L; RFC 8032 requires s < L."""
    sig = ed25519.sign(TEST_SIGNING_SEED, b"m")
    s = int.from_bytes(sig[32:], "little") + ed25519._Q
    malleated = sig[:32] + s.to_bytes(32, "little")
    assert ed25519.verify(TEST_PUBLIC_KEY, b"m", sig)
    assert not ed25519.verify(TEST_PUBLIC_KEY, b"m", malleated)


# --- forged / tampered / mutated ------------------------------------------------------

def test_unsigned_certificate_is_refused():
    assert _verify(dataclasses.replace(fixture_certificate(), signature="")).reason == "BAD_SIGNATURE"


def test_non_hex_signature_is_refused():
    assert _verify(dataclasses.replace(fixture_certificate(), signature="zz" * 64)).reason == "MALFORMED_CERTIFICATE"


def test_flipping_any_signature_bit_is_refused():
    cert = fixture_certificate()
    raw = bytearray(bytes.fromhex(cert.signature))
    for index in (0, 31, 32, 63):
        flipped = bytearray(raw)
        flipped[index] ^= 0x01
        assert _verify(dataclasses.replace(cert, signature=flipped.hex())).reason == "BAD_SIGNATURE", index


def test_certificate_signed_by_another_key_is_refused():
    forged = fixture_certificate(seed=ATTACKER_SEED)
    assert _verify(forged).reason == "BAD_SIGNATURE"
    # ... even if the attacker copies the trusted key id into the signed payload
    assert _verify(_resign(forged, ATTACKER_SEED)).reason == "BAD_SIGNATURE"


def test_a_runtime_holding_only_the_public_key_cannot_sign():
    """The public key is not a signing key: using it as a seed yields a
    different key pair, so its signatures do not verify."""
    impostor = fixture_certificate(seed=TEST_PUBLIC_KEY)
    assert _verify(impostor).reason == "BAD_SIGNATURE"


def _mutated(value):
    if isinstance(value, bool):
        return not value
    if isinstance(value, int):
        return value + 1
    if isinstance(value, float):
        return value + 0.001
    if value is None:
        return 0.0
    return value + "x"


def test_mutating_every_signed_field_breaks_the_signature():
    cert = fixture_certificate()
    names = [f.name for f in dataclasses.fields(ValidationCertificate) if f.name != "signature"]
    assert len(names) == 49
    for name in names:
        mutated = dataclasses.replace(cert, **{name: _mutated(getattr(cert, name))})
        assert not _verify(mutated).ok, name
        assert mutated.canonical_payload() != cert.canonical_payload(), name


def test_every_field_is_inside_the_signed_payload():
    cert = fixture_certificate()
    payload = json.loads(cert.canonical_payload())
    assert set(payload) == {f.name for f in dataclasses.fields(ValidationCertificate)} - {"signature"}


def test_canonical_serialization_is_deterministic_and_type_strict():
    a = fixture_certificate()
    b = fixture_certificate()
    assert a.canonical_payload() == b.canonical_payload() and a.signature == b.signature
    # 1 vs 1.0 vs True are different payloads -- no silent type coercion
    assert dataclasses.replace(a, strategy_version=1.0).canonical_payload() != a.canonical_payload()
    assert dataclasses.replace(a, strategy_version=True).canonical_payload() != a.canonical_payload()
    assert _verify(dataclasses.replace(a, strategy_version=True)).reason == "BAD_SIGNATURE"


@pytest.mark.parametrize("field", ["brier", "psr", "mean_net_r", "avg_realized_win_r", "pbo"])
@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_values_cannot_be_signed_or_verified(field, value):
    with pytest.raises(ValueError):
        fixture_certificate(**{field: value})
    assert _verify(dataclasses.replace(fixture_certificate(), **{field: value})).reason == "MALFORMED_CERTIFICATE"


@pytest.mark.parametrize("change, reason", [
    ({"schema": "edge_certificate/v1"}, "WRONG_SCHEMA"),
    ({"schema": "edge_certificate/v3"}, "WRONG_SCHEMA"),
    ({"signature_scheme": "hmac-sha256/v1"}, "UNKNOWN_SIGNATURE_SCHEME"),
    ({"protocol_version": "forward_evidence_protocol/older"}, "WRONG_PROTOCOL_VERSION"),
    ({"feature_schema_version": 2}, "WRONG_FEATURE_SCHEMA"),
    ({"feature_transform_version": "numeric_feature_vector/2"}, "WRONG_FEATURE_TRANSFORM"),
    ({"model_artifact_sha256": ""}, "MISSING_ARTIFACT_HASH"),
    ({"calibrator_artifact_sha256": "ABC"}, "MISSING_ARTIFACT_HASH"),
    ({"direction": "LONG"}, "INVALID_DIRECTION_POOLING"),
])
def test_unknown_versions_and_malformed_identities_fail_closed_even_when_validly_signed(change, reason):
    cert = _resign(fixture_certificate(), **change)
    if change.get("signature_scheme"):
        cert = dataclasses.replace(fixture_certificate(), **change)
    assert _verify(cert).reason == reason


def test_issuer_key_id_must_match_the_trusted_key():
    cert = fixture_certificate()
    assert cert.issuer_key_id == key_id(TEST_PUBLIC_KEY)
    unsigned = dataclasses.replace(cert, issuer_key_id="0" * 16, signature="")
    sig = ed25519.sign(TEST_SIGNING_SEED, unsigned.canonical_payload()).hex()
    assert _verify(dataclasses.replace(unsigned, signature=sig)).reason == "WRONG_ISSUER_KEY_ID"


def test_not_a_certificate_object_is_refused():
    duck = type("Duck", (), {f.name: getattr(fixture_certificate(), f.name)
                             for f in dataclasses.fields(ValidationCertificate)})()
    assert _verify(duck).reason == "NOT_A_CERTIFICATE"


def test_no_public_key_means_nothing_verifies():
    assert _verify(fixture_certificate(), key=None).reason == "NO_PUBLIC_KEY"
    assert _verify(fixture_certificate(), key=b"").reason == "NO_PUBLIC_KEY"


def test_a_bare_validated_status_cannot_be_constructed_without_a_certificate():
    with pytest.raises(ValueError):
        EdgeEvidence(1.0, EVIDENCE_VALIDATED, "forged")


# --- replay / binding ---------------------------------------------------------------

@pytest.mark.parametrize("kwargs, reason", [
    ({"strategy": "range_breakout"}, "WRONG_STRATEGY"),
    ({"version": 2}, "WRONG_STRATEGY_VERSION"),
    ({"symbol": "BTCUSD"}, "WRONG_SYMBOL"),
    ({"symbol": "GBPJPY"}, "WRONG_SYMBOL"),
])
def test_replaying_a_certificate_for_another_strategy_version_or_symbol_is_refused(kwargs, reason):
    assert _verify(fixture_certificate(), **kwargs).reason == reason


def test_direction_specific_certificate_refuses_the_other_direction():
    assert _verify(fixture_certificate(direction="SELL"), direction="BUY").reason == "WRONG_DIRECTION"
    assert _verify(fixture_certificate(direction="BUY"), direction="SELL").reason == "WRONG_DIRECTION"
    assert _verify(fixture_certificate(direction="BUY"), direction="BUY").ok
    assert _verify(fixture_certificate(direction="ANY"), direction="SELL").ok      # pooling validated


def test_wrong_lifecycle_or_horizon_is_refused():
    assert _verify(fixture_certificate(lifecycle_version="V2_EXPLICIT/1")).reason == "WRONG_LIFECYCLE_VERSION"
    assert _verify(fixture_certificate(lifecycle_id="range_breakout")).reason == "WRONG_LIFECYCLE"
    assert _verify(fixture_certificate(horizon_seconds=900)).reason == "WRONG_HORIZON"


def test_gate_refuses_a_valid_certificate_replayed_onto_another_proposal():
    evidence = fixture_validated_evidence()
    assert _gate(evidence, _signal(stop_distance=2.5)).reason == "EVIDENCE_FOR_ANOTHER_PROPOSAL"
    assert _gate(evidence, _signal(canonical_symbol="BTCUSD")).reason == "WRONG_SYMBOL"
    assert _gate(evidence, _signal(strategy_version=2)).reason == "WRONG_STRATEGY_VERSION"


# --- time -------------------------------------------------------------------------

def test_expired_certificate_is_refused():
    assert _verify(fixture_certificate(expires_utc=NOW)).reason == "EXPIRED"


def test_certificate_from_the_future_is_refused():
    assert _verify(fixture_certificate(as_of_utc=NOW + 1, validation_end_utc=NOW + 1)).reason == "AS_OF_IN_FUTURE"


def test_non_chronological_or_empty_intervals_are_refused():
    assert _verify(fixture_certificate(calibration_start_utc=1_500)).reason == "INTERVALS_NOT_CHRONOLOGICAL"
    assert _verify(fixture_certificate(training_end_utc=1_000)).reason == "EMPTY_INTERVAL"


def test_production_protocol_refuses_evidence_older_than_the_research_freeze():
    check = _verify_against(fixture_certificate(), public_key=TEST_PUBLIC_KEY, now_utc=NOW,
                            strategy_key="momentum_continuation", strategy_version=1, canonical_symbol="XAUUSD",
                            direction="BUY", protocol=PREREGISTERED_PROTOCOL)
    assert check.reason == "EVIDENCE_PREDATES_RESEARCH_FREEZE"
    assert PREREGISTERED_PROTOCOL.min_evidence_start_utc == RESEARCH_FREEZE_UTC


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
    ({"positive_blocks": 5}, "INSUFFICIENT_POSITIVE_BLOCKS"),
    ({"positive_blocks": 9}, "INSUFFICIENT_POSITIVE_BLOCKS"),
    ({"mean_gross_r": 0.0}, "NON_POSITIVE_GROSS"),
    ({"mean_net_r": -0.01}, "NON_POSITIVE_NET"),
    ({"net_r_lower_bound_95": 0.0}, "NON_POSITIVE_LOWER_BOUND"),
    ({"cost_stress_net_r": -0.01}, "FAILS_COST_STRESS"),
    ({"max_single_trade_share": 0.11}, "SINGLE_TRADE_DEPENDENCE"),
    ({"cost_provenance": "UNVERIFIED_ASSUMPTION"}, "UNVERIFIED_COST_EVIDENCE"),
    ({"cost_provenance": "BROKER_SPEC_ESTIMATE"}, "UNVERIFIED_COST_EVIDENCE"),
    ({"cost_model_version": "fill_model/v2"}, "WRONG_COST_MODEL_VERSION"),
    ({"calibration_passed": False}, "CALIBRATION_FAILED"),
    ({"brier": 1.5}, "CALIBRATION_FAILED"),
    ({"ece": 0.06}, "ECE_ABOVE_THRESHOLD"),
    ({"psr": 0.94}, "PSR_BELOW_THRESHOLD"),
    ({"dsr": 0.5}, "DSR_BELOW_THRESHOLD"),
    ({"pbo": 0.21}, "PBO_ABOVE_THRESHOLD"),
    ({"ledger_trial_count": 10}, "TRIAL_COUNT_UNDERSTATED"),
    ({"avg_realized_loss_r": 0.0}, "NO_REALIZED_PAYOFF"),
    ({"safety_validation_passed": False}, "SAFETY_VALIDATION_FAILED"),
    ({"forbidden_data_access": True}, "FORBIDDEN_DATA_ACCESS"),
])
def test_every_protocol_failure_fails_closed(change, reason):
    assert _verify(fixture_certificate(**change)).reason == reason


# --- probability provenance (certified model binding) -------------------------------------

def test_valid_certificate_with_an_invented_probability_never_reaches_exposure():
    from test_final_permission import _full_allow_input

    from adaptive_scalper.core.final_permission import evaluate_final_permission

    good = fixture_validated_evidence(p=0.6)
    invented = CalibratedWinProbability(0.99, good.probability.calibration_id, "platt", 400)
    forged = EdgeEvidence.from_certificate(good.certificate, invented, stop_distance_price=1.0,
                                           model_artifact=good.model_artifact,
                                           calibrator_artifact=good.calibrator_artifact)
    assert _verify(forged.certificate).ok                                   # the certificate itself is fine
    assert _gate(forged).reason == "PROBABILITY_NOT_FROM_CERTIFIED_MODEL"
    result = evaluate_final_permission(_full_allow_input(edge_evidence=forged))
    assert result.decision == BLOCK_EDGE_UNVALIDATED


def test_substituting_the_model_artifact_is_refused():
    good = fixture_validated_evidence(p=0.6)
    other_model, _ = fixture_artifacts(p=0.9)
    swapped = dataclasses.replace(good, model_artifact=other_model)
    assert _gate(swapped).reason == "WRONG_MODEL_ARTIFACT"


def test_substituting_the_calibrator_artifact_is_refused():
    good = fixture_validated_evidence(p=0.6)
    calibrator = json.loads(good.calibrator_artifact)
    calibrator["a"] = 5.0
    swapped = dataclasses.replace(good, calibrator_artifact=json.dumps(calibrator, sort_keys=True).encode())
    assert _gate(swapped).reason == "WRONG_CALIBRATOR_ARTIFACT"


def test_missing_artifacts_are_refused():
    good = fixture_validated_evidence()
    assert certified_probability(good.certificate, None, good.calibrator_artifact, {}).reason == "MISSING_MODEL_ARTIFACT"
    assert certified_probability(good.certificate, good.model_artifact, None, {}).reason == "MISSING_MODEL_ARTIFACT"


def test_artifacts_of_another_certified_model_cannot_be_combined():
    """Cert B (signed, sound) paired with cert A's artifacts: hashes differ."""
    a = fixture_validated_evidence(p=0.6)
    # same model id, different certified artifact bytes (e.g. a retrain)
    b_cert = fixture_certificate(artifacts=fixture_artifacts(p=0.7))
    assert _verify(b_cert).ok
    combined = dataclasses.replace(a, certificate=b_cert)
    assert _gate(combined).reason == "WRONG_MODEL_ARTIFACT"
    # another model id cannot even be assembled into VALIDATED evidence
    other = fixture_certificate(artifacts=fixture_artifacts(p=0.7, model=("OTHER_MODEL", "1")),
                                model_id="OTHER_MODEL")
    with pytest.raises(ValueError, match="model"):
        dataclasses.replace(a, certificate=other, model_id="OTHER_MODEL")


def test_artifact_identity_must_match_the_certificate():
    """Hash-correct artifacts whose declared identity disagrees with the
    signed certificate (e.g. a model trained for another symbol)."""
    artifacts = fixture_artifacts(symbol="BTCUSD")
    cert = fixture_certificate(artifacts=artifacts)                      # binds XAUUSD
    assert certified_probability(cert, *artifacts, {}).reason == "MODEL_IDENTITY_MISMATCH"
    model, _ = fixture_artifacts()
    calibrator = json.dumps({"schema": "edge_calibrator_artifact/v1", "model_artifact_sha256": "0" * 64,
                             "method": "platt", "a": 1.0, "b": 0.0}).encode()
    cert = fixture_certificate(artifacts=(model, calibrator))
    assert certified_probability(cert, model, calibrator, {}).reason == "CALIBRATOR_IDENTITY_MISMATCH"
    cert = fixture_certificate(artifacts=fixture_artifacts(method="isotonic"))
    assert certified_probability(cert, *fixture_artifacts(method="isotonic"), {}).reason == \
        "CALIBRATOR_IDENTITY_MISMATCH"


def test_probability_is_recomputed_from_the_proposal_features():
    artifacts = fixture_artifacts(features=("atr_pct", "trend"), weights=(2.0, -1.0), p=0.5)
    cert = fixture_certificate(artifacts=artifacts)
    low = certified_probability(cert, *artifacts, {"atr_pct": 0.1, "trend": 1.0})
    high = certified_probability(cert, *artifacts, {"atr_pct": 1.0, "trend": 0.0})
    assert low.ok and high.ok and low.probability < 0.5 < high.probability
    # evidence computed for one feature vector is refused for another
    probability = CalibratedWinProbability(high.probability, "TEST_FIXTURE_ONLY:1", "platt", 400)
    evidence = EdgeEvidence.from_certificate(cert, probability, stop_distance_price=1.0, model_artifact=artifacts[0],
                                             calibrator_artifact=artifacts[1])
    assert _gate(evidence, features={"atr_pct": 1.0, "trend": 0.0}).ok
    assert _gate(evidence, features={"atr_pct": 0.1, "trend": 1.0}).reason == "PROBABILITY_NOT_FROM_CERTIFIED_MODEL"


@pytest.mark.parametrize("features, reason", [
    (None, "NO_PROPOSAL_FEATURES"),
    ({"atr_pct": 0.5}, "MISSING_OR_NON_FINITE_FEATURE"),
    ({"atr_pct": 0.5, "trend": None}, "MISSING_OR_NON_FINITE_FEATURE"),
    ({"atr_pct": float("nan"), "trend": 1.0}, "MISSING_OR_NON_FINITE_FEATURE"),
    ({"atr_pct": True, "trend": 1.0}, "MISSING_OR_NON_FINITE_FEATURE"),
])
def test_missing_or_non_finite_features_fail_closed(features, reason):
    artifacts = fixture_artifacts(features=("atr_pct", "trend"), weights=(2.0, -1.0))
    cert = fixture_certificate(artifacts=artifacts)
    assert certified_probability(cert, *artifacts, features).reason == reason


def test_malformed_or_unknown_artifacts_fail_closed():
    model = b"not json"
    calibrator = b"{}"
    cert = fixture_certificate(artifacts=(model, calibrator))
    assert certified_probability(cert, model, calibrator, {}).reason == "MALFORMED_ARTIFACT"
    model = json.dumps({"schema": "edge_model_artifact/v9"}).encode()
    calibrator = json.dumps({"schema": "edge_calibrator_artifact/v1"}).encode()
    cert = fixture_certificate(artifacts=(model, calibrator))
    assert certified_probability(cert, model, calibrator, {}).reason == "UNKNOWN_ARTIFACT_SCHEMA"


# --- executable gate / final permission -------------------------------------------------

@pytest.mark.parametrize("overrides", [
    {"expires_utc": NOW}, {"effective_observations": 10}, {"cost_provenance": "UNVERIFIED_ASSUMPTION"},
    {"strategy_version": 2},
])
def test_final_permission_blocks_unverifiable_evidence_end_to_end(overrides):
    from test_final_permission import _full_allow_input

    from adaptive_scalper.core.final_permission import evaluate_final_permission

    assert evaluate_final_permission(_full_allow_input()).decision == "ALLOW"
    bad = fixture_validated_evidence(**overrides)
    result = evaluate_final_permission(_full_allow_input(edge_evidence=bad))
    assert result.decision == BLOCK_EDGE_UNVALIDATED


def test_final_permission_blocks_when_the_runtime_has_no_public_key():
    from test_final_permission import _full_allow_input

    from adaptive_scalper.core.final_permission import evaluate_final_permission

    result = evaluate_final_permission(_full_allow_input(public_key=None))
    assert result.decision == BLOCK_EDGE_UNVALIDATED and "NO_PUBLIC_KEY" in result.reason


def test_final_permission_blocks_with_the_wrong_trusted_public_key():
    from test_final_permission import _full_allow_input

    from adaptive_scalper.core.final_permission import evaluate_final_permission

    result = evaluate_final_permission(_full_allow_input(public_key=ed25519.public_key(ATTACKER_SEED)))
    assert result.decision == BLOCK_EDGE_UNVALIDATED and "BAD_SIGNATURE" in result.reason


# --- key handling ---------------------------------------------------------------------------

def test_public_key_file_loading_fails_closed(tmp_path, monkeypatch):
    monkeypatch.delenv(PUBLIC_KEY_ENV, raising=False)
    assert load_certificate_public_key() is None
    assert load_certificate_public_key(str(tmp_path / "missing.pub")) is None
    cases = {
        "raw.pub": TEST_PUBLIC_KEY.hex(),                                  # untagged
        "short.pub": PUBLIC_KEY_PREFIX + "ab" * 31,
        "nothex.pub": PUBLIC_KEY_PREFIX + "zz" * 32,
        "private.key": PRIVATE_KEY_PREFIX + TEST_SIGNING_SEED.hex(),     # a PRIVATE key is never loaded
    }
    for name, text in cases.items():
        (tmp_path / name).write_text(text, encoding="ascii")
        assert load_certificate_public_key(str(tmp_path / name)) is None, name
    good = tmp_path / "issuer.pub"
    good.write_text(PUBLIC_KEY_PREFIX + TEST_PUBLIC_KEY.hex() + "\n", encoding="ascii")
    monkeypatch.setenv(PUBLIC_KEY_ENV, str(good))
    assert load_certificate_public_key() == TEST_PUBLIC_KEY


def test_issuing_requires_a_32_byte_seed():
    for seed in (b"", b"short", b"x" * 64, "s" * 32):
        with pytest.raises(ValueError):
            issue_signature(fixture_certificate(), seed)


# --- architecture: no HMAC, no signing outside the issuer, no injectable protocol --------------

def _production_trees():
    for path in ROOT.rglob("*.py"):
        yield path.relative_to(ROOT).as_posix(), ast.parse(path.read_text(encoding="utf-8"))


def _calls(tree, name):
    return [n for n in ast.walk(tree) if isinstance(n, ast.Call)
            and getattr(n.func, "id", getattr(n.func, "attr", None)) == name]


def _imported(tree) -> set[str]:
    names = {n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    names |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    return names


def test_no_hmac_or_symmetric_certificate_path_exists():
    for rel, tree in _production_trees():
        if rel.startswith("validation/") or rel.startswith("costs/") or rel.startswith("runtime/") \
                or rel.startswith("core/"):
            assert "hmac" not in _imported(tree), rel
    source = (ROOT / "validation" / "certificate.py").read_text(encoding="utf-8")
    for legacy in ("seal_certificate", "load_certificate_key", "ASN_EDGE_CERTIFICATE_KEY_FILE", "compare_digest"):
        assert legacy not in source, legacy
    assert CERTIFICATE_SCHEMA == "edge_certificate/v2"


def test_only_the_validation_package_may_sign():
    offenders = []
    for rel, tree in _production_trees():
        if rel.startswith("validation/"):
            continue
        if _calls(tree, "issue_signature") or _calls(tree, "sign"):
            offenders.append(rel)
        attrs = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        aliases = {a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) for a in n.names}
        if {"issue_signature"} & (attrs | aliases) or ("sign" in aliases):
            offenders.append(rel)
    assert offenders == []


def test_runtime_execution_strategy_and_shadow_code_never_touch_private_keys():
    for rel, tree in _production_trees():
        if rel.startswith("validation/"):
            continue
        source = (ROOT / rel).read_text(encoding="utf-8")
        assert "ed25519-private" not in source and "PRIVATE_KEY_PREFIX" not in source, rel
        if rel.startswith(("runtime/", "execution/", "strategies/", "shadow/", "core/", "gateway/")):
            assert "adaptive_scalper.validation.ed25519" not in _imported(tree), rel


def test_config_and_repository_hold_no_certificate_key_material():
    default = (REPO / "config" / "default.toml").read_text(encoding="utf-8")
    assert "ed25519-private" not in default and "ed25519-public" not in default
    tracked = subprocess.run(["git", "ls-files"], cwd=REPO, capture_output=True, text=True, check=True).stdout
    assert not [f for f in tracked.splitlines() if f.endswith((".key", ".pem", ".seed"))]


def test_executable_api_has_no_protocol_parameter():
    from adaptive_scalper.core.final_permission import FinalPermissionInput
    from adaptive_scalper.runtime.demo import DemoRuntime
    from adaptive_scalper.runtime.engine import RuntimeComponents

    for fn in (verify_certificate, executable_edge_check, evaluate_cost_gate):
        params = set(inspect.signature(fn).parameters)
        assert not {"protocol", "validation_protocol", "thresholds"} & params, fn.__name__
    for cls in (FinalPermissionInput, DemoRuntime, RuntimeComponents):
        names = {f.name for f in dataclasses.fields(cls)}
        assert not {n for n in names if "protocol" in n or "threshold" in n}, cls.__name__
        assert "certificate_key" not in names, cls.__name__


def test_production_never_rebinds_or_builds_the_protocol():
    rebinds, builders, private_use = [], [], []
    for rel, tree in _production_trees():
        for node in ast.walk(tree):
            targets = []
            if isinstance(node, ast.Assign):
                targets = node.targets
            elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
                targets = [node.target]
            for target in targets:
                name = getattr(target, "id", getattr(target, "attr", None))
                if name in ("CURRENT_PROTOCOL", "PREREGISTERED_PROTOCOL") and rel != "validation/certificate.py":
                    rebinds.append(rel)
            if isinstance(node, ast.Call) and getattr(node.func, "attr", None) == "setattr":
                if any(isinstance(a, ast.Constant) and a.value in ("CURRENT_PROTOCOL", "PREREGISTERED_PROTOCOL")
                       for a in node.args):
                    rebinds.append(rel)
        if _calls(tree, "ProtocolThresholds") and rel != "validation/certificate.py":
            builders.append(rel)
        if _calls(tree, "_verify_against") and rel != "validation/certificate.py":
            private_use.append(rel)
    assert rebinds == [] and builders == [] and private_use == []


def test_production_current_protocol_is_the_preregistered_one_in_a_clean_interpreter():
    """conftest relaxes the freeze date for fixtures; a fresh interpreter
    (no pytest, no conftest) must see the preregistered values."""
    code = ("from adaptive_scalper.validation import certificate as c;"
            "import dataclasses, json;"
            "print(json.dumps([c.CURRENT_PROTOCOL is c.PREREGISTERED_PROTOCOL, dataclasses.asdict(c.CURRENT_PROTOCOL)]))")
    out = subprocess.run([sys.executable, "-c", code], cwd=REPO, capture_output=True, text=True, check=True).stdout
    same, values = json.loads(out)
    assert same is True
    assert values == dataclasses.asdict(ProtocolThresholds())
    assert values["min_evidence_start_utc"] == RESEARCH_FREEZE_UTC
    assert values["min_effective_observations"] == 300 and values["min_ledger_trials"] == 262


def test_the_test_session_relaxes_only_the_freeze_date():
    relaxed = dataclasses.asdict(certificate_module.CURRENT_PROTOCOL)
    preregistered = dataclasses.asdict(PREREGISTERED_PROTOCOL)
    assert {k for k in relaxed if relaxed[k] != preregistered[k]} == {"min_evidence_start_utc"}


def test_validation_package_cannot_reach_risk_kill_switch_gateway_or_execution():
    for path in (ROOT / "validation").glob("*.py"):
        modules = _imported(ast.parse(path.read_text(encoding="utf-8")))
        forbidden = ("adaptive_scalper.risk", "adaptive_scalper.core", "adaptive_scalper.gateway",
                     "adaptive_scalper.execution", "adaptive_scalper.config", "MetaTrader5")
        assert not [m for m in modules if m.startswith(forbidden)], (path.name, modules)


def test_artifact_hash_is_plain_sha256():
    assert artifact_sha256(b"abc") == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
