"""Dataset integrity (directive section 65).

Every dataset snapshot a backtest, walk-forward fold, or Monte Carlo run
consumes gets its own persisted `DatasetSnapshot` — a real record, not
just an in-memory implicit assumption — so "what exactly did this run
see" is always answerable later, and an OOS slice that has ALREADY been
used (via `dataset_usage`) is never silently reused as if untouched
(directive section 65: "An OOS dataset that influenced design is no
longer untouched").
"""

from __future__ import annotations

import hashlib
import sqlite3
import time
from dataclasses import dataclass

from adaptive_scalper.gateway.types import Bar
from adaptive_scalper.simulation.types import EvidenceOrigin


def compute_bars_checksum(bars: list[Bar]) -> str:
    """A deterministic content checksum over the exact bar sequence a
    dataset snapshot covers — proves later exactly which data a run saw,
    without needing to re-store the bars themselves. SHA-256 over each
    bar's fields in order; any difference (a corrected data point, a
    different range, a different broker source) changes the checksum."""
    hasher = hashlib.sha256()
    for b in bars:
        hasher.update(
            f"{b.time}|{b.open}|{b.high}|{b.low}|{b.close}|{b.tick_volume}|{b.spread}|{b.real_volume}\n".encode("utf-8")
        )
    return hasher.hexdigest()


@dataclass(frozen=True)
class DatasetSnapshot:
    dataset_id: str
    created_at_utc: int
    canonical_symbol: str
    resolution: str
    strategies: tuple[str, ...]
    origin: EvidenceOrigin
    feature_schema_version: int
    row_count: int
    range_start_utc: int
    range_end_utc: int
    checksum: str
    account_scope: str | None = None
    label_version: int | None = None
    excluded_row_count: int = 0
    training_range: tuple[int, int] | None = None
    validation_range: tuple[int, int] | None = None
    oos_range: tuple[int, int] | None = None


def build_dataset_snapshot(
    bars: list[Bar],
    *,
    canonical_symbol: str,
    resolution: str,
    strategies: tuple[str, ...],
    feature_schema_version: int,
    origin: EvidenceOrigin = EvidenceOrigin.HISTORICAL_MT5_REPLAY,
    account_scope: str | None = None,
    excluded_row_count: int = 0,
    training_range: tuple[int, int] | None = None,
    validation_range: tuple[int, int] | None = None,
    oos_range: tuple[int, int] | None = None,
    now_utc: int | None = None,
) -> DatasetSnapshot:
    if not bars:
        raise ValueError("build_dataset_snapshot() requires at least one bar")
    now = now_utc if now_utc is not None else int(time.time())
    checksum = compute_bars_checksum(bars)
    # dataset_id is content-derived (checksum-prefixed) so the SAME exact
    # bar range/content always resolves to the SAME dataset_id -- two
    # runs over identical data share one dataset row, never silently
    # diverge into duplicates.
    dataset_id = f"{canonical_symbol}:{resolution}:{bars[0].time}:{bars[-1].time}:{checksum[:16]}"
    return DatasetSnapshot(
        dataset_id=dataset_id, created_at_utc=now, canonical_symbol=canonical_symbol, resolution=resolution,
        strategies=strategies, origin=origin, feature_schema_version=feature_schema_version,
        row_count=len(bars), range_start_utc=bars[0].time, range_end_utc=bars[-1].time, checksum=checksum,
        account_scope=account_scope, excluded_row_count=excluded_row_count,
        training_range=training_range, validation_range=validation_range, oos_range=oos_range,
    )


def record_dataset(conn: sqlite3.Connection, snapshot: DatasetSnapshot) -> None:
    """Idempotent on `dataset_id` (content-derived, so a repeat call for
    the identical bar range/content is a safe no-op)."""
    import json

    existing = conn.execute("SELECT id FROM datasets WHERE dataset_id = ?", (snapshot.dataset_id,)).fetchone()
    if existing is not None:
        return
    conn.execute(
        """
        INSERT INTO datasets
            (dataset_id, created_at_utc, canonical_symbol, resolution, strategies_json, origin,
             account_scope, feature_schema_version, label_version, row_count, excluded_row_count,
             range_start_utc, range_end_utc, training_range_start_utc, training_range_end_utc,
             validation_range_start_utc, validation_range_end_utc, oos_range_start_utc, oos_range_end_utc,
             checksum)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            snapshot.dataset_id, snapshot.created_at_utc, snapshot.canonical_symbol, snapshot.resolution,
            json.dumps(list(snapshot.strategies)), snapshot.origin.value, snapshot.account_scope,
            snapshot.feature_schema_version, snapshot.label_version, snapshot.row_count,
            snapshot.excluded_row_count, snapshot.range_start_utc, snapshot.range_end_utc,
            snapshot.training_range[0] if snapshot.training_range else None,
            snapshot.training_range[1] if snapshot.training_range else None,
            snapshot.validation_range[0] if snapshot.validation_range else None,
            snapshot.validation_range[1] if snapshot.validation_range else None,
            snapshot.oos_range[0] if snapshot.oos_range else None,
            snapshot.oos_range[1] if snapshot.oos_range else None,
            snapshot.checksum,
        ),
    )
    conn.commit()


def record_dataset_usage(
    conn: sqlite3.Connection, dataset_id: str, used_by_run_id: str, used_for: str, *, now_utc: int | None = None,
) -> None:
    now = now_utc if now_utc is not None else int(time.time())
    conn.execute(
        "INSERT INTO dataset_usage (dataset_id, used_by_run_id, used_for, used_at_utc) VALUES (?, ?, ?, ?)",
        (dataset_id, used_by_run_id, used_for, now),
    )
    conn.commit()


def has_dataset_been_used_as(conn: sqlite3.Connection, dataset_id: str, used_for: str) -> bool:
    """`True` if this exact dataset has EVER been recorded as used for
    `used_for` before (e.g. `'OOS'`) — the guard `run_untouched_oos()`
    (see `oos.py`) uses to refuse reusing a slice that has already
    influenced a design decision."""
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM dataset_usage WHERE dataset_id = ? AND used_for = ?", (dataset_id, used_for),
    ).fetchone()
    return row["n"] > 0


def find_overlapping_usage(
    conn: sqlite3.Connection, canonical_symbol: str, range_start_utc: int, range_end_utc: int, used_for: str,
) -> list[sqlite3.Row]:
    """Every recorded `used_for` usage of ANY dataset of `canonical_symbol`
    whose time range intersects `[range_start_utc, range_end_utc]`,
    regardless of resolution, data provenance or content checksum -- an M1
    training range and an M5 "OOS" range over the same calendar period are
    the same market, a re-downloaded or other-source copy of that period is
    still the data the design already saw, and a one-bar-shifted window is
    not a fresh holdout. Ranges are closed intervals: sharing even one bar
    timestamp is an overlap; a range starting on the next bar is not."""
    return conn.execute(
        """
        SELECT d.dataset_id, d.resolution, d.origin, d.range_start_utc, d.range_end_utc, u.used_by_run_id
        FROM dataset_usage u JOIN datasets d ON d.dataset_id = u.dataset_id
        WHERE d.canonical_symbol = ? AND u.used_for = ?
          AND d.range_start_utc <= ? AND d.range_end_utc >= ?
        ORDER BY d.range_start_utc
        """,
        (canonical_symbol, used_for, range_end_utc, range_start_utc),
    ).fetchall()
