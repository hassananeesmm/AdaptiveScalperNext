"""Runtime state published for observers (migration `0022_runtime`).

Everything the dashboard and CLI show about a RUNNING engine comes from
here -- they never talk to MT5 themselves -- so an observer crash or a
closed browser can never touch trading, and an engine crash leaves the
last-known state plus a stale heartbeat, never a fabricated "running".
"""

from __future__ import annotations

import json
import sqlite3
import time

SEVERITIES = ("INFO", "WARNING", "BLOCKED", "ERROR", "CRITICAL")


def put_state(conn: sqlite3.Connection, key: str, value, *, now_utc: int | None = None) -> None:
    now = now_utc if now_utc is not None else int(time.time())
    conn.execute(
        "INSERT INTO runtime_state (key, value_json, updated_at_utc) VALUES (?, ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json, updated_at_utc = excluded.updated_at_utc",
        (key, json.dumps(value, default=str, sort_keys=True), now),
    )


def get_state(conn: sqlite3.Connection, key: str, default=None):
    row = conn.execute("SELECT value_json FROM runtime_state WHERE key = ?", (key,)).fetchone()
    return json.loads(row["value_json"]) if row is not None else default


def get_state_with_age(conn: sqlite3.Connection, key: str, *, now_utc: int | None = None):
    """(value, seconds since last update) or (None, None)."""
    now = now_utc if now_utc is not None else int(time.time())
    row = conn.execute("SELECT value_json, updated_at_utc FROM runtime_state WHERE key = ?", (key,)).fetchone()
    if row is None:
        return None, None
    return json.loads(row["value_json"]), now - row["updated_at_utc"]


def record_event(
    conn: sqlite3.Connection, severity: str, component: str, event: str, detail: str, *,
    canonical_symbol: str | None = None, dedup_key: str | None = None, now_utc: int | None = None,
) -> int:
    """A still-open event with the same `dedup_key` is updated in place
    (occurrence_count/last_seen), never duplicated every cycle."""
    if severity not in SEVERITIES:
        raise ValueError(f"severity must be one of {SEVERITIES}, got {severity!r}")
    now = now_utc if now_utc is not None else int(time.time())
    if dedup_key is not None:
        row = conn.execute(
            "SELECT id FROM runtime_events WHERE dedup_key = ? AND cleared_at_utc IS NULL", (dedup_key,),
        ).fetchone()
        if row is not None:
            conn.execute(
                "UPDATE runtime_events SET last_seen_at_utc = ?, occurrence_count = occurrence_count + 1, "
                "detail = ?, severity = ? WHERE id = ?", (now, detail, severity, row["id"]),
            )
            return row["id"]
    cursor = conn.execute(
        "INSERT INTO runtime_events (severity, component, event, canonical_symbol, detail, dedup_key, "
        "first_seen_at_utc, last_seen_at_utc) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (severity, component, event, canonical_symbol, detail, dedup_key, now, now),
    )
    return cursor.lastrowid


def clear_event(conn: sqlite3.Connection, dedup_key: str, *, now_utc: int | None = None) -> bool:
    """Close an ongoing condition (e.g. a news block ended). True if one was open."""
    now = now_utc if now_utc is not None else int(time.time())
    cursor = conn.execute(
        "UPDATE runtime_events SET cleared_at_utc = ? WHERE dedup_key = ? AND cleared_at_utc IS NULL",
        (now, dedup_key),
    )
    return cursor.rowcount > 0


def recent_events(conn: sqlite3.Connection, limit: int = 100) -> list[dict]:
    rows = conn.execute("SELECT * FROM runtime_events ORDER BY last_seen_at_utc DESC, id DESC LIMIT ?", (limit,))
    return [dict(r) for r in rows]


def record_entry_decision(
    conn: sqlite3.Connection, *, mode: str, stage: str, decision: str, reason: str,
    canonical_symbol: str | None = None, bar_time_utc: int | None = None, strategy_key: str | None = None,
    direction: str | None = None, chain_key: str | None = None, detail: dict | None = None,
    now_utc: int | None = None,
) -> None:
    now = now_utc if now_utc is not None else int(time.time())
    conn.execute(
        "INSERT INTO entry_decisions (decided_at_utc, mode, canonical_symbol, bar_time_utc, strategy_key, direction, "
        "stage, decision, reason, chain_key, detail_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (now, mode, canonical_symbol, bar_time_utc, strategy_key, direction, stage, decision, reason, chain_key,
         json.dumps(detail, default=str, sort_keys=True) if detail is not None else None),
    )


def decision_counts(conn: sqlite3.Connection, *, since_utc: int, mode: str | None = None) -> dict[str, int]:
    sql = "SELECT decision, COUNT(*) AS n FROM entry_decisions WHERE decided_at_utc >= ?"
    params: list = [since_utc]
    if mode:
        sql += " AND mode = ?"
        params.append(mode)
    return {r["decision"]: r["n"] for r in conn.execute(sql + " GROUP BY decision ORDER BY n DESC", params)}


def record_position_entry_context(
    conn: sqlite3.Connection, *, broker_position_id: str, canonical_symbol: str, strategy_key: str,
    strategy_version: int | None, direction: str, entry_regime: str, raw_confidence: float | None,
    stop_distance_price: float, target_distance_price: float, signal_bar_time_utc: int | None,
    chain_key: str | None, now_utc: int | None = None,
) -> None:
    now = now_utc if now_utc is not None else int(time.time())
    conn.execute(
        "INSERT OR IGNORE INTO position_entry_context (broker_position_id, canonical_symbol, strategy_key, "
        "strategy_version, direction, entry_regime, raw_confidence, stop_distance_price, target_distance_price, "
        "signal_bar_time_utc, chain_key, recorded_at_utc) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (broker_position_id, canonical_symbol, strategy_key, strategy_version, direction, entry_regime,
         raw_confidence, stop_distance_price, target_distance_price, signal_bar_time_utc, chain_key, now),
    )


def get_position_entry_context(conn: sqlite3.Connection, broker_position_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM position_entry_context WHERE broker_position_id = ?", (str(broker_position_id),),
    ).fetchone()
