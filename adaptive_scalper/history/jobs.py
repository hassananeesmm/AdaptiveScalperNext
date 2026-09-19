"""Resumable historical-import job checkpoints (directive section 49).

One job row per (canonical_symbol, data_kind, resolution). The bootstrap
orchestrator in `bootstrap.py` resumes a PENDING/IN_PROGRESS job from its
persisted `cursor_utc` rather than `requested_start_utc`, so a restart never
redownloads a full five-year history from scratch.

`resolution` is always `''` (never NULL) for TICK jobs — see
migration 0004's comment on why NULL can't be used as the "no resolution"
marker in a UNIQUE constraint.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

BAR = "BAR"
TICK = "TICK"

PENDING = "PENDING"
IN_PROGRESS = "IN_PROGRESS"
COMPLETE = "COMPLETE"
FAILED = "FAILED"


@dataclass(frozen=True)
class ImportJob:
    id: int
    canonical_symbol: str
    data_kind: str
    resolution: str
    requested_start_utc: int
    requested_end_utc: int
    cursor_utc: int
    status: str
    last_error: str | None


def _row_to_job(row: sqlite3.Row) -> ImportJob:
    return ImportJob(
        id=row["id"],
        canonical_symbol=row["canonical_symbol"],
        data_kind=row["data_kind"],
        resolution=row["resolution"],
        requested_start_utc=row["requested_start_utc"],
        requested_end_utc=row["requested_end_utc"],
        cursor_utc=row["cursor_utc"],
        status=row["status"],
        last_error=row["last_error"],
    )


def get_job(conn: sqlite3.Connection, canonical_symbol: str, data_kind: str, resolution: str = "") -> ImportJob | None:
    row = conn.execute(
        """
        SELECT id, canonical_symbol, data_kind, resolution, requested_start_utc,
               requested_end_utc, cursor_utc, status, last_error
        FROM historical_import_jobs
        WHERE canonical_symbol = ? AND data_kind = ? AND resolution = ?
        """,
        (canonical_symbol, data_kind, resolution),
    ).fetchone()
    return _row_to_job(row) if row else None


def get_or_create_job(
    conn: sqlite3.Connection,
    canonical_symbol: str,
    data_kind: str,
    requested_start_utc: int,
    requested_end_utc: int,
    resolution: str = "",
) -> ImportJob:
    """Return the existing job for this (symbol, kind, resolution), extending
    its `requested_end_utc` (and re-opening it if it had already completed)
    when the caller now wants a later end than before — this is what makes
    an incremental "only sync new data since last time" re-run work.

    Narrowing/widening `requested_start_utc` backward (asking for MORE
    history than an existing job already committed to) is not supported by
    this single-cursor model and raises ValueError rather than silently
    reinterpreting progress — see BUG_BACKLOG.md.
    """
    existing = get_job(conn, canonical_symbol, data_kind, resolution)
    if existing is None:
        conn.execute(
            """
            INSERT INTO historical_import_jobs
                (canonical_symbol, data_kind, resolution, requested_start_utc,
                 requested_end_utc, cursor_utc, status)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (canonical_symbol, data_kind, resolution, requested_start_utc, requested_end_utc, requested_start_utc, PENDING),
        )
        return get_job(conn, canonical_symbol, data_kind, resolution)  # type: ignore[return-value]

    if requested_start_utc < existing.requested_start_utc:
        raise ValueError(
            f"job for {canonical_symbol}/{data_kind}/{resolution!r} already covers "
            f"from {existing.requested_start_utc}; widening the start backward to "
            f"{requested_start_utc} is not supported — create a new job/table if a "
            f"deeper history pull is genuinely needed"
        )

    if requested_end_utc > existing.requested_end_utc:
        new_status = IN_PROGRESS if existing.status == COMPLETE else existing.status
        conn.execute(
            """
            UPDATE historical_import_jobs
            SET requested_end_utc = ?, status = ?, updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            WHERE id = ?
            """,
            (requested_end_utc, new_status, existing.id),
        )
        return get_job(conn, canonical_symbol, data_kind, resolution)  # type: ignore[return-value]

    return existing


def update_cursor(conn: sqlite3.Connection, job_id: int, cursor_utc: int, status: str = IN_PROGRESS) -> None:
    conn.execute(
        """
        UPDATE historical_import_jobs
        SET cursor_utc = ?, status = ?, last_error = NULL,
            updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
        WHERE id = ?
        """,
        (cursor_utc, status, job_id),
    )


def mark_failed(conn: sqlite3.Connection, job_id: int, error: str) -> None:
    conn.execute(
        """
        UPDATE historical_import_jobs
        SET status = ?, last_error = ?, updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
        WHERE id = ?
        """,
        (FAILED, error, job_id),
    )
