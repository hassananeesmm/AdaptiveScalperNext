"""Edge-validation certificates (FLAT/SHADOW release hardening; audit sections 4-9).

Trust model
-----------
- ISSUER (offline validation pipeline, does not exist yet): holds an Ed25519
  PRIVATE seed outside Git and signs certificates (`issue_signature`).
- RUNTIME (DEMO/PAPER): holds only the PUBLIC key (`ASN_EDGE_CERTIFICATE_PUBLIC_KEY_FILE`,
  a file `ed25519-public:<64 hex>` outside Git). Nothing available to the
  runtime can create a valid signature. No public key -> nothing verifies ->
  FLAT. A file tagged `ed25519-private:` is refused, never loaded.

A certificate binds ONE strategy key + version, symbol, direction (or ANY
when direction pooling was validated), lifecycle id + version, horizon, the
exact model and calibrator artifacts (SHA-256), calibration method, feature
schema and feature-transform version, cost model, protocol version, evidence
intervals and every statistic the preregistered forward protocol requires.
Every field is inside the signed canonical JSON (sorted keys, no NaN/Inf).

Probability provenance
----------------------
An executable probability is never taken from a caller. `certified_probability`
recomputes it from the certified model + calibrator ARTIFACT BYTES (whose
SHA-256 must equal the signed hashes) and the proposal's causal feature
vector. A caller-supplied p that differs is rejected.

Protocol
--------
`verify_certificate` (the executable API) takes NO protocol argument: it
always checks the module-level `CURRENT_PROTOCOL` at the moment of use.
`_verify_against` exists for low-level tests only.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from dataclasses import asdict, dataclass, fields, replace
from pathlib import Path

from adaptive_scalper.shadow.lifecycle import lifecycle_for
from adaptive_scalper.simulation.fill_model import COST_BROKER_DEMO_CONFIRMED, FILL_MODEL_VERSION
from adaptive_scalper.validation import ed25519

CERTIFICATE_SCHEMA = "edge_certificate/v2"
SIGNATURE_SCHEME = "ed25519/v1"
PROTOCOL_VERSION = "forward_evidence_protocol/2026-10-03"
PUBLIC_KEY_ENV = "ASN_EDGE_CERTIFICATE_PUBLIC_KEY_FILE"
PUBLIC_KEY_PREFIX = "ed25519-public:"
PRIVATE_KEY_PREFIX = "ed25519-private:"
MODEL_ARTIFACT_SCHEMA = "edge_model_artifact/v1"
CALIBRATOR_ARTIFACT_SCHEMA = "edge_calibrator_artifact/v1"
FEATURE_TRANSFORM_VERSION = "numeric_feature_vector/1"   # features.bar_features.numeric_feature_vector, as-is
# H1-H9 strategy research froze on 2026-10-02; evidence must be newer.
RESEARCH_FREEZE_UTC = 1790985600            # 2026-10-03 00:00:00 UTC
DIRECTION_ANY = "ANY"


@dataclass(frozen=True)
class ProtocolThresholds:
    """docs/audits/FORWARD_EVIDENCE_PROTOCOL.md -- never lowered after data."""
    min_effective_observations: int = 300
    min_blocks: int = 8
    min_positive_blocks: int = 6
    max_single_trade_share: float = 0.10
    min_psr: float = 0.95
    min_dsr: float = 0.95
    max_pbo: float = 0.20
    max_ece: float = 0.05
    min_ledger_trials: int = 262             # ledger size at registration; DSR must use at least this
    min_evidence_start_utc: int = RESEARCH_FREEZE_UTC


# The preregistered protocol. Production code never reassigns this
# (AST test); the executable path has no way to pass another one.
PREREGISTERED_PROTOCOL = ProtocolThresholds()
CURRENT_PROTOCOL = PREREGISTERED_PROTOCOL


@dataclass(frozen=True)
class ValidationCertificate:
    schema: str
    signature_scheme: str
    issuer_key_id: str                       # sha256(public key)[:16] -- rotation/audit, not trust
    protocol_version: str
    strategy_key: str
    strategy_version: int
    canonical_symbol: str
    direction: str                           # BUY | SELL | ANY (pooling validated)
    lifecycle_id: str
    lifecycle_version: str
    horizon_seconds: int
    model_id: str
    model_version: str
    model_artifact_sha256: str
    calibrator_artifact_sha256: str
    calibration_method: str
    feature_schema_version: int
    feature_transform_version: str
    training_start_utc: int
    training_end_utc: int
    calibration_start_utc: int
    calibration_end_utc: int
    validation_start_utc: int
    validation_end_utc: int
    as_of_utc: int
    expires_utc: int
    raw_observations: int
    effective_observations: int
    chronological_blocks: int
    positive_blocks: int
    brier: float
    log_loss: float
    ece: float
    calibration_passed: bool
    mean_gross_r: float
    mean_net_r: float
    net_r_lower_bound_95: float
    avg_realized_win_r: float
    avg_realized_loss_r: float               # positive magnitude
    max_single_trade_share: float
    cost_model_version: str
    cost_provenance: str
    cost_stress_net_r: float                 # slippage x1.5, spread at p75
    ledger_trial_count: int
    psr: float
    dsr: float
    pbo: float | None                        # None = not applicable (single variant)
    safety_validation_passed: bool
    forbidden_data_access: bool
    signature: str = ""                      # hex Ed25519 signature over canonical_payload()

    def canonical_payload(self) -> bytes:
        body = {k: v for k, v in asdict(self).items() if k != "signature"}
        return json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


@dataclass(frozen=True)
class CertificateCheck:
    ok: bool
    reason: str


def key_id(public_key: bytes) -> str:
    return hashlib.sha256(public_key).hexdigest()[:16]


def issue_signature(certificate: ValidationCertificate, private_seed: bytes) -> ValidationCertificate:
    """ISSUER ONLY (AST-enforced: only validation/ may call it). Returns the
    certificate signed with the issuer's Ed25519 private seed."""
    if not isinstance(private_seed, (bytes, bytearray)) or len(private_seed) != 32:
        raise ValueError("an Ed25519 private seed is 32 bytes")
    public = ed25519.public_key(bytes(private_seed))
    unsigned = replace(certificate, signature="", issuer_key_id=key_id(public), signature_scheme=SIGNATURE_SCHEME)
    return replace(unsigned, signature=ed25519.sign(bytes(private_seed), unsigned.canonical_payload()).hex())


