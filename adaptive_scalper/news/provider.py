"""EconomicCalendarProvider abstraction (directive section 38)."""

from __future__ import annotations

from typing import Protocol

from adaptive_scalper.news.types import EconomicEvent


class EconomicCalendarProvider(Protocol):
    name: str

    def fetch(self, now_utc: int) -> list[EconomicEvent]:
        """Return this provider's current view of upcoming events.
        Raises `adaptive_scalper.news.types.ProviderError` on any failure
        — never returns an empty list to mean "couldn't fetch"."""
        ...
