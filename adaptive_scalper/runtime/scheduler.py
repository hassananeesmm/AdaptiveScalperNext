"""Single-threaded cadence scheduler (directive section 16).

One thread runs every task, so no two tasks can ever issue overlapping
MT5 calls. Tasks carry a PRIORITY: when several are due, the lowest
number runs first -- position/reconciliation work (0) always before new-
entry scanning (1), directive section 15. Each run is isolated: an
exception is recorded (and reported to `on_error`) and the next task still
runs, so a failing scanner can never starve position management.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable


@dataclass
class ScheduledTask:
    name: str
    interval_seconds: float
    priority: int
    run: Callable[[], None]
    next_due: float = 0.0
    runs: int = 0
    failures: int = 0
    last_error: str | None = None
    last_duration_seconds: float | None = None


@dataclass
class Scheduler:
    monotonic: Callable[[], float] = time.monotonic
    on_error: Callable[[ScheduledTask, BaseException], None] | None = None
    tasks: list[ScheduledTask] = field(default_factory=list)

    def add(self, name: str, interval_seconds: float, priority: int, run: Callable[[], None]) -> ScheduledTask:
        task = ScheduledTask(name, interval_seconds, priority, run, next_due=self.monotonic())
        self.tasks.append(task)
        return task

    def run_due(self) -> list[str]:
        """Run every due task once, in priority order. Returns their names."""
        now = self.monotonic()
        ran = []
        for task in sorted((t for t in self.tasks if t.next_due <= now), key=lambda t: (t.priority, t.name)):
            started = self.monotonic()
            try:
                task.run()
                task.last_error = None
            except Exception as exc:
                task.failures += 1
                task.last_error = f"{type(exc).__name__}: {exc}"
                if self.on_error is not None:
                    self.on_error(task, exc)
            task.runs += 1
            task.last_duration_seconds = self.monotonic() - started
            task.next_due = started + task.interval_seconds
            ran.append(task.name)
        return ran

    def seconds_until_next(self) -> float:
        if not self.tasks:
            return 1.0
        return max(0.0, min(t.next_due for t in self.tasks) - self.monotonic())

    def run_forever(self, should_stop: Callable[[], bool], sleep: Callable[[float], None] = time.sleep) -> None:
        while not should_stop():
            self.run_due()
            sleep(min(0.25, self.seconds_until_next()))

    def snapshot(self) -> dict:
        return {
            t.name: {"interval_seconds": t.interval_seconds, "priority": t.priority, "runs": t.runs,
                     "failures": t.failures, "last_error": t.last_error,
                     "last_duration_seconds": t.last_duration_seconds}
            for t in self.tasks
        }
