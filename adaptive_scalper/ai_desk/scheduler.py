"""The autonomous research service (`ai-desk serve`), a separate process.

Loop: poll events (read-only) -> drop events already processed (journal) ->
map new events to agents -> drop agents still inside their minimum interval ->
run one cycle for the remaining agents -> score due forward checks -> prune
by retention -> heartbeat. Agents never run per tick; a burst of events
becomes one cycle. A failure in any step is recorded in the service state and
the loop continues after a backoff; the trading runtime is unaffected either
way because nothing here runs inside it.

The service refuses to start unless the operator enabled it in
`config/ai_desk.toml` (`enabled = true`). It never starts the trading runtime,
never touches the kill switch and never changes broker state.
"""

from __future__ import annotations

import threading
import time

from adaptive_scalper.ai_desk import DESK_VERSION
from adaptive_scalper.ai_desk.configuration import AiDeskConfig
from adaptive_scalper.ai_desk.evaluator import score_forward_checks
from adaptive_scalper.ai_desk.event_bus import EventBus
from adaptive_scalper.ai_desk.journal import Journal
from adaptive_scalper.ai_desk.orchestration import DeskOrchestrator


class DeskDisabledError(Exception):
    pass


class DeskService:
    def __init__(self, config: AiDeskConfig, *, trading_db_path: str, journal: Journal, trading_config=None,
                 orchestrator: DeskOrchestrator | None = None, bus: EventBus | None = None, clock=time.time,
                 sleep=None) -> None:
        self.config = config
        self.journal = journal
        self.trading_db_path = trading_db_path
        self.orchestrator = orchestrator or DeskOrchestrator(config, trading_db_path=trading_db_path, journal=journal,
                                                             trading_config=trading_config)
        self.bus = bus or EventBus(trading_db_path, symbols=config.symbols,
                                   scheduled_report_seconds=config.intervals.scheduled_report)
        self.clock = clock
        self.stop_event = threading.Event()
        self.sleep = sleep or (lambda s: self.stop_event.wait(s))
        self.consecutive_errors = 0

    def _interval(self, agent: str) -> float:
        return float(getattr(self.config.intervals, agent, 0.0))

    def due_agents(self, events, now: int) -> tuple[set[str], list[str]]:
        agents: set[str] = set()
        triggers: list[str] = []
        scheduled = any(e.event_type == "SCHEDULED_REPORT" for e in events)
        for e in events:
            triggers.append(e.event_type)
            for a in e.agents:
                last = float(self.journal.get_watermark(f"last_run:{a}", "0") or 0)
                if scheduled or now - last >= self._interval(a):
                    agents.add(a)
        return agents, sorted(set(triggers))

    def tick(self) -> dict:
        now = int(self.clock())
        out: dict = {"at": now}
        try:
            events = self.bus.poll(now)
            new = [e for e in events if self.journal.mark_event(e.key, e.event_type)]
            agents, triggers = self.due_agents(new, now)
            out["new_events"] = [e.key for e in new]
            if agents:
                report = self.orchestrator.run_cycle(agents=agents, trigger=",".join(triggers), as_of_utc=now)
                out["cycle"] = {"run_id": report.run_id, "status": report.status, "agents": sorted(agents),
                                "duration_ms": report.duration_ms, "error": report.error}
                if report.status == "COMPLETED":
                    for a in agents:
                        self.journal.set_watermark(f"last_run:{a}", str(now))
            last_score = int(self.journal.get_watermark("last_forward_scoring", "0") or 0)
            if now - last_score >= 3600:
                out["forward_checks"] = score_forward_checks(self.journal, self.trading_db_path, now=now)
                self.journal.set_watermark("last_forward_scoring", str(now))
            last_prune = int(self.journal.get_watermark("last_prune", "0") or 0)
            if now - last_prune >= 86400:
                out["pruned"] = self.journal.prune(self.config.retention_days, now=now)
                self.journal.set_watermark("last_prune", str(now))
            self.consecutive_errors = 0
            out["status"] = "OK"
        except Exception as exc:  # noqa: BLE001 - the service survives any single failure
            self.consecutive_errors += 1
            out["status"] = "ERROR"
            out["error"] = f"{type(exc).__name__}: {exc}"[:500]
        self.journal.put_state("service", {"heartbeat_utc": now, "pid_version": DESK_VERSION,
                                           "last_tick": out, "consecutive_errors": self.consecutive_errors,
                                           "enabled": self.config.enabled, "provider": self.config.provider,
                                           "config_fingerprint": self.config.fingerprint()})
        return out

    def serve(self, *, max_ticks: int | None = None) -> int:
        if not self.config.enabled:
            raise DeskDisabledError("the AI desk research service is disabled: set `enabled = true` in "
                                    "config/ai_desk.toml to run it (advisory only, no execution authority)")
        recovered = self.journal.recover_incomplete_runs()
        self.journal.put_state("service_start", {"started_at_utc": int(self.clock()), "recovered_runs": recovered})
        ticks = 0
        while not self.stop_event.is_set():
            result = self.tick()
            ticks += 1
            if max_ticks is not None and ticks >= max_ticks:
                break
            backoff = min(300.0, self.config.poll_seconds * (2 ** min(self.consecutive_errors, 4))) \
                if result["status"] == "ERROR" else self.config.poll_seconds
            self.sleep(backoff)
        return ticks

    def stop(self) -> None:
        self.stop_event.set()
