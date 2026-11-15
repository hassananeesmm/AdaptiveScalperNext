"""Agent 5 - Skeptic: challenges every other agent, the Coordinator included.

For each claim it returns SUPPORTED, WEAK_EVIDENCE, CONTRADICTED,
INSUFFICIENT_DATA or ABSTAIN with explicit objections. Rules (deterministic):

- evidence newer than the as-of instant (look-ahead) -> CONTRADICTED;
- no evidence, a FAILED/STALE source, or an untested hypothesis -> INSUFFICIENT_DATA;
- profitability needs >= MIN_SAMPLE_PROFITABILITY observations AND a 95 % CI of
  the expectancy that excludes zero in the claimed direction AND verified
  costs; anything less is WEAK_EVIDENCE (small sample / CI spans zero /
  unverified costs);
- a forecast is WEAK_EVIDENCE until the evaluator has scored >= 30 of them
  with a hit rate better than chance;
- descriptions with internal indicator conflicts, or contradicted by another
  agent (e.g. "low volatility" right before a high-impact event), are weakened;
- the same evidence behind the same conclusion from several agents is ONE
  confirmation, not several (counted and reported);
- confidence above 0.8 on incomplete or conflicted analysis is called out.

Agreement among agents is never treated as proof of an edge.
"""

from __future__ import annotations

from collections import defaultdict

from adaptive_scalper.ai_desk.agents.base import Agent, AgentContext, Draft, age_seconds
from adaptive_scalper.ai_desk.contracts import (
    AgentResult,
    Disagreement,
    SkepticVerdict,
    Snapshot,
)
from adaptive_scalper.ai_desk.metrics import MIN_SAMPLE_PROFITABILITY

MIN_SCORED_FORECASTS = 30


