"""Scheduler lag metrics and news-fetch starvation (Windows validation).

The scheduler is single-threaded so no two tasks ever overlap MT5 calls.
Before this fix the scheduled news refresh did its HTTP fetch on that
thread, so a slow calendar (httpx timeout 10 s per phase) held up the 1 s
protective position cycle for its whole duration. Now only the network
fetch runs on a worker thread and the scheduler waits at most a budget.
"""

from __future__ import annotations

import threading
import time

import adaptive_scalper.runtime.engine as engine_module
from adaptive_scalper.runtime.scheduler import Scheduler
from runtime_helpers import STEP, T0, FakeClock, StaticNewsProvider, build_engine, step

START_AT = T0 + 60 * STEP + 10


def test_scheduler_records_lag_and_max_duration():
    clock = [100.0]
    scheduler = Scheduler(monotonic=lambda: clock[0])

    def slow():
        clock[0] += 3.0  # a 3 s task

    scheduler.add("slow", 10.0, 2, slow)
    scheduler.add("protective", 1.0, 0, lambda: None)
    scheduler.run_due()                   # both due at t=100: protective first, then slow
    clock[0] = 104.5                      # protective was due at 101 -> started 3.5 s late
    scheduler.run_due()
    snap = scheduler.snapshot()
    assert snap["slow"]["max_duration_seconds"] == 3.0
    assert snap["protective"]["last_lag_seconds"] == 3.5
    assert snap["protective"]["max_lag_seconds"] == 3.5


class HangingProvider(StaticNewsProvider):
    """Answers the synchronous startup refresh, then hangs until released."""

    def __init__(self):
        super().__init__("hanging")
        self.calls = 0
        self.release = threading.Event()

    def fetch(self, now_utc=None):
        self.calls += 1
        if self.calls > 1:
            self.release.wait(30)
        return []


def test_a_hanging_calendar_fetch_never_starves_demo_position_management(tmp_path, monkeypatch):
    monkeypatch.setattr(engine_module, "NEWS_FETCH_BUDGET_SECONDS", 0.2)
    provider = HangingProvider()
    clock = FakeClock(START_AT)
    engine, conn, _ = build_engine(tmp_path, mode="DEMO", clock=clock, news=[provider], news_refresh_seconds=60)
    engine.startup()
    tasks = {t.name: t for t in engine.scheduler.tasks}
    try:
        started = time.monotonic()
        step(engine, clock, seconds=90)   # the 60 s news refresh falls inside this window
        elapsed = time.monotonic() - started
        assert provider.calls == 2 and not provider.release.is_set()   # the fetch is still hanging
        assert elapsed < 10, f"scheduler blocked {elapsed:.1f}s behind the hanging calendar fetch"
        before = tasks["position_cycle"].runs
        step(engine, clock, seconds=10)
        assert tasks["position_cycle"].runs >= before + 9   # still reviewing positions every second
        assert tasks["news_refresh"].failures == 0
    finally:
        provider.release.set()
    deadline = time.monotonic() + 5
    while engine.news.fetch_in_flight_seconds not in (None, 0) and time.monotonic() < deadline:
        time.sleep(0.01)
    step(engine, clock, seconds=2)       # news_poll applies the finished fetch on the scheduler thread
    assert engine.news.fetch_in_flight_seconds is None
    assert engine.news.last_success_utc is not None and engine.news.last_success_utc > START_AT
