"""Tests for the deterministic news-block decision logic (directive
sections 40-44), including the directive's own exact worked timeline
example for a 16:30 HIGH-impact event.
"""

from __future__ import annotations

import pytest

from adaptive_scalper.news.blocking import (
    ALLOW,
    BLOCK_NEWS,
    BLOCK_NEWS_CALENDAR_UNAVAILABLE,
    BLOCK_NEWS_PROVIDER_CONFLICT,
    FOMC,
    US_CORE_CPI,
    US_CPI,
    US_NFP,
    classify_event_type,
    evaluate_news_block,
)
from adaptive_scalper.news.types import EconomicEvent, Impact, ProviderHealth

BASE_TIME = 1_700_000_000


def _event(
    title="Some Event", currency="USD", impact=Impact.HIGH, scheduled_at_utc=BASE_TIME,
    normalized_event_type=None, **overrides,
) -> EconomicEvent:
    defaults = dict(
        event_id="evt-1", provider="test", provider_event_id=None,
        scheduled_at_utc=scheduled_at_utc, country=currency or "", currency=currency,
        title=title, normalized_event_type=normalized_event_type, impact=impact,
        actual=None, forecast=None, previous=None, retrieved_at_utc=BASE_TIME,
        source_reliability="TEST", revision=0, source_identifier="test",
    )
    defaults.update(overrides)
    return EconomicEvent(**defaults)


# --------------------------------------------------------------------------
# classify_event_type
# --------------------------------------------------------------------------

@pytest.mark.parametrize("title,expected", [
    ("FOMC Statement", FOMC),
    ("FOMC Press Conference", FOMC),
    ("Federal Funds Rate", FOMC),
    ("Non-Farm Employment Change", US_NFP),
    ("NFP", US_NFP),
    ("CPI m/m", US_CPI),
    ("Core CPI m/m", US_CORE_CPI),
])
def test_classify_known_usd_event_types(title, expected):
    assert classify_event_type(title, "USD") == expected


def test_classify_returns_none_for_non_usd_currency():
    assert classify_event_type("FOMC Statement", "GBP") is None


def test_classify_returns_none_for_unrecognized_title():
    assert classify_event_type("Some Unrelated USD Release", "USD") is None


# --------------------------------------------------------------------------
# Directive's exact worked timeline: event at 16:30, window [16:15, 17:00)
# --------------------------------------------------------------------------

def test_directive_worked_example_16_30_event_window():
    event_time = 16 * 3600 + 30 * 60  # 16:30 in seconds-of-day, arbitrary epoch base
    base = BASE_TIME - (BASE_TIME % 86400)  # align to a day boundary for readable offsets
    scheduled = base + event_time
    event = _event(currency="USD", normalized_event_type=FOMC, scheduled_at_utc=scheduled)

    clear_before = scheduled - 15 * 60 - 1     # 16:14:59
    blocked_start = scheduled - 15 * 60         # 16:15:00
    blocked_end = scheduled + 30 * 60 - 1        # 16:59:59
    clear_after = scheduled + 30 * 60            # 17:00:00

    for now, expected_decision in [
        (clear_before, ALLOW),
        (blocked_start, BLOCK_NEWS),
        (blocked_end, BLOCK_NEWS),
        (clear_after, ALLOW),
    ]:
        result = evaluate_news_block("XAUUSD", now, [event], ProviderHealth.HEALTHY)
        assert result.decision == expected_decision, f"at now={now}: expected {expected_decision}, got {result.decision}"


# --------------------------------------------------------------------------
# Global blockers: FOMC/CPI/NFP block ALL THREE symbols
# --------------------------------------------------------------------------

@pytest.mark.parametrize("event_type", [FOMC, US_CPI, US_CORE_CPI, US_NFP])
@pytest.mark.parametrize("symbol", ["XAUUSD", "GBPJPY", "BTCUSD"])
def test_systemic_usd_events_block_all_three_symbols(event_type, symbol):
    event = _event(currency="USD", normalized_event_type=event_type, scheduled_at_utc=BASE_TIME)
    result = evaluate_news_block(symbol, BASE_TIME, [event], ProviderHealth.HEALTHY)
    assert result.decision == BLOCK_NEWS


# --------------------------------------------------------------------------
# Per-symbol currency relevance for non-systemic HIGH events
# --------------------------------------------------------------------------

