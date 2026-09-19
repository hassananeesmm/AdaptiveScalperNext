"""TERTIARY last-known-good local cache provider (directive section 38).

Not a live network provider — `CacheProvider.fetch()` reads whatever was
last successfully persisted by `persist_events()`, which
`calendar_service.py` calls after every successful live-provider fetch.
This is what lets the system fall back to "the last thing we definitely
knew" when every live provider is down, rather than treating that as "no
news" (directive section 38's core requirement).

Known limitation (not yet addressed): `news_events` has no pruning of
old rows, so it grows unboundedly over the system's lifetime. Low
priority — event rows are tiny and the table is queried by an indexed
`scheduled_at_utc` range, so this doesn't affect correctness — but
flagged honestly rather than silently left implicit.
"""

from __future__ import annotations

import sqlite3
import time

from adaptive_scalper.news.types import EconomicEvent, Impact, ProviderError

DEFAULT_MAX_AGE_SECONDS = 24 * 3600  # directive section 43: "stale beyond a safe limit" -> blocked


def persist_events(conn: sqlite3.Connection, events: list[EconomicEvent]) -> None:
    """Upsert events into the cache. Re-fetching an already-cached event
    (same event_id + provider) updates its mutable fields and increments
    `revision`, rather than creating a duplicate row."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        for e in events:
            conn.execute(
                """
                INSERT INTO news_events
                    (event_id, provider, provider_event_id, scheduled_at_utc, country, currency,
                     title, normalized_event_type, impact, actual, forecast, previous,
                     retrieved_at_utc, source_reliability, revision, source_identifier)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(event_id, provider) DO UPDATE SET
                    scheduled_at_utc = excluded.scheduled_at_utc,
                    actual = excluded.actual,
                    forecast = excluded.forecast,
                    previous = excluded.previous,
                    retrieved_at_utc = excluded.retrieved_at_utc,
                    revision = news_events.revision + 1
                """,
                (
                    e.event_id, e.provider, e.provider_event_id, e.scheduled_at_utc, e.country, e.currency,
                    e.title, e.normalized_event_type, e.impact.value, e.actual, e.forecast, e.previous,
                    e.retrieved_at_utc, e.source_reliability, e.revision, e.source_identifier,
                ),
            )
        conn.execute("COMMIT")
    except sqlite3.Error:
        conn.execute("ROLLBACK")
        raise


def record_provider_attempt(
    conn: sqlite3.Connection,
    provider: str,
    *,
    success: bool,
    error: str | None = None,
    now_utc: int | None = None,
) -> None:
    now = now_utc if now_utc is not None else int(time.time())
    row = conn.execute(
        "SELECT last_success_at_utc FROM news_provider_state WHERE provider = ?", (provider,)
    ).fetchone()
    last_success = row["last_success_at_utc"] if row else None
    if success:
        last_success = now
    health = "HEALTHY" if success else "UNAVAILABLE"
    conn.execute(
        """
        INSERT INTO news_provider_state (provider, last_success_at_utc, last_attempt_at_utc, last_error, health)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(provider) DO UPDATE SET
            last_success_at_utc = excluded.last_success_at_utc,
            last_attempt_at_utc = excluded.last_attempt_at_utc,
            last_error = excluded.last_error,
            health = excluded.health
        """,
        (provider, last_success, now, error, health),
    )


def load_cached_events(conn: sqlite3.Connection, since_utc: int) -> list[EconomicEvent]:
    rows = conn.execute(
        "SELECT * FROM news_events WHERE scheduled_at_utc >= ? ORDER BY scheduled_at_utc",
        (since_utc,),
    ).fetchall()
    return [
        EconomicEvent(
            event_id=r["event_id"], provider=r["provider"], provider_event_id=r["provider_event_id"],
            scheduled_at_utc=r["scheduled_at_utc"], country=r["country"], currency=r["currency"],
            title=r["title"], normalized_event_type=r["normalized_event_type"], impact=Impact(r["impact"]),
            actual=r["actual"], forecast=r["forecast"], previous=r["previous"],
            retrieved_at_utc=r["retrieved_at_utc"], source_reliability=r["source_reliability"],
            revision=r["revision"], source_identifier=r["source_identifier"],
        )
        for r in rows
    ]


class CacheProvider:
    name = "cache"

    def __init__(self, conn: sqlite3.Connection, max_age_seconds: float = DEFAULT_MAX_AGE_SECONDS) -> None:
        self._conn = conn
        self.max_age_seconds = max_age_seconds

    def fetch(self, now_utc: int) -> list[EconomicEvent]:
        row = self._conn.execute("SELECT MAX(retrieved_at_utc) AS latest FROM news_events").fetchone()
        latest = row["latest"] if row else None
        if latest is None:
            raise ProviderError("cache: no cached events available (never successfully fetched)")
        age = now_utc - latest
        if age > self.max_age_seconds:
            raise ProviderError(
                f"cache: stale — newest cached data is {age}s old, exceeds max {self.max_age_seconds}s"
            )
        return load_cached_events(self._conn, since_utc=now_utc - 3600)
