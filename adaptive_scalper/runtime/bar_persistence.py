"""Persist the CLOSED bars the trading runtime already holds -- no MT5 calls.

The runtime is the only MT5 client. Every entry cycle it already fetches the
latest `bar_history_count` closed bars per symbol (DEMO: `DemoRuntime.analyze`
-> `shadow_bars`, also under a global entry block; PAPER: `PaperRuntime.cycle`
-> `closed_bars_cache`). `LiveBarSink` copies the ones not yet stored into the
`bars` table so offline readers (the AI desk, research) see fresh data. It
never touches the gateway, and nothing in trading reads what it writes.

Guarantees (disabled by default: `[runtime] persist_closed_bars = false`):
- Closed bars only. The source already dropped the forming bar against the
  symbol's own tick; the sink re-checks `time + step <= now` on the local
  UTC clock. A bar ahead of the clock stops that symbol for the run (and is
  reported) instead of being written -- a stale tick can only make the source
  hold FEWER bars, never a forming one.
- UTC only. The engine refuses to start on a non-UTC database; the sink
  re-checks `require_utc` before its first write and disables itself if it
  fails. Bars within one hour of a server-clock DST change are never written:
  the repeated autumn server hour maps two broker bars onto one UTC instant
  and the skipped spring hour has none, so such a label cannot be trusted
  (`history bootstrap` may fill them deliberately while the runtime is stopped).
- Idempotent. A per-symbol watermark (seeded from `MAX(ts_utc)`) skips what is
  stored; `INSERT OR IGNORE` on UNIQUE(canonical_symbol, resolution, ts_utc)
  catches the rest. The first write wins; a later broker revision is ignored.
- Bounded. A run with nothing new opens no transaction. Otherwise ONE short
  `BEGIN IMMEDIATE` transaction attempted with a ZERO busy timeout: a database
  whose write lock another connection holds is skipped at once (SQLITE_BUSY,
  no busy-handler sleep -- on Windows even a 50 ms timeout cost ~100 ms per
  attempt) and retried next run. At most `max_rows_per_symbol` bars per symbol
  are written per run, so a cold start after a long stop catches up over a
  few runs instead of in one long transaction. `run()` never raises; failures
  are kept in memory (component health) and the failure path writes nothing,
  since a write could wait on the same lock.
- No table scans on the scheduler. Coverage (`historical_bar_coverage`) is
  advanced arithmetically from the rows this transaction inserted (all newer
  than the stored maximum): one primary-key read and one update, never the
  MIN/MAX/COUNT aggregate over `bars`. A symbol with no coverage row yet is
  left to `history bootstrap` (reported as `coverage_pending`).
- Progress only on commit. Watermarks, counters and the published snapshot
  change only after COMMIT succeeds; a skipped or failed run leaves them, and
  the database, exactly as they were.
- Health. `active_error` is the error of the most recent run and is cleared by
  the next successful run; `last_error`/`last_error_at` keep the most recent
  error for history and `recovered_at` marks when a run succeeded after it,
  so "recovered" and "currently failing" are distinguishable.
- Gaps. When the cached window no longer reaches the stored watermark (the
  runtime was down longer than the cache covers) a BAR_GAP warning names the
  range; it can include ordinary market closures.
"""

from __future__ import annotations

import logging
import math
import sqlite3
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field, replace

from adaptive_scalper.gateway.server_time import (
    RULE_UTC,
    offset_seconds_at_utc,
    validate_rule,
)
from adaptive_scalper.gateway.types import Bar
from adaptive_scalper.history.resolutions import resolution_seconds
from adaptive_scalper.history.store import insert_bars
from adaptive_scalper.history.time_basis import TimeBasisError, require_utc
from adaptive_scalper.runtime.state import put_state, record_event

logger = logging.getLogger(__name__)

HOUR = 3600
STATE_KEY = "bar_persistence"
SINK_BUSY_TIMEOUT_MS = 0
MAX_ROWS_PER_SYMBOL = 100
LOCK_SKIPS_DEGRADED = 5

# Health states (`LiveBarSink.health_state`).
HEALTH_OK = "OK"
HEALTH_RECOVERED = "RECOVERED"
HEALTH_LOCK_CONTENDED = "LOCK_CONTENDED"
HEALTH_ERROR = "ERROR"
HEALTH_DISABLED = "DISABLED"

OK = "OK"
IDLE = "IDLE"
SKIPPED = "SKIPPED"
FAILED = "FAILED"
DISABLED = "DISABLED"

NOT_CLOSED = "not closed by the local UTC clock"

BarSource = Callable[[], Mapping[str, Sequence[Bar]]]


