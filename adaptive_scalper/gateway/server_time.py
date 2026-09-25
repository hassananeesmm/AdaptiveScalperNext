"""Broker server clock <-> UTC (BUG_BACKLOG #14).

MT5 reports every tick, bar, order and deal time as the broker SERVER's
wall clock written as a naive epoch, not UTC. Measured on the laptop's IC
Markets DEMO terminal on 2026-09-24: `tick.time` was exactly +10800 s ahead
of the real UTC clock on XAUUSD, GBPJPY and BTCUSD while US daylight saving
was in effect -- the common "New York close" server clock (UTC+2, UTC+3
while US DST is in effect, i.e. New York + 7 h, so the FX week closes at
00:00 server time).

The rule is configuration (`[mt5] server_time_rule`), never guessed.
`Mt5Gateway` converts at the boundary so everything past it is real UTC,
and `classify_quote_clock` lets startup check the configured rule against a
live quote: a converted quote in the future is proof
the rule is wrong, so the runtime refuses to start rather than shifting
every bar, news window and quote-freshness check.

Pure functions, no tzdata dependency: the US DST rule (second Sunday of
March 02:00 EST to first Sunday of November 02:00 EDT) has applied since
2007, which covers every bar MT5 brokers serve for this project.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

RULE_UTC = "UTC"
RULE_UTC2_US_DST = "UTC+2/US_DST"
SERVER_TIME_RULES = (RULE_UTC, RULE_UTC2_US_DST)

HOUR = 3600
VERIFIED = "VERIFIED"
MISMATCH = "MISMATCH"
INCONCLUSIVE = "INCONCLUSIVE"
# A fresh quote is at most this far from the rule's offset (quote age plus
# local clock skew).
DEFAULT_OFFSET_TOLERANCE_SECONDS = 120


def _nth_sunday_utc(year: int, month: int, n: int, hour_utc: int) -> int:
    first = datetime(year, month, 1, tzinfo=timezone.utc)
    days_to_sunday = (6 - first.weekday()) % 7
    day = first + timedelta(days=days_to_sunday + 7 * (n - 1), hours=hour_utc)
    return int(day.timestamp())


def us_dst_in_effect(utc_ts: int) -> bool:
    year = datetime.fromtimestamp(utc_ts, tz=timezone.utc).year
    start = _nth_sunday_utc(year, 3, 2, 7)   # 02:00 EST = 07:00 UTC
    end = _nth_sunday_utc(year, 11, 1, 6)    # 02:00 EDT = 06:00 UTC
    return start <= utc_ts < end


def validate_rule(rule: str) -> str:
    if rule not in SERVER_TIME_RULES:
        raise ValueError(f"unknown MT5 server time rule {rule!r}; supported: {list(SERVER_TIME_RULES)}")
    return rule


def offset_seconds_at_utc(rule: str, utc_ts: int) -> int:
    """Server clock minus UTC at the UTC instant `utc_ts`."""
    validate_rule(rule)
    if rule == RULE_UTC:
        return 0
    return 3 * HOUR if us_dst_in_effect(utc_ts) else 2 * HOUR


def utc_to_server(rule: str, utc_ts: int) -> int:
    return int(utc_ts) + offset_seconds_at_utc(rule, int(utc_ts))


def server_to_utc(rule: str, server_ts: int) -> int:
    """Inverse of `utc_to_server`. A server time in the repeated autumn hour
    maps to its earlier UTC instant. A server time in the skipped spring
    hour has no UTC instant (`is_skipped_server_time`); it maps to UTC-2 h,
    which collides with a real later time, so callers must filter it first."""
    validate_rule(rule)
    server_ts = int(server_ts)
    if rule == RULE_UTC:
        return server_ts
    summer = server_ts - 3 * HOUR
    if offset_seconds_at_utc(rule, summer) == 3 * HOUR:
        return summer
    return server_ts - 2 * HOUR


def is_skipped_server_time(rule: str, server_ts: int) -> bool:
    """True for a server time inside the hour the server clock skips at the
    spring DST change: no UTC instant produces it, so it cannot be converted
    without guessing (observed: 4 stray BTCUSD M15 bars in 3 years). Callers
    quarantine or drop such rows rather than invent a time for them."""
    validate_rule(rule)
    if rule == RULE_UTC:
        return False
    server_ts = int(server_ts)
    return (offset_seconds_at_utc(rule, server_ts - 3 * HOUR) != 3 * HOUR
            and offset_seconds_at_utc(rule, server_ts - 2 * HOUR) != 2 * HOUR)


def server_ms_to_utc_ms(rule: str, server_ms: int) -> int:
    server_s, ms = divmod(int(server_ms), 1000)
    return server_to_utc(rule, server_s) * 1000 + ms


def classify_quote_clock(
    utc_tick_time: float, utc_now: float,
    tolerance_seconds: float = DEFAULT_OFFSET_TOLERANCE_SECONDS,
) -> tuple[str, float]:
    """(verdict, residual) for one quote time already converted by the
    gateway, against the real UTC clock; residual = tick time - now.

    VERIFIED: the quote is fresh, so the configured rule is right.
    MISMATCH: the quote is in the future, which no correct rule allows
    (e.g. rule UTC on a UTC+3 server). INCONCLUSIVE: an older quote (market
    closed) cannot tell a stale feed from a wrong rule; the execution-time
    quote-freshness checks still fail closed in that case."""
    residual = utc_tick_time - utc_now
    if abs(residual) <= tolerance_seconds:
        return VERIFIED, residual
    if residual > tolerance_seconds:
        return MISMATCH, residual
    return INCONCLUSIVE, residual
