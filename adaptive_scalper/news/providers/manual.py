"""Optional operator-supplied normalized JSON fallback (directive
section 38's "OPTIONAL MANUAL FALLBACK"). Never used automatically by
`calendar_service.py`'s default chain — an operator wires this in
explicitly (e.g. ahead of a known extended outage of both live
providers) by constructing it and passing it as an extra fallback.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from adaptive_scalper.news.blocking import classify_event_type
from adaptive_scalper.news.types import EconomicEvent, Impact, ProviderError


class ManualJSONProvider:
    name = "manual"

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def fetch(self, now_utc: int | None = None) -> list[EconomicEvent]:
        if not self.path.exists():
            raise ProviderError(f"manual: file not found: {self.path}")
        try:
            raw_events = json.loads(self.path.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise ProviderError(f"manual: {self.path} is not valid JSON: {exc}") from exc
        if not isinstance(raw_events, list):
            raise ProviderError(f"manual: expected a JSON list in {self.path}")

        retrieved_at = int(now_utc if now_utc is not None else time.time())
        events: list[EconomicEvent] = []
        for raw in raw_events:
            try:
                currency = raw.get("currency")
                impact = Impact(raw["impact"].upper()) if raw.get("impact") else Impact.UNKNOWN
                events.append(EconomicEvent(
                    event_id=raw["event_id"],
                    provider="manual",
                    provider_event_id=raw.get("provider_event_id"),
                    scheduled_at_utc=int(raw["scheduled_at_utc"]),
                    country=raw.get("country", ""),
                    currency=currency,
                    title=raw["title"],
                    normalized_event_type=raw.get("normalized_event_type") or classify_event_type(raw["title"], currency),
                    impact=impact,
                    actual=raw.get("actual"),
                    forecast=raw.get("forecast"),
                    previous=raw.get("previous"),
                    retrieved_at_utc=retrieved_at,
                    source_reliability="MANUAL",
                    revision=0,
                    source_identifier=str(self.path),
                ))
            except (KeyError, ValueError) as exc:
                raise ProviderError(f"manual: malformed event {raw!r} in {self.path}: {exc}") from exc
        return events
