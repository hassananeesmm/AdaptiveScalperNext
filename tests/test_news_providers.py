"""Tests for the concrete news providers: ForexFactory (mocked HTTP),
FinanceCalendar (always-fails stub), manual JSON, and the SQLite cache.
"""

from __future__ import annotations

import json

import pytest

from adaptive_scalper.news.providers.cache import CacheProvider, persist_events, record_provider_attempt
from adaptive_scalper.news.providers.financecalendar import FinanceCalendarProvider
from adaptive_scalper.news.providers.forexfactory import ForexFactoryProvider
from adaptive_scalper.news.providers.manual import ManualJSONProvider
from adaptive_scalper.news.types import EconomicEvent, Impact, ProviderError
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
        country="USD", currency="USD", title="Test Event", normalized_event_type=None,
        impact=Impact.HIGH, actual=None, forecast=None, previous=None, retrieved_at_utc=BASE_TIME,
        source_reliability="TEST", revision=0, source_identifier="test",
    )
    defaults.update(overrides)
    return EconomicEvent(**defaults)


# --------------------------------------------------------------------------
# ForexFactoryProvider (HTTP mocked — no real network call in tests)
# --------------------------------------------------------------------------

class _FakeResponse:
    def __init__(self, status_code: int, json_body=None, text: str = ""):
        self.status_code = status_code
        self._json_body = json_body
        self.text = text if text else (json.dumps(json_body) if json_body is not None else "")

    def json(self):
        if self._json_body is None:
            raise ValueError("no JSON body")
        return self._json_body


def test_forexfactory_parses_a_real_shaped_response(monkeypatch):
    raw = [
        {"title": "CPI m/m", "country": "USD", "date": "2026-09-14T08:30:00-04:00",
         "impact": "High", "forecast": "0.2%", "previous": "0.1%"},
        {"title": "BusinessNZ Services Index", "country": "NZD", "date": "2026-09-13T18:30:00-04:00",
         "impact": "Low", "forecast": "", "previous": "50.6"},
    ]
    monkeypatch.setattr(
        "adaptive_scalper.news.providers.forexfactory.httpx.get",
        lambda url, timeout: _FakeResponse(200, raw),
    )
    provider = ForexFactoryProvider()
    events = provider.fetch(BASE_TIME)
    assert len(events) == 2
    assert events[0].title == "CPI m/m"
    assert events[0].currency == "USD"
    assert events[0].impact == Impact.HIGH
    assert events[0].normalized_event_type == "US_CPI"
    assert events[0].source_reliability == "SECONDARY"
    assert events[1].currency == "NZD"


def test_forexfactory_normalizes_all_country_to_none_currency(monkeypatch):
    raw = [{"title": "BRICS Summit", "country": "All", "date": "2026-09-13T04:15:00-04:00",
            "impact": "Low", "forecast": "", "previous": ""}]
    monkeypatch.setattr(
        "adaptive_scalper.news.providers.forexfactory.httpx.get",
        lambda url, timeout: _FakeResponse(200, raw),
    )
    events = ForexFactoryProvider().fetch(BASE_TIME)
    assert events[0].currency is None


def test_forexfactory_raises_on_non_200_status(monkeypatch):
    monkeypatch.setattr(
        "adaptive_scalper.news.providers.forexfactory.httpx.get",
        lambda url, timeout: _FakeResponse(429, text="Rate Limited"),
    )
    with pytest.raises(ProviderError, match="429"):
        ForexFactoryProvider().fetch(BASE_TIME)


def test_forexfactory_raises_on_invalid_json(monkeypatch):
    resp = _FakeResponse(200, json_body=None, text="<html>not json</html>")
    monkeypatch.setattr("adaptive_scalper.news.providers.forexfactory.httpx.get", lambda url, timeout: resp)
    with pytest.raises(ProviderError):
        ForexFactoryProvider().fetch(BASE_TIME)


def test_forexfactory_raises_on_non_list_json(monkeypatch):
    monkeypatch.setattr(
        "adaptive_scalper.news.providers.forexfactory.httpx.get",
        lambda url, timeout: _FakeResponse(200, {"not": "a list"}),
    )
    with pytest.raises(ProviderError):
        ForexFactoryProvider().fetch(BASE_TIME)


def test_forexfactory_raises_on_malformed_event(monkeypatch):
    raw = [{"title": "Missing date field", "country": "USD", "impact": "High"}]
    monkeypatch.setattr(
        "adaptive_scalper.news.providers.forexfactory.httpx.get",
        lambda url, timeout: _FakeResponse(200, raw),
    )
    with pytest.raises(ProviderError):
        ForexFactoryProvider().fetch(BASE_TIME)


