"""Provider fallback orchestration (directive sections 38, 44).

Tries every configured live provider (so PRIMARY and SECONDARY can be
cross-checked against each other for conflict — directive section 44 —
not merely "stop at the first success"); persists every successful
fetch to the cache; falls back to the cache only when every live
provider fails; and reports `ProviderHealth.UNAVAILABLE` (never a
silent empty "no news") when even the cache can't help.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass, field

from adaptive_scalper.news.provider import EconomicCalendarProvider
from adaptive_scalper.news.providers.cache import CacheProvider, persist_events, record_provider_attempt
from adaptive_scalper.news.types import EconomicEvent, Impact, ProviderError, ProviderHealth

DEFAULT_CONFLICT_THRESHOLD_SECONDS = 300


@dataclass(frozen=True)
class CalendarFetchResult:
    events: list[EconomicEvent]
    health: ProviderHealth
    successful_provider: str | None
    errors: dict[str, str] = field(default_factory=dict)


def _match_key(event: EconomicEvent) -> tuple:
    if event.normalized_event_type:
        return (event.currency, event.normalized_event_type)
    return (event.currency, event.title.strip().lower())


def _detect_conflict(
    events_a: list[EconomicEvent], events_b: list[EconomicEvent],
    threshold_seconds: int = DEFAULT_CONFLICT_THRESHOLD_SECONDS,
) -> bool:
    """True if the two providers materially disagree on the SCHEDULED
    TIME of what looks like the same HIGH-impact event (directive
    section 44). Only HIGH-impact events are compared — that's the only
    disagreement this system needs to react to."""
    index_b: dict[tuple, list[EconomicEvent]] = {}
    for e in events_b:
        if e.impact == Impact.HIGH:
            index_b.setdefault(_match_key(e), []).append(e)

    for e in events_a:
        if e.impact != Impact.HIGH:
            continue
        for match in index_b.get(_match_key(e), []):
            if abs(match.scheduled_at_utc - e.scheduled_at_utc) > threshold_seconds:
                return True
    return False


def fetch_live(
    live_providers: list[EconomicCalendarProvider], now_utc: int,
) -> list[tuple[str, list[EconomicEvent] | ProviderError]]:
    """The network phase only: every live provider's events or its
    ProviderError, in provider order. Touches no database, so the runtime
    can run it off the scheduler thread (runtime/news_monitor.py)."""
    fetched: list[tuple[str, list[EconomicEvent] | ProviderError]] = []
    for provider in live_providers:
        try:
            fetched.append((provider.name, provider.fetch(now_utc)))
        except ProviderError as exc:
            fetched.append((provider.name, exc))
    return fetched


def resolve_fetched(
    conn: sqlite3.Connection,
    fetched: list[tuple[str, list[EconomicEvent] | ProviderError]],
    cache_provider: CacheProvider,
    now_utc: int,
) -> CalendarFetchResult:
    """The database phase: record attempts, persist successful fetches,
    cross-check for conflicts, fall back to the cache."""
    results: dict[str, list[EconomicEvent]] = {}
    errors: dict[str, str] = {}

    for name, outcome in fetched:
        if isinstance(outcome, ProviderError):
            errors[name] = str(outcome)
            record_provider_attempt(conn, name, success=False, error=str(outcome), now_utc=now_utc)
            continue
        record_provider_attempt(conn, name, success=True, now_utc=now_utc)
        results[name] = outcome
        persist_events(conn, outcome)

    names = list(results.keys())
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            if _detect_conflict(results[names[i]], results[names[j]]):
                return CalendarFetchResult(results[names[i]], ProviderHealth.CONFLICT, names[i], errors)

    for name, _ in fetched:
        if name in results:
            return CalendarFetchResult(results[name], ProviderHealth.HEALTHY, name, errors)

    try:
        cached = cache_provider.fetch(now_utc)
    except ProviderError as exc:
        errors[cache_provider.name] = str(exc)
        return CalendarFetchResult([], ProviderHealth.UNAVAILABLE, None, errors)

    return CalendarFetchResult(cached, ProviderHealth.DEGRADED, cache_provider.name, errors)


def fetch_with_fallback(
    conn: sqlite3.Connection,
    live_providers: list[EconomicCalendarProvider],
    cache_provider: CacheProvider,
    now_utc: int | None = None,
) -> CalendarFetchResult:
    now = now_utc if now_utc is not None else int(time.time())
    return resolve_fetched(conn, fetch_live(live_providers, now), cache_provider, now)
