"""Versioned, validated contracts between the desk's agents.

Everything an agent emits is an `AgentResult` (pydantic, `extra="forbid"`):
unknown fields are rejected, statuses and verdicts are closed enumerations,
and `validate_result()` additionally proves that

- every cited evidence id exists in the snapshot the agent was given;
- no structured finding carries an execution-shaped key (order request,
  lot size, stop, target, kill-switch action, risk-limit override,
  certificate) -- the contract has no way to express an instruction;
- confidence, when present, is a number in [0, 1] with a stated basis.

Evidence snapshots are immutable: `Snapshot.snapshot_id` is the SHA-256 of
the canonical JSON of its evidence, so `verify()` detects any mutation and
two builds from identical data share one id (which makes journaling
idempotent).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from adaptive_scalper.ai_desk import SCHEMA_VERSION

Status = Literal["COMPLETE", "INCOMPLETE", "STALE", "FAILED", "ABSTAIN"]
Verdict = Literal["SUPPORTED", "WEAK_EVIDENCE", "CONTRADICTED", "INSUFFICIENT_DATA", "ABSTAIN"]
ClaimKind = Literal["DESCRIPTIVE", "PREDICTIVE", "PROFITABILITY", "RISK", "DATA_QUALITY", "HYPOTHESIS"]
SnapshotMode = Literal["CURRENT", "REPLAY"]

# Keys that would make a finding look like an instruction to the trading
# engine. The desk has no execution authority, so its contract cannot carry
# them anywhere (checked recursively).
FORBIDDEN_FINDING_KEYS = frozenset({
    "order", "order_request", "orders_to_send", "lot_size", "lots", "position_size", "stop_loss",
    "take_profit", "execute", "execution", "trade_instruction", "kill_switch_action", "risk_limit_override",
    "certificate", "approve_certificate", "enable_strategy", "send_order",
})


def utc_iso(epoch: float | None) -> str | None:
    if epoch is None:
        return None
    return datetime.fromtimestamp(int(epoch), UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def sha(text: str, n: int = 16) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:n]


@dataclass(frozen=True)
class EvidenceItem:
    """One read-only fact taken from the trading database."""

    source: str                    # e.g. "runtime_state:microstructure", "bars:XAUUSD:M5"
    kind: str                      # e.g. MARKET_BARS, RISK_STATE, NEWS_EVENTS
    data_timestamp_utc: int | None  # newest underlying data point (None = timeless config)
    payload_json: str              # canonical JSON (immutable string)
    symbol: str | None = None

    @property
    def evidence_id(self) -> str:
        return "ev-" + sha(f"{self.source}|{self.kind}|{self.data_timestamp_utc}|{self.payload_json}")

    @property
    def payload(self) -> Any:
        return json.loads(self.payload_json)

    @staticmethod
    def make(source: str, kind: str, payload: Any, data_timestamp_utc: int | None,
             symbol: str | None = None) -> EvidenceItem:
        return EvidenceItem(source=source, kind=kind, data_timestamp_utc=None if data_timestamp_utc is None
                            else int(data_timestamp_utc), payload_json=canonical_json(payload), symbol=symbol)


@dataclass(frozen=True)
class Snapshot:
    as_of_utc: int
    mode: SnapshotMode
    evidence: tuple[EvidenceItem, ...]
    limitations: tuple[str, ...] = ()
    runtime_live: bool = False
    _id: str = field(default="", compare=False)

    def __post_init__(self):
        object.__setattr__(self, "_id", self.compute_id())

    def compute_id(self) -> str:
        body = canonical_json({"as_of": self.as_of_utc, "mode": self.mode,
                               "evidence": sorted(e.evidence_id for e in self.evidence),
                               "limitations": list(self.limitations)})
        return "snap-" + sha(body, 20)

    @property
    def snapshot_id(self) -> str:
        return self._id

    def verify(self) -> bool:
        return self.compute_id() == self._id

    def ids(self) -> frozenset[str]:
        return frozenset(e.evidence_id for e in self.evidence)

    def by_kind(self, kind: str, symbol: str | None = None) -> list[EvidenceItem]:
        return [e for e in self.evidence if e.kind == kind and (symbol is None or e.symbol == symbol)]

    def one(self, kind: str, symbol: str | None = None) -> EvidenceItem | None:
        items = self.by_kind(kind, symbol)
        return items[0] if items else None

    def future_items(self) -> list[EvidenceItem]:
        """Evidence newer than the as-of instant: look-ahead leakage."""
        return [e for e in self.evidence if e.data_timestamp_utc is not None and e.data_timestamp_utc > self.as_of_utc]


class Claim(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    claim_id: str
    kind: ClaimKind
    statement: str = Field(max_length=600)
    evidence_ids: list[str] = Field(default_factory=list)
    sample_size: int | None = None
    conclusion_key: str | None = None   # what the claim supports, for independence checks


class SkepticVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    target_agent: str
    claim_id: str
    verdict: Verdict
    objections: list[str] = Field(default_factory=list)


class Disagreement(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    topic: str
    parties: list[str]
    positions: dict[str, str]
    severity: Literal["INFO", "WARNING", "CRITICAL"] = "WARNING"
    resolution: Literal["PRESERVED_UNRESOLVED", "AUDIT_FINDING"] = "PRESERVED_UNRESOLVED"


class AgentResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1"] = SCHEMA_VERSION
    analysis_id: str
    agent: str
    agent_version: str
    symbol: str | None
    snapshot_id: str
    as_of_utc: str
    status: Status
    assessment: str = Field(max_length=2000)
    confidence: float | None = None
    confidence_basis: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)
    counterarguments: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    claims: list[Claim] = Field(default_factory=list)
    verdicts: list[SkepticVerdict] = Field(default_factory=list)
    disagreements: list[Disagreement] = Field(default_factory=list)
    findings: dict[str, Any] = Field(default_factory=dict)
    model_provider: str = "deterministic"
    model_id: str = "deterministic/1"
    config_fingerprint: str = ""
    duration_ms: int = 0
    llm_narrative: str | None = None          # UNTRUSTED model text, never a decision
    llm_narrative_status: str = "NOT_REQUESTED"

    @field_validator("confidence")
    @classmethod
    def _conf(cls, v):
        if v is not None and not (0.0 <= v <= 1.0):
            raise ValueError("confidence must lie in [0, 1]")
        return v


class ContractViolation(ValueError):
    pass


def _forbidden_keys(obj: Any, path: str = "") -> list[str]:
    hits: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if str(k).lower() in FORBIDDEN_FINDING_KEYS:
                hits.append(f"{path}.{k}")
            hits.extend(_forbidden_keys(v, f"{path}.{k}"))
    elif isinstance(obj, list | tuple):
        for i, v in enumerate(obj):
            hits.extend(_forbidden_keys(v, f"{path}[{i}]"))
    return hits


def validate_result(result: AgentResult, snapshot: Snapshot) -> AgentResult:
    """Raise `ContractViolation` unless the result is internally valid and
    every evidence reference resolves inside `snapshot`."""
    if result.snapshot_id != snapshot.snapshot_id:
        raise ContractViolation(f"{result.agent}: result references another snapshot")
    known = snapshot.ids()
    cited = set(result.evidence_ids)
    for c in result.claims:
        cited.update(c.evidence_ids)
    unknown = sorted(cited - known)
    if unknown:
        raise ContractViolation(f"{result.agent}: cites evidence not in the snapshot: {unknown[:5]}")
    hits = _forbidden_keys(result.findings, "findings")
    if hits:
        raise ContractViolation(f"{result.agent}: execution-shaped keys are not allowed: {hits[:5]}")
    if result.confidence is not None and not result.confidence_basis:
        raise ContractViolation(f"{result.agent}: a confidence needs a stated basis")
    try:
        canonical_json(result.findings)
    except (TypeError, ValueError) as exc:
        raise ContractViolation(f"{result.agent}: findings are not JSON-serializable: {exc}") from exc
    return result


def analysis_id(agent: str, agent_version: str, snapshot_id: str, symbol: str | None, provider: str,
                config_fingerprint: str) -> str:
    """Deterministic: a retry of the same analysis maps to the same id, so the
    journal's UNIQUE constraint makes retries idempotent."""
    return "an-" + sha(f"{agent}|{agent_version}|{snapshot_id}|{symbol}|{provider}|{config_fingerprint}", 20)
