"""Derived bar/tick coverage summaries (directive sections 50, 97).

Recomputed from the authoritative `bars`/`ticks` tables after each import
chunk and cached in `historical_bar_coverage`/`historical_tick_coverage` so
the dashboard's historical-data panel doesn't aggregate the full tables on
every request.

Gap counting is intentionally naive: it compares the actual bar count over
[earliest, latest] against the count implied by a perfectly continuous
series at that resolution. Weekends and legitimate session closures show up
as "gaps" under this definition — directive section 46 says to preserve
those, not fill them, so this is reported as informational coverage data,
never treated as a data-quality failure to correct.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from adaptive_scalper.history.resolutions import resolution_seconds


@dataclass(frozen=True)
class BarCoverage:
    canonical_symbol: str
    resolution: str
    earliest_utc: int | None
    latest_utc: int | None
    bar_count: int
    expected_bar_count: int | None
    gap_count: int
    last_sync_at: str | None


@dataclass(frozen=True)
class TickCoverage:
    canonical_symbol: str
    earliest_utc: int | None
    latest_utc: int | None
    tick_count: int
    last_sync_at: str | None


def refresh_bar_coverage(conn: sqlite3.Connection, canonical_symbol: str, resolution: str) -> BarCoverage:
    row = conn.execute(
        """
        SELECT MIN(ts_utc) AS earliest, MAX(ts_utc) AS latest, COUNT(*) AS n
        FROM bars WHERE canonical_symbol = ? AND resolution = ?
        """,
        (canonical_symbol, resolution),
    ).fetchone()
    earliest, latest, count = row["earliest"], row["latest"], row["n"]

    expected: int | None = None
    gap_count = 0
    if count > 0:
        step = resolution_seconds(resolution)
        expected = ((latest - earliest) // step) + 1
        gap_count = max(expected - count, 0)

    conn.execute(
        """
        INSERT INTO historical_bar_coverage
            (canonical_symbol, resolution, earliest_utc, latest_utc, bar_count,
             expected_bar_count, gap_count, last_sync_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
        ON CONFLICT(canonical_symbol, resolution) DO UPDATE SET
            earliest_utc = excluded.earliest_utc,
            latest_utc = excluded.latest_utc,
            bar_count = excluded.bar_count,
            expected_bar_count = excluded.expected_bar_count,
            gap_count = excluded.gap_count,
            last_sync_at = excluded.last_sync_at
        """,
        (canonical_symbol, resolution, earliest, latest, count, expected, gap_count),
    )
    return BarCoverage(canonical_symbol, resolution, earliest, latest, count, expected, gap_count, None)


def refresh_tick_coverage(conn: sqlite3.Connection, canonical_symbol: str) -> TickCoverage:
    row = conn.execute(
        """
        SELECT MIN(ts_utc) AS earliest, MAX(ts_utc) AS latest, COUNT(*) AS n
        FROM ticks WHERE canonical_symbol = ?
        """,
        (canonical_symbol,),
    ).fetchone()
    earliest, latest, count = row["earliest"], row["latest"], row["n"]

    conn.execute(
        """
        INSERT INTO historical_tick_coverage
            (canonical_symbol, earliest_utc, latest_utc, tick_count, last_sync_at)
        VALUES (?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
        ON CONFLICT(canonical_symbol) DO UPDATE SET
            earliest_utc = excluded.earliest_utc,
            latest_utc = excluded.latest_utc,
            tick_count = excluded.tick_count,
            last_sync_at = excluded.last_sync_at
        """,
        (canonical_symbol, earliest, latest, count),
    )
    return TickCoverage(canonical_symbol, earliest, latest, count, None)


def get_bar_coverage(conn: sqlite3.Connection, canonical_symbol: str, resolution: str) -> BarCoverage | None:
    row = conn.execute(
        """
        SELECT canonical_symbol, resolution, earliest_utc, latest_utc, bar_count,
               expected_bar_count, gap_count, last_sync_at
        FROM historical_bar_coverage WHERE canonical_symbol = ? AND resolution = ?
        """,
        (canonical_symbol, resolution),
    ).fetchone()
    return BarCoverage(**dict(row)) if row else None


def get_tick_coverage(conn: sqlite3.Connection, canonical_symbol: str) -> TickCoverage | None:
    row = conn.execute(
        """
        SELECT canonical_symbol, earliest_utc, latest_utc, tick_count, last_sync_at
        FROM historical_tick_coverage WHERE canonical_symbol = ?
        """,
        (canonical_symbol,),
    ).fetchone()
    return TickCoverage(**dict(row)) if row else None