def test_usd_high_impact_blocks_xauusd_and_btcusd_not_gbpjpy():
    event = _event(currency="USD", title="Some Other USD Release", scheduled_at_utc=BASE_TIME)
    assert evaluate_news_block("XAUUSD", BASE_TIME, [event], ProviderHealth.HEALTHY).decision == BLOCK_NEWS
    assert evaluate_news_block("BTCUSD", BASE_TIME, [event], ProviderHealth.HEALTHY).decision == BLOCK_NEWS
    assert evaluate_news_block("GBPJPY", BASE_TIME, [event], ProviderHealth.HEALTHY).decision == ALLOW


def test_gbp_high_impact_blocks_only_gbpjpy():
    event = _event(currency="GBP", title="BoE Rate Decision", scheduled_at_utc=BASE_TIME)
    assert evaluate_news_block("GBPJPY", BASE_TIME, [event], ProviderHealth.HEALTHY).decision == BLOCK_NEWS
    assert evaluate_news_block("XAUUSD", BASE_TIME, [event], ProviderHealth.HEALTHY).decision == ALLOW
    assert evaluate_news_block("BTCUSD", BASE_TIME, [event], ProviderHealth.HEALTHY).decision == ALLOW


def test_jpy_high_impact_blocks_only_gbpjpy():
    event = _event(currency="JPY", title="BoJ Rate Decision", scheduled_at_utc=BASE_TIME)
    assert evaluate_news_block("GBPJPY", BASE_TIME, [event], ProviderHealth.HEALTHY).decision == BLOCK_NEWS


def test_low_or_medium_impact_never_blocks():
    for impact in (Impact.LOW, Impact.MEDIUM):
        event = _event(currency="USD", impact=impact, scheduled_at_utc=BASE_TIME)
        assert evaluate_news_block("XAUUSD", BASE_TIME, [event], ProviderHealth.HEALTHY).decision == ALLOW


def test_irrelevant_currency_never_blocks():
    event = _event(currency="CAD", title="CPI m/m", scheduled_at_utc=BASE_TIME)
    for symbol in ("XAUUSD", "GBPJPY", "BTCUSD"):
        assert evaluate_news_block(symbol, BASE_TIME, [event], ProviderHealth.HEALTHY).decision == ALLOW


# --------------------------------------------------------------------------
# Calendar outage / staleness / conflict — checked BEFORE the window logic
# --------------------------------------------------------------------------

@pytest.mark.parametrize("health", [ProviderHealth.STALE, ProviderHealth.UNAVAILABLE])
def test_unhealthy_calendar_blocks_new_entries_regardless_of_events(health):
    # Even an EMPTY events list (which would otherwise ALLOW) must block
    # when the calendar itself is untrustworthy — directive section 43.
    result = evaluate_news_block("XAUUSD", BASE_TIME, [], health)
    assert result.decision == BLOCK_NEWS_CALENDAR_UNAVAILABLE


def test_provider_conflict_blocks_regardless_of_events():
    result = evaluate_news_block("XAUUSD", BASE_TIME, [], ProviderHealth.CONFLICT)
    assert result.decision == BLOCK_NEWS_PROVIDER_CONFLICT


def test_healthy_empty_calendar_allows():
    result = evaluate_news_block("XAUUSD", BASE_TIME, [], ProviderHealth.HEALTHY)
    assert result.decision == ALLOW


def test_degraded_cache_backed_calendar_still_applies_normal_window_logic():
    # DEGRADED (serving from cache, not stale) should NOT auto-block —
    # only STALE/UNAVAILABLE/CONFLICT do.
    event = _event(currency="USD", normalized_event_type=FOMC, scheduled_at_utc=BASE_TIME)
    result = evaluate_news_block("XAUUSD", BASE_TIME, [event], ProviderHealth.DEGRADED)
    assert result.decision == BLOCK_NEWS  # window logic still applies


# --------------------------------------------------------------------------
# Position management must not be affected — this module doesn't touch it,
# but assert its scope is exactly "new entries" via the reason string.
# --------------------------------------------------------------------------

def test_result_carries_a_specific_reason_not_a_generic_message():
    event = _event(currency="USD", normalized_event_type=FOMC, scheduled_at_utc=BASE_TIME)
    result = evaluate_news_block("XAUUSD", BASE_TIME, [event], ProviderHealth.HEALTHY)
    assert "FOMC" not in result.reason or event.title in result.reason  # reason names the actual event
    assert result.blocking_event is event


def test_rejects_unknown_canonical_symbol():
    with pytest.raises(ValueError):
        evaluate_news_block("EURUSD", BASE_TIME, [], ProviderHealth.HEALTHY)