def load_certificate_public_key(path: str | None = None) -> bytes | None:
    """The runtime's PUBLIC verification key from a file outside Git
    (`ed25519-public:<64 hex>`). None when not configured, unreadable,
    malformed, or a PRIVATE key file -- then no certificate verifies (FLAT)."""
    target = path or os.environ.get(PUBLIC_KEY_ENV)
    if not target:
        return None
    try:
        text = Path(target).read_text(encoding="ascii").strip()
    except (OSError, UnicodeDecodeError):
        return None
    if text.startswith(PRIVATE_KEY_PREFIX) or not text.startswith(PUBLIC_KEY_PREFIX):
        return None
    try:
        key = bytes.fromhex(text[len(PUBLIC_KEY_PREFIX):])
    except ValueError:
        return None
    return key if len(key) == 32 else None


def _overlaps(a: tuple[int, int], b: tuple[int, int]) -> bool:
    return a[0] < b[1] and b[0] < a[1]


def verify_certificate(
    certificate: ValidationCertificate | None, *, public_key: bytes | None, now_utc: int, strategy_key: str,
    strategy_version: int, canonical_symbol: str, direction: str,
) -> CertificateCheck:
    """THE executable verification: signature, binding, freshness and the
    CURRENT protocol. There is deliberately no protocol parameter."""
    return _verify_against(certificate, public_key=public_key, now_utc=now_utc, strategy_key=strategy_key,
                           strategy_version=strategy_version, canonical_symbol=canonical_symbol,
                           direction=direction, protocol=CURRENT_PROTOCOL)


