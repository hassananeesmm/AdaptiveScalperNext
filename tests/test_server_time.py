"""Broker server clock <-> UTC (BUG_BACKLOG #14, gateway/server_time.py)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from adaptive_scalper.gateway.server_time import (
    INCONCLUSIVE,
    MISMATCH,
    RULE_UTC,
    RULE_UTC2_US_DST,
    VERIFIED,
    classify_quote_clock,
    offset_seconds_at_utc,
    server_ms_to_utc_ms,
    server_to_utc,
    us_dst_in_effect,
    utc_to_server,
    validate_rule,
)

H = 3600


def utc(*args) -> int:
    return int(datetime(*args, tzinfo=timezone.utc).timestamp())


def test_the_laptop_measurement_is_reproduced():
    # 2026-09-24 20:12:34 UTC: IC Markets DEMO tick.time read 23:12:35 (+3 h).
    now = utc(2026, 9, 24, 20, 12, 34)
    assert offset_seconds_at_utc(RULE_UTC2_US_DST, now) == 3 * H
    assert server_to_utc(RULE_UTC2_US_DST, utc(2026, 9, 24, 23, 12, 35)) == now + 1


def test_winter_offset_is_two_hours():
    assert offset_seconds_at_utc(RULE_UTC2_US_DST, utc(2026, 1, 15, 12)) == 2 * H
    assert offset_seconds_at_utc(RULE_UTC2_US_DST, utc(2026, 12, 1, 12)) == 2 * H


@pytest.mark.parametrize("year,start,end", [
    (2024, utc(2024, 3, 10, 7), utc(2024, 11, 3, 6)),
    (2025, utc(2025, 3, 9, 7), utc(2025, 11, 2, 6)),
    (2026, utc(2026, 3, 8, 7), utc(2026, 11, 1, 6)),
])
def test_us_dst_boundaries(year, start, end):
    assert not us_dst_in_effect(start - 1) and us_dst_in_effect(start)
    assert us_dst_in_effect(end - 1) and not us_dst_in_effect(end)


def test_fx_week_close_is_midnight_server_time_in_both_seasons():
    # 17:00 New York on a Friday = 00:00 server under the New-York-close clock.
    summer_close = utc(2026, 9, 18, 21)   # 17:00 EDT
    winter_close = utc(2026, 1, 16, 22)   # 17:00 EST
    assert datetime.fromtimestamp(utc_to_server(RULE_UTC2_US_DST, summer_close), tz=timezone.utc).hour == 0
    assert datetime.fromtimestamp(utc_to_server(RULE_UTC2_US_DST, winter_close), tz=timezone.utc).hour == 0


def test_round_trip_is_exact_except_the_repeated_autumn_server_hour():
    # At the November fall-back the server clock repeats an hour: the first
    # UTC hour after the switch shares its server times with the hour before
    # it, so the broker's own data cannot tell them apart. They map to the
    # earlier instant; every other instant of the year round-trips exactly.
    fall_back = utc(2026, 11, 1, 6)
    for u in range(utc(2025, 12, 25), utc(2027, 1, 5), 900):
        back = server_to_utc(RULE_UTC2_US_DST, utc_to_server(RULE_UTC2_US_DST, u))
        assert back == (u - H if fall_back <= u < fall_back + H else u)


def test_server_to_utc_is_injective_so_unique_bar_keys_stay_unique():
    servers = range(utc_to_server(RULE_UTC2_US_DST, utc(2026, 10, 31)),
                    utc_to_server(RULE_UTC2_US_DST, utc(2026, 11, 2)), 60)
    converted = [server_to_utc(RULE_UTC2_US_DST, s) for s in servers]
    assert len(set(converted)) == len(converted)
    assert converted == sorted(converted)


def test_milliseconds_are_preserved():
    s = utc_to_server(RULE_UTC2_US_DST, utc(2026, 9, 24, 20))
    assert server_ms_to_utc_ms(RULE_UTC2_US_DST, s * 1000 + 457) == utc(2026, 9, 24, 20) * 1000 + 457


def test_rule_utc_is_the_identity():
    assert server_to_utc(RULE_UTC, 1_700_000_000) == 1_700_000_000
    assert utc_to_server(RULE_UTC, 1_700_000_000) == 1_700_000_000


def test_unknown_rules_are_refused():
    with pytest.raises(ValueError):
        validate_rule("EET")
    with pytest.raises(ValueError):
        server_to_utc("UTC+3", 0)


def test_quote_clock_classification():
    now = 1_800_000_000.0
    assert classify_quote_clock(now - 5, now)[0] == VERIFIED
    assert classify_quote_clock(now + 30, now)[0] == VERIFIED          # clock skew
    assert classify_quote_clock(now + 3 * H, now)[0] == MISMATCH       # rule UTC on a UTC+3 server
    assert classify_quote_clock(now - 2 * 86400, now)[0] == INCONCLUSIVE  # weekend: stale quote


def test_config_validates_the_rule_and_default_toml_names_the_measured_broker_clock():
    from adaptive_scalper.config.loader import AppConfig, load_config

    assert AppConfig().mt5.server_time_rule == RULE_UTC
    with pytest.raises(Exception):
        AppConfig.model_validate({"mt5": {"server_time_rule": "UTC+3"}})
    assert load_config("config/default.toml").mt5.server_time_rule == RULE_UTC2_US_DST
