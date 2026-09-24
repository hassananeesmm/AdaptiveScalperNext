"""The production runtime coordinator (directive sections 14-16, 115).

`RuntimeEngine` owns ONE database connection and ONE gateway (in real use
`gateway.factory.create_live_gateway()`, a `SynchronizedGateway`) and runs
everything on one thread via `runtime.scheduler.Scheduler`, so MT5 calls
can never overlap.

Startup (`startup()`), in directive section 115's order, failing CLOSED:
database integrity -> kill-switch state READ (never bootstrapped, never
cleared) -> MT5 initialize -> account must be DEMO (a REAL/CONTEST account
refuses to start, in PAPER as well as DEMO) -> resolve + validate the
three canonical symbols (unresolvable ones are excluded, never
substituted) -> retired strategies asserted absent -> DEMO only: quarantine
submissions a dead process left in flight, reconcile, apply UNKNOWN
resolutions -> news refresh -> journal->RAG ingestion + index rebuild ->
OKF knowledge bundle load (advisory; failures = DEGRADED) -> heartbeat.

Scheduled tasks (priority, cadence from `[runtime]`):
    0 position_cycle  ~1s   DEMO: reconcile/UNKNOWN/position reviews
    1 entry_cycle     ~4s   DEMO: new-entry pipeline; PAPER: paper cycle
    2 news_refresh    ~20m  remote calendar refresh (never per entry cycle)
    3 rag_ingest      ~60s  journal/paper/incident rows -> typed RAG memories
                            (the trading cycles never write RAG themselves)
    3 rag_rebuild     ~10m  rebuild the derived TF-IDF index
    3 cost_evidence_sweep ~60s  DEMO: complete execution cost observations
                            for closed positions (evidence only)
    4 heartbeat       ~1s   engine state for the observer-only dashboard

The dashboard never runs inside this process: it reads the database, so
its failure cannot reach trading.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from dataclasses import dataclass
from typing import Callable

from adaptive_scalper.config.constants import RETIRED_STRATEGY_KEYS
from adaptive_scalper.config.loader import AppConfig
from adaptive_scalper.core.kill_switch import get_state as get_kill_switch_state
from adaptive_scalper.costs.observations import sweep_exit_costs
from adaptive_scalper.execution.reconciliation import run_reconciliation
from adaptive_scalper.execution.recovery import apply_unknown_resolutions, quarantine_interrupted_submissions
from adaptive_scalper.gateway.protocol import Gateway
from adaptive_scalper.gateway.spec_store import save_symbol_spec
from adaptive_scalper.gateway.symbol_resolver import persist_all, resolve_all
from adaptive_scalper.gateway.symbol_validation import validate_resolved_symbol
from adaptive_scalper.gateway.types import TradeMode
from adaptive_scalper.knowledge.advisor import KnowledgeAdvisor
from adaptive_scalper.learning.observer import EntryObserver
from adaptive_scalper.news.providers.cache import CacheProvider
from adaptive_scalper.persistence.database import integrity_check
from adaptive_scalper.rag.ingestion import ingest as ingest_rag_memories
from adaptive_scalper.rag.service import RagService
from adaptive_scalper.runtime.advisory import AdvisoryPanel
from adaptive_scalper.runtime.demo import DemoRuntime
from adaptive_scalper.runtime.news_monitor import NewsMonitor, default_live_providers
from adaptive_scalper.runtime.paper import PaperRuntime
from adaptive_scalper.runtime.scheduler import Scheduler
from adaptive_scalper.runtime.state import put_state, record_event
from adaptive_scalper.strategies import build_active_registry

logger = logging.getLogger(__name__)

ENGINE_RUNNING = "RUNNING"
ENGINE_DEGRADED = "DEGRADED"
ENGINE_STOPPED = "STOPPED"
RAG_INGEST_SECONDS = 60.0


class RuntimeStartupError(RuntimeError):
    """Startup refused -- the engine must not run in this state."""


@dataclass
class RuntimeComponents:
    news_providers: list | None = None     # None -> the real FinanceCalendar/ForexFactory chain
    rag: RagService | None = None
    observer: EntryObserver | None = None
    okf: object | None = None             # None -> KnowledgeAdvisor over the Git-tracked `knowledge/` bundle


class RuntimeEngine:
    def __init__(
        self, config: AppConfig, conn: sqlite3.Connection, gateway: Gateway, *,
        clock: Callable[[], float] = time.time, monotonic: Callable[[], float] = time.monotonic,
        components: RuntimeComponents | None = None,
    ) -> None:
        self.config = config
        self.mode = config.mode
        self.conn = conn
        self.gateway = gateway
        self.clock = clock
        self.components = components or RuntimeComponents()
        self.rag = self.components.rag if self.components.rag is not None else RagService()
        self.observer = self.components.observer if self.components.observer is not None else EntryObserver()
        self.okf = self.components.okf
        self.news = NewsMonitor(
            conn,
            self.components.news_providers if self.components.news_providers is not None else default_live_providers(),
            CacheProvider(conn, max_age_seconds=config.runtime.news_stale_after_seconds),
            pre_minutes=config.news.pre_high_impact_minutes, post_minutes=config.news.post_high_impact_minutes,
            refresh_seconds=config.runtime.news_refresh_seconds,
            stale_after_seconds=config.runtime.news_stale_after_seconds,
        )
        self.scheduler = Scheduler(monotonic=monotonic, on_error=self._task_failed)
        self.symbols: dict[str, str] = {}
        self.demo: DemoRuntime | None = None
        self.paper: PaperRuntime | None = None
        self.started_at: int | None = None
        self.component_health: dict[str, dict] = {}
        self._stop = False

    # ------------------------------------------------------------------
    def now(self) -> int:
        return int(self.clock())

    def _health(self, component: str, status: str, detail: str = "") -> None:
        self.component_health[component] = {"status": status, "detail": detail, "at": self.now()}

    def _task_failed(self, task, exc: BaseException) -> None:
        logger.error("scheduled task %s failed: %s", task.name, exc, exc_info=exc)
        record_event(self.conn, "ERROR", "scheduler", "TASK_FAILED", f"{task.name}: {type(exc).__name__}: {exc}",
                     dedup_key=f"task_failed:{task.name}", now_utc=self.now())

    # ------------------------------------------------------------------
    def startup(self) -> dict:
        now = self.now()
        integrity = integrity_check(self.conn)
        if integrity != "ok":
            raise RuntimeStartupError(f"database integrity check failed: {integrity}")
        kill = get_kill_switch_state(self.conn)  # READ only -- never bootstrapped or cleared here

        if not self.gateway.initialize():
            raise RuntimeStartupError("MT5 terminal could not be initialized -- start MT5 and log in to a DEMO account")
        account = self.gateway.account_info()
        if account is None:
            raise RuntimeStartupError("MT5 account_info() returned nothing -- terminal not logged in")
        if account.trade_mode != TradeMode.DEMO:
            raise RuntimeStartupError(
                f"connected account trade_mode={account.trade_mode.name}: REAL-MONEY EXECUTION IS DISABLED -- "
                f"this system only runs against an MT5 DEMO account"
            )

        results = resolve_all(self.gateway.symbols_get())
        persist_all(self.conn, results)
        excluded = {}
        for canonical in self.config.market.symbols:
            result = results.get(canonical)
            if result is None or not result.resolved:
                excluded[canonical] = f"unresolved: {result.reason if result else 'no result'}"
                continue
            validation = validate_resolved_symbol(self.gateway, canonical, result.broker_symbol, now=self.clock())
            if not validation.valid:
                excluded[canonical] = f"{validation.reason}: {validation.detail}"
                continue
            self.symbols[canonical] = result.broker_symbol
            spec = self.gateway.symbol_info(result.broker_symbol)
            if spec is not None:
                save_symbol_spec(self.conn, canonical, spec, now_utc=now)  # for offline research commands
        for canonical, why in excluded.items():
            record_event(self.conn, "WARNING", "symbols", "SYMBOL_EXCLUDED", why, canonical_symbol=canonical,
                         dedup_key=f"symbol_excluded:{canonical}", now_utc=now)
        if not self.symbols:
            raise RuntimeStartupError(f"no canonical symbol resolved and validated: {excluded}")

        registry = build_active_registry()
        active = {s.key for s in registry.all_active()}
        if active & RETIRED_STRATEGY_KEYS:
            raise RuntimeStartupError(f"retired strategies are active: {sorted(active & RETIRED_STRATEGY_KEYS)}")

        recovery = {}
        if self.mode == "DEMO":
            recovery["quarantined_orders"] = quarantine_interrupted_submissions(self.conn, now_utc=now)
            report = run_reconciliation(self.conn, self.gateway, f"startup-reconcile:{now}", now_utc=now)
            put_state(self.conn, "reconciliation", {"status": report.status, "at": now,
                                                    "unrepaired_positions": report.unrepaired_position_ids,
                                                    "unrepaired_orders": report.unrepaired_order_ids}, now_utc=now)
            recovery["reconciliation"] = report.status
            recovery["unknown_resolutions"] = [o.detail for o in apply_unknown_resolutions(self.conn, self.gateway, now_utc=now)]

        self._refresh_news()
        self._ingest_rag(rebuild=False)
        self._rebuild_rag()

        if self.okf is None:
            self.okf = self._load_knowledge()
        advisory = AdvisoryPanel(rag=self.rag, observer=self.observer, okf=self.okf)
        if self.mode == "DEMO":
            self.demo = DemoRuntime(self.conn, self.gateway, self.config, self.symbols, registry, self.news, advisory,
                                    clock=self.clock)
            self.scheduler.add("position_cycle", self.config.runtime.position_cycle_seconds, 0, self.demo.position_cycle)
            self.scheduler.add("entry_cycle", self.config.runtime.entry_cycle_seconds, 1, self.demo.entry_cycle)
            self.scheduler.add("cost_evidence_sweep", 60.0, 3, self._sweep_cost_evidence)
        else:
            self.paper = PaperRuntime(self.conn, self.gateway, self.config, self.symbols, self.news,
                                      clock=self.clock)
            self.scheduler.add("entry_cycle", self.config.runtime.entry_cycle_seconds, 1, self.paper.cycle)
        self.scheduler.add("news_refresh", self.config.runtime.news_refresh_seconds, 2, self._refresh_news)
        self.scheduler.add("rag_ingest", RAG_INGEST_SECONDS, 3, self._ingest_rag)
        self.scheduler.add("rag_rebuild", 600, 3, self._rebuild_rag)
        self.scheduler.add("heartbeat", self.config.runtime.heartbeat_seconds, 4, self.heartbeat)
        # news/rag already ran during startup: don't repeat them on the first tick
        for task in self.scheduler.tasks:
            if task.name in ("news_refresh", "rag_ingest", "rag_rebuild"):
                task.next_due += task.interval_seconds

        self.started_at = now
        summary = {
            "mode": self.mode, "symbols": self.symbols, "excluded_symbols": excluded,
            "kill_switch": kill.status.value, "kill_switch_blocks_new_entries": kill.blocks_new_entries,
            "account_server": account.server, "account_trade_mode": account.trade_mode.name,
            "recovery": recovery, "news": self.news.health(now).value,
        }
        put_state(self.conn, "startup", summary, now_utc=now)
        record_event(self.conn, "INFO", "engine", "ENGINE_STARTED", f"{self.mode} runtime started", now_utc=now)
        self.heartbeat()
        return summary

    def _refresh_news(self) -> None:
        now = self.now()
        health = self.news.refresh(now)
        self._health("news", health.value, "; ".join(f"{k}: {v}" for k, v in self.news.errors.items()))
        put_state(self.conn, "news", self.news.snapshot(now), now_utc=now)
        if self.news.global_block(now) is not None:
            record_event(self.conn, "BLOCKED", "news", "NEWS_CALENDAR_UNAVAILABLE",
                         f"calendar health {health.value}: new entries blocked", dedup_key="news:unavailable",
                         now_utc=now)
        else:
            from adaptive_scalper.runtime.state import clear_event
            clear_event(self.conn, "news:unavailable", now_utc=now)

    def _load_knowledge(self) -> KnowledgeAdvisor | None:
        """The curated OKF bundle (advisory; a missing or invalid bundle
        degrades knowledge context, never the engine)."""
        try:
            advisor = KnowledgeAdvisor.from_path(clock=self.clock)
        except Exception as exc:
            logger.warning("OKF knowledge bundle unavailable: %s", exc)
            self._health("okf", "DEGRADED", f"bundle unavailable: {type(exc).__name__}")
            return None
        errors = sum(1 for i in advisor.bundle.issues if i.severity == "ERROR")
        self._health("okf", "OK" if not errors else "DEGRADED",
                     f"{len(advisor.bundle.concepts)} concepts, {errors} quarantined issue(s)")
        return advisor

    def _ingest_rag(self, rebuild: bool = True) -> None:
        """Journal -> RAG ingestion, off the trading hot path. A failure
        degrades advisory context only; it never stops the engine."""
        try:
            report = ingest_rag_memories(self.conn, now_utc=self.now())
        except Exception as exc:
            logger.warning("RAG ingestion raised: %s", exc)
            self._health("rag_ingest", "DEGRADED", f"ingestion failed: {type(exc).__name__}")
            return
        self._health("rag_ingest", "OK")
        if rebuild and report.inserted:
            self._rebuild_rag()

    def _sweep_cost_evidence(self) -> None:
        """Completes DEMO execution cost observations once positions close
        (off the hot path; evidence only)."""
        try:
            sweep_exit_costs(self.conn, now_utc=self.now())
            self._health("cost_evidence", "OK")
        except Exception as exc:
            logger.warning("cost evidence sweep failed: %s", exc)
            self._health("cost_evidence", "DEGRADED", f"sweep failed: {type(exc).__name__}")

    def _rebuild_rag(self) -> None:
        try:
            status = self.rag.rebuild_index(self.conn)
        except Exception as exc:  # advisory only: never allowed to fail the engine
            status = "DEGRADED"
            logger.warning("RAG index rebuild raised: %s", exc)
        self._health("rag", status, "" if status == "OK" else "index rebuild failed; advisory context degraded")

    def heartbeat(self) -> None:
        now = self.now()
        degraded = [name for name, h in self.component_health.items() if h["status"] not in ("OK", "HEALTHY")]
        state = ENGINE_DEGRADED if degraded else ENGINE_RUNNING
        put_state(self.conn, "engine", {
            "state": state, "mode": self.mode, "started_at_utc": self.started_at, "heartbeat_utc": now,
            "symbols": self.symbols, "degraded_components": degraded, "tasks": self.scheduler.snapshot(),
        }, now_utc=now)
        put_state(self.conn, "components", self.component_health, now_utc=now)

    def stop(self) -> None:
        self._stop = True

    def run(self, *, sleep: Callable[[float], None] = time.sleep, max_iterations: int | None = None) -> None:
        iterations = 0

        def should_stop() -> bool:
            nonlocal iterations
            iterations += 1
            return self._stop or (max_iterations is not None and iterations > max_iterations)

        try:
            self.scheduler.run_forever(should_stop, sleep=sleep)
        finally:
            put_state(self.conn, "engine", {"state": ENGINE_STOPPED, "mode": self.mode,
                                            "stopped_at_utc": self.now(), "started_at_utc": self.started_at},
                      now_utc=self.now())
            record_event(self.conn, "INFO", "engine", "ENGINE_STOPPED", f"{self.mode} runtime stopped",
                         now_utc=self.now())
