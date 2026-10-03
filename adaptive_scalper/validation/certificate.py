"""Edge-validation certificate (integrated FLAT/SHADOW release; audit section 8).

A string `status = "VALIDATED"` must never be able to authorize broker
exposure. Executable expected-edge evidence therefore has to carry a
`ValidationCertificate`:

- typed and immutable, binding the evidence to ONE strategy key + version,
  symbol, direction (or ANY when direction-pooling was validated), lifecycle
  version and horizon;
- recording every statistic the preregistered forward protocol
  (docs/audits/FORWARD_EVIDENCE_PROTOCOL.md) requires;
- SEALED with HMAC-SHA256 over its canonical JSON by the validation pipeline.
  The key lives in a file outside Git (env `ASN_EDGE_CERTIFICATE_KEY_FILE`).
  No key configured -> nothing verifies -> FLAT.

`verify_certificate()` re-checks the seal AND the CURRENT protocol at the
moment of use -- a sealed certificate that no longer satisfies the protocol,
has expired, or belongs to another strategy/version/symbol/lifecycle fails
closed with a specific reason. Payoff is in R units (multiples of the
proposal's own stop distance), so it transfers across ATR-scaled stops.

Only `adaptive_scalper/validation/` may call `seal_certificate`
(tests/test_validation_certificate.py enforces it by AST); this module
imports nothing from risk, the kill switch, the gateway or execution.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
from dataclasses import asdict, dataclass, fields, replace
from pathlib import Path

from adaptive_scalper.shadow.lifecycle import lifecycle_for
from adaptive_scalper.simulation.fill_model import COST_BROKER_DEMO_CONFIRMED, FILL_MODEL_VERSION

CERTIFICATE_SCHEMA = "edge_certificate/v1"
PROTOCOL_VERSION = "forward_evidence_protocol/2026-10-03"
KEY_ENV = "ASN_EDGE_CERTIFICATE_KEY_FILE"
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


CURRENT_PROTOCOL = ProtocolThresholds()


@dataclass(frozen=True)
class ValidationCertificate:
    schema: str
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
    calibration_method: str
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
    seal: str = ""

    def canonical_payload(self) -> bytes:
        body = {k: v for k, v in asdict(self).items() if k != "seal"}
        return json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


@dataclass(frozen=True)
class CertificateCheck:
    ok: bool
    reason: str


def seal_certificate(certificate: ValidationCertificate, key: bytes) -> ValidationCertificate:
    """Validation-pipeline only (AST-enforced): returns the certificate with
    its HMAC seal. Raises on non-finite numbers (allow_nan=False)."""
    if not key:
        raise ValueError("an empty certificate key cannot seal")
    digest = hmac.new(key, certificate.canonical_payload(), hashlib.sha256).hexdigest()
    return replace(certificate, seal=digest)


def load_certificate_key(path: str | None = None) -> bytes | None:
    """The validation key, from a file OUTSIDE Git. None when not configured
    or unreadable -- every certificate then fails verification (FLAT)."""
    target = path or os.environ.get(KEY_ENV)
    if not target:
        return None
    try:
        data = Path(target).read_bytes().strip()
    except OSError:
        return None
    return data if len(data) >= 32 else None


def _overlaps(a: tuple[int, int], b: tuple[int, int]) -> bool:
    return a[0] < b[1] and b[0] < a[1]


def verify_certificate(
    certificate: ValidationCertificate | None, *, key: bytes | None, now_utc: int, strategy_key: str,
    strategy_version: int, canonical_symbol: str, direction: str,
    protocol: ProtocolThresholds = CURRENT_PROTOCOL,
) -> CertificateCheck:
    """Every reason a certificate may not authorize this proposal NOW."""
    def no(reason: str) -> CertificateCheck:
        return CertificateCheck(False, reason)

    if certificate is None:
        return no("NO_CERTIFICATE")
    if not isinstance(certificate, ValidationCertificate):
        return no("NOT_A_CERTIFICATE")
    if not key:
        return no("NO_VERIFICATION_KEY")
    try:
        expected = hmac.new(key, certificate.canonical_payload(), hashlib.sha256).hexdigest()
    except (TypeError, ValueError):
        return no("UNSERIALISABLE_CERTIFICATE")
    if not certificate.seal or not hmac.compare_digest(expected, certificate.seal):
        return no("BAD_SEAL")
    c = certificate
    for f in fields(c):
        value = getattr(c, f.name)
        if isinstance(value, float) and not math.isfinite(value):
            return no(f"NON_FINITE_{f.name.upper()}")
    if c.schema != CERTIFICATE_SCHEMA:
        return no("WRONG_SCHEMA")
    if c.protocol_version != PROTOCOL_VERSION:
        return no("WRONG_PROTOCOL_VERSION")
    # binding
    if c.strategy_key != strategy_key:
        return no("WRONG_STRATEGY")
    if c.strategy_version != strategy_version:
        return no("WRONG_STRATEGY_VERSION")
    if c.canonical_symbol != canonical_symbol:
        return no("WRONG_SYMBOL")
    if c.direction not in (direction, DIRECTION_ANY):
        return no("WRONG_DIRECTION")
    try:
        lifecycle = lifecycle_for(strategy_key)
    except KeyError:
        return no("NO_LIFECYCLE")
    if c.lifecycle_id != lifecycle.strategy_key or c.lifecycle_version != lifecycle.lifecycle_version:
        return no("WRONG_LIFECYCLE_VERSION")
    if c.horizon_seconds != lifecycle.horizon_seconds:
        return no("WRONG_HORIZON")
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
    # Imported here: research.independent pulls in the backtest engine, which
    # imports edge evidence (an import cycle at module load).
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
    if c.chronological_blocks < protocol.min_blocks or c.positive_blocks < protocol.min_positive_blocks \
            or c.positive_blocks > c.chronological_blocks:
        return no("INSUFFICIENT_BLOCKS")
    if not (c.mean_gross_r > 0 and c.mean_net_r > 0 and c.net_r_lower_bound_95 > 0):
        return no("NO_POSITIVE_NET_LOWER_BOUND")
    if c.cost_stress_net_r <= 0:
        return no("FAILS_COST_STRESS")
    if c.max_single_trade_share > protocol.max_single_trade_share:
        return no("SINGLE_TRADE_DEPENDENCE")
    if c.cost_provenance != COST_BROKER_DEMO_CONFIRMED:
        return no("UNVERIFIED_COST_EVIDENCE")
    if c.cost_model_version != FILL_MODEL_VERSION:
        return no("WRONG_COST_MODEL_VERSION")
    if not c.calibration_passed or c.ece > protocol.max_ece or not (0 <= c.brier <= 1) or c.log_loss < 0:
        return no("CALIBRATION_FAILED")
    if c.psr < protocol.min_psr or c.dsr < protocol.min_dsr:
        return no("PSR_DSR_BELOW_THRESHOLD")
    if c.pbo is not None and c.pbo > protocol.max_pbo:
        return no("PBO_ABOVE_THRESHOLD")
    if c.ledger_trial_count < protocol.min_ledger_trials:
        return no("TRIAL_COUNT_UNDERSTATED")
    if not (c.avg_realized_win_r > 0 and c.avg_realized_loss_r > 0):
        return no("NO_REALIZED_PAYOFF")
    if not c.safety_validation_passed or c.forbidden_data_access:
        return no("SAFETY_VALIDATION_FAILED")
    return CertificateCheck(True, "VERIFIED")
