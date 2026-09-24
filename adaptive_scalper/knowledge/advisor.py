"""`KnowledgeAdvisor`: the OKF source for `runtime.advisory.AdvisoryPanel`.

Evidence, never authority. `advise()` returns plain data that the runtime
journals next to a decision; it is never passed to the selector, cost
gate, risk governor, final permission, kill switch or execution service,
and it carries `"influence": "NONE"` so a reader of the journal cannot
mistake it for an input. Concepts that failed validation are quarantined;
deprecated concepts are skipped; stale ones are flagged; evidence-bearing
concepts that no human verified are listed separately as UNVERIFIED and
never under `approved`.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from adaptive_scalper.knowledge.model import EVIDENCE_TYPES, TRUST_HUMAN_REVIEWED, KnowledgeBundle
from adaptive_scalper.knowledge.search import concept_summary, filter_concepts
from adaptive_scalper.knowledge.validate import validate_bundle

DEFAULT_BUNDLE = Path(__file__).resolve().parents[2] / "knowledge"
MAX_CONCEPTS = 8


class KnowledgeAdvisor:
    def __init__(self, bundle: KnowledgeBundle, *, clock: Callable[[], float] = time.time) -> None:
        self.bundle = bundle
        self.clock = clock

    @classmethod
    def from_path(cls, root: str | Path | None = None, *, clock: Callable[[], float] = time.time) -> "KnowledgeAdvisor":
        root = DEFAULT_BUNDLE if root is None else root
        return cls(validate_bundle(root, now=datetime.fromtimestamp(clock(), timezone.utc)), clock=clock)

    def advise(self, *, canonical_symbol: str, strategy_key: str) -> dict:
        now = datetime.fromtimestamp(self.clock(), timezone.utc)
        relevant = filter_concepts(self.bundle, canonical_symbol=canonical_symbol, strategy_key=strategy_key)
        approved, unverified = [], []
        for concept in relevant:
            summary = concept_summary(concept, now)
            if concept.type in EVIDENCE_TYPES and concept.trust_tier != TRUST_HUMAN_REVIEWED:
                unverified.append({**summary, "label": "UNVERIFIED - not approved knowledge"})
            else:
                approved.append(summary)
        quarantined = sorted({i.path for i in self.bundle.issues if i.severity == "ERROR"})
        return {
            "influence": "NONE",
            "okf_version": self.bundle.okf_version,
            "bundle_checksum": self.bundle.checksum[:16],
            "approved": approved[:MAX_CONCEPTS],
            "unverified": unverified[:MAX_CONCEPTS],
            "stale": [c["id"] for c in approved + unverified if c["stale"]],
            "quarantined_files": len(quarantined),
            "detail": f"{len(approved)} approved / {len(unverified)} unverified concept(s) for "
                      f"{canonical_symbol} {strategy_key}",
        }