def bar_problem(bar: Bar, step: int, now: int) -> str | None:
    """Why `bar` must not be stored, or None when it is a sound closed bar."""
    if bar.time <= 0 or bar.time % step:
        return "timestamp not aligned to the resolution"
    if bar.time + step > now:
        return NOT_CLOSED
    prices = (bar.open, bar.high, bar.low, bar.close)
    if not all(isinstance(p, int | float) and math.isfinite(p) and p > 0 for p in prices):
        return "non-finite or non-positive price"
    if bar.low > min(bar.open, bar.close) or bar.high < max(bar.open, bar.close):
        return "high/low do not bound open/close"
    if min(bar.tick_volume, bar.spread, bar.real_volume) < 0:
        return "negative volume or spread"
    return None


def near_dst_change(rule: str, bar_time: int, step: int) -> bool:
    """True when a server-clock offset change falls within one hour of the bar."""
    if rule == RULE_UTC:
        return False
    return offset_seconds_at_utc(rule, bar_time - HOUR) != offset_seconds_at_utc(rule, bar_time + step + HOUR)


@dataclass
class SinkStats:
    runs: int = 0
    transactions: int = 0
    rows_written: int = 0
    last_rows: int = 0
    rejected: int = 0
    dst_skipped: int = 0
    gaps_reported: int = 0
    lock_skips: int = 0
    failures: int = 0
    consecutive_unsuccessful: int = 0     # SKIPPED (lock) or FAILED runs since the last OK/IDLE run
    last_status: str | None = None
    active_error: str | None = None       # error of the latest run; None once a run succeeds
    last_error: str | None = None         # most recent error ever seen (history)
    last_error_at: int | None = None
    last_success_at: int | None = None
    recovered_at: int | None = None       # first successful run after last_error
    last_duration_ms: float | None = None
    max_duration_ms: float = 0.0
    db_bytes: int | None = None
    coverage_pending: list[str] = field(default_factory=list)


@dataclass
class _Plan:
    rows: dict[str, list[Bar]]
    marks: dict[str, int]
    events: list[tuple[str, str, str, str, str]]   # severity, event, detail, symbol, dedup key
    rejected: int = 0
    dst_skipped: int = 0
    gaps: int = 0