def _verify_against(
    certificate, *, public_key, now_utc, strategy_key, strategy_version, canonical_symbol, direction,
    protocol: ProtocolThresholds,
) -> CertificateCheck:
    def no(reason: str) -> CertificateCheck:
        return CertificateCheck(False, reason)

    if certificate is None:
        return no("NO_CERTIFICATE")
    if not isinstance(certificate, ValidationCertificate):
        return no("NOT_A_CERTIFICATE")
    if not public_key:
        return no("NO_PUBLIC_KEY")
    c = certificate
    if c.schema != CERTIFICATE_SCHEMA:
        return no("WRONG_SCHEMA")
    if c.signature_scheme != SIGNATURE_SCHEME:
        return no("UNKNOWN_SIGNATURE_SCHEME")
    try:
        payload = c.canonical_payload()
        signature = bytes.fromhex(c.signature)
    except (TypeError, ValueError):
        return no("MALFORMED_CERTIFICATE")
    if not ed25519.verify(public_key, payload, signature):
        return no("BAD_SIGNATURE")
    if c.issuer_key_id != key_id(public_key):
        return no("WRONG_ISSUER_KEY_ID")
    for f in fields(c):
        value = getattr(c, f.name)
        if isinstance(value, float) and not math.isfinite(value):
            return no(f"NON_FINITE_{f.name.upper()}")
    if c.protocol_version != PROTOCOL_VERSION:
        return no("WRONG_PROTOCOL_VERSION")
    # binding
    if c.strategy_key != strategy_key:
        return no("WRONG_STRATEGY")
    if c.strategy_version != strategy_version:
        return no("WRONG_STRATEGY_VERSION")
    if c.canonical_symbol != canonical_symbol:
        return no("WRONG_SYMBOL")
    if c.direction not in ("BUY", "SELL", DIRECTION_ANY):
        return no("INVALID_DIRECTION_POOLING")
    if c.direction not in (direction, DIRECTION_ANY):
        return no("WRONG_DIRECTION")
    try:
        lifecycle = lifecycle_for(strategy_key)
    except KeyError:
        return no("NO_LIFECYCLE")
    if c.lifecycle_id != lifecycle.strategy_key:
        return no("WRONG_LIFECYCLE")
    if c.lifecycle_version != lifecycle.lifecycle_version:
        return no("WRONG_LIFECYCLE_VERSION")
    if c.horizon_seconds != lifecycle.horizon_seconds:
        return no("WRONG_HORIZON")
    from adaptive_scalper.features.bar_features import FEATURE_SCHEMA_VERSION

    if c.feature_schema_version != FEATURE_SCHEMA_VERSION:
        return no("WRONG_FEATURE_SCHEMA")
    if c.feature_transform_version != FEATURE_TRANSFORM_VERSION:
        return no("WRONG_FEATURE_TRANSFORM")
    if not (_is_sha256(c.model_artifact_sha256) and _is_sha256(c.calibrator_artifact_sha256)):
        return no("MISSING_ARTIFACT_HASH")
    # time
    intervals = ((c.training_start_utc, c.training_end_utc), (c.calibration_start_utc, c.calibration_end_utc),
                 (c.validation_start_utc, c.validation_end_utc))
    if any(start >= end for start, end in intervals):
        return no("EMPTY_INTERVAL")
    if not (c.training_end_utc <= c.calibration_start_utc and c.calibration_end_utc <= c.validation_start_utc
            and c.validation_end_utc <= c.as_of_utc):
        return no("INTERVALS_NOT_CHRONOLOGICAL")
    if c.training_start_utc < protocol.min_evidence_start_utc:
        return no("EVIDENCE_PREDATES_RESEARCH_FREEZE")
    # Imported here: research.independent pulls in the backtest engine (import cycle at load).
    from adaptive_scalper.research.independent import RESERVED_OOS_INTERVALS

    if any(_overlaps(iv, oos) for iv in intervals for oos in RESERVED_OOS_INTERVALS):
        return no("SEALED_OOS_OVERLAP")
    if c.as_of_utc > now_utc:
        return no("AS_OF_IN_FUTURE")
    if now_utc >= c.expires_utc:
        return no("EXPIRED")
    # protocol
    if c.effective_observations < protocol.min_effective_observations or c.raw_observations < c.effective_observations:
        return no("INSUFFICIENT_SAMPLE")
    if c.chronological_blocks < protocol.min_blocks:
        return no("INSUFFICIENT_BLOCKS")
    if c.positive_blocks < protocol.min_positive_blocks or c.positive_blocks > c.chronological_blocks:
        return no("INSUFFICIENT_POSITIVE_BLOCKS")
    if not c.mean_gross_r > 0:
        return no("NON_POSITIVE_GROSS")
    if not c.mean_net_r > 0:
        return no("NON_POSITIVE_NET")
    if not c.net_r_lower_bound_95 > 0:
        return no("NON_POSITIVE_LOWER_BOUND")
    if c.cost_stress_net_r <= 0:
        return no("FAILS_COST_STRESS")
    if c.max_single_trade_share > protocol.max_single_trade_share:
        return no("SINGLE_TRADE_DEPENDENCE")
    if c.cost_provenance != COST_BROKER_DEMO_CONFIRMED:
        return no("UNVERIFIED_COST_EVIDENCE")
    if c.cost_model_version != FILL_MODEL_VERSION:
        return no("WRONG_COST_MODEL_VERSION")
    if not c.calibration_passed or not (0 <= c.brier <= 1) or c.log_loss < 0:
        return no("CALIBRATION_FAILED")
    if c.ece > protocol.max_ece:
        return no("ECE_ABOVE_THRESHOLD")
    if c.psr < protocol.min_psr:
        return no("PSR_BELOW_THRESHOLD")
    if c.dsr < protocol.min_dsr:
        return no("DSR_BELOW_THRESHOLD")
    if c.pbo is not None and c.pbo > protocol.max_pbo:
        return no("PBO_ABOVE_THRESHOLD")
    if c.ledger_trial_count < protocol.min_ledger_trials:
        return no("TRIAL_COUNT_UNDERSTATED")
    if not (c.avg_realized_win_r > 0 and c.avg_realized_loss_r > 0):
        return no("NO_REALIZED_PAYOFF")
    if not c.safety_validation_passed:
        return no("SAFETY_VALIDATION_FAILED")
    if c.forbidden_data_access:
        return no("FORBIDDEN_DATA_ACCESS")
    return CertificateCheck(True, "VERIFIED")


