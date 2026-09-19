"""Deterministic news-block decision logic (directive sections 40-44).

Pure functions, no I/O — this is the safety-critical part of the news
system and is fully testable without any network access. Everything
above this module (provider fetching, caching) exists only to produce
the `events` list and `provider_health` this module consumes.
"""

from __future__ import annotations

from dataclasses import dataclass

from adaptive_scalper.config.constants import ALLOWED_CANONICAL_SYMBOLS
from adaptive_scalper.news.types import EconomicEvent, Impact, ProviderHealth

BLOCK_NEWS = "BLOCK_NEWS"
BLOCK_NEWS_CALENDAR_UNAVAILABLE = "BLOCK_NEWS_CALENDAR_UNAVAILABLE"
BLOCK_NEWS_PROVIDER_CONFLICT = "BLOCK_NEWS_PROVIDER_CONFLICT"
ALLOW = "ALLOW"

FOMC = "FOMC"
US_CPI = "US_CPI"
US_CORE_CPI = "US_CORE_CPI"
US_NFP = "US_NFP"

# Directive section 40: these four event types are SYSTEMIC — they block
# ALL THREE canonical symbols regardless of each symbol's own currency
# relevance (section 40's separate "Systemic global events" list, not
# the broader "recognize at minimum" list which also includes BoE/BoJ
# events that are only relevant via the per-symbol currency mapping
# below).
GLOBAL_BLOCKING_EVENT_TYPES = frozenset({FOMC, US_CPI, US_CORE_CPI, US_NFP})

# Directive section 40's "Relevant currency logic".
SYMBOL_RELEVANT_CURRENCIES: dict[str, frozenset[str]] = {
    "XAUUSD": frozenset({"USD"}),
    "BTCUSD": frozenset({"USD"}),
    "GBPJPY": frozenset({"GBP", "JPY"}),
}
assert frozenset(SYMBOL_RELEVANT_CURRENCIES) == ALLOWED_CANONICAL_SYMBOLS, (
    "SYMBOL_RELEVANT_CURRENCIES must have exactly one entry per ALLOWED_CANONICAL_SYMBOLS"
)

_FOMC_KEYWORDS = ("fomc", "federal funds rate", "fed interest rate decision")
_NFP_KEYWORDS = ("non-farm", "nonfarm", "non farm", "nfp")


def classify_event_type(title: str, currency: str | None) -> str | None:
    """Recognize the directive section 40 systemic USD events from a raw
    provider title. Deliberately conservative keyword matching — a title
    this doesn't recognize returns None (falls back to the generic
    per-symbol currency-relevance check below, not silently ignored)."""
    if (currency or "").upper() != "USD":
        return None
    t = title.lower()
    if any(k in t for k in _FOMC_KEYWORDS):
        return FOMC
    if any(k in t for k in _NFP_KEYWORDS):
        return US_NFP
    if "core cpi" in t or "core consumer price index" in t:
        return US_CORE_CPI
    if "cpi" in t or "consumer price index" in t:
        return US_CPI
    return None


@dataclass(frozen=True)
class NewsBlockResult:
    decision: str          # ALLOW / BLOCK_NEWS / BLOCK_NEWS_CALENDAR_UNAVAILABLE / BLOCK_NEWS_PROVIDER_CONFLICT
    reason: str
    blocking_event: EconomicEvent | None
    block_start_utc: int | None
    block_end_utc: int | None


def _event_window(event: EconomicEvent, pre_minutes: int, post_minutes: int) -> tuple[int, int]:
    return (event.scheduled_at_utc - pre_minutes * 60, event.scheduled_at_utc + post_minutes * 60)


def _event_applies_to_symbol(event: EconomicEvent, canonical_symbol: str) -> bool:
    if event.impact != Impact.HIGH:
        return False
    event_type = event.normalized_event_type or classify_event_type(event.title, event.currency)
    if event_type in GLOBAL_BLOCKING_EVENT_TYPES:
        return True
    relevant_currencies = SYMBOL_RELEVANT_CURRENCIES[canonical_symbol]
    return (event.currency or "").upper() in relevant_currencies


def evaluate_news_block(
    canonical_symbol: str,
    now_utc: int,
    events: list[EconomicEvent],
    provider_health: ProviderHealth,
    *,
    pre_high_impact_minutes: int = 15,
    post_high_impact_minutes: int = 30,
) -> NewsBlockResult:
    """The single entry point the eventual final permission gate calls.

    Precedence (directive sections 43-44): calendar outage/staleness and
    provider conflict are checked FIRST and block unconditionally —
    section 43 explicitly forbids interpreting an unavailable calendar
    as "no events," so a healthy-looking `events` list computed from a
    stale/failed fetch must never reach the normal window check below.
    """
    if canonical_symbol not in ALLOWED_CANONICAL_SYMBOLS:
        raise ValueError(f"{canonical_symbol!r} is not an allowed canonical symbol")

    if provider_health == ProviderHealth.CONFLICT:
        return NewsBlockResult(BLOCK_NEWS_PROVIDER_CONFLICT, "trusted providers materially disagree on a HIGH-impact event's timing", None, None, None)

    if provider_health in (ProviderHealth.STALE, ProviderHealth.UNAVAILABLE):
        return NewsBlockResult(BLOCK_NEWS_CALENDAR_UNAVAILABLE, f"news calendar health={provider_health.value}", None, None, None)

    for event in events:
        if not _event_applies_to_symbol(event, canonical_symbol):
            continue
        start, end = _event_window(event, pre_high_impact_minutes, post_high_impact_minutes)
        # Directive's worked example (section 41) is explicit: for a
        # 16:30 event with a 30-minute post-window, 16:59:59 is blocked
        # but 17:00:00 is clear — the end boundary is EXCLUSIVE. The
        # start boundary is inclusive (16:15:00 is blocked).
        if start <= now_utc < end:
            return NewsBlockResult(
                BLOCK_NEWS,
                f"{event.title} ({event.currency}, impact={event.impact.value}) window "
                f"[{start}, {end}] contains now={now_utc}",
                event, start, end,
            )

    return NewsBlockResult(ALLOW, "no applicable HIGH-impact event window covers now", None, None, None)
