"""Advisory evidence for a proposal: RAG precedent, ML observer score, OKF
knowledge (directive sections 70-72; completion directive Phases 4-6).

Evidence, never authority. `AdvisoryPanel.gather()` returns plain data; the
runtime journals it (RAG_USED / MODEL_USED payloads) next to the decision
and never passes it to the selector, cost gate, risk governor, final
permission or execution service. Each source fails independently to a
DEGRADED/UNAVAILABLE status -- an advisory outage is a health degradation,
never an execution failure. `tests/test_runtime_advisory.py` proves the
same decisions are taken whether every source works or every source raises.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

OK = "OK"
DEGRADED = "DEGRADED"
NOT_CONFIGURED = "NOT_CONFIGURED"


@dataclass(frozen=True)
class AdvisoryEvidence:
    source: str          # RAG / ML_OBSERVER / OKF
    status: str
    summary: dict = field(default_factory=dict)


def safe_rag_record(rag, conn: sqlite3.Connection, memory_type: str, text: str, metadata: dict, **kwargs):
    """Record a RAG memory without trusting the RAG object's own "never
    raises" contract: advisory memory must never be able to fail the
    trading path that is reporting to it."""
    if rag is None:
        return None
    try:
        return rag.record(conn, memory_type, text, metadata, **kwargs)
    except Exception:
        return None


def proposal_text(canonical_symbol: str, strategy_key: str, direction: str, regime: str) -> str:
    return f"{canonical_symbol} {strategy_key} {direction} regime {regime}"


class AdvisoryPanel:
    def __init__(self, *, rag=None, observer=None, okf=None) -> None:
        self.rag = rag
        self.observer = observer
        self.okf = okf

    def gather(
        self, conn: sqlite3.Connection, *, canonical_symbol: str, strategy_key: str, direction: str, regime: str,
        features: dict[str, float | None],
    ) -> tuple[AdvisoryEvidence, ...]:
        return (
            self._rag(canonical_symbol, strategy_key, direction, regime),
            self._observer(conn, canonical_symbol, features),
            self._okf(canonical_symbol, strategy_key),
        )

    def _rag(self, symbol, strategy_key, direction, regime) -> AdvisoryEvidence:
        if self.rag is None:
            return AdvisoryEvidence("RAG", NOT_CONFIGURED)
        try:
            result = self.rag.query_similar(proposal_text(symbol, strategy_key, direction, regime), top_k=5)
            return AdvisoryEvidence("RAG", result.status, {
                "detail": result.detail,
                "matches": [
                    {"memory_id": m.memory.id, "memory_type": m.memory.memory_type, "score": round(m.score, 4)}
                    for m in result.matches
                ],
            })
        except Exception as exc:
            return AdvisoryEvidence("RAG", DEGRADED, {"detail": f"{type(exc).__name__}: {exc}"})

    def _observer(self, conn, symbol, features) -> AdvisoryEvidence:
        if self.observer is None:
            return AdvisoryEvidence("ML_OBSERVER", NOT_CONFIGURED)
        try:
            score = self.observer.score(conn, symbol, features)
            return AdvisoryEvidence("ML_OBSERVER", OK if score.status != "DEGRADED" else DEGRADED, {
                "status": score.status, "model_key": score.model_key, "version": score.version,
                "lifecycle_state": score.lifecycle_state, "probability": score.probability,
                "influence": score.influence, "detail": score.detail,
            })
        except Exception as exc:
            return AdvisoryEvidence("ML_OBSERVER", DEGRADED, {"detail": f"{type(exc).__name__}: {exc}"})

    def _okf(self, symbol, strategy_key) -> AdvisoryEvidence:
        if self.okf is None:
            return AdvisoryEvidence("OKF", NOT_CONFIGURED)
        try:
            return AdvisoryEvidence("OKF", OK, self.okf.advise(canonical_symbol=symbol, strategy_key=strategy_key))
        except Exception as exc:
            return AdvisoryEvidence("OKF", DEGRADED, {"detail": f"{type(exc).__name__}: {exc}"})
