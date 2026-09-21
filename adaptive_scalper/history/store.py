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


def get_bars(
    conn: sqlite3.Connection, canonical_symbol: str, resolution: str, from_utc: int, to_utc: int,
) -> list[Bar]:
    """Read back stored bars for one symbol/resolution/range, strictly
    ascending by time (the `UNIQUE(canonical_symbol, resolution, ts_utc)`
    constraint means there is at most one row per timestamp, so no
    dedup step is needed here) — the read half of `insert_bars()`, used
    by `adaptive_scalper/backtest/` and any other offline-research
    consumer of the historical bar store."""
    rows = conn.execute(
        """
        SELECT ts_utc, open, high, low, close, tick_volume, spread, real_volume
        FROM bars
        WHERE canonical_symbol = ? AND resolution = ? AND ts_utc >= ? AND ts_utc <= ?
        ORDER BY ts_utc ASC
        """,
        (canonical_symbol, resolution, from_utc, to_utc),
    ).fetchall()
    return [
        Bar(
            time=r["ts_utc"], open=r["open"], high=r["high"], low=r["low"], close=r["close"],
            tick_volume=r["tick_volume"], spread=r["spread"], real_volume=r["real_volume"],
        )
        for r in rows
    ]


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
