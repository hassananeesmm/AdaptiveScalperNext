"""Agent 3 - News Analyst: interprets the EXISTING verified economic calendar.

It reuses the calendar rows the runtime's keyless providers stored (no new
outbound source, nothing fabricated): classifies events (central bank,
inflation, employment, growth, other), maps currencies to the instruments
they move, separates FACTS (title, currency, impact, scheduled and retrieved
timestamps, provider, revision) from INTERPRETATION (labelled as historical
tendency, not prediction), measures the realized range reaction around past
high-impact events where stored bars cover them, and flags conflicting or
revised entries and provider health problems.

The news blackout in the trading engine is authoritative. This agent reports
the engine's own news decision and can never authorize a trade during a
restricted window. Titles are untrusted text and were sanitized at snapshot
build time.
"""

from __future__ import annotations

import statistics
from collections import defaultdict

from adaptive_scalper.ai_desk.agents.base import Agent, AgentContext, Draft
from adaptive_scalper.ai_desk.agents.market_analyst import bars_from_payload
from adaptive_scalper.ai_desk.contracts import Claim, Snapshot

CURRENCY_INSTRUMENTS = {"USD": ("XAUUSD", "BTCUSD"), "GBP": ("GBPJPY",), "JPY": ("GBPJPY",)}
CATEGORIES = (
    ("CENTRAL_BANK", ("rate decision", "interest rate", "fomc", "monetary policy", "policy rate", "boe", "boj",
                      "fed chair", "powell", "bailey", "ueda", "press conference", "minutes", "bank rate")),
    ("INFLATION", ("cpi", "pce", "ppi", "inflation", "price index")),
    ("EMPLOYMENT", ("non-farm", "nonfarm", "employment", "unemployment", "payroll", "jobless", "claimant", "jobs")),
    ("GROWTH", ("gdp", "retail sales", "pmi", "industrial production", "ism", "budget")),
)
IMMINENT_SECONDS = 3600


def categorize(title: str) -> str:
    t = (title or "").lower()
    for cat, words in CATEGORIES:
        if any(w in t for w in words):
            return cat
    return "OTHER"


def event_reaction(bars, event_ts: int, bar_seconds: int) -> float | None:
    """Mean bar range in the 6 bars after the event / mean range in the 12 bars before it."""
    before = [b.high - b.low for b in bars if event_ts - 12 * bar_seconds <= b.time < event_ts]
    after = [b.high - b.low for b in bars if event_ts <= b.time < event_ts + 6 * bar_seconds]
    if len(before) < 8 or len(after) < 4:
        return None
    base = statistics.fmean(before)
    return None if base <= 0 else statistics.fmean(after) / base