def test_forexfactory_never_raises_on_network_error(monkeypatch):
    import httpx as httpx_module

    def _raise(*args, **kwargs):
        raise httpx_module.ConnectError("connection refused")

    monkeypatch.setattr("adaptive_scalper.news.providers.forexfactory.httpx.get", _raise)
    with pytest.raises(ProviderError):
        ForexFactoryProvider().fetch(BASE_TIME)


# --------------------------------------------------------------------------
# FinanceCalendarProvider — documented stub, always fails, no network call
# --------------------------------------------------------------------------

def test_financecalendar_always_raises_provider_error():
    with pytest.raises(ProviderError):
        FinanceCalendarProvider().fetch(BASE_TIME)


def test_financecalendar_makes_no_network_call(monkeypatch):
    def _fail_if_called(*args, **kwargs):
        raise AssertionError("FinanceCalendarProvider must never make a network call")

    monkeypatch.setattr("httpx.get", _fail_if_called)
    with pytest.raises(ProviderError):
        FinanceCalendarProvider().fetch(BASE_TIME)


# --------------------------------------------------------------------------
# ManualJSONProvider
# --------------------------------------------------------------------------

def test_manual_provider_parses_a_valid_file(tmp_path):
    path = tmp_path / "manual_events.json"
    path.write_text(json.dumps([
        {"event_id": "e1", "scheduled_at_utc": BASE_TIME, "country": "USD", "currency": "USD",
         "title": "FOMC Statement", "impact": "high"},
    ]), encoding="utf-8")
    events = ManualJSONProvider(path).fetch(BASE_TIME)
    assert len(events) == 1
    assert events[0].source_reliability == "MANUAL"
    assert events[0].normalized_event_type == "FOMC"


def test_manual_provider_raises_if_file_missing(tmp_path):
    with pytest.raises(ProviderError):
        ManualJSONProvider(tmp_path / "does_not_exist.json").fetch(BASE_TIME)


def test_manual_provider_raises_on_invalid_json(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("not json{{{", encoding="utf-8")
    with pytest.raises(ProviderError):
        ManualJSONProvider(path).fetch(BASE_TIME)


def test_manual_provider_raises_on_missing_required_field(tmp_path):
    path = tmp_path / "missing_field.json"
    path.write_text(json.dumps([{"event_id": "e1", "scheduled_at_utc": BASE_TIME}]), encoding="utf-8")
    with pytest.raises(ProviderError):
        ManualJSONProvider(path).fetch(BASE_TIME)


# --------------------------------------------------------------------------
# CacheProvider
# --------------------------------------------------------------------------

def test_cache_provider_raises_when_never_populated(db):
    with pytest.raises(ProviderError):
        CacheProvider(db).fetch(BASE_TIME)


def test_cache_provider_returns_persisted_events(db):
    persist_events(db, [_event(scheduled_at_utc=BASE_TIME + 100)])
    events = CacheProvider(db, max_age_seconds=3600).fetch(BASE_TIME + 200)
    assert len(events) == 1


def test_cache_provider_raises_when_stale(db):
    persist_events(db, [_event(retrieved_at_utc=BASE_TIME)])
    with pytest.raises(ProviderError, match="stale"):
        CacheProvider(db, max_age_seconds=100).fetch(BASE_TIME + 1000)


def test_persist_events_upserts_and_increments_revision(db):
    persist_events(db, [_event(event_id="e1", provider="p", forecast="1.0")])
    persist_events(db, [_event(event_id="e1", provider="p", forecast="2.0")])
    rows = db.execute("SELECT forecast, revision FROM news_events WHERE event_id='e1'").fetchall()
    assert len(rows) == 1  # upsert, not duplicate
    assert rows[0]["forecast"] == "2.0"
    assert rows[0]["revision"] == 1


def test_record_provider_attempt_tracks_health(db):
    record_provider_attempt(db, "forexfactory", success=True, now_utc=BASE_TIME)
    row = db.execute("SELECT health, last_success_at_utc FROM news_provider_state WHERE provider='forexfactory'").fetchone()
    assert row["health"] == "HEALTHY"
    assert row["last_success_at_utc"] == BASE_TIME

    record_provider_attempt(db, "forexfactory", success=False, error="boom", now_utc=BASE_TIME + 100)
    row2 = db.execute("SELECT health, last_success_at_utc, last_error FROM news_provider_state WHERE provider='forexfactory'").fetchone()
    assert row2["health"] == "UNAVAILABLE"
    assert row2["last_success_at_utc"] == BASE_TIME  # preserved from the earlier success
    assert row2["last_error"] == "boom"
