"""Agent base class: timing, deterministic analysis ids, failure isolation and
contract validation. An agent that raises produces a FAILED result (never an
exception into the orchestrator); a result that violates the contract is
replaced by a FAILED result naming the violation."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from adaptive_scalper.ai_desk.contracts import (
    AgentResult,
    Claim,
    ContractViolation,
    Snapshot,
    analysis_id,
    utc_iso,
    validate_result,
)


@dataclass
class AgentContext:
    """What an agent may see besides the snapshot: earlier results of THIS
    cycle (read-only copies) and the config fingerprint."""

    config_fingerprint: str = ""
    results: dict[str, list[AgentResult]] = field(default_factory=dict)
    stale_after_seconds: int = 1800
    options: dict[str, Any] = field(default_factory=dict)

    def of(self, agent: str) -> list[AgentResult]:
        return list(self.results.get(agent, []))


@dataclass
class Draft:
    """What an agent's analysis returns before it is wrapped and validated."""

    status: str
    assessment: str
    symbol: str | None = None
    confidence: float | None = None
    confidence_basis: str | None = None
    evidence_ids: list[str] = field(default_factory=list)
    counterarguments: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    claims: list[Claim] = field(default_factory=list)
    verdicts: list = field(default_factory=list)
    disagreements: list = field(default_factory=list)
    findings: dict[str, Any] = field(default_factory=dict)


class Agent:
    key: str = "agent"
    version: str = "1.0.0"
    per_symbol: bool = False

    def analyze(self, snapshot: Snapshot, context: AgentContext, symbol: str | None = None) -> Draft:
        raise NotImplementedError

    def run(self, snapshot: Snapshot, context: AgentContext, symbol: str | None = None) -> AgentResult:
        started = time.perf_counter()
        aid = analysis_id(self.key, self.version, snapshot.snapshot_id, symbol, "deterministic",
                          context.config_fingerprint)
        try:
            draft = self.analyze(snapshot, context, symbol)
            result = AgentResult(
                analysis_id=aid, agent=self.key, agent_version=self.version, symbol=draft.symbol or symbol,
                snapshot_id=snapshot.snapshot_id, as_of_utc=utc_iso(snapshot.as_of_utc), status=draft.status,
                assessment=draft.assessment[:2000], confidence=draft.confidence,
                confidence_basis=draft.confidence_basis, evidence_ids=sorted(set(draft.evidence_ids)),
                counterarguments=draft.counterarguments, limitations=draft.limitations, claims=draft.claims,
                verdicts=draft.verdicts, disagreements=draft.disagreements, findings=draft.findings,
                config_fingerprint=context.config_fingerprint,
                duration_ms=int((time.perf_counter() - started) * 1000))
            return validate_result(result, snapshot)
        except (ContractViolation, ValueError) as exc:
            return self._failed(aid, snapshot, context, symbol, f"contract violation: {exc}", started)
        except Exception as exc:  # noqa: BLE001 - an agent failure is a FAILED result, never a crash
            return self._failed(aid, snapshot, context, symbol, f"{type(exc).__name__}: {exc}", started)

    def _failed(self, aid, snapshot, context, symbol, reason, started) -> AgentResult:
        return AgentResult(
            analysis_id=aid, agent=self.key, agent_version=self.version, symbol=symbol,
            snapshot_id=snapshot.snapshot_id, as_of_utc=utc_iso(snapshot.as_of_utc), status="FAILED",
            assessment=f"{self.key} failed; no assessment is available from this agent.",
            limitations=[reason[:500]], config_fingerprint=context.config_fingerprint,
            duration_ms=int((time.perf_counter() - started) * 1000))


def age_seconds(snapshot: Snapshot, data_ts: int | None) -> int | None:
    return None if data_ts is None else snapshot.as_of_utc - int(data_ts)
