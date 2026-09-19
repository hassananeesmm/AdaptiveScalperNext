"""Tests for the provider fallback chain (directive sections 38, 44)."""

from __future__ import annotations

import pytest

from adaptive_scalper.news.calendar_service import fetch_with_fallback
from adaptive_scalper.news.providers.cache import CacheProvider, persist_events
from adaptive_scalper.news.types import EconomicEvent, Impact, ProviderError, ProviderHealth
from adaptive_scalper.persistence import connect, migrate

BASE_TIME = 1_700_000_000


@pytest.fixture()
def db(tmp_path):
    conn = connect(tmp_path / "test.sqlite3")
    migrate(conn)
    yield conn
    conn.close()


def _event(**overrides) -> EconomicEvent:
    defaults = dict(
        event_id="evt-1", provider="test", provider_event_id=None, scheduled_at_utc=BASE_TIME,
        country="USD", currency="USD", title="CPI m/m", normalized_event_type="US_CPI",
        impact=Impact.HIGH, actual=None, forecast=None, previous=None, retrieved_at_utc=BASE_TIME,
        source_reliability="TEST", revision=0, source_identifier="test",
    )
    defaults.update(overrides)
    return EconomicEvent(**defaults)


class _StubProvider:
    def __init__(self, name: str, events=None, error: str | None = None):
        self.name = name
        self._events = events or []
        self._error = error
        self.call_count = 0

    def fetch(self, now_utc):
        self.call_count += 1
        if self._error:
            raise ProviderError(self._error)
        return self._events


def test_first_successful_provider_wins_when_no_conflict(db):
    primary = _StubProvider("primary", error="primary is down")
    secondary = _StubProvider("secondary", events=[_event()])
    result = fetch_with_fallback(db, [primary, secondary], CacheProvider(db), now_utc=BASE_TIME)
    assert result.health == ProviderHealth.HEALTHY
    assert result.successful_provider == "secondary"
    assert len(result.events) == 1
    assert "primary" in result.errors


def test_falls_back_to_cache_when_all_live_providers_fail(db):
    persist_events(db, [_event(retrieved_at_utc=BASE_TIME)])
    primary = _StubProvider("primary", error="down")
    secondary = _StubProvider("secondary", error="also down")
    result = fetch_with_fallback(db, [primary, secondary], CacheProvider(db, max_age_seconds=3600), now_utc=BASE_TIME + 100)
    assert result.health == ProviderHealth.DEGRADED
    assert result.successful_provider == "cache"
    assert len(result.events) == 1


def test_unavailable_when_all_providers_including_cache_fail(db):
    primary = _StubProvider("primary", error="down")
    secondary = _StubProvider("secondary", error="also down")
    result = fetch_with_fallback(db, [primary, secondary], CacheProvider(db), now_utc=BASE_TIME)
    assert result.health == ProviderHealth.UNAVAILABLE
    assert result.successful_provider is None
    assert result.events == []
    assert "primary" in result.errors and "secondary" in result.errors and "cache" in result.errors


def test_conflict_detected_between_two_successful_providers(db):
    primary = _StubProvider("primary", events=[_event(scheduled_at_utc=BASE_TIME)])
    secondary = _StubProvider("secondary", events=[_event(scheduled_at_utc=BASE_TIME + 3600)])
    result = fetch_with_fallback(db, [primary, secondary], CacheProvider(db), now_utc=BASE_TIME)
    assert result.health == ProviderHealth.CONFLICT


def test_no_conflict_when_providers_agree_within_threshold(db):
    primary = _StubProvider("primary", events=[_event(scheduled_at_utc=BASE_TIME)])
    secondary = _StubProvider("secondary", events=[_event(scheduled_at_utc=BASE_TIME + 60)])
    result = fetch_with_fallback(db, [primary, secondary], CacheProvider(db), now_utc=BASE_TIME)
    assert result.health == ProviderHealth.HEALTHY


def test_no_conflict_when_events_are_for_different_currencies(db):
    primary = _StubProvider("primary", events=[_event(currency="USD", normalized_event_type="US_CPI", scheduled_at_utc=BASE_TIME)])
    secondary = _StubProvider("secondary", events=[_event(currency="GBP", normalized_event_type=None, title="BoE Rate Decision", scheduled_at_utc=BASE_TIME + 7200)])
    result = fetch_with_fallback(db, [primary, secondary], CacheProvider(db), now_utc=BASE_TIME)
    assert result.health == ProviderHealth.HEALTHY


def test_successful_fetch_is_persisted_to_cache(db):
    provider = _StubProvider("secondary", events=[_event(event_id="e-persist")])
    fetch_with_fallback(db, [_StubProvider("primary", error="down"), provider], CacheProvider(db), now_utc=BASE_TIME)
    row = db.execute("SELECT * FROM news_events WHERE event_id = 'e-persist'").fetchone()
    assert row is not None


def test_provider_state_recorded_for_every_attempt(db):
    fetch_with_fallback(
        db,
        [_StubProvider("primary", error="down"), _StubProvider("secondary", events=[_event()])],
        CacheProvider(db), now_utc=BASE_TIME,
    )
    primary_state = db.execute("SELECT health FROM news_provider_state WHERE provider='primary'").fetchone()
    secondary_state = db.execute("SELECT health FROM news_provider_state WHERE provider='secondary'").fetchone()
    assert primary_state["health"] == "UNAVAILABLE"
    assert secondary_state["health"] == "HEALTHY"
