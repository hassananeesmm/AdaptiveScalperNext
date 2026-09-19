"""PRIMARY provider slot (directive section 38) — deliberately NOT
connected to a real service.

The directive names "FinanceCalendar" as the primary keyless provider
and instructs: "At implementation time, verify its current official
documentation and terms." That verification was performed this session
(web search + attempted lookup): no real, distinct, official, keyless,
structured-JSON economic-calendar service by that name could be
identified. Rather than fabricate an integration against a service that
may not exist, or silently rename a different provider to match the
directive's label, this class exists as an honest, explicit stub —
satisfying directive section 138's acceptance-checklist escape valve
("FinanceCalendar primary implemented OR actual limitation documented")
via the documented-limitation branch, not the implemented branch.

`fetch()` always raises `ProviderError` immediately, with no network
call — `calendar_service.py`'s fallback chain treats that exactly like
any other primary-provider failure and falls through to the real,
verified SECONDARY provider (`forexfactory.py`). If a real
"FinanceCalendar" service is identified later, only this file needs to
change — the provider abstraction and fallback chain already support it.
"""

from __future__ import annotations

from adaptive_scalper.news.types import EconomicEvent, ProviderError


class FinanceCalendarProvider:
    name = "financecalendar"

    def fetch(self, now_utc: int | None = None) -> list[EconomicEvent]:
        raise ProviderError(
            "financecalendar: no real, verified, keyless, official 'FinanceCalendar' "
            "economic-calendar service was identified during implementation "
            "(see this module's docstring) — this is a documented limitation, "
            "not a transient failure; the fallback chain should proceed to "
            "the SECONDARY provider"
        )