class LiveBarSink:
    def __init__(
        self, conn: sqlite3.Connection, *, resolution: str, server_time_rule: str, source: BarSource,
        clock: Callable[[], float] = time.time, monotonic: Callable[[], float] = time.monotonic,
        busy_timeout_ms: int = SINK_BUSY_TIMEOUT_MS, max_rows_per_symbol: int = MAX_ROWS_PER_SYMBOL,
    ) -> None:
        if max_rows_per_symbol < 1:
            raise ValueError("max_rows_per_symbol must be >= 1")
        self.conn = conn
        self.resolution = resolution
        self.step = resolution_seconds(resolution)
        self.rule = validate_rule(server_time_rule)
        self.source = source
        self.clock = clock
        self.monotonic = monotonic
        self.busy_timeout_ms = int(busy_timeout_ms)
        self.max_rows_per_symbol = int(max_rows_per_symbol)
        self.watermarks: dict[str, int | None] = {}
        self.disabled_reason: str | None = None
        self.stats = SinkStats()
        self._time_basis_checked = False

    # ------------------------------------------------------------------
    def run(self) -> dict:
        """One persistence pass. Never raises."""
        started = self.monotonic()
        now = None
        try:
            now = int(self.clock())
            result = self._run(now)
        except sqlite3.OperationalError as exc:
            locked = "locked" in str(exc) or "busy" in str(exc)
            if locked:
                self.stats.lock_skips += 1
            else:
                self.stats.failures += 1
            logger.warning("bar persistence %s: %s", "skipped (database locked)" if locked else "failed", exc)
            result = {"status": SKIPPED if locked else FAILED, "error": f"{type(exc).__name__}: {exc}"}
        except Exception as exc:
            self.stats.failures += 1
            logger.warning("bar persistence failed: %s", exc, exc_info=exc)
            result = {"status": FAILED, "error": f"{type(exc).__name__}: {exc}"}
        status = result["status"]
        if status in (OK, IDLE):
            if self.stats.consecutive_unsuccessful and self.stats.last_error is not None:
                self.stats.recovered_at = now
            self.stats.consecutive_unsuccessful = 0
            self.stats.active_error = None
            self.stats.last_success_at = now
        elif "error" in result:
            self.stats.consecutive_unsuccessful += 1
            self.stats.active_error = result["error"]
            self.stats.last_error = result["error"]
            self.stats.last_error_at = now
            self.stats.recovered_at = None
        elif status == DISABLED:
            self.stats.active_error = result.get("reason")
        elapsed_ms = (self.monotonic() - started) * 1000.0
        self.stats.runs += 1
        self.stats.last_status = status
        self.stats.last_duration_ms = elapsed_ms
        self.stats.max_duration_ms = max(self.stats.max_duration_ms, elapsed_ms)
        result["duration_ms"] = round(elapsed_ms, 3)
        return result

    def health_state(self, stats: SinkStats | None = None) -> str:
        """OK / RECOVERED / LOCK_CONTENDED / ERROR / DISABLED (see module doc)."""
        stats = stats if stats is not None else self.stats
        if self.disabled_reason is not None:
            return HEALTH_DISABLED
        if stats.active_error is not None:
            if stats.last_status == SKIPPED and stats.consecutive_unsuccessful < LOCK_SKIPS_DEGRADED:
                return HEALTH_LOCK_CONTENDED
            return HEALTH_ERROR
        if stats.recovered_at is not None:
            return HEALTH_RECOVERED
        return HEALTH_OK

    def snapshot(self, stats: SinkStats | None = None, watermarks: Mapping[str, int | None] | None = None) -> dict:
        stats = stats if stats is not None else self.stats
        return {"resolution": self.resolution, "disabled_reason": self.disabled_reason,
                "watermarks": dict(self.watermarks if watermarks is None else watermarks),
                "health": self.health_state(stats), **asdict(stats)}

    # ------------------------------------------------------------------
    def _run(self, now: int) -> dict:
        if self.disabled_reason is not None:
            return {"status": DISABLED, "reason": self.disabled_reason}
        if not self._time_basis_checked:
            try:
                require_utc(self.conn)
            except TimeBasisError as exc:
                self.disabled_reason = f"database time basis is not UTC: {exc}"
                return {"status": DISABLED, "reason": self.disabled_reason}
            self._time_basis_checked = True
        if self.conn.in_transaction:   # never nest inside someone else's transaction
            return {"status": SKIPPED, "reason": "connection already in a transaction"}

        cached = {c: list(bars) for c, bars in (self.source() or {}).items() if bars}
        for canonical in cached:
            if canonical not in self.watermarks:
                # index seek on UNIQUE(canonical_symbol, resolution, ts_utc): no scan
                row = self.conn.execute("SELECT MAX(ts_utc) FROM bars WHERE canonical_symbol = ? AND resolution = ?",
                                        (canonical, self.resolution)).fetchone()
                self.watermarks[canonical] = row[0]
        plan = self._plan(cached, now)
        if not plan.rows and not plan.events:
            self.watermarks.update(plan.marks)
            return {"status": IDLE, "rows": 0}

        previous_timeout = self.conn.execute("PRAGMA busy_timeout").fetchone()[0]
        self.conn.execute(f"PRAGMA busy_timeout = {self.busy_timeout_ms}")
        try:
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                written, pending = 0, []
                for canonical, bars in plan.rows.items():
                    inserted = insert_bars(self.conn, canonical, self.resolution, bars)
                    written += inserted
                    if not self._advance_coverage(canonical, bars, inserted):
                        pending.append(canonical)
                for severity, event, detail, canonical, dedup in plan.events:
                    record_event(self.conn, severity, "bar_persistence", event, detail,
                                 canonical_symbol=canonical, dedup_key=dedup, now_utc=now)
                pages = self.conn.execute("PRAGMA page_count").fetchone()[0]
                size = self.conn.execute("PRAGMA page_size").fetchone()[0]
                # Prospective progress: published inside the transaction, applied in memory after COMMIT.
                after = replace(
                    self.stats, transactions=self.stats.transactions + 1,
                    rows_written=self.stats.rows_written + written, last_rows=written,
                    rejected=self.stats.rejected + plan.rejected, dst_skipped=self.stats.dst_skipped + plan.dst_skipped,
                    gaps_reported=self.stats.gaps_reported + plan.gaps, db_bytes=int(pages) * int(size),
                    coverage_pending=sorted(set(self.stats.coverage_pending) - set(plan.rows) | set(pending)),
                    last_status=OK, active_error=None, consecutive_unsuccessful=0, last_success_at=now,
                    recovered_at=(now if self.stats.consecutive_unsuccessful and self.stats.last_error
                                  else self.stats.recovered_at),
                )
                put_state(self.conn, STATE_KEY, self.snapshot(after, {**self.watermarks, **plan.marks}), now_utc=now)
                self.conn.execute("COMMIT")
            except BaseException:
                if self.conn.in_transaction:
                    self.conn.execute("ROLLBACK")
                raise
        finally:
            self.conn.execute(f"PRAGMA busy_timeout = {int(previous_timeout)}")
        # Only after a successful commit (run() sets the status/health fields).
        for name in ("transactions", "rows_written", "last_rows", "rejected", "dst_skipped", "gaps_reported",
                     "db_bytes", "coverage_pending"):
            setattr(self.stats, name, getattr(after, name))
        self.watermarks.update(plan.marks)
        return {"status": OK, "rows": written, "events": len(plan.events)}

    def _advance_coverage(self, canonical: str, bars: list[Bar], inserted: int) -> bool:
        """O(1) coverage update for bars newer than the stored maximum. With no
        coverage row: seeded exactly when the symbol had no stored bars before
        this run (its watermark is still None until COMMIT); otherwise False --
        the full recount is left to `history bootstrap`, never this scheduler."""
        row = self.conn.execute(
            "SELECT earliest_utc, latest_utc, bar_count FROM historical_bar_coverage "
            "WHERE canonical_symbol = ? AND resolution = ?", (canonical, self.resolution)).fetchone()
        if row is None:
            if self.watermarks.get(canonical) is not None:
                return False
            self.conn.execute(
                "INSERT INTO historical_bar_coverage (canonical_symbol, resolution, earliest_utc, latest_utc, "
                "bar_count, expected_bar_count, gap_count, last_sync_at) VALUES (?, ?, NULL, NULL, 0, NULL, 0, NULL)",
                (canonical, self.resolution))
            row = (None, None, 0)
        earliest, latest, count = row[0], row[1], int(row[2] or 0)
        first, last = bars[0].time, bars[-1].time
        earliest = first if earliest is None else min(earliest, first)
        latest = last if latest is None else max(latest, last)
        count += inserted
        expected = (latest - earliest) // self.step + 1
        self.conn.execute(
            "UPDATE historical_bar_coverage SET earliest_utc = ?, latest_utc = ?, bar_count = ?, "
            "expected_bar_count = ?, gap_count = ?, last_sync_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') "
            "WHERE canonical_symbol = ? AND resolution = ?",
            (earliest, latest, count, expected, max(expected - count, 0), canonical, self.resolution))
        return True

    def _plan(self, cached: dict[str, list[Bar]], now: int) -> _Plan:
        plan = _Plan(rows={}, marks={}, events=[])
        for canonical, raw in cached.items():
            bars = [b for _, b in sorted({b.time: b for b in raw}.items())]
            mark = self.watermarks.get(canonical)
            fresh = [b for b in bars if mark is None or b.time > mark]
            if not fresh:
                continue
            if mark is not None and bars[0].time > mark + self.step:
                plan.gaps += 1
                plan.events.append((
                    "WARNING", "BAR_GAP",
                    (f"{self.resolution} bars after {mark} up to {bars[0].time - self.step} are not in the runtime "
                     f"cache (runtime was down longer than it covers, or the market was closed); fill with "
                     f"`history bootstrap` while the runtime is stopped"),
                    canonical, f"bar_gap:{canonical}:{self.resolution}:{mark}"))
            accepted, new_mark = [], mark
            for bar in fresh:
                if len(accepted) >= self.max_rows_per_symbol:
                    break       # bounded write: the rest stays in the cache for the next run
                problem = bar_problem(bar, self.step, now)
                if problem == NOT_CLOSED:
                    plan.events.append((
                        "WARNING", "BAR_NOT_PERSISTED",
                        f"{self.resolution} bar {bar.time} is not closed at {now}: stopping this symbol's run",
                        canonical, f"bar_not_closed:{canonical}"))
                    break
                if problem is not None:
                    plan.rejected += 1
                    plan.events.append(("WARNING", "BAR_REJECTED", f"{self.resolution} bar {bar.time}: {problem}",
                                        canonical, f"bar_rejected:{canonical}:{bar.time}"))
                elif near_dst_change(self.rule, bar.time, self.step):
                    plan.dst_skipped += 1
                    plan.events.append(("INFO", "BAR_DST_WINDOW_SKIPPED",
                                        (f"{self.resolution} bar {bar.time} lies within one hour of a server-clock "
                                         f"DST change ({self.rule}); not stored"),
                                        canonical, f"bar_dst:{canonical}:{bar.time // 86400}"))
                else:
                    accepted.append(bar)
                new_mark = bar.time
            if accepted:
                plan.rows[canonical] = accepted
            if new_mark is not None and new_mark != mark:
                plan.marks[canonical] = new_mark
        return plan