class Skeptic(Agent):
    key = "skeptic"
    version = "1.0.0"

    def analyze(self, snapshot: Snapshot, context: AgentContext, symbol: str | None = None) -> Draft:
        targets: list[AgentResult] = [r for agent, rs in sorted(context.results.items()) if agent != self.key
                                      for r in rs]
        future_ids = {e.evidence_id for e in snapshot.future_items()}
        evidence_by_id = {e.evidence_id: e for e in snapshot.evidence}
        unverified_cost_symbols = set()
        cfg = snapshot.one("STRATEGY_CONFIG")
        if cfg is not None:
            unverified_cost_symbols = {s for s, c in (cfg.payload.get("costs") or {}).items()
                                       if c.get("provenance") == "UNVERIFIED_ASSUMPTION"}
        research = snapshot.one("RESEARCH_TRIALS")
        trials_without_costs = [t for t in (research.payload["trials"] if research else []) if not t["has_cost_model"]]
        imminent_events = self._imminent_events(context)
        track_record = context.options.get("forecast_track_record") or {}

        verdicts: list[SkepticVerdict] = []
        general: list[str] = []
        disagreements: list[Disagreement] = []
        by_conclusion: dict[str, list[tuple[str, frozenset]]] = defaultdict(list)

        for r in targets:
            if r.confidence is not None and r.confidence > 0.8 and (
                    r.status != "COMPLETE" or (r.findings.get("conflicts") or [])):
                general.append(f"{r.agent}{'/' + r.symbol if r.symbol else ''}: confidence {r.confidence:.2f} is "
                               "high for an incomplete or internally conflicted analysis")
            if not r.claims and r.status in ("FAILED", "STALE"):
                verdicts.append(SkepticVerdict(target_agent=r.agent, claim_id=f"{r.agent}:{r.symbol}:result",
                                               verdict="ABSTAIN" if r.status == "FAILED" else "INSUFFICIENT_DATA",
                                               objections=[f"source status {r.status}"]))
            for c in r.claims:
                objections: list[str] = []
                verdict = "SUPPORTED"
                cited = set(c.evidence_ids)
                if c.conclusion_key:
                    by_conclusion[c.conclusion_key].append((r.agent, frozenset(cited)))
                if cited & future_ids:
                    verdict = "CONTRADICTED"
                    objections.append("cites evidence timestamped after the as-of instant (look-ahead)")
                elif not cited:
                    verdict = "INSUFFICIENT_DATA"
                    objections.append("no evidence cited")
                elif r.status in ("FAILED", "STALE"):
                    verdict = "INSUFFICIENT_DATA"
                    objections.append(f"the analysis is {r.status}; its data does not describe now")
                elif c.kind == "HYPOTHESIS":
                    verdict = "INSUFFICIENT_DATA"
                    objections.append("untested hypothesis: needs the research pipeline (purged CV, untouched OOS, "
                                      "forward protocol) before it means anything")
                elif c.kind == "PROFITABILITY":
                    verdict, objs = self._profitability(r, c, unverified_cost_symbols, trials_without_costs)
                    objections += objs
                elif c.kind == "PREDICTIVE":
                    prefix = c.claim_id.rsplit(":", 1)[-1]
                    rec = track_record.get(prefix) or {}
                    if rec.get("n", 0) >= MIN_SCORED_FORECASTS and (rec.get("hit_rate_ci95") or [0])[0] > \
                            rec.get("chance_rate", 1 / 3):
                        verdict = "SUPPORTED"
                    else:
                        verdict = "WEAK_EVIDENCE"
                        objections.append(f"forecast type has {rec.get('n', 0)} scored outcomes (need "
                                          f"{MIN_SCORED_FORECASTS}) or no hit rate above chance")
                elif c.kind == "DESCRIPTIVE":
                    conflicts = r.findings.get("conflicts") or []
                    if r.status != "COMPLETE":
                        verdict = "WEAK_EVIDENCE"
                        objections.append(f"analysis status {r.status}")
                    if conflicts:
                        verdict = "WEAK_EVIDENCE"
                        objections += [f"internal conflict: {x}" for x in conflicts[:3]]
                    if r.symbol in imminent_events and r.findings.get("vol_bucket") == "LOW":
                        verdict = "CONTRADICTED"
                        objections.append(f"low-volatility description ignores an imminent high-impact event: "
                                          f"{imminent_events[r.symbol]}")
                        disagreements.append(Disagreement(
                            topic=f"volatility_outlook:{r.symbol}", parties=["market_analyst", "news_analyst"],
                            positions={"market_analyst": "LOW volatility", "news_analyst": imminent_events[r.symbol]},
                            severity="WARNING"))
                elif c.kind in ("RISK", "DATA_QUALITY"):
                    if not snapshot.runtime_live and c.kind == "RISK":
                        verdict = "WEAK_EVIDENCE"
                        objections.append("based on last-known runtime state; the runtime is not live")
                stale = [i for i in cited if i in evidence_by_id and evidence_by_id[i].data_timestamp_utc is not None
                         and (age_seconds(snapshot, evidence_by_id[i].data_timestamp_utc) or 0)
                         > context.stale_after_seconds * 48 and c.kind in ("DESCRIPTIVE", "RISK")]
                if stale and verdict == "SUPPORTED":
                    verdict = "WEAK_EVIDENCE"
                    objections.append("relies on evidence older than 48x the staleness limit")
                verdicts.append(SkepticVerdict(target_agent=r.agent, claim_id=c.claim_id, verdict=verdict,
                                               objections=objections[:6]))

        non_independent = []
        for key, entries in sorted(by_conclusion.items()):
            agents = {a for a, _ in entries}
            if len(agents) > 1:
                sets = [s for _, s in entries]
                if all(s == sets[0] for s in sets) or all(s & sets[0] for s in sets):
                    non_independent.append({"conclusion": key, "agents": sorted(agents),
                                            "note": "same evidence behind the same conclusion: one confirmation"})
        if non_independent:
            general.append(f"{len(non_independent)} conclusion(s) are repeated by several agents on shared evidence; "
                           "they count once")
        general.append("Agreement among agents is not evidence of a trading edge; only the authoritative "
                       "forward-evidence protocol and certificate process can establish one.")

        counts: dict[str, int] = defaultdict(int)
        for v in verdicts:
            counts[v.verdict] += 1
        edge_claims = [v for v in verdicts if v.claim_id.startswith("edge:")]
        findings = {"verdict_counts": dict(counts), "non_independent": non_independent,
                    "general_objections": general,
                    "edge_claims_supported": sum(1 for v in edge_claims if v.verdict == "SUPPORTED"),
                    "edge_claims_total": len(edge_claims),
                    "research_trials_without_cost_model": len(trials_without_costs),
                    "reviewed_agents": sorted({r.agent for r in targets})}
        if not targets:
            return Draft(status="ABSTAIN", assessment="Nothing to review: no agent results were provided.",
                         findings=findings, limitations=["no inputs"])
        assessment = (f"Skeptic reviewed {sum(len(r.claims) for r in targets)} claim(s) from "
                      f"{len(findings['reviewed_agents'])} agent(s): "
                      + ", ".join(f"{k} {v}" for k, v in sorted(counts.items()))
                      + ". " + general[-1])
        return Draft(status="COMPLETE", assessment=assessment, verdicts=verdicts[:400], disagreements=disagreements,
                     counterarguments=general[:10], findings=findings,
                     evidence_ids=[e for e in (research.evidence_id if research else None,
                                               cfg.evidence_id if cfg else None) if e])

    @staticmethod
    def _profitability(result: AgentResult, claim, unverified_costs, trials_without_costs):
        objections: list[str] = []
        stats = (result.findings.get("claim_stats") or {}).get(claim.claim_id) or {}
        n = claim.sample_size or stats.get("n") or 0
        ci = stats.get("expectancy_ci95")
        positive = ":positive:" in claim.claim_id
        verdict = "SUPPORTED"
        if n < MIN_SAMPLE_PROFITABILITY:
            verdict = "WEAK_EVIDENCE"
            objections.append(f"small sample: n={n} < {MIN_SAMPLE_PROFITABILITY}")
        if not ci:
            verdict = "INSUFFICIENT_DATA"
            objections.append("no confidence interval")
        elif positive and ci[0] <= 0 or not positive and ci[1] >= 0:
            verdict = "WEAK_EVIDENCE"
            objections.append(f"95% CI of expectancy {ci} includes zero")
        sym = stats.get("symbol")
        if positive and (sym in unverified_costs or not stats.get("costs_included", True)):
            verdict = "WEAK_EVIDENCE"
            objections.append("costs are unverified or missing: spreads/commission/swap/slippage not proven")
        if positive and trials_without_costs:
            objections.append(f"{len(trials_without_costs)} research trial(s) ran without a cost model")
        if positive and stats.get("source") in ("SHADOW", "BACKTEST"):
            verdict = "WEAK_EVIDENCE" if verdict == "SUPPORTED" else verdict
            objections.append("theoretical (shadow/backtest) outcomes, not realized fills")
        return verdict, objections

    @staticmethod
    def _imminent_events(context: AgentContext) -> dict[str, str]:
        out: dict[str, str] = {}
        for r in context.of("news_analyst"):
            for sym, ev in (r.findings.get("imminent_high_impact") or {}).items():
                if ev:
                    out[sym] = ev
        return out
