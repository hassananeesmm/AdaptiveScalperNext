"""Persisted broker symbol specifications (migration 0025).

Offline research needs contract size, tick size/value, digits and volume
steps; those come from the broker. `save_symbol_spec()` snapshots the
latest `SymbolSpec` whenever a live MT5 session sees one, and
`load_symbol_spec()` reads it back so `backtest`/`walk-forward`/`oos` run
without a terminal. A missing spec is reported, never invented.
"""

from __future__ import annotations

import dataclasses
import json
import sqlite3
import time

from adaptive_scalper.gateway.types import SymbolSpec, SymbolTradeMode


def save_symbol_spec(conn: sqlite3.Connection, canonical_symbol: str, spec: SymbolSpec, *,
                     now_utc: int | None = None) -> None:
    payload = dataclasses.asdict(spec)
    payload["trade_mode"] = spec.trade_mode.name
    conn.execute(
        "INSERT INTO symbol_specs (canonical_symbol, broker_symbol, spec_json, captured_at_utc) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(canonical_symbol) DO UPDATE SET broker_symbol = excluded.broker_symbol, "
        "spec_json = excluded.spec_json, captured_at_utc = excluded.captured_at_utc",
        (canonical_symbol, spec.name, json.dumps(payload, sort_keys=True),
         now_utc if now_utc is not None else int(time.time())),
    )
    conn.commit()


def load_symbol_spec(conn: sqlite3.Connection, canonical_symbol: str) -> tuple[SymbolSpec, int] | None:
    """(spec, captured_at_utc), or None when no live session captured one."""
    row = conn.execute("SELECT spec_json, captured_at_utc FROM symbol_specs WHERE canonical_symbol = ?",
                       (canonical_symbol,)).fetchone()
    if row is None:
        return None
    payload = json.loads(row["spec_json"])
    payload["trade_mode"] = SymbolTradeMode[payload["trade_mode"]]
    known = {f.name for f in dataclasses.fields(SymbolSpec)}
    return SymbolSpec(**{k: v for k, v in payload.items() if k in known}), row["captured_at_utc"]
