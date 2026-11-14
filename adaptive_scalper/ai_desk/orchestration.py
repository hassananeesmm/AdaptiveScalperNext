"""One desk cycle: snapshot -> agents -> Skeptic -> Coordinator -> Skeptic
review of the Coordinator -> journal.

Order: Market (per symbol), News, Risk, Strategy, Learning, then the Skeptic
over all of them, then the Coordinator. If the Coordinator asks for justified
read-only follow-ups they run ONCE, after which the Skeptic and Coordinator
run a final time and only that final pass is journaled. Each agent runs under
a time limit in a worker thread; a slow or failing agent yields a FAILED
result, never a crashed cycle. Model narratives (optional) are attached after
the deterministic result is validated and never change its structured fields.

The cycle reads the trading database read-only and writes only the journal.
"""

from __future__ import annotations

import concurrent.futures
import time
from dataclasses import dataclass, field

from adaptive_scalper.ai_desk.agents import (
    Coordinator,
    LearningAnalyst,
    MarketAnalyst,
    NewsAnalyst,
    RiskAnalyst,
    Skeptic,
    StrategyAnalyst,
)
from adaptive_scalper.ai_desk.agents.base import Agent, AgentContext
from adaptive_scalper.ai_desk.agents.coordinator import review_summary
from adaptive_scalper.ai_desk.configuration import AiDeskConfig
from adaptive_scalper.ai_desk.contracts import AgentResult, Snapshot, utc_iso
from adaptive_scalper.ai_desk.journal import Journal
from adaptive_scalper.ai_desk.providers.base import ProviderRouter, utc_day_start
from adaptive_scalper.ai_desk.snapshot_builder import SnapshotBuilder

AGENT_ORDER = ("market_analyst", "news_analyst", "risk_analyst", "strategy_analyst", "learning_analyst")
AGENT_CLASSES: dict[str, type[Agent]] = {
    "market_analyst": MarketAnalyst, "news_analyst": NewsAnalyst, "risk_analyst": RiskAnalyst,
    "strategy_analyst": StrategyAnalyst, "learning_analyst": LearningAnalyst, "skeptic": Skeptic,
    "coordinator": Coordinator,
}
PHASE1_AGENTS = frozenset({"market_analyst", "risk_analyst", "skeptic"})
CATEGORY_OF_AGENT = {"market_analyst": "market_features", "strategy_analyst": "strategy_statistics",
                     "learning_analyst": "strategy_statistics", "news_analyst": "news_calendar",
                     "risk_analyst": "risk_summary", "skeptic": "strategy_statistics",
                     "coordinator": "risk_summary"}


@dataclass
class CycleReport:
    run_id: str
    snapshot_id: str | None
    status: str
    trigger: str
    as_of_utc: int
    mode: str
    results: list[AgentResult] = field(default_factory=list)
    inserted: int = 0
    duplicates: int = 0
    duration_ms: int = 0
    error: str | None = None

    def summary(self) -> dict:
        return {"run_id": self.run_id, "snapshot_id": self.snapshot_id, "status": self.status, "trigger": self.trigger,
                "as_of": utc_iso(self.as_of_utc), "mode": self.mode, "duration_ms": self.duration_ms,
                "journal_inserted": self.inserted, "journal_duplicates": self.duplicates, "error": self.error,
                "agents": [{"agent": r.agent, "symbol": r.symbol, "status": r.status, "duration_ms": r.duration_ms,
                            "assessment": r.assessment, "narrative": r.llm_narrative_status} for r in self.results]}


def _digest(result: AgentResult) -> dict:
    """What an external model may see: the assessment, limitations, claims and a
    small part of the findings - never raw account data."""
    keep = {k: v for k, v in result.findings.items() if k in (
        "condition", "regime", "trend", "vol_bucket", "conflicts", "change", "underperformers", "hypotheses",
        "imminent_high_impact", "conflicts", "interpretation", "warnings", "summary", "verdict_counts",
        "recommendations", "deterioration", "feature_drift")}
    if "summary" in keep:
        keep["summary"] = {k: keep["summary"].get(k) for k in ("overall", "edge_statement", "lines",
                                                              "critical_missing", "market_conditions")}
    return {"agent": result.agent, "symbol": result.symbol, "status": result.status,
            "assessment": result.assessment, "limitations": result.limitations[:8],
            "claims": [{"id": c.claim_id, "statement": c.statement, "evidence": c.evidence_ids} for c in result.claims[:20]],
            "findings": keep}


