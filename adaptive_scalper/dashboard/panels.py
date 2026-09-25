"""Dashboard panels: read-only queries over the SQLite database.

The dashboard is an OBSERVER (directive section 104). It never connects to
MT5 -- the runtime process is the only MT5 client, and terminal/broker
state reaches the dashboard through `runtime_state` (heartbeat, component
health, why-no-trade, reconciliation). Each panel is computed
independently; a panel that fails, or whose data does not exist yet,
reports `UNAVAILABLE` / `NO_DATA` with a reason instead of a fabricated
value, and never takes the other panels down.
"""

from __future__ import annotations

import json
import sqlite3
import time
from typing import Callable

from adaptive_scalper.config.constants import ALLOWED_CANONICAL_SYMBOLS, RETIRED_STRATEGY_KEYS
from adaptive_scalper.core.kill_switch import get_state as kill_switch_state
from adaptive_scalper.dashboard.health import compute_health

STALE_HEARTBEAT_SECONDS = 15
OPEN_ORDER_STATES = ("SUBMITTED", "ACCEPTED", "PENDING", "RESTING", "PARTIAL", "UNKNOWN", "PENDING_RECONCILIATION")


def _state(conn: sqlite3.Connection, key: str, now: int):
    row = conn.execute("SELECT value_json, updated_at_utc FROM runtime_state WHERE key = ?", (key,)).fetchone()
    return (json.loads(row["value_json"]), now - row["updated_at_utc"]) if row else (None, None)


