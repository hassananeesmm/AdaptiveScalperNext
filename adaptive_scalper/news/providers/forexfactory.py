"""Forex Factory structured weekly calendar (directive section 38,
SECONDARY provider — see `financecalendar.py` for why this project's
PRIMARY slot is a documented, deliberately-unimplemented stub instead).

Uses the public JSON feed Forex Factory's own calendar widget serves
(`nfs.faireconomy.media/ff_calendar_thisweek.json`) — structured JSON,
no API key, no HTML scraping (directive section 38 explicitly forbids
"fragile HTML calendar scraping"; this is not that). Live-verified
during development: returns real structured events (schema: title,
country, date, impact, forecast, previous) — also live-observed
returning HTTP 429 "Rate Limited" under repeated polling, which is
exactly the kind of real provider failure `ProviderError` exists to
surface rather than silently swallow.
"""

from __future__ import annotations

import hashlib
import time
from datetime import datetime

import httpx

from adaptive_scalper.news.blocking import classify_event_type
from adaptive_scalper.news.types import EconomicEvent, Impact, ProviderError

DEFAULT_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
DEFAULT_TIMEOUT_SECONDS = 10.0

_IMPACT_MAP = {
    "low": Impact.LOW,
    "medium": Impact.MEDIUM,
    "high": Impact.HIGH,
    "holiday": Impact.HOLIDAY,
}


def _synthetic_event_id(title: str, country: str, date_str: str) -> str:
    # Forex Factory's feed has no stable per-event id, so we derive one
    # from the fields that identify "the same logical event" across
    # re-fetches — good enough for dedup, not a claim of provider-issued
    # identity (provider_event_id stays None).
    raw = f"forexfactory|{title}|{country}|{date_str}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _parse_event(raw: dict, retrieved_at_utc: int, source_identifier: str) -> EconomicEvent:
    title = raw["title"]
    country = raw.get("country") or ""
    date_str = raw["date"]
    scheduled_dt = datetime.fromisoformat(date_str)  # handles the feed's "-04:00"-style offsets
    scheduled_at_utc = int(scheduled_dt.timestamp())

    currency = None if country.upper() == "ALL" else country.upper() or None
    impact = _IMPACT_MAP.get((raw.get("impact") or "").lower(), Impact.UNKNOWN)

    return EconomicEvent(
        event_id=_synthetic_event_id(title, country, date_str),
        provider="forexfactory",
        provider_event_id=None,
        scheduled_at_utc=scheduled_at_utc,
        country=country,
        currency=currency,
        title=title,
        normalized_event_type=classify_event_type(title, currency),
        impact=impact,
        actual=raw.get("actual") or None,
        forecast=raw.get("forecast") or None,
        previous=raw.get("previous") or None,
        retrieved_at_utc=retrieved_at_utc,
        source_reliability="SECONDARY",
        revision=0,
        source_identifier=source_identifier,
    )


class ForexFactoryProvider:
    name = "forexfactory"

    def __init__(self, url: str = DEFAULT_URL, timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS) -> None:
        self.url = url
        self.timeout_seconds = timeout_seconds

    def fetch(self, now_utc: int | None = None) -> list[EconomicEvent]:
        retrieved_at = int(now_utc if now_utc is not None else time.time())
        try:
            response = httpx.get(self.url, timeout=self.timeout_seconds)
        except httpx.HTTPError as exc:
            raise ProviderError(f"forexfactory: network error fetching {self.url}: {exc}") from exc

        if response.status_code != 200:
            raise ProviderError(
                f"forexfactory: unexpected HTTP status {response.status_code} from {self.url} "
                f"(body starts: {response.text[:200]!r})"
            )

        try:
            raw_events = response.json()
        except ValueError as exc:
            raise ProviderError(f"forexfactory: response was not valid JSON: {exc}") from exc

        if not isinstance(raw_events, list):
            raise ProviderError(f"forexfactory: expected a JSON list, got {type(raw_events).__name__}")

        events = []
        for raw in raw_events:
            try:
                events.append(_parse_event(raw, retrieved_at, self.url))
            except (KeyError, ValueError) as exc:
                raise ProviderError(f"forexfactory: malformed event {raw!r}: {exc}") from exc
        return events
