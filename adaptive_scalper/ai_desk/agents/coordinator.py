"""Agent 7 - AI Desk Coordinator: assembles the desk's advisory summary.

It checks completeness and freshness of every agent result, gathers all
disagreements (keeping each side, never averaging them away), separates
claims the Skeptic SUPPORTED from those it did not, asks for justified
read-only follow-up analysis, reports provider availability / latency / cost,
and publishes an auditable summary.

It is not an execution agent. The summary has no direction, size, entry or
exit field; agent consensus is never turned into an order or an edge claim.
When critical evidence is missing the summary says the assessment is
INCOMPLETE. The Skeptic reviews this summary afterwards and may disagree.
"""

from __future__ import annotations

from collections import Counter

from adaptive_scalper.ai_desk import ADVISORY_NOTICE
from adaptive_scalper.ai_desk.agents.base import Agent, AgentContext, Draft
from adaptive_scalper.ai_desk.contracts import Claim, Disagreement, Snapshot

REQUIRED = ("market_analyst", "risk_analyst", "skeptic")
FULL_DESK = ("market_analyst", "strategy_analyst", "news_analyst", "risk_analyst", "skeptic", "learning_analyst")


class Coordinator(Agent):
    key = "coordinator"
    version = "1.0.0"

    def analyze(self, snapshot: Snapshot, context: AgentContext, symbol: str | None = None) -> Draft:
        present = {a: rs for a, rs in context.results.items() if a != self.key and rs}
        statuses = {a: Counter(r.status for r in rs) for a, rs in present.items()}
        missing_required = [a for a in REQUIRED if a not in present]
        missing_optional = [a for a in FULL_DESK if a not in present and a not in REQUIRED]
        failed = sorted({f"{r.agent}{'/' + r.symbol if r.symbol else ''}" for rs in present.values() for r in rs
                         if r.status == "FAILED"})
        stale = sorted({f"{r.agent}{'/' + r.symbol if r.symbol else ''}" for rs in present.values() for r in rs
                        if r.status == "STALE"})

        critical_missing = []
        if missing_required:
            critical_missing.append(f"required agents missing: {missing_required}")
        if any(r.status != "COMPLETE" for r in present.get("risk_analyst", [])):
            critical_missing.append("risk assessment incomplete (runtime not live or broker state unknown)")
        market = present.get("market_analyst", [])
        if market and all(r.status in ("STALE", "FAILED") for r in market):
            critical_missing.append("no fresh market data for any symbol")
        if failed:
            critical_missing.append(f"failed analyses: {failed}")
        complete = not critical_missing

        # Disagreements: everything agents recorded, plus skeptic objections to claims, preserved verbatim.
        disagreements: list[Disagreement] = [d for rs in present.values() for r in rs for d in r.disagreements]
        verdicts = [v for r in present.get("skeptic", []) for v in r.verdicts]
        claim_index = {(r.agent, c.claim_id): (r, c) for rs in present.values() for r in rs for c in r.claims}
        supported, unsupported = [], []
        for v in verdicts:
            entry = claim_index.get((v.target_agent, v.claim_id))
            text = entry[1].statement if entry else v.claim_id
            row = {"agent": v.target_agent, "claim_id": v.claim_id, "statement": text, "verdict": v.verdict,
                   "objections": v.objections}
            (supported if v.verdict == "SUPPORTED" else unsupported).append(row)
            if v.verdict == "CONTRADICTED":
                disagreements.append(Disagreement(topic=f"claim:{v.claim_id}", parties=[v.target_agent, "skeptic"],
                                                  positions={v.target_agent: text[:300],
                                                             "skeptic": "; ".join(v.objections)[:300]},
                                                  severity="WARNING"))

        # Justified read-only follow-ups (executed once by the orchestrator).
        follow_ups = []
        strat = present.get("strategy_analyst", [])
        if strat and strat[0].findings.get("underperformers") and "learning_analyst" not in present:
            follow_ups.append({"agent": "learning_analyst", "reason": "strategy analyst found repeated "
                                                                     "underperformers: examine losing outcomes"})
        if any(d.topic.startswith("volatility_outlook") for d in disagreements) and "news_analyst" not in present:
            follow_ups.append({"agent": "news_analyst", "reason": "volatility disagreement needs the calendar view"})
        for r in market:
            if r.status == "STALE" and "risk_analyst" in present:
                follow_ups.append({"agent": "market_analyst", "symbol": r.symbol, "reason": "stale market data: "
                                   "re-check once against a fresh read-only snapshot"})

        risk = present.get("risk_analyst", [None])[0]
        warnings = (risk.findings.get("warnings") if risk else []) or []
        critical_warnings = [w for w in warnings if w["severity"] == "CRITICAL"]
        engine_block = (risk.findings.get("engine_why_no_trade") if risk else None)
        conditions = {r.symbol: {"condition": r.findings.get("condition"), "status": r.status}
                      for r in market if r.symbol}
        provider = context.options.get("provider_stats") or {}

        overall = ("INCOMPLETE ASSESSMENT - " + "; ".join(critical_missing)) if not complete else \
            "COMPLETE ASSESSMENT"
        edge = ("NO EDGE CLAIM IS SUPPORTED by the desk; the engine's certificate/forward-evidence process is the "
                "only authority on edge.")
        skeptic = present.get("skeptic", [None])[0]
        if skeptic and skeptic.findings.get("edge_claims_supported"):
            edge = (f"{skeptic.findings['edge_claims_supported']} realized-profitability claim(s) passed the "
                    "Skeptic's statistical bar; this is research evidence, NOT permission to trade.")
        lines = [ADVISORY_NOTICE, overall, edge,
                 (f"{len(critical_warnings)} critical risk warning(s); engine block: "
                  f"{(engine_block or {}).get('decision', 'n/a')}"),
                 (f"{len(supported)} claim(s) supported, {len(unsupported)} not supported; "
                  f"{len(disagreements)} disagreement(s) preserved.")]
        summary = {
            "notice": ADVISORY_NOTICE, "overall": overall, "complete": complete, "edge_statement": edge,
            "critical_missing": critical_missing, "missing_optional_agents": missing_optional,
            "agent_statuses": {a: dict(c) for a, c in statuses.items()}, "failed": failed, "stale": stale,
            "market_conditions": conditions, "critical_risk_warnings": critical_warnings[:10],
            "engine_decision_reference": engine_block,
            "supported_claims": supported[:50], "unsupported_claims": unsupported[:100],
            "disagreement_count": len(disagreements), "follow_up_requests": follow_ups,
            "provider_stats": provider, "lines": lines,
        }
        claims = [Claim(claim_id="coordinator:supported_summary", kind="DESCRIPTIVE",
                        statement=f"{len(supported)} desk claims are supported by evidence",
                        evidence_ids=sorted({e for rs in present.values() for r in rs for e in r.evidence_ids})[:20],
                        conclusion_key="coordinator:summary")]
        return Draft(status="COMPLETE" if complete else "INCOMPLETE", assessment=" ".join(lines)[:2000],
                     evidence_ids=claims[0].evidence_ids, claims=claims, disagreements=disagreements[:100],
                     limitations=list(snapshot.limitations)[:20], findings={"summary": summary})


