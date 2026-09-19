"""Chunked, resumable historical bootstrap orchestration (directive
sections 46-49).

Design decisions, made explicit per section 49/section 48's "document
exact decision" requirement:

- Bars target a full 5 years (`DEFAULT_BAR_YEARS`) across every resolution
  in `SUPPORTED_BAR_RESOLUTIONS`. Bar volume at M1 for 5 years is a few
  million rows per symbol — comfortably within a local SQLite database.
- Ticks default to a much shorter rolling window (`DEFAULT_TICK_DAYS` = 30),
  not 5 years. Raw MT5 tick history for XAUUSD/GBPJPY/BTCUSD at even one
  year would plausibly run into the hundreds of millions of rows /
  many-GB range — directive section 48 explicitly permits retaining "a
  shorter raw tick horizon" when the full window is impractical, rather
  than pretending it was obtained. This is that documented decision; it is
  configurable per call, not hardcoded policy.
- Each chunk is inserted and its job checkpoint advanced as one immediate
  operation (the project's SQLite connections run in autocommit mode — see
  persistence/database.py). If the process is killed mid-bootstrap, the
  next run re-fetches at most one partially-processed chunk; because
  storage is idempotent (UNIQUE constraint + INSERT OR IGNORE), that
  re-fetch never creates duplicates. This is what satisfies "resume from
  last verified chunk" without needing a separate write-ahead log.
"""

from __future__ import annotations

import sqlite3

from adaptive_scalper.gateway.protocol import Gateway
from adaptive_scalper.history import coverage, jobs, store
from adaptive_scalper.history.resolutions import SUPPORTED_BAR_RESOLUTIONS, resolution_seconds

DEFAULT_BAR_YEARS = 5
DEFAULT_TICK_DAYS = 30

DEFAULT_BAR_CHUNK_SECONDS = 30 * 86400   # 30 days per request
DEFAULT_TICK_CHUNK_SECONDS = 1 * 86400   # 1 day per request


def bootstrap_bars(
    conn: sqlite3.Connection,
    gateway: Gateway,
    canonical_symbol: str,
    broker_symbol: str,
    resolution: str,
    target_start_utc: int,
    target_end_utc: int,
    chunk_seconds: int = DEFAULT_BAR_CHUNK_SECONDS,
) -> jobs.ImportJob:
    """Fetch bars for one (symbol, resolution) up to `target_end_utc`,
    resuming from any existing job's checkpoint. Idempotent and safe to
    call repeatedly (e.g. as a periodic incremental sync)."""
    job = jobs.get_or_create_job(
        conn, canonical_symbol, jobs.BAR, target_start_utc, target_end_utc, resolution=resolution
    )
    if job.status == jobs.COMPLETE:
        return job

    step = resolution_seconds(resolution)
    cursor = job.cursor_utc
    end = job.requested_end_utc
    try:
        while cursor < end:
            chunk_end = min(cursor + chunk_seconds - 1, end)
            bars = gateway.copy_rates_range(broker_symbol, resolution, cursor, chunk_end)
            store.insert_bars(conn, canonical_symbol, resolution, bars)
            # Advance to just past the last bar actually received, never
            # straight to chunk_end+1 — if the broker truncated a large
            # response, this makes the next iteration re-request the
            # remainder of the same chunk instead of silently skipping it.
            next_cursor = (max(b.time for b in bars) + step) if bars else (chunk_end + 1)
            if next_cursor <= cursor:
                raise RuntimeError(
                    f"bar bootstrap made no progress for {canonical_symbol}/{resolution} "
                    f"at cursor={cursor} (broker returned data not past the request start)"
                )
            cursor = next_cursor
            jobs.update_cursor(conn, job.id, cursor, status=jobs.IN_PROGRESS)
            coverage.refresh_bar_coverage(conn, canonical_symbol, resolution)
    except Exception as exc:  # noqa: BLE001 - persist failure reason, then re-raise
        jobs.mark_failed(conn, job.id, str(exc))
        raise

    jobs.update_cursor(conn, job.id, end, status=jobs.COMPLETE)
    return jobs.get_job(conn, canonical_symbol, jobs.BAR, resolution)  # type: ignore[return-value]