class NewsAnalyst(Agent):
    key = "news_analyst"
    version = "1.0.0"

    def analyze(self, snapshot: Snapshot, context: AgentContext, symbol: str | None = None) -> Draft:
        window = snapshot.one("NEWS_EVENTS")
        history = snapshot.one("NEWS_HISTORY")
        providers = snapshot.one("NEWS_PROVIDERS")
        runtime_news = snapshot.one("NEWS_RUNTIME")
        readiness = snapshot.one("MULTI_POSITION_READINESS")
        evidence = [e.evidence_id for e in (window, history, providers, runtime_news, readiness) if e]
        limitations = ["the trading engine's news blackout is authoritative; this agent cannot authorize trading",
                       ("event categories come from title keywords; interpretation is a historical tendency, "
                        "not a forecast")]
        as_of = snapshot.as_of_utc
        events = window.payload["events"] if window else []

        facts = []
        imminent: dict[str, str] = {}
        by_key: dict[tuple, list[dict]] = defaultdict(list)
        for e in events:
            cat = categorize(e["title"])
            instruments = CURRENCY_INSTRUMENTS.get(e["currency"] or "", ())
            facts.append({"title": e["title"], "currency": e["currency"], "impact": e["impact"], "category": cat,
                          "scheduled_utc": e["scheduled_at_utc"], "retrieved_utc": e["retrieved_at_utc"],
                          "provider": e["provider"], "revision": e["revision"], "actual": e["actual"],
                          "forecast": e["forecast"], "previous": e["previous"],
                          "affected_instruments": list(instruments),
                          "seconds_until": e["scheduled_at_utc"] - as_of})
            by_key[(e["currency"], cat, e["title"])].append(e)
            if e["impact"] == "HIGH" and 0 <= e["scheduled_at_utc"] - as_of <= IMMINENT_SECONDS:
                for sym in instruments:
                    imminent.setdefault(sym, f"{e['title']} ({e['currency']}) at T+{(e['scheduled_at_utc'] - as_of) // 60}m")

        conflicts = []
        for (ccy, cat, title), rows in sorted(by_key.items(), key=lambda kv: str(kv[0])):
            providers_seen = {r["provider"] for r in rows}
            times = {r["scheduled_at_utc"] for r in rows}
            if len(providers_seen) > 1 and len(times) > 1:
                conflicts.append({"title": title, "currency": ccy, "providers": sorted(providers_seen),
                                  "scheduled_utc": sorted(times), "issue": "providers disagree on the time"})
            if any((r["revision"] or 0) > 0 for r in rows):
                conflicts.append({"title": title, "currency": ccy, "issue": "event was revised after first retrieval"})

        interpretation = []
        for f in facts:
            if f["impact"] == "HIGH" and f["affected_instruments"]:
                interpretation.append({
                    "event": f["title"], "label": "INTERPRETATION (historical tendency, not prediction)",
                    "text": f"High-impact {f['category'].lower().replace('_', ' ')} release for {f['currency']}: such "
                            f"releases are commonly followed by wider ranges and spreads in "
                            f"{', '.join(f['affected_instruments'])}; direction is not predictable from the calendar."})

        # Measured reaction around past high-impact events where stored bars cover them.
        reactions: dict[str, list[float]] = defaultdict(list)
        for sym in ("XAUUSD", "BTCUSD", "GBPJPY"):
            bars_item = snapshot.one("MARKET_BARS", sym)
            if bars_item is None or history is None:
                continue
            bars = bars_from_payload(bars_item.payload)
            for e in history.payload["events"]:
                if sym in CURRENCY_INSTRUMENTS.get(e["currency"] or "", ()):
                    ratio = event_reaction(bars, e["scheduled_at_utc"], bars_item.payload["bar_seconds"])
                    if ratio is not None:
                        reactions[sym].append(ratio)
        reaction_summary = {sym: {"n": len(v), "median_range_ratio_after_vs_before": round(statistics.median(v), 3)}
                            for sym, v in reactions.items() if v}
        if not reaction_summary:
            limitations.append("no stored bars cover past high-impact events in this snapshot: the volatility "
                               "reaction could not be measured")

        provider_health = providers.payload if providers else None
        rt = runtime_news.payload if runtime_news else {}
        engine_news = (readiness.payload.get("news") if readiness else None) or {}
        status = "COMPLETE"
        if not events:
            limitations.append("no events retrieved for the window - absence of rows is not proof that no events exist")
            status = "INCOMPLETE"
        if rt and rt.get("health") not in (None, "HEALTHY"):
            limitations.append(f"news runtime health {rt.get('health')}")
            status = "INCOMPLETE"
        if not snapshot.runtime_live:
            limitations.append("runtime not live: the calendar may be stale (last retrieval "
                               f"{max((f['retrieved_utc'] for f in facts), default=None)})")
            status = "INCOMPLETE" if status == "COMPLETE" else status

        upcoming_high = [f for f in facts if f["impact"] == "HIGH" and f["seconds_until"] >= 0]
        assessment = (f"{len(facts)} calendar event(s) in [-6h, +48h], {len(upcoming_high)} upcoming high-impact; "
                      f"imminent (<= 60 min) high-impact for: {', '.join(sorted(imminent)) or 'none'}; "
                      f"{len(conflicts)} conflicting/revised entr(ies). The engine's blackout decides.")
        claims = [Claim(claim_id=f"news:{sym}:imminent", kind="DESCRIPTIVE",
                        statement=f"High-impact event within 60 minutes for {sym}: {txt}",
                        evidence_ids=[window.evidence_id], conclusion_key=f"news:{sym}:imminent")
                  for sym, txt in sorted(imminent.items())]
        if conflicts:
            claims.append(Claim(claim_id="news:calendar:conflicts", kind="DATA_QUALITY",
                                statement=f"{len(conflicts)} calendar entries conflict or were revised",
                                evidence_ids=[window.evidence_id]))
        return Draft(status=status, assessment=assessment, evidence_ids=evidence, limitations=limitations,
                     claims=claims,
                     counterarguments=[("Scheduled releases can be moved or cancelled; unscheduled news is not in "
                                        "the calendar at all.")],
                     findings={"facts": facts[:80], "interpretation": interpretation[:20], "conflicts": conflicts,
                               "imminent_high_impact": imminent, "event_reaction": reaction_summary,
                               "provider_health": provider_health, "engine_news_decision": engine_news,
                               "runtime_news_health": rt.get("health")})
