"""Runtime news state (directive sections 38-45).

Remote providers are refreshed on the SLOW cadence only (never every
entry cycle); every decision then runs a purely LOCAL check against the
cached result immediately before use ("final news check"). Health is
fail-closed:

- never refreshed successfully                  -> UNAVAILABLE
- last success older than `stale_after_seconds` -> STALE
- providers disagree on a HIGH event's timing   -> CONFLICT
- only the local cache answered                 -> DEGRADED (still usable)

UNAVAILABLE/STALE/CONFLICT block NEW entries on every symbol; they never
engage the persistent kill switch and never stop position management.
"""

from __future__ import annotations

import sqlite3
import time
from typing import Callable

from adaptive_scalper.news.blocking import (
    ALLOW,
    BLOCK_NEWS_CALENDAR_UNAVAILABLE,
    BLOCK_NEWS_PROVIDER_CONFLICT,
    NewsBlockResult,
    _event_applies_to_symbol,
    _event_window,
    evaluate_news_block,
)
from adaptive_scalper.news.calendar_service import fetch_with_fallback
from adaptive_scalper.news.provider import EconomicCalendarProvider
from adaptive_scalper.news.types import EconomicEvent, ProviderHealth


class NewsMonitor:
    def __init__(
        self, conn: sqlite3.Connection, live_providers: list[EconomicCalendarProvider], cache_provider, *,
        pre_minutes: int, post_minutes: int, refresh_seconds: int, stale_after_seconds: int,
    ) -> None:
        self._conn = conn
        self._live = live_providers
        self._cache = cache_provider
        self.pre_minutes = pre_minutes
        self.post_minutes = post_minutes
        self.refresh_seconds = refresh_seconds
        self.stale_after_seconds = stale_after_seconds
        self.events: list[EconomicEvent] = []
        self._fetched_health: ProviderHealth | None = None
        self.last_attempt_utc: int | None = None
        self.last_success_utc: int | None = None
        self.successful_provider: str | None = None
        self.errors: dict[str, str] = {}

    def refresh(self, now_utc: int) -> ProviderHealth:
        self.last_attempt_utc = now_utc
        try:
            result = fetch_with_fallback(self._conn, self._live, self._cache, now_utc=now_utc)
        except Exception as exc:  # a broken provider must degrade news, never crash the runtime
            self.errors = {"calendar_service": f"{type(exc).__name__}: {exc}"}
            return self.health(now_utc)
        self.errors = dict(result.errors)
        if result.health != ProviderHealth.UNAVAILABLE:
            self.events = list(result.events)
            self._fetched_health = result.health
            self.successful_provider = result.successful_provider
            self.last_success_utc = now_utc
        return self.health(now_utc)

    def refresh_if_due(self, now_utc: int) -> bool:
        if self.last_attempt_utc is None or now_utc - self.last_attempt_utc >= self.refresh_seconds:
            self.refresh(now_utc)
            return True
        return False

    def health(self, now_utc: int) -> ProviderHealth:
        if self.last_success_utc is None or self._fetched_health is None:
            return ProviderHealth.UNAVAILABLE
        if now_utc - self.last_success_utc > self.stale_after_seconds:
            return ProviderHealth.STALE
        return self._fetched_health

    def global_block(self, now_utc: int) -> tuple[str, str] | None:
        """(block_code, reason) when the calendar itself blocks every symbol."""
        health = self.health(now_utc)
        if health == ProviderHealth.CONFLICT:
            return BLOCK_NEWS_PROVIDER_CONFLICT, "news providers disagree on a HIGH-impact event's timing"
        if health in (ProviderHealth.STALE, ProviderHealth.UNAVAILABLE):
            return BLOCK_NEWS_CALENDAR_UNAVAILABLE, f"news calendar {health.value} -- new entries blocked"
        return None

    def block_for(self, canonical_symbol: str, now_utc: int) -> NewsBlockResult:
        return evaluate_news_block(
            canonical_symbol, now_utc, self.events, self.health(now_utc),
            pre_high_impact_minutes=self.pre_minutes, post_high_impact_minutes=self.post_minutes,
        )

    def windows_for(self, canonical_symbol: str) -> tuple[tuple[int, int], ...]:
        """Block windows of every applicable HIGH event -- the point-in-time
        news input PAPER feeds to the simulation engine."""
        return tuple(sorted(
            _event_window(e, self.pre_minutes, self.post_minutes)
            for e in self.events if _event_applies_to_symbol(e, canonical_symbol)
        ))

    def upcoming(self, now_utc: int, horizon_seconds: int = 86400) -> list[EconomicEvent]:
        return sorted(
            (e for e in self.events if now_utc <= e.scheduled_at_utc <= now_utc + horizon_seconds),
            key=lambda e: e.scheduled_at_utc,
        )

    def snapshot(self, now_utc: int) -> dict:
        return {
            "health": self.health(now_utc).value, "successful_provider": self.successful_provider,
            "last_attempt_utc": self.last_attempt_utc, "last_success_utc": self.last_success_utc,
            "cache_age_seconds": (now_utc - self.last_success_utc) if self.last_success_utc else None,
            "errors": self.errors, "event_count": len(self.events),
            "upcoming_high_impact": [
                {"title": e.title, "currency": e.currency, "scheduled_at_utc": e.scheduled_at_utc,
                 "impact": e.impact.value}
                for e in self.upcoming(now_utc) if e.impact.value == "HIGH"
            ][:20],
        }


def default_live_providers() -> list[EconomicCalendarProvider]:
    from adaptive_scalper.news.providers.financecalendar import FinanceCalendarProvider
    from adaptive_scalper.news.providers.forexfactory import ForexFactoryProvider

    return [FinanceCalendarProvider(), ForexFactoryProvider()]


__all__ = ["NewsMonitor", "default_live_providers", "ALLOW"]