def bootstrap_ticks(
    conn: sqlite3.Connection,
    gateway: Gateway,
    canonical_symbol: str,
    broker_symbol: str,
    target_start_utc: int,
    target_end_utc: int,
    chunk_seconds: int = DEFAULT_TICK_CHUNK_SECONDS,
) -> jobs.ImportJob:
    """Same as bootstrap_bars but for raw ticks (no resolution)."""
    job = jobs.get_or_create_job(conn, canonical_symbol, jobs.TICK, target_start_utc, target_end_utc, resolution="")
    if job.status == jobs.COMPLETE:
        return job

    cursor = job.cursor_utc
    end = job.requested_end_utc
    try:
        while cursor < end:
            chunk_end = min(cursor + chunk_seconds - 1, end)
            ticks = gateway.copy_ticks_range(broker_symbol, cursor, chunk_end)
            store.insert_ticks(conn, canonical_symbol, ticks)
            # Same truncation defense as bootstrap_bars: advance only past
            # the last tick's whole second, not straight to chunk_end+1.
            next_cursor = (max(t.time for t in ticks) + 1) if ticks else (chunk_end + 1)
            if next_cursor <= cursor:
                raise RuntimeError(
                    f"tick bootstrap made no progress for {canonical_symbol} "
                    f"at cursor={cursor} (broker returned data not past the request start)"
                )
            cursor = next_cursor
            jobs.update_cursor(conn, job.id, cursor, status=jobs.IN_PROGRESS)
            coverage.refresh_tick_coverage(conn, canonical_symbol)
    except Exception as exc:  # noqa: BLE001
        jobs.mark_failed(conn, job.id, str(exc))
        raise

    jobs.update_cursor(conn, job.id, end, status=jobs.COMPLETE)
    return jobs.get_job(conn, canonical_symbol, jobs.TICK, "")  # type: ignore[return-value]


def bootstrap_symbol(
    conn: sqlite3.Connection,
    gateway: Gateway,
    canonical_symbol: str,
    broker_symbol: str,
    now_utc: int,
    resolutions: tuple[str, ...] = SUPPORTED_BAR_RESOLUTIONS,
    bar_years: int = DEFAULT_BAR_YEARS,
    include_ticks: bool = True,
    tick_days: int = DEFAULT_TICK_DAYS,
) -> dict[str, jobs.ImportJob]:
    """Bootstrap every configured bar resolution plus (optionally) the tick
    window for one symbol. Returns the resulting job per data stream, keyed
    by resolution string (and "TICK" for the tick job)."""
    bar_start = now_utc - bar_years * 365 * 86400
    results: dict[str, jobs.ImportJob] = {}
    for resolution in resolutions:
        results[resolution] = bootstrap_bars(
            conn, gateway, canonical_symbol, broker_symbol, resolution, bar_start, now_utc
        )
    if include_ticks:
        tick_start = now_utc - tick_days * 86400
        results["TICK"] = bootstrap_ticks(conn, gateway, canonical_symbol, broker_symbol, tick_start, now_utc)
    return results


def bootstrap_all(
    conn: sqlite3.Connection,
    gateway: Gateway,
    canonical_to_broker: dict[str, str],
    now_utc: int,
    resolutions: tuple[str, ...] = SUPPORTED_BAR_RESOLUTIONS,
    bar_years: int = DEFAULT_BAR_YEARS,
    include_ticks: bool = True,
    tick_days: int = DEFAULT_TICK_DAYS,
) -> dict[str, dict[str, jobs.ImportJob]]:
    """Bootstrap every resolved symbol. Only symbols present in
    `canonical_to_broker` (i.e. already successfully broker-resolved — see
    gateway/symbol_resolver.py) are attempted; an unresolved canonical
    symbol has no broker name to request history for and is silently
    skipped here, not a bootstrap failure."""
    return {
        canonical: bootstrap_symbol(
            conn, gateway, canonical, broker_symbol, now_utc,
            resolutions=resolutions, bar_years=bar_years,
            include_ticks=include_ticks, tick_days=tick_days,
        )
        for canonical, broker_symbol in canonical_to_broker.items()
    }