def _rows(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> list[dict]:
    return [dict(r) for r in conn.execute(sql, params)]


def overview(conn: sqlite3.Connection, now: int) -> dict:
    health = compute_health(conn)
    ks = kill_switch_state(conn)
    engine, age = _state(conn, "engine", now)
    if engine is None:
        runtime = {"status": "NOT_STARTED", "detail": "no runtime has run against this database"}
    elif engine.get("state") == "STOPPED":
        runtime = {"status": "STOPPED", **engine}
    elif age is not None and age > STALE_HEARTBEAT_SECONDS:
        runtime = {"status": "STALE", "detail": f"no heartbeat for {age}s -- runtime not running or hung", **engine}
    else:
        runtime = {"status": engine.get("state"), "heartbeat_age_seconds": age, **engine}
    return {
        "real_money_execution": "DISABLED", "health": health.state.value, "reasons": list(health.reasons),
        "kill_switch": {"status": ks.status.value, "blocks_new_entries": ks.blocks_new_entries, "reason": ks.reason,
                        "changed_by": ks.changed_by, "changed_at": ks.changed_at},
        "runtime": runtime,
    }


def components(conn: sqlite3.Connection, now: int) -> dict:
    value, age = _state(conn, "components", now)
    if value is None:
        return {"status": "NO_DATA", "detail": "the runtime has not reported component health yet"}
    return {"age_seconds": age, "components": value}


def symbols(conn: sqlite3.Connection, now: int) -> dict:
    why, age = _state(conn, "why_no_trade", now)
    mapping = {r["canonical"]: r for r in _rows(conn, "SELECT * FROM symbol_mapping")}
    specs = {r["canonical_symbol"]: r["captured_at_utc"] for r in _rows(conn, "SELECT * FROM symbol_specs")}
    out = {}
    for symbol in sorted(ALLOWED_CANONICAL_SYMBOLS):
        regime, _ = _state(conn, f"regime:{symbol}", now)
        m = mapping.get(symbol)
        out[symbol] = {
            "broker_symbol": m["broker_symbol"] if m else None, "resolved": bool(m["resolved"]) if m else None,
            "valid": m["valid"] if m else None, "spec_captured_at_utc": specs.get(symbol),
            "confirmed_regime": regime.get("confirmed") if regime else None,
            "latest_cycle": (why or {}).get("symbols", {}).get(symbol),
        }
    return {"cycle_age_seconds": age, "global_block": (why or {}).get("global_block"), "symbols": out}


def positions(conn: sqlite3.Connection, now: int) -> dict:
    demo = _rows(conn, "SELECT p.broker_position_id, p.canonical_symbol, p.direction, p.volume, p.entry_price, "
                       "p.initial_monetary_risk, p.strategy_key, p.opened_at_utc, pms.peak_r, pms.current_r, "
                       "pms.latest_regime, pms.last_review_at_utc "
                       "FROM positions p LEFT JOIN position_management_state pms ON pms.position_id = p.id "
                       "WHERE p.status = 'OPEN' ORDER BY p.opened_at_utc")
    paper = []
    for r in _rows(conn, "SELECT session_key, canonical_symbol, equity, open_position_json, pending_entry_json "
                         "FROM paper_session_state ORDER BY session_key"):
        pos = json.loads(r["open_position_json"]) if r["open_position_json"] else None
        paper.append({"session_key": r["session_key"], "equity": r["equity"],
                      "open_position": None if pos is None else {k: pos.get(k) for k in (
                          "strategy_key", "direction", "entry_price", "stop_price", "target_price", "peak_r",
                          "initial_monetary_risk")},
                      "pending_entry": r["pending_entry_json"] is not None})
    return {"demo_open_positions": demo, "paper_sessions": paper}


def orders(conn: sqlite3.Connection, now: int) -> dict:
    marks = ", ".join("?" * len(OPEN_ORDER_STATES))
    active = _rows(conn, f"SELECT id, canonical_symbol, direction, requested_volume, state, last_broker_retcode, "  # nosec B608 - placeholders only
                         f"updated_at_utc FROM orders WHERE state IN ({marks}) ORDER BY updated_at_utc DESC",
                   OPEN_ORDER_STATES)
    dangerous = [o for o in active if o["state"] in ("UNKNOWN", "PENDING_RECONCILIATION")]
    reconciliation, age = _state(conn, "reconciliation", now)
    return {"active_orders": active, "dangerous_unknown": dangerous, "reconciliation": reconciliation,
            "reconciliation_age_seconds": age}


def decisions(conn: sqlite3.Connection, now: int) -> dict:
    counts = {r["decision"]: r["n"] for r in conn.execute(
        "SELECT decision, COUNT(*) AS n FROM entry_decisions WHERE decided_at_utc >= ? GROUP BY decision "
        "ORDER BY n DESC", (now - 86400,))}
    recent = _rows(conn, "SELECT decided_at_utc, mode, canonical_symbol, stage, decision, reason, strategy_key, "
                         "direction FROM entry_decisions ORDER BY id DESC LIMIT 30")
    return {"counts_24h": counts, "recent": recent}


def risk(conn: sqlite3.Connection, now: int) -> dict:
    """Observed risk from local exposure and a sampled DEMO account snapshot.

    Local open/pending risk is an estimate, not proof of broker stop coverage.
    Never suggest that a stale broker snapshot represents current equity.
    """
    peak, _ = _state(conn, "peak_equity", now)
    limits, _ = _state(conn, "risk_limits", now)
    telemetry, age = _state(conn, "live_telemetry", now)
    fresh = bool(telemetry and age is not None and age <= 15
                 and telemetry.get("terminal", {}).get("connected")
                 and (telemetry.get("account") or {}).get("trade_mode") == "DEMO")
    account = telemetry.get("account") if fresh else None
    open_risk = conn.execute(
        "SELECT COALESCE(SUM(MAX(0, initial_monetary_risk)), 0) FROM positions "
        "WHERE status = 'OPEN'").fetchone()[0]
    pending_risk = conn.execute(
        "SELECT COALESCE(SUM(MAX(0, COALESCE(remaining_pending_monetary_risk, "
        "requested_monetary_risk, 0))), 0) FROM orders "
        "WHERE state IN ('SUBMITTED', 'ACCEPTED', 'PENDING', 'RESTING', "
        "'PARTIAL', 'UNKNOWN', 'PENDING_RECONCILIATION')").fetchone()[0]
    booked_today = conn.execute(
        "SELECT COALESCE(SUM(profit + commission + swap + COALESCE(fee, 0)), 0) "
        "FROM deals WHERE occurred_at_utc >= ?", (now - now % 86400,)).fetchone()[0]
    equity = account.get("equity") if account else None
    open_pct = round(100 * (open_risk + pending_risk) / equity, 4) if equity and equity > 0 else None
    dd_pct = round(100 * (peak - equity) / peak, 4) if equity is not None and isinstance(peak, (float, int)) and peak > 0 else None
    return {
        "configured_limits": limits if limits is not None else {"status": "NO_DATA", "detail": "Runtime has not published configured limits"},
        "broker_equity": equity, "account_currency": account.get("currency") if account else None,
        "broker_snapshot_age_seconds": age, "broker_snapshot_fresh": fresh,
        "local_open_initial_monetary_risk": open_risk,
        "local_pending_remaining_monetary_risk": pending_risk,
        "estimated_total_risk_pct_of_fresh_equity": open_pct,
        "peak_equity": peak, "observed_drawdown_pct": dd_pct,
        "demo_booked_net_today_utc": booked_today,
        "demo_realized_pnl_today_utc": booked_today,
        "note": "Open/pending risk is local initial or remaining risk; confirm broker stops by reconciliation. "
                "Booked net is observed deals, not a broker-verified daily-loss calculation. "
                "PAPER uses separate per-symbol equity; no simulated portfolio aggregation."
    }

def news(conn: sqlite3.Connection, now: int) -> dict:
    snapshot, age = _state(conn, "news", now)
    upcoming = _rows(conn, "SELECT scheduled_at_utc, currency, title, impact FROM news_events "
                           "WHERE scheduled_at_utc BETWEEN ? AND ? AND impact = 'HIGH' ORDER BY scheduled_at_utc "
                           "LIMIT 20", (now, now + 86400))
    return {"runtime_snapshot": snapshot, "snapshot_age_seconds": age, "cached_upcoming_high_impact": upcoming}


def events(conn: sqlite3.Connection, now: int) -> dict:
    incidents = _rows(conn, "SELECT id, order_id, incident_type, detail, first_seen_at_utc, last_seen_at_utc, "
                            "occurrence_count FROM execution_incidents WHERE resolved_at_utc IS NULL "
                            "ORDER BY last_seen_at_utc DESC LIMIT 50")
    runtime = _rows(conn, "SELECT severity, component, event, canonical_symbol, detail, last_seen_at_utc, "
                          "occurrence_count, cleared_at_utc FROM runtime_events ORDER BY last_seen_at_utc DESC, id DESC "
                          "LIMIT 50")
    return {"open_execution_incidents": incidents, "runtime_events": runtime}


def research(conn: sqlite3.Connection, now: int) -> dict:
    runs = _rows(conn, "SELECT run_id, canonical_symbol, run_type, trade_count, net_pnl, win_rate, profit_factor, "
                       "cost_provenance, created_at_utc FROM backtest_runs ORDER BY created_at_utc DESC LIMIT 20")
    trials = {r["kind"]: r["n"] for r in conn.execute("SELECT kind, COUNT(*) AS n FROM research_trials GROUP BY kind")}
    spent_oos = _rows(conn, "SELECT d.canonical_symbol, d.range_start_utc, d.range_end_utc, u.used_for "
                            "FROM dataset_usage u JOIN datasets d ON d.dataset_id = u.dataset_id "
                            "WHERE u.used_for IN ('OOS', 'OOS_ANALYSIS_REUSE') ORDER BY d.range_start_utc")
    return {"recent_runs": runs, "trials_by_kind": trials, "oos_ranges": spent_oos,
            "retired_strategies": sorted(RETIRED_STRATEGY_KEYS)}


def learning(conn: sqlite3.Connection, now: int) -> dict:
    models = _rows(conn, "SELECT model_key, version, lifecycle_state, training_sample_count, created_at_utc "
                         "FROM models ORDER BY model_key, version DESC")
    return {"models": models, "stage": "1 -- observer only, zero influence"}


def memory(conn: sqlite3.Connection, now: int) -> dict:
    by_type = _rows(conn, "SELECT memory_type, COALESCE(origin, 'UNLABELLED') AS origin, COUNT(*) AS n "
                          "FROM rag_memories GROUP BY memory_type, origin")
    marks = _rows(conn, "SELECT * FROM rag_ingestion_state")
    return {"rag_memories": by_type, "ingestion_watermarks": marks}


def knowledge(conn: sqlite3.Connection, now: int) -> dict:
    from adaptive_scalper.knowledge.advisor import DEFAULT_BUNDLE
    from adaptive_scalper.knowledge.validate import validate_bundle

    bundle = validate_bundle(DEFAULT_BUNDLE)
    tiers: dict[str, int] = {}
    for concept in bundle.concepts.values():
        tiers[concept.trust_tier] = tiers.get(concept.trust_tier, 0) + 1
    return {"okf_version": bundle.okf_version, "concepts": len(bundle.concepts), "checksum": bundle.checksum[:16],
            "trust_tiers": tiers, "errors": sum(1 for i in bundle.issues if i.severity == "ERROR")}


def costs(conn: sqlite3.Connection, now: int) -> dict:
    from adaptive_scalper.costs.observations import summarize_observations

    return {s: summarize_observations(conn, s).__dict__ for s in sorted(ALLOWED_CANONICAL_SYMBOLS)}


def history(conn: sqlite3.Connection, now: int) -> dict:
    return {"bar_coverage": _rows(conn, "SELECT * FROM historical_bar_coverage ORDER BY canonical_symbol, resolution")}



def market(conn: sqlite3.Connection, now: int) -> dict:
    """Last sampled broker truth, published by the sole MT5 runtime.

    The dashboard itself never opens the broker terminal. Each value carries
    provenance and age; a disconnected or expired snapshot is NEVER 'LIVE'.
    """
    value, age = _state(conn, "live_telemetry", now)
    clock, clock_age = _state(conn, "server_clock", now)
    if value is None:
        return {"status": "NO_DATA", "detail": "Awaiting the runtime's first MT5 telemetry sample",
                "server_clock": clock, "clock_age_seconds": clock_age}
    quotes = {}
    for symbol in sorted(ALLOWED_CANONICAL_SYMBOLS):
        quote = (value.get("quotes") or {}).get(symbol)
        if quote is None:
            quotes[symbol] = {"status": "UNAVAILABLE", "detail": "No validated broker quote"}
            continue
        quote_age = now - quote["time_utc"] if quote.get("time_utc") else None
        quotes[symbol] = {**quote, "quote_age_seconds": quote_age,
                          "status": "LIVE" if age is not None and age <= 15
                          and quote_age is not None and -3 <= quote_age <= 15
                          and value.get("terminal", {}).get("connected") else "STALE"}
    return {
        "status": "OK" if age is not None and age <= 15
        and value.get("terminal", {}).get("connected") else "STALE",
        "source": "MT5 runtime snapshot (never a dashboard-side broker connection)",
        "sampled_at_utc": value.get("sampled_at_utc"), "age_seconds": age,
        "account": value.get("account"), "terminal": value.get("terminal"),
        "quotes": quotes, "broker_positions": value.get("positions"),
        "errors": value.get("errors", []),
        "server_clock": clock, "clock_age_seconds": clock_age,
    }


def performance(conn: sqlite3.Connection, now: int) -> dict:
    """Separate evidence classes and currencies; never combine PAPER with DEMO."""
    demo = _rows(conn,
        "SELECT date(occurred_at_utc, 'unixepoch') AS day_utc, "
        "COUNT(*) AS observed_deals, "
        "ROUND(SUM(profit + commission + swap + COALESCE(fee, 0)), 2) AS booked_net "
        "FROM deals WHERE occurred_at_utc >= ? "
        "GROUP BY date(occurred_at_utc, 'unixepoch') ORDER BY day_utc",
        (now - 30 * 86400,))
    paper = _rows(conn,
        "SELECT canonical_symbol, COUNT(*) AS closed_trades, "
        "ROUND(SUM(realized_pnl), 2) AS net_pnl "
        "FROM paper_trades GROUP BY canonical_symbol ORDER BY canonical_symbol")
    recent_paper = _rows(conn,
        "SELECT canonical_symbol, strategy_key, direction, entry_time_utc, exit_time_utc, "
        "realized_pnl, realized_r, total_cost, origin "
        "FROM paper_trades ORDER BY exit_time_utc DESC LIMIT 20")
    demo_total = sum(float(row["booked_net"] or 0.0) for row in demo)
    return {
        "demo": {"label": "DEMO booked net, observed deals only; not account equity or a profitability forecast",
                 "period_days": 30, "observed_deals": sum(r["observed_deals"] for r in demo),
                 "net": round(demo_total, 2), "daily_net": demo},
        "paper": {"label": "PAPER per-symbol simulated results; never pool independent session equity",
                  "by_symbol": paper, "recent_closed_trades": recent_paper},
    }


PANELS: dict[str, Callable[[sqlite3.Connection, int], dict]] = {
    "overview": overview, "components": components, "symbols": symbols, "positions": positions, "orders": orders,
    "decisions": decisions, "risk": risk, "news": news, "events": events, "research": research,
    "learning": learning, "memory": memory, "knowledge": knowledge, "costs": costs, "history": history,
    "market": market, "performance": performance,
}


def compute_panel(conn: sqlite3.Connection, name: str, now: int | None = None) -> dict:
    now = now if now is not None else int(time.time())
    try:
        return {"status": "OK", **PANELS[name](conn, now)}
    except Exception as exc:  # one panel's failure is shown, never propagated
        return {"status": "UNAVAILABLE", "detail": f"{type(exc).__name__}: {exc}"}


def compute_all(conn: sqlite3.Connection, now: int | None = None) -> dict:
    now = now if now is not None else int(time.time())
    return {"generated_at_utc": now, "panels": {name: compute_panel(conn, name, now) for name in PANELS}}