def review_summary(coordinator_result, skeptic_result) -> list[Disagreement]:
    """The Skeptic's second pass over the Coordinator: it may disagree with the
    summary itself (e.g. the summary calls the assessment complete while the
    Skeptic found contradicted claims, or claims an edge)."""
    out: list[Disagreement] = []
    if coordinator_result is None or skeptic_result is None:
        return out
    summary = coordinator_result.findings.get("summary") or {}
    counts = skeptic_result.findings.get("verdict_counts") or {}
    if summary.get("complete") and counts.get("CONTRADICTED"):
        out.append(Disagreement(topic="coordinator_summary_completeness", parties=["coordinator", "skeptic"],
                                positions={"coordinator": "COMPLETE ASSESSMENT",
                                           "skeptic": f"{counts['CONTRADICTED']} contradicted claim(s) in the inputs"},
                                severity="WARNING"))
    total = sum(counts.values()) or 0
    weak = counts.get("WEAK_EVIDENCE", 0) + counts.get("INSUFFICIENT_DATA", 0)
    if total and weak / total > 0.5:
        out.append(Disagreement(topic="coordinator_summary_strength", parties=["coordinator", "skeptic"],
                                positions={"coordinator": f"{len(summary.get('supported_claims', []))} supported",
                                           "skeptic": f"{weak}/{total} claims weak or insufficient: the desk's "
                                                      "picture is mostly uncertain"},
                                severity="INFO"))
    if "NOT permission" not in summary.get("edge_statement", "") and "NO EDGE" not in summary.get("edge_statement", ""):
        out.append(Disagreement(topic="coordinator_edge_language", parties=["coordinator", "skeptic"],
                                positions={"coordinator": summary.get("edge_statement", "")[:200],
                                           "skeptic": "edge language must defer to the certificate process"},
                                severity="CRITICAL"))
    return out
