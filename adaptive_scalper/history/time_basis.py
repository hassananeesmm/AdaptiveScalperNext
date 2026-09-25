"""Time basis of stored MT5-derived rows (BUG_BACKLOG #14).

Schema 27 marks a database that held MT5 rows before the gateway converted
server time to UTC as SERVER_UNCONVERTED. `require_utc` is the guard every
path that reads or appends those rows calls, so server-time and UTC rows
are never mixed. `convert_server_time_to_utc` is the one-shot operator
conversion (`history convert-server-time`): it backs the database up with
SQLite's online backup API, then converts every MT5-derived time column in
one transaction.

Audit records (journal events, decision chains, runtime events) are never
rewritten: they stay exactly as recorded, and the conversion's `detail`
says so.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from adaptive_scalper.gateway.server_time import (
    is_skipped_server_time,
    server_ms_to_utc_ms,
    server_to_utc,
    validate_rule,
)

UTC = "UTC"
SERVER_UNCONVERTED = "SERVER_UNCONVERTED"

# (table, time columns). `bars` and `ticks` carry UNIQUE keys on a time
# column, handled by the two-phase update below.
_PLAIN_COLUMNS = (
    ("broker_account_orders", ("time_setup_utc", "time_done_utc")),
    ("broker_account_deals", ("time_utc",)),
    ("historical_import_jobs", ("requested_start_utc", "requested_end_utc", "cursor_utc")),
    ("historical_bar_coverage", ("earliest_utc", "latest_utc")),
    ("historical_tick_coverage", ("earliest_utc", "latest_utc")),
)


class TimeBasisError(RuntimeError):
    """Stored MT5 rows are still in broker server time."""


def get_basis(conn: sqlite3.Connection) -> dict:
    row = conn.execute("SELECT basis, rule, converted_at_utc, detail FROM mt5_time_basis WHERE id = 1").fetchone()
    return {"basis": row[0], "rule": row[1], "converted_at_utc": row[2], "detail": row[3]}


def require_utc(conn: sqlite3.Connection) -> None:
    if get_basis(conn)["basis"] != UTC:
        raise TimeBasisError(
            "stored MT5 bars/ticks/broker history are still in broker SERVER time (recorded before schema 27); "
            "run `python -m adaptive_scalper.cli history convert-server-time` once (it backs the database up first)"
        )


def _backup(conn: sqlite3.Connection, db_path: str, backup_dir: Path, now_utc: int) -> Path:
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.fromtimestamp(now_utc, tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = backup_dir / f"{Path(db_path).stem}.pre-server-time-conversion.{stamp}.sqlite3"
    dst = sqlite3.connect(target)
    try:
        conn.backup(dst)
        if dst.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise TimeBasisError(f"backup {target} failed its integrity check; nothing converted")
    finally:
        dst.close()
    return target


def convert_server_time_to_utc(
    conn: sqlite3.Connection, rule: str, *, db_path: str, backup_dir: Path, now_utc: int,
) -> dict:
    """Back up, then convert every MT5-derived time column from `rule`'s
    server clock to UTC in one transaction. Refuses a database already UTC."""
    validate_rule(rule)
    basis = get_basis(conn)
    if basis["basis"] == UTC:
        raise TimeBasisError(f"already UTC ({basis['detail']}); refusing to convert twice")
    backup = _backup(conn, db_path, backup_dir, now_utc)

    conn.create_function("asn_s2u", 1, lambda v: None if v is None else server_to_utc(rule, v), deterministic=True)
    conn.create_function("asn_skipped", 1, lambda v: int(is_skipped_server_time(rule, v)), deterministic=True)
    conn.create_function("asn_ms2u", 1, lambda v: None if v is None else server_ms_to_utc_ms(rule, v),
                         deterministic=True)
    counts: dict[str, int] = {}
    conn.execute("BEGIN IMMEDIATE")
    try:
        # Server times in the hour the clock skips at the spring DST change
        # have no UTC instant: move them, unchanged, to the quarantine tables
        # (schema 28) rather than guess a time or delete them.
        reason = "server time inside the skipped spring DST hour: no UTC instant"
        counts["bars_quarantined"] = conn.execute(
            "INSERT INTO bars_unconvertible (original_id, canonical_symbol, resolution, server_ts, open, high, low, "
            "close, tick_volume, spread, real_volume, rule, reason, moved_at_utc) "
            "SELECT id, canonical_symbol, resolution, ts_utc, open, high, low, close, tick_volume, spread, "
            "real_volume, ?, ?, ? FROM bars WHERE asn_skipped(ts_utc)", (rule, reason, now_utc)).rowcount
        conn.execute("DELETE FROM bars WHERE asn_skipped(ts_utc)")
        counts["ticks_quarantined"] = conn.execute(
            "INSERT INTO ticks_unconvertible (original_id, canonical_symbol, server_ts, server_ts_msc, bid, ask, "
            "last, volume, rule, reason, moved_at_utc) "
            "SELECT id, canonical_symbol, ts_utc, ts_msc, bid, ask, last, volume, ?, ?, ? FROM ticks "
            "WHERE asn_skipped(ts_utc)", (rule, reason, now_utc)).rowcount
        conn.execute("DELETE FROM ticks WHERE asn_skipped(ts_utc)")
        # Two phases so no row transiently collides with a not-yet-shifted
        # neighbour on the UNIQUE time keys: move every key to the negative
        # range first, then to its converted value (with the skipped hour
        # removed, server_to_utc maps distinct server times to distinct UTC
        # times, so the final keys are unique too).
        counts["bars"] = conn.execute("UPDATE bars SET ts_utc = -ts_utc - 1").rowcount
        conn.execute("UPDATE bars SET ts_utc = asn_s2u(-ts_utc - 1)")
        counts["ticks"] = conn.execute("UPDATE ticks SET ts_msc = -ts_msc - 1").rowcount
        conn.execute("UPDATE ticks SET ts_msc = asn_ms2u(-ts_msc - 1), ts_utc = asn_s2u(ts_utc)")
        for table, columns in _PLAIN_COLUMNS:
            assignments = ", ".join(f"{c} = asn_s2u({c})" for c in columns)
            counts[table] = conn.execute(f"UPDATE {table} SET {assignments}").rowcount
        detail = (f"converted from {rule} at {now_utc}; backup {backup}; rows {counts}; "
                  "journal/decision/runtime audit records left as recorded")
        conn.execute("UPDATE mt5_time_basis SET basis = ?, rule = ?, converted_at_utc = ?, detail = ? WHERE id = 1",
                     (UTC, rule, now_utc, detail))
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return {"status": "CONVERTED", "rule": rule, "backup": str(backup), "rows": counts}
