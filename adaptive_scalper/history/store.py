"""Idempotent bar/tick storage.

`INSERT OR IGNORE` against the UNIQUE constraints in migration
0004_historical_data is what makes re-importing an already-covered range a
no-op (directive section 49) — callers never need to pre-check what's
already there.
"""

from __future__ import annotations

import sqlite3

from adaptive_scalper.gateway.types import Bar, Tick


def insert_bars(conn: sqlite3.Connection, canonical_symbol: str, resolution: str, bars: list[Bar]) -> int:
    """Insert bars, skipping any already present. Returns the count of rows
    actually inserted (not the count of bars passed in)."""
    if not bars:
        return 0
    before = conn.total_changes
    conn.executemany(
        """
        INSERT OR IGNORE INTO bars
            (canonical_symbol, resolution, ts_utc, open, high, low, close,
             tick_volume, spread, real_volume)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                canonical_symbol,
                resolution,
                b.time,
                b.open,
                b.high,
                b.low,
                b.close,
                b.tick_volume,
                b.spread,
                b.real_volume,
            )
            for b in bars
        ],
    )
    return conn.total_changes - before


def insert_ticks(conn: sqlite3.Connection, canonical_symbol: str, ticks: list[Tick]) -> int:
    """Insert ticks, skipping any already present (by canonical_symbol +
    time_msc). Returns the count of rows actually inserted."""
    if not ticks:
        return 0
    before = conn.total_changes
    conn.executemany(
        """
        INSERT OR IGNORE INTO ticks
            (canonical_symbol, ts_utc, ts_msc, bid, ask, last, volume)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [(canonical_symbol, t.time, t.time_msc, t.bid, t.ask, t.last, t.volume) for t in ticks],
    )
    return conn.total_changes - before