class DeskOrchestrator:
    def __init__(self, config: AiDeskConfig, *, trading_db_path: str, journal: Journal, trading_config=None,
                 router: ProviderRouter | None = None) -> None:
        self.config = config
        self.journal = journal
        self.builder = SnapshotBuilder(trading_db_path, symbols=config.symbols, resolution=config.market_resolution,
                                       runtime_state_stale_seconds=config.runtime_state_stale_seconds,
                                       trading_config=trading_config)
        self.router = router or ProviderRouter(config, journal)
        self.fingerprint = config.fingerprint()

    # --------------------------------------------------------------- helpers
    def _run_agent(self, agent: Agent, snapshot: Snapshot, ctx: AgentContext, symbol: str | None) -> AgentResult:
        pool = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"ai-desk-{agent.key}")
        try:
            future = pool.submit(agent.run, snapshot, ctx, symbol)
            try:
                return future.result(timeout=self.config.agent_timeout_seconds)
            except concurrent.futures.TimeoutError:
                return agent._failed(f"timeout-{agent.key}-{snapshot.snapshot_id}-{symbol}", snapshot, ctx, symbol,
                                     f"timed out after {self.config.agent_timeout_seconds}s", time.perf_counter())
        finally:
            pool.shutdown(wait=False, cancel_futures=True)

    def _narrate(self, result: AgentResult, snapshot: Snapshot, run_id: str,
                 deadline: float | None = None) -> AgentResult:
        if result.status == "FAILED":
            return result
        try:
            outcome = self.router.narrate(agent=result.agent, category=CATEGORY_OF_AGENT.get(result.agent, ""),
                                          digest=_digest(result), allowed_evidence_ids=snapshot.ids(),
                                          run_id=run_id, deadline=deadline)
        except Exception as exc:  # noqa: BLE001 - the deterministic result never depends on a narrative
            return result.model_copy(update={"llm_narrative_status": f"FAILED:{type(exc).__name__}"})
        if outcome.narrative is None:
            return result.model_copy(update={"llm_narrative_status": outcome.status})
        n = outcome.narrative
        text = n["narrative"] + ("\nCounterarguments: " + " | ".join(n["counterarguments"])
                                 if n["counterarguments"] else "")
        return result.model_copy(update={
            "llm_narrative": f"[UNTRUSTED MODEL TEXT - {outcome.provider}/{outcome.model}] {text}"[:1900],
            "llm_narrative_status": f"{outcome.status}:{outcome.provider}/{outcome.model}"})

    def provider_stats(self) -> dict:
        return {"providers": self.router.status(), "usage_today": self.journal.usage_since(utc_day_start())}

    # ------------------------------------------------------------------- run
    def run_cycle(self, *, agents: set[str] | None = None, trigger: str = "manual", as_of_utc: int | None = None,
                  mode: str = "CURRENT", options: dict | None = None) -> CycleReport:
        started = time.perf_counter()
        wanted = set(agents or AGENT_CLASSES)
        wanted |= {"skeptic", "coordinator"}          # the Skeptic and Coordinator always review
        as_of = int(as_of_utc if as_of_utc is not None else time.time())
        run_id = self.journal.start_run(trigger=trigger, mode=mode, config_fingerprint=self.fingerprint,
                                        agents=sorted(wanted))
        report = CycleReport(run_id=run_id, snapshot_id=None, status="FAILED", trigger=trigger, as_of_utc=as_of,
                             mode=mode)
        try:
            prior = self.journal.prior_market_assessments(as_of)
            snapshot = self.builder.build(as_of_utc=as_of, mode=mode, prior_assessments=prior)
            report.snapshot_id = snapshot.snapshot_id
            self.journal.record_snapshot(snapshot)
            opts = {"provider_stats": self.provider_stats(), **(options or {})}
            if "forecast_track_record" not in opts:
                from adaptive_scalper.ai_desk.evaluator import forecast_track_record

                opts["forecast_track_record"] = forecast_track_record(self.journal)
            ctx = AgentContext(config_fingerprint=self.fingerprint, stale_after_seconds=self.config.stale_after_seconds,
                               options=opts)

            def run_one(key: str, symbol: str | None = None) -> AgentResult:
                result = self._run_agent(AGENT_CLASSES[key](), snapshot, ctx, symbol)
                ctx.results.setdefault(key, []).append(result)
                return result

            for key in AGENT_ORDER:
                if key not in wanted:
                    continue
                if AGENT_CLASSES[key].per_symbol:
                    for sym in self.config.symbols:
                        run_one(key, sym)
                else:
                    run_one(key)
            skeptic = run_one("skeptic")
            coordinator = run_one("coordinator")
            follow = coordinator.findings.get("summary", {}).get("follow_up_requests") or []
            if follow:
                for req in follow:
                    key = req["agent"]
                    if key in AGENT_CLASSES and key not in ("skeptic", "coordinator") and key not in ctx.results:
                        if AGENT_CLASSES[key].per_symbol:
                            for sym in self.config.symbols:
                                run_one(key, sym)
                        else:
                            run_one(key)
                ctx.results.pop("skeptic", None)
                ctx.results.pop("coordinator", None)
                ctx.options["follow_ups_executed"] = follow
                skeptic = run_one("skeptic")
                coordinator = run_one("coordinator")
            review = review_summary(coordinator, skeptic)

            final: list[AgentResult] = []
            deadline = self.router.clock() + self.config.narrative_budget_seconds
            for key in (*AGENT_ORDER, "skeptic", "coordinator"):
                for r in ctx.results.get(key, []):
                    final.append(self._narrate(r, snapshot, run_id, deadline))
            for r in final:
                if self.journal.record_result(run_id, r, as_of_epoch=as_of):
                    report.inserted += 1
                else:
                    report.duplicates += 1
            if review:
                self.journal.record_disagreements(run_id, review)
            report.results = final
            report.status = "COMPLETED"
            self.journal.finish_run(run_id, status="COMPLETED", snapshot_id=snapshot.snapshot_id)
        except Exception as exc:  # noqa: BLE001 - a desk failure is journaled, never raised into a caller loop
            report.error = f"{type(exc).__name__}: {exc}"[:1000]
            self.journal.finish_run(run_id, status="FAILED", snapshot_id=report.snapshot_id, error=report.error)
        report.duration_ms = int((time.perf_counter() - started) * 1000)
        return report
