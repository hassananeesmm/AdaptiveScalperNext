"""Normalized economic event model (directive section 39) and provider
health states (directive section 44).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Impact(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    HOLIDAY = "HOLIDAY"
    UNKNOWN = "UNKNOWN"


class ProviderHealth(str, Enum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    STALE = "STALE"
    UNAVAILABLE = "UNAVAILABLE"
    CONFLICT = "CONFLICT"


@dataclass(frozen=True)
class EconomicEvent:
    event_id: str                      # stable synthetic id (provider lacks a real one for most feeds)
    provider: str
    provider_event_id: str | None      # the provider's own id, when it has one
    scheduled_at_utc: int              # epoch seconds
    country: str                       # provider's raw country/currency code (e.g. "USD", "All")
    currency: str | None               # normalized ISO currency code, None for non-currency-specific events
    title: str
    normalized_event_type: str | None  # e.g. "FOMC", "US_CPI", "US_NFP" — see blocking.classify_event_type
    impact: Impact
    actual: str | None
    forecast: str | None
    previous: str | None
    retrieved_at_utc: int
    source_reliability: str            # documented, not calibrated — e.g. "PRIMARY"/"SECONDARY"/"CACHE"/"MANUAL"
    revision: int                      # how many times this same logical event has been re-fetched with changes
    source_identifier: str             # e.g. the URL or file path data was retrieved from


class ProviderError(Exception):
    """Raised on ANY provider failure — network error, non-200 response,
    malformed payload. Per directive section 38: "Never interpret
    provider failure as 'no events.'" A provider that genuinely found
    zero events this week returns an empty list; a provider that could
    not determine that returns via this exception instead — the two
    must never be conflated."""