# ---------------------------------------------------------------- certified prediction

def _is_sha256(value) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(ch in "0123456789abcdef" for ch in value)


def artifact_sha256(artifact: bytes) -> str:
    return hashlib.sha256(artifact).hexdigest()


@dataclass(frozen=True)
class CertifiedPrediction:
    ok: bool
    reason: str
    probability: float | None = None


def certified_probability(
    certificate: ValidationCertificate, model_artifact: bytes | None, calibrator_artifact: bytes | None,
    features: dict | None,
) -> CertifiedPrediction:
    """P(win) recomputed from the EXACT certified artifacts and the proposal's
    causal features. Model artifact (`edge_model_artifact/v1`, canonical JSON):
    a linear score over named features; calibrator (`edge_calibrator_artifact/v1`):
    Platt sigmoid on that score. Any mismatch, missing or non-finite input
    -> not ok (the caller blocks)."""
    def no(reason: str) -> CertifiedPrediction:
        return CertifiedPrediction(False, reason)

    if not isinstance(model_artifact, (bytes, bytearray)) or not isinstance(calibrator_artifact, (bytes, bytearray)):
        return no("MISSING_MODEL_ARTIFACT")
    if artifact_sha256(bytes(model_artifact)) != certificate.model_artifact_sha256:
        return no("WRONG_MODEL_ARTIFACT")
    if artifact_sha256(bytes(calibrator_artifact)) != certificate.calibrator_artifact_sha256:
        return no("WRONG_CALIBRATOR_ARTIFACT")
    try:
        model = json.loads(bytes(model_artifact))
        calibrator = json.loads(bytes(calibrator_artifact))
    except (ValueError, UnicodeDecodeError):
        return no("MALFORMED_ARTIFACT")
    if model.get("schema") != MODEL_ARTIFACT_SCHEMA or calibrator.get("schema") != CALIBRATOR_ARTIFACT_SCHEMA:
        return no("UNKNOWN_ARTIFACT_SCHEMA")
    expected = {"model_id": certificate.model_id, "model_version": certificate.model_version,
                "strategy_key": certificate.strategy_key, "strategy_version": certificate.strategy_version,
                "canonical_symbol": certificate.canonical_symbol,
                "feature_schema_version": certificate.feature_schema_version,
                "feature_transform_version": certificate.feature_transform_version}
    if any(model.get(k) != v for k, v in expected.items()):
        return no("MODEL_IDENTITY_MISMATCH")
    if calibrator.get("model_artifact_sha256") != certificate.model_artifact_sha256 \
            or calibrator.get("method") != certificate.calibration_method:
        return no("CALIBRATOR_IDENTITY_MISMATCH")
    names, weights = model.get("features"), model.get("weights")
    if not isinstance(names, list) or not isinstance(weights, list) or len(names) != len(weights):
        return no("MALFORMED_ARTIFACT")
    if features is None:
        return no("NO_PROPOSAL_FEATURES")
    try:
        score = float(model["intercept"])
        for name, weight in zip(names, weights):
            value = features.get(name)
            if value is None or isinstance(value, bool) or not math.isfinite(float(value)):
                return no("MISSING_OR_NON_FINITE_FEATURE")
            score += float(weight) * float(value)
        z = float(calibrator["a"]) * score + float(calibrator["b"])
    except (KeyError, TypeError, ValueError):
        return no("MALFORMED_ARTIFACT")
    if not math.isfinite(z):
        return no("NON_FINITE_PREDICTION")
    p = 1.0 / (1.0 + math.exp(-z)) if z >= 0 else math.exp(z) / (1.0 + math.exp(z))
    if not (0.0 < p < 1.0):
        return no("DEGENERATE_PROBABILITY")
    return CertifiedPrediction(True, "CERTIFIED", p)
