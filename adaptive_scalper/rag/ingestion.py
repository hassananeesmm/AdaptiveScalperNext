"""Journal -> RAG ingestion (directive sections 57-58).

Runs OFF the trading hot path (a slow scheduled task / `rag ingest`):
reads only rows newer than each source's watermark and converts them into
compact typed memories, each carrying `source_key` (the authoritative row
it came from) and `origin` (its evidence class). Idempotent: re-running
never duplicates a memory (unique `source_key`).

Mapping (all eight memory types):

    journal ENTRY_BLOCKED / SIGNAL_REJECTED /
            PROPOSAL_REJECTED                       -> REJECTION (DECISION)
    journal ENTRY_BLOCKED with BLOCK_REENTRY_CHURN,
            REENTRY_*                               -> REENTRY_DECISION (DECISION)
    journal POSITION_OPENED                         -> TRADE_SETUP (BROKER_DEMO_CONFIRMED)
    journal POSITION_CLOSED                         -> TRADE_RESULT (BROKER_DEMO_CONFIRMED)
    journal POSITION_REVIEWED FULL_CLOSE,
            STOP_ADVANCED                           -> EXIT_DECISION (BROKER_DEMO_CONFIRMED)
    journal ORDER_UNKNOWN / ORDER_REJECTED,
            execution_incidents                     -> EXECUTION_INCIDENT (EXECUTION)
    paper_trades                                    -> TRADE_RESULT (PAPER_LIVE_DATA)
    research_trials (COMPLETED)                     -> STRATEGY_CONTEXT (BACKTEST)
    runtime_events ERROR / CRITICAL                 -> SYSTEM_EVENT (SYSTEM)

A rejected proposal is never recorded as a losing trade (directive
section 57): REJECTION and TRADE_RESULT are distinct types. Memory text
never contains credentials or account identifiers -- only the fields
named below.
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass, field

from adaptive_scalper.rag.store import store_memory

ORIGIN_DEMO = "BROKER_DEMO_CONFIRMED"
ORIGIN_PAPER = "PAPER_LIVE_DATA"
ORIGIN_BACKTEST = "BACKTEST"
ORIGIN_DECISION = "DECISION"
ORIGIN_EXECUTION = "EXECUTION"
ORIGIN_SYSTEM = "SYSTEM"


@dataclass
class IngestionReport:
    inserted: dict[str, int] = field(default_factory=dict)
    scanned: dict[str, int] = field(default_factory=dict)

    def add(self, memory_type: str) -> None:
        self.inserted[memory_type] = self.inserted.get(memory_type, 0) + 1


def _watermark(conn: sqlite3.Connection, source: str) -> int:
    row = conn.execute("SELECT last_id FROM rag_ingestion_state WHERE source = ?", (source,)).fetchone()
    return row["last_id"] if row else 0


def _advance(conn: sqlite3.Connection, source: str, last_id: int, now: int) -> None:
    conn.execute(
        "INSERT INTO rag_ingestion_state (source, last_id, updated_at_utc) VALUES (?, ?, ?) "
        "ON CONFLICT(source) DO UPDATE SET last_id = excluded.last_id, updated_at_utc = excluded.updated_at_utc",
        (source, last_id, now),
    )
    conn.commit()


def _fmt(value, digits: int = 2) -> str:
    return f"{value:.{digits}f}" if isinstance(value, (int, float)) else "n/a"


def _entry_context(conn: sqlite3.Connection, broker_position_id) -> dict:
    if broker_position_id is None:
        return {}
    row = conn.execute("SELECT * FROM position_entry_context WHERE broker_position_id = ?",
                       (str(broker_position_id),)).fetchone()
    if row is None:
        return {}
    return {k: row[k] for k in ("strategy_key", "strategy_version", "direction", "entry_regime", "raw_confidence",
                                "stop_distance_price", "target_distance_price", "signal_bar_time_utc")}


def _journal_memory(conn: sqlite3.Connection, row: sqlite3.Row) -> tuple[str, str, str, dict] | None:
    """(memory_type, origin, text, metadata) for one journal event, or None."""
    p = json.loads(row["payload_json"])
    et, symbol, strategy = row["event_type"], row["canonical_symbol"], row["strategy_key"] or "unknown_strategy"
    base = {"journal_event_id": row["id"], "chain_key": row["chain_key"], "event_type": et}
    if et in ("ENTRY_BLOCKED", "SIGNAL_REJECTED", "PROPOSAL_REJECTED"):
        decision = p.get("decision") or et
        reason = p.get("reason", "")
        memory_type = "REENTRY_DECISION" if decision == "BLOCK_REENTRY_CHURN" else "REJECTION"
        text = f"{symbol} {strategy} {p.get('direction', '')} rejected {decision}: {reason}"[:600]
        return memory_type, ORIGIN_DECISION, text, {**base, "decision": decision, "reason": reason}
    if et.startswith("REENTRY_"):
        return "REENTRY_DECISION", ORIGIN_DECISION, f"{symbol} {strategy} {et}: {p.get('reason', '')}"[:600], {**base, **p}
    if et == "POSITION_OPENED":
        ctx = _entry_context(conn, row["broker_position_id"])
        strategy = ctx.get("strategy_key") or strategy
        text = (f"{symbol} {strategy} {ctx.get('direction', '')} regime {ctx.get('entry_regime', 'unknown')} "
                f"position opened volume {_fmt(p.get('volume'))} at {_fmt(p.get('price'), 5)} "
                f"initial risk {_fmt(p.get('initial_monetary_risk'))}")
        return "TRADE_SETUP", ORIGIN_DEMO, text, {**base, **{k: p.get(k) for k in ("volume", "price", "initial_monetary_risk")},
                                                   **ctx, "broker_position_id": row["broker_position_id"]}
    if et == "POSITION_CLOSED":
        profit = p.get("total_profit")
        text = (f"{symbol} position closed: {p.get('reason', '')} profit {_fmt(profit)} "
                f"commission {_fmt(p.get('total_commission'))} swap {_fmt(p.get('total_swap'))}")
        return "TRADE_RESULT", ORIGIN_DEMO, text, {**base, **p, "broker_position_id": row["broker_position_id"]}
    if et == "POSITION_REVIEWED" and p.get("selected_action") == "FULL_CLOSE":
        text = (f"{symbol} {strategy} exit decision FULL_CLOSE current_r {_fmt(p.get('current_r'))} peak_r "
                f"{_fmt(p.get('peak_r'))} regime {p.get('entry_regime')}->{p.get('current_regime')}: {p.get('reason', '')}")[:600]
        return "EXIT_DECISION", ORIGIN_DEMO, text, {**base, **p}
    if et == "STOP_ADVANCED":
        text = f"{symbol} {strategy} protective stop advanced at current_r {_fmt(p.get('current_r'))}: {p.get('reason', '')}"
        return "EXIT_DECISION", ORIGIN_DEMO, text[:600], {**base, **p}
    if et in ("ORDER_UNKNOWN", "ORDER_REJECTED"):
        text = f"{symbol} order {et}: {p.get('detail') or p.get('comment') or p.get('retcode', '')}"
        return "EXECUTION_INCIDENT", ORIGIN_EXECUTION, text[:600], {**base, **p}
    return None


def ingest(conn: sqlite3.Connection, *, now_utc: int | None = None, batch_limit: int = 5000) -> IngestionReport:
    now = now_utc if now_utc is not None else int(time.time())
    report = IngestionReport()

    last = _watermark(conn, "journal")
    rows = conn.execute(
        "SELECT je.*, dc.chain_key FROM journal_events je JOIN decision_chains dc ON dc.id = je.chain_id "
        "WHERE je.id > ? ORDER BY je.id LIMIT ?", (last, batch_limit),
    ).fetchall()
    report.scanned["journal"] = len(rows)
    for row in rows:
        mapped = _journal_memory(conn, row)
        if mapped is not None:
            memory_type, origin, text, metadata = mapped
            store_memory(conn, memory_type, text, metadata, canonical_symbol=row["canonical_symbol"],
                         strategy_key=row["strategy_key"], chain_key=row["chain_key"], now_utc=now,
                         source_key=f"journal:{row['id']}", origin=origin)
            report.add(memory_type)
        last = row["id"]
    if rows:
        _advance(conn, "journal", last, now)

    last = _watermark(conn, "paper_trades")
    rows = conn.execute("SELECT * FROM paper_trades WHERE id > ? ORDER BY id LIMIT ?", (last, batch_limit)).fetchall()
    report.scanned["paper_trades"] = len(rows)
    for row in rows:
        text = (f"{row['canonical_symbol']} {row['strategy_key']} {row['direction']} regime {row['entry_regime']} "
                f"-> {row['exit_reason']} R {_fmt(row['realized_r'])} net {_fmt(row['realized_pnl'])}")
        store_memory(conn, "TRADE_RESULT", text[:600], {
            "paper_trade_id": row["id"], "session_key": row["session_key"], "realized_r": row["realized_r"],
            "realized_pnl": row["realized_pnl"], "total_cost": row["total_cost"], "exit_reason": row["exit_reason"],
            "cost_provenance": row["cost_provenance"], "entry_time_utc": row["entry_time_utc"],
        }, canonical_symbol=row["canonical_symbol"], strategy_key=row["strategy_key"], now_utc=now,
            source_key=f"paper_trade:{row['id']}", origin=ORIGIN_PAPER)
        report.add("TRADE_RESULT")
        last = row["id"]
    if rows:
        _advance(conn, "paper_trades", last, now)

    last = _watermark(conn, "execution_incidents")
    rows = conn.execute("SELECT * FROM execution_incidents WHERE id > ? ORDER BY id LIMIT ?", (last, batch_limit)).fetchall()
    report.scanned["execution_incidents"] = len(rows)
    for row in rows:
        store_memory(conn, "EXECUTION_INCIDENT", f"execution incident {row['incident_type']}: {row['detail']}"[:600], {
            "incident_id": row["id"], "incident_type": row["incident_type"], "order_id": row["order_id"],
        }, now_utc=now, source_key=f"incident:{row['id']}", origin=ORIGIN_EXECUTION)
        report.add("EXECUTION_INCIDENT")
        last = row["id"]
    if rows:
        _advance(conn, "execution_incidents", last, now)

    last = _watermark(conn, "research_trials")
    rows = conn.execute("SELECT * FROM research_trials WHERE id > ? ORDER BY id LIMIT ?", (last, batch_limit)).fetchall()
    report.scanned["research_trials"] = len(rows)
    for row in rows:
        if row["status"] == "COMPLETED":
            text = (f"research {row['family']} {row['kind']} {row['strategy_versions_json']} sharpe "
                    f"{_fmt(row['sharpe'], 3)} n {row['n_observations']}")
            store_memory(conn, "STRATEGY_CONTEXT", text[:600], {
                "trial_id": row["trial_id"], "family": row["family"], "dataset_id": row["dataset_id"],
                "sharpe": row["sharpe"], "n_observations": row["n_observations"],
            }, now_utc=now, source_key=f"trial:{row['trial_id']}", origin=ORIGIN_BACKTEST)
            report.add("STRATEGY_CONTEXT")
        last = row["id"]
    if rows:
        _advance(conn, "research_trials", last, now)

    last = _watermark(conn, "runtime_events")
    rows = conn.execute("SELECT * FROM runtime_events WHERE id > ? ORDER BY id LIMIT ?", (last, batch_limit)).fetchall()
    report.scanned["runtime_events"] = len(rows)
    for row in rows:
        if row["severity"] in ("ERROR", "CRITICAL"):
            store_memory(conn, "SYSTEM_EVENT", f"{row['severity']} {row['component']} {row['event']}: {row['detail']}"[:600], {
                "runtime_event_id": row["id"], "severity": row["severity"], "component": row["component"],
            }, canonical_symbol=row["canonical_symbol"], now_utc=now, source_key=f"runtime_event:{row['id']}",
                origin=ORIGIN_SYSTEM)
            report.add("SYSTEM_EVENT")
        last = row["id"]
    if rows:
        _advance(conn, "runtime_events", last, now)
    return report
