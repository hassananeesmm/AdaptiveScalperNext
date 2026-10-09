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
from adaptive_scalper.dashboard.health import DASHBOARD_INTEGRITY, compute_health

STALE_HEARTBEAT_SECONDS = 15
OPEN_ORDER_STATES = ("SUBMITTED", "ACCEPTED", "PENDING", "RESTING", "PARTIAL", "UNKNOWN", "PENDING_RECONCILIATION")


def _state(conn: sqlite3.Connection, key: str, now: int):
    row = conn.execute("SELECT value_json, updated_at_utc FROM runtime_state WHERE key = ?", (key,)).fetchone()
    return (json.loads(row["value_json"]), now - row["updated_at_utc"]) if row else (None, None)


def _rows(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> list[dict]:
    return [dict(r) for r in conn.execute(sql, params)]


def overview(conn: sqlite3.Connection, now: int) -> dict:
    db_path = conn.execute("PRAGMA database_list").fetchone()[2]
    integrity, checked_at = DASHBOARD_INTEGRITY.status(db_path)
    health = compute_health(conn, integrity=integrity)
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
        "database_integrity": {"result": integrity,
                               "checked_age_seconds": None if checked_at is None else max(0, int(now - checked_at))},
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
    broker_truth, truth_age = _state(conn, "broker_truth", now)
    has_close_requests = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'close_requests'").fetchone() is not None
    unresolved_closes = _rows(
        conn, "SELECT broker_position_id, broker_symbol, position_direction, requested_volume, requested_at_utc, "
              "send_outcome, attempt_count, last_attempt_at_utc, last_attempt_detail FROM close_requests "
              "WHERE status = 'UNRESOLVED' ORDER BY requested_at_utc", ()) if has_close_requests else []
    return {"active_orders": active, "dangerous_unknown": dangerous, "reconciliation": reconciliation,
            "reconciliation_age_seconds": age, "broker_truth": broker_truth, "broker_truth_age_seconds": truth_age,
            "unresolved_closes": unresolved_closes}


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

EXECUTION_QUOTE_MAX_AGE_SECONDS = 30
MARKET_CLOSED_PREFIXES = ("no_quote", "invalid_quote", "stale_quote")  # runtime.engine.MARKET_CLOSED_REASONS
# Final-permission gate code (journal ENTRY_BLOCKED payload) -> readiness label.
NO_SIGNAL = "NO SIGNAL"
AWAITING_COMPLETED_BAR = "AWAITING COMPLETED BAR"
EXISTING_POSITION = "EXISTING POSITION"
STALE_QUOTE = "STALE QUOTE"
MARKET_CLOSED = "MARKET CLOSED"
BLOCKED_BY_CORRELATION = "BLOCKED BY CORRELATION"
BLOCKED_BY_COST = "BLOCKED BY COST"
BLOCKED_BY_NEWS = "BLOCKED BY NEWS"
BLOCKED_BY_RISK = "BLOCKED BY RISK"
BLOCKED_BY_MARGIN = "BLOCKED BY MARGIN"
BLOCKED_BY_RECONCILIATION = "BLOCKED BY RECONCILIATION"
ELIGIBLE = "ELIGIBLE FOR A NATURAL SIGNAL"
_GATE_LABELS = {
    "BLOCK_CORRELATION": BLOCKED_BY_CORRELATION, "BLOCK_RISK": BLOCKED_BY_RISK,
    "BLOCK_PORTFOLIO_RISK": BLOCKED_BY_RISK,
    "BLOCK_NEWS": BLOCKED_BY_NEWS, "BLOCK_NEWS_CALENDAR_UNAVAILABLE": BLOCKED_BY_NEWS,
    "BLOCK_NEWS_PROVIDER_CONFLICT": BLOCKED_BY_NEWS, "BLOCK_COST": BLOCKED_BY_COST,
    "BLOCK_EXPECTED_EDGE": BLOCKED_BY_COST,
    "BLOCK_RECONCILIATION": BLOCKED_BY_RECONCILIATION, "BLOCK_UNKNOWN_ORDER": "UNKNOWN ORDER",
    "BLOCK_STALE_QUOTE": STALE_QUOTE, "BLOCK_DUPLICATE": EXISTING_POSITION, "BLOCK_KILL_SWITCH": "KILL SWITCH",
    "BLOCK_REENTRY_CHURN": "RE-ENTRY RULE",
}
_EXECUTION_LABELS = {
    "BLOCKED_MARGIN": BLOCKED_BY_MARGIN, "BLOCKED_BROKER_CONSTRAINT": "BROKER BLOCK",
    "BLOCKED_BROKER_STATE": "BROKER BLOCK", "BLOCKED_PRESEND_RECHECK": "BROKER BLOCK", "REJECTED": "BROKER BLOCK",
    "CANCELLED": "BROKER BLOCK", "UNKNOWN": "UNKNOWN ORDER",
}


def _global_label(code: str) -> str:
    if code in _GATE_LABELS:
        return _GATE_LABELS[code]
    return "BROKER BLOCK"  # MT5 disconnected, account not DEMO, terminal/broker trading disabled


def _decision_label(conn: sqlite3.Connection, row: dict) -> str:
    decision, stage = row["decision"], row["stage"]
    if stage == "SIGNAL" and decision == "FLAT":
        return EXISTING_POSITION if "already open" in (row["reason"] or "") else NO_SIGNAL
    if stage == "SELECTOR":
        return BLOCKED_BY_COST if decision == "BLOCK_COST" else NO_SIGNAL
    if stage == "SIZING":
        return BLOCKED_BY_RISK
    if stage == "DATA":
        return "BROKER BLOCK"
    if decision == "BLOCKED_PERMISSION" and row["chain_key"]:
        event = conn.execute(
            "SELECT je.payload_json FROM journal_events je JOIN decision_chains dc ON dc.id = je.chain_id "
            "WHERE dc.chain_key = ? AND je.event_type = 'ENTRY_BLOCKED' ORDER BY je.sequence_in_chain DESC LIMIT 1",
            (row["chain_key"],)).fetchone()
        code = json.loads(event["payload_json"]).get("decision") if event else None
        return _GATE_LABELS.get(code, "PERMISSION BLOCK")
    if decision in ("FILLED", "PARTIAL", "RESTING"):
        return ELIGIBLE
    return _EXECUTION_LABELS.get(decision, _global_label(decision) if decision.startswith("BLOCK") else decision)


def _last_block(conn: sqlite3.Connection, symbol: str) -> dict | None:
    """The most recent DEMO decision on `symbol` that was a real block (a
    signal existed but a gate stopped it), as opposed to a bar with no
    signal or a fill."""
    row = conn.execute(
        "SELECT decided_at_utc, stage, decision, reason, strategy_key, chain_key FROM entry_decisions "
        "WHERE canonical_symbol = ? AND mode = 'DEMO' AND decision NOT IN ('FILLED', 'PARTIAL', 'RESTING', 'FLAT') "
        "AND NOT (stage IN ('SIGNAL', 'SELECTOR') AND decision != 'BLOCK_COST') "
        "ORDER BY id DESC LIMIT 1", (symbol,)).fetchone()
    if row is None:
        return None
    row = dict(row)
    row["label"] = _decision_label(conn, row)
    return row


def _latest_signals(conn: sqlite3.Connection, symbol: str) -> dict:
    """Strategy signals created on the symbol's most recent decided bar
    (DEMO runtime entry chains only)."""
    last = conn.execute(
        "SELECT MAX(j.event_timestamp_utc) FROM journal_events j JOIN decision_chains d ON d.id = j.chain_id "
        "WHERE d.chain_key LIKE 'entry:%' AND j.canonical_symbol = ? AND j.event_type = 'SIGNAL_CREATED'",
        (symbol,)).fetchone()[0]
    if last is None:
        return {"at_utc": None, "signals": []}
    rows = conn.execute(
        "SELECT j.strategy_key, j.payload_json FROM journal_events j JOIN decision_chains d ON d.id = j.chain_id "
        "WHERE d.chain_key LIKE 'entry:%' AND j.canonical_symbol = ? AND j.event_type = 'SIGNAL_CREATED' "
        "AND j.event_timestamp_utc = ? ORDER BY j.id", (symbol, last)).fetchall()
    signals = []
    for r in rows:
        payload = json.loads(r["payload_json"]) if r["payload_json"] else {}
        signals.append({"strategy_key": r["strategy_key"], "direction": payload.get("direction"),
                        "raw_confidence": payload.get("raw_confidence")})
    return {"at_utc": last, "signals": signals}


def multi_position(conn: sqlite3.Connection, now: int) -> dict:
    """MULTI-POSITION READINESS: whether a second simultaneous DEMO position
    could open now and, per symbol, the evidence for why one has not.

    Observer only: reads runtime_state / SQLite, never MT5, never orders.
    A free position slot is capacity, not a forecast: every entry still needs
    a natural strategy signal and every final-permission gate."""
    limits, _ = _state(conn, "risk_limits", now)
    if limits is None:
        return {"status": "NO_DATA", "detail": "the runtime has not published its risk limits yet"}
    admission, _ = _state(conn, "symbol_admission", now)
    startup, _ = _state(conn, "startup", now)
    readiness, readiness_age = _state(conn, "multi_position_readiness", now)
    why, _ = _state(conn, "why_no_trade", now)
    telemetry, telemetry_age = _state(conn, "live_telemetry", now)
    if admission is None:  # a runtime older than the symbol-admission task: it never re-admits
        excluded = (startup or {}).get("excluded_symbols", {})
        closed = {s: f"{why} -- this runtime predates symbol re-admission: restart required once the market "
                     f"is open" for s, why in excluded.items() if why.split(":")[0] in MARKET_CLOSED_PREFIXES}
        admission = {"active": sorted((startup or {}).get("symbols", {})), "awaiting_market": closed,
                     "excluded": {s: why for s, why in excluded.items() if s not in closed}}

    open_rows = _rows(conn, "SELECT broker_position_id, canonical_symbol, direction, volume, initial_monetary_risk, "
                            "strategy_key, opened_at_utc FROM positions WHERE status = 'OPEN' ORDER BY opened_at_utc")
    marks = ", ".join("?" * len(OPEN_ORDER_STATES))
    pending_rows = _rows(conn, f"SELECT canonical_symbol, direction, state, "  # nosec B608 - placeholders only
                               f"COALESCE(remaining_pending_monetary_risk, requested_monetary_risk, 0) AS risk "
                               f"FROM orders WHERE broker_order_id IS NOT NULL AND state IN ({marks})",
                         OPEN_ORDER_STATES)
    open_symbols = {r["canonical_symbol"] for r in open_rows}
    pending_only = [r for r in pending_rows if r["canonical_symbol"] not in open_symbols]
    open_risk = sum(max(0.0, r["initial_monetary_risk"] or 0.0) for r in open_rows)
    pending_risk = sum(max(0.0, r["risk"] or 0.0) for r in pending_rows)
    max_positions = limits["max_open_positions"]
    slots_left = max(0, max_positions - len(open_rows) - len(pending_only))

    fresh = bool(telemetry and telemetry_age is not None and telemetry_age <= STALE_HEARTBEAT_SECONDS
                 and (telemetry.get("terminal") or {}).get("connected"))
    equity = ((telemetry or {}).get("account") or {}).get("equity") if fresh else None
    if equity:
        max_total = equity * limits["max_total_open_risk_pct"] / 100.0
        per_trade = equity * limits["risk_per_trade_pct"] / 100.0
        remaining = max(0.0, max_total - open_risk - pending_risk)
        risk_capacity = {"equity": equity, "max_total_risk": round(max_total, 2),
                         "remaining_aggregate_risk": round(remaining, 2), "per_trade_risk": round(per_trade, 2),
                         "additional_full_size_trades_by_risk": int(remaining // per_trade) if per_trade > 0 else 0}
    else:
        risk_capacity = {"status": "UNAVAILABLE", "detail": "no fresh DEMO account snapshot (equity unknown)"}
    # Floating P&L is broker truth from the runtime's sampled snapshot; stale -> None, never a guess.
    floating = ({str(p["broker_position_id"]): p.get("floating_pnl") for p in (telemetry or {}).get("positions") or []}
                if fresh else {})
    open_positions = [{
        "broker_position_id": r["broker_position_id"], "symbol": r["canonical_symbol"], "direction": r["direction"],
        "volume": r["volume"], "strategy_key": r["strategy_key"] or "UNATTRIBUTED",
        "initial_monetary_risk": r["initial_monetary_risk"], "opened_at_utc": r["opened_at_utc"],
        "floating_pnl": floating.get(str(r["broker_position_id"])),
    } for r in open_rows]

    global_block = (why or {}).get("global_block")
    readiness = readiness or {}
    per_symbol = {}
    for symbol in sorted(ALLOWED_CANONICAL_SYMBOLS):
        quote = ((telemetry or {}).get("quotes") or {}).get(symbol)
        quote_age = now - quote["time_utc"] if quote and quote.get("time_utc") else None
        quote_status = ("NO QUOTE" if quote is None else
                        "FRESH" if fresh and quote_age is not None and quote_age <= EXECUTION_QUOTE_MAX_AGE_SECONDS
                        else "STALE")
        cost = (readiness.get("cost_evidence") or {}).get(symbol)
        news = (readiness.get("news") or {}).get(symbol)
        latest = conn.execute(
            "SELECT decided_at_utc, stage, decision, reason, strategy_key, chain_key FROM entry_decisions "
            "WHERE canonical_symbol = ? AND mode = 'DEMO' ORDER BY id DESC LIMIT 1", (symbol,)).fetchone()
        latest = dict(latest) if latest else None
        signal_status = (None if latest is None else
                         "SIGNAL" if latest["stage"] in ("EXECUTION", "SIZING") or latest["decision"] == "BLOCK_COST"
                         else "NO SIGNAL" if latest["stage"] in ("SIGNAL", "SELECTOR") else latest["stage"])

        bar_wait = ((why or {}).get("symbols") or {}).get(symbol, {}).get("decision") == "WAITING_FOR_BAR_CLOSE"
        if symbol in admission.get("excluded", {}):
            label, detail = "BROKER BLOCK", f"excluded at startup: {admission['excluded'][symbol]}"
        elif symbol in admission.get("awaiting_market", {}):
            label, detail = MARKET_CLOSED, admission["awaiting_market"][symbol]
        elif symbol not in admission.get("active", []):
            label, detail = "NOT ENABLED", "not in the running configuration's market.symbols"
        elif symbol in open_symbols:
            label, detail = EXISTING_POSITION, "one position per symbol (max_positions_per_symbol)"
        elif any(r["canonical_symbol"] == symbol for r in pending_rows):
            label, detail = "UNKNOWN ORDER" if any(r["state"] in ("UNKNOWN", "PENDING_RECONCILIATION")
                                                   for r in pending_rows if r["canonical_symbol"] == symbol) \
                else EXISTING_POSITION, "an order on this symbol is still working"
        elif global_block:
            label, detail = _global_label(global_block["decision"]), global_block["reason"]
        elif slots_left == 0:
            label, detail = BLOCKED_BY_RISK, f"max_open_positions reached ({max_positions}/{max_positions})"
        elif quote_status != "FRESH":
            label, detail = STALE_QUOTE, (f"quote age {quote_age}s" if quote_age is not None
                                          else "no quote in the runtime's broker snapshot")
        elif cost is not None and not cost["eligible"]:
            label, detail = BLOCKED_BY_COST, f"unknown cost evidence: {', '.join(cost['unknown_components'])}"
        elif news is not None and news["decision"] != "ALLOW":
            label, detail = BLOCKED_BY_NEWS, news["reason"]
        elif latest is None:
            label, detail = AWAITING_COMPLETED_BAR, "no completed-bar decision recorded yet"
        else:
            label, detail = _decision_label(conn, latest), latest["reason"]
            if label == EXISTING_POSITION:  # that position has closed since
                label = ELIGIBLE
        per_symbol[symbol] = {
            "status": label, "detail": detail, "quote_status": quote_status, "quote_age_seconds": quote_age,
            "signal_status": signal_status, "last_decision": latest,
            "open_position_strategy": next((p["strategy_key"] for p in open_positions if p["symbol"] == symbol), None),
            "cost_eligible": None if cost is None else cost["eligible"],
            "news": None if news is None else news["decision"],
            "bar_status": AWAITING_COMPLETED_BAR if bar_wait else None,
            "latest_signals": _latest_signals(conn, symbol),
            "last_block": _last_block(conn, symbol),
        }

    waiting = {s: v for s, v in per_symbol.items() if v["status"] not in (EXISTING_POSITION, "NOT ENABLED")}
    if not open_rows and not pending_only:
        headline = "No position is open; a first position needs a natural signal that passes every gate."
    elif slots_left == 0:
        headline = f"Position capacity is full ({max_positions}/{max_positions}); no further entry is possible."
    else:
        reasons = "; ".join(f"{s}: {v['status']}" for s, v in waiting.items()) or "no other symbol is enabled"
        headline = f"{slots_left} slot(s) free. Why no second trade yet — {reasons}."
    return {
        "headline": headline,
        "guarantee": "A free slot is capacity, not a forecast: a second trade still needs a natural signal "
                     "that passes correlation, risk, cost, news and broker checks.",
        "max_open_positions": max_positions, "max_positions_per_symbol": limits["max_positions_per_symbol"],
        "open_positions": len(open_rows), "pending_orders": len(pending_rows),
        "positions": open_positions,
        "account_currency": ((telemetry or {}).get("account") or {}).get("currency"),
        "remaining_position_slots": slots_left,
        "open_monetary_risk": round(open_risk, 2), "pending_monetary_risk": round(pending_risk, 2),
        "risk_capacity": risk_capacity,
        "enabled_symbols": admission.get("active", []), "awaiting_market": admission.get("awaiting_market", {}),
        "excluded_symbols": admission.get("excluded", {}),
        "global_block": global_block,
        "symbols": per_symbol,
        "correlation": readiness.get("correlation", []),
        "correlation_min_samples": readiness.get("correlation_min_samples"),
        "readiness_age_seconds": readiness_age,
        "status_vocabulary": [NO_SIGNAL, AWAITING_COMPLETED_BAR, EXISTING_POSITION, STALE_QUOTE, MARKET_CLOSED,
                              BLOCKED_BY_CORRELATION, BLOCKED_BY_COST, BLOCKED_BY_NEWS, BLOCKED_BY_RISK,
                              BLOCKED_BY_MARGIN, BLOCKED_BY_RECONCILIATION, ELIGIBLE, "UNKNOWN ORDER",
                              "KILL SWITCH", "RE-ENTRY RULE", "BROKER BLOCK", "NOT ENABLED"],
    }


def news(conn: sqlite3.Connection, now: int) -> dict:
    snapshot, age = _state(conn, "news", now)
    upcoming = _rows(conn, "SELECT scheduled_at_utc, currency, title, impact FROM news_events "
                           "WHERE scheduled_at_utc BETWEEN ? AND ? AND impact = 'HIGH' ORDER BY scheduled_at_utc "
                           "LIMIT 20", (now, now + 86400))
    return {"runtime_snapshot": snapshot, "snapshot_age_seconds": age, "cached_upcoming_high_impact": upcoming}


def microstructure(conn: sqlite3.Connection, now: int) -> dict:
    """Latest observer-only microstructure snapshot (runtime/microstructure.py):
    rolling VWAP +/- k sigma and the newest closed entry bar's candle ratios,
    EMA and ADX. Read from runtime_state only; no strategy or gate uses it."""
    snapshot, age = _state(conn, "microstructure", now)
    if snapshot is None:
        return {"status": "NO_DATA", "detail": "No microstructure snapshot yet (runtime not running, or "
                                               "[microstructure] tracks no symbol)"}
    return {"authority": "NONE (observation only)", "snapshot_age_seconds": age,
            "vwap_bands": snapshot.get("symbols", {}), "candles": snapshot.get("candles", {})}


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
    provenance and age; a disconnected or expired snapshot is never FRESH.
    (Freshness is FRESH/STALE -- the word for an execution mode that does
    not exist is kept out of the source; test_runtime_architecture audits it.)
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
                          "status": "FRESH" if age is not None and age <= 15
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


_STRATEGY_FUNNEL_EVENT_TYPES = (
    "SIGNAL_CREATED", "SIGNAL_REJECTED", "PROPOSAL_CREATED", "ENTRY_ALLOWED", "ENTRY_BLOCKED",
)
# Strategy Lab lookback for funnel counts and "last genuine X" evidence -- bounded so a
# strategy that stopped firing months ago does not force an unbounded historical scan.
_STRATEGY_LAB_WINDOW_SECONDS = 30 * 86400


def _strategy_source_metadata() -> dict[str, dict]:
    """Introspects the LIVE strategy classes directly (never a hand-copied
    description that could drift from the code): module docstring, class
    name, and the strategy's own constructor defaults, which is where every
    active strategy's stop/target ATR multiples, confidence floor, etc.
    actually live (see e.g. `MicrostructureAccelerationStrategy.__init__`)."""
    import inspect

    from adaptive_scalper.strategies import build_active_registry

    metadata: dict[str, dict] = {}
    for strategy in build_active_registry().all_active():
        cls = type(strategy)
        module = inspect.getmodule(cls)
        doc = " ".join((module.__doc__ or "").split()) if module else ""
        sig = inspect.signature(cls.__init__)
        defaults = {
            name: param.default for name, param in sig.parameters.items()
            if name != "self" and param.default is not inspect.Parameter.empty
        }
        metadata[strategy.key] = {
            "version": strategy.version,
            "class_name": cls.__name__,
            "source_module": module.__name__ if module else None,
            "source_description": doc,
            "default_parameters": defaults,
        }
    return metadata


def _latest_journal_event(conn: sqlite3.Connection, event_type: str, strategy_key: str) -> dict | None:
    row = conn.execute(
        "SELECT j.event_timestamp_utc, j.canonical_symbol, j.payload_json FROM journal_events j "
        "JOIN decision_chains d ON d.id = j.chain_id WHERE j.event_type = ? AND j.strategy_key = ? "
        "AND d.chain_key LIKE 'entry:%' ORDER BY j.event_timestamp_utc DESC LIMIT 1",
        (event_type, strategy_key),
    ).fetchone()
    if row is None:
        return None
    return {
        "event_timestamp_utc": row["event_timestamp_utc"], "canonical_symbol": row["canonical_symbol"],
        "payload": json.loads(row["payload_json"]) if row["payload_json"] else None,
    }


def _latest_position_for_strategy(conn: sqlite3.Connection, strategy_key: str) -> dict | None:
    row = conn.execute(
        "SELECT broker_position_id, canonical_symbol, direction, volume, entry_price, status, "
        "opened_at_utc, closed_at_utc FROM positions WHERE strategy_key = ? "
        "ORDER BY opened_at_utc DESC LIMIT 1",
        (strategy_key,),
    ).fetchone()
    return dict(row) if row is not None else None


def strategy_registry(conn: sqlite3.Connection, now: int) -> dict:
    """Strategy Lab: the actual registered/retired strategy set (directive
    sections 8, 90, 121), source-verified entry logic and parameters, and
    real evidence of what each one has genuinely done. A strategy is never
    labelled executed merely because it proposed -- SIGNAL/PROPOSAL/ALLOWED
    counts, the latest broker-verified position, and its current lifecycle
    stage are reported as separate, independently-evidenced fields."""
    source = _strategy_source_metadata()
    window_start = now - _STRATEGY_LAB_WINDOW_SECONDS
    placeholders = ",".join("?" * len(_STRATEGY_FUNNEL_EVENT_TYPES))
    funnel_rows = conn.execute(
        f"SELECT j.strategy_key, j.event_type, COUNT(*) AS n FROM journal_events j "  # nosec B608 - placeholders only
        f"JOIN decision_chains d ON d.id = j.chain_id "
        f"WHERE j.event_type IN ({placeholders}) AND j.event_timestamp_utc >= ? AND j.strategy_key IS NOT NULL "
        f"AND d.chain_key LIKE 'entry:%' GROUP BY j.strategy_key, j.event_type",
        (*_STRATEGY_FUNNEL_EVENT_TYPES, window_start),
    ).fetchall()
    funnel: dict[str, dict[str, int]] = {}
    for row in funnel_rows:
        funnel.setdefault(row["strategy_key"], {})[row["event_type"]] = row["n"]

    all_keys = sorted(set(source) | set(RETIRED_STRATEGY_KEYS) | set(funnel))
    strategies = []
    for key in all_keys:
        retired = key in RETIRED_STRATEGY_KEYS
        meta = source.get(key)
        counts = funnel.get(key, {})
        last_position = _latest_position_for_strategy(conn, key)
        last_signal = _latest_journal_event(conn, "SIGNAL_CREATED", key)
        last_proposal = _latest_journal_event(conn, "PROPOSAL_CREATED", key)
        last_allowed = _latest_journal_event(conn, "ENTRY_ALLOWED", key)
        last_blocked = _latest_journal_event(conn, "ENTRY_BLOCKED", key)
        if retired:
            lifecycle = "RETIRED"
        elif last_position is not None and last_position["status"] == "OPEN":
            lifecycle = "FILLED"
        elif last_position is not None and last_position["status"] == "CLOSED":
            lifecycle = "CLOSED"
        elif counts.get("ENTRY_ALLOWED"):
            lifecycle = "SUBMITTED"
        elif counts.get("PROPOSAL_CREATED"):
            lifecycle = "SELECTED"
        elif counts.get("SIGNAL_CREATED"):
            lifecycle = "PROPOSED"
        else:
            lifecycle = "REGISTERED"
        strategies.append({
            "strategy_key": key,
            "registration_status": "RETIRED_PERMANENTLY" if retired else ("REGISTERED" if meta else "UNKNOWN"),
            "lifecycle_stage": lifecycle,
            "version": meta["version"] if meta else None,
            "source_module": meta["source_module"] if meta else None,
            "source_description": meta["source_description"] if meta else (
                "Permanently retired (directive section 8): no source module remains registered."
                if retired else None
            ),
            "default_parameters": meta["default_parameters"] if meta else None,
            f"counts_last_{_STRATEGY_LAB_WINDOW_SECONDS // 86400}d": {
                "signals_created": counts.get("SIGNAL_CREATED", 0),
                "signals_rejected": counts.get("SIGNAL_REJECTED", 0),
                "proposals_selected": counts.get("PROPOSAL_CREATED", 0),
                "entries_allowed": counts.get("ENTRY_ALLOWED", 0),
                "entries_blocked": counts.get("ENTRY_BLOCKED", 0),
            },
            "last_genuine_signal": last_signal,
            "last_selection": last_proposal,
            "last_entry_allowed": last_allowed,
            "last_entry_blocked": last_blocked,
            "last_position": last_position,
        })
    non_runtime = conn.execute(
        "SELECT COUNT(*) FROM journal_events j JOIN decision_chains d ON d.id = j.chain_id "
        "WHERE j.event_type = 'SIGNAL_CREATED' AND j.event_timestamp_utc >= ? AND d.chain_key NOT LIKE 'entry:%'",
        (window_start,),
    ).fetchone()[0]
    return {
        "window_days": _STRATEGY_LAB_WINDOW_SECONDS // 86400, "strategies": strategies,
        "counting_basis": "DEMO runtime entry chains (entry:...) only",
        "non_runtime_signal_events_excluded": non_runtime,
    }


def strategy_activity(conn: sqlite3.Connection, now: int) -> dict:
    """Strategy Lab: the latest genuine per-symbol strategy evaluations,
    real database evidence only (`entry_decisions`, written once per real
    decision by the runtime). `reason` and `detail_json` are the complete
    recorded explanation -- never truncated or re-derived."""
    rows = _rows(
        conn,
        "SELECT id, decided_at_utc, mode, canonical_symbol, bar_time_utc, strategy_key, direction, stage, "
        "decision, reason, chain_key, detail_json FROM entry_decisions ORDER BY id DESC LIMIT 200",
    )
    for row in rows:
        row["detail"] = json.loads(row.pop("detail_json")) if row.get("detail_json") else None
    return {"recent_evaluations": rows}


def strategy_attribution(conn: sqlite3.Connection, now: int) -> dict:
    """Strategy Lab: broker-verified DEMO trade attribution (directive
    section 31: broker is authoritative). Every closing/entry deal for a
    locally-tracked position is summed exactly once (never double-counted);
    a position with more than two deals genuinely had a partial fill or
    multiple closing deals, reported as such rather than guessed at. Any
    broker deal whose `broker_position_id` does not match a locally-tracked
    position is reported as UNATTRIBUTED rather than assigned a guessed
    strategy."""
    positions_rows = _rows(
        conn,
        "SELECT id, broker_position_id, canonical_symbol, direction, volume, entry_price, "
        "initial_monetary_risk, strategy_key, status, opened_at_utc, closed_at_utc, entry_order_id "
        "FROM positions ORDER BY opened_at_utc DESC LIMIT 300",
    )
    known_position_ids = {p["broker_position_id"] for p in positions_rows if p["broker_position_id"]}
    attributed = []
    for p in positions_rows:
        deals = _rows(
            conn,
            "SELECT broker_deal_id, price, volume, commission, swap, profit, fee, entry_type, deal_type, "
            "occurred_at_utc, comment FROM deals WHERE broker_position_id = ? ORDER BY occurred_at_utc",
            (p["broker_position_id"],),
        )
        gross_pnl = round(sum(d["profit"] or 0.0 for d in deals), 2)
        costs = round(sum((d["commission"] or 0.0) + (d["swap"] or 0.0) + (d["fee"] or 0.0) for d in deals), 2)
        entry_deals = [d for d in deals if d["entry_type"] == "IN"]
        closing_deals = [d for d in deals if d["entry_type"] in ("OUT", "INOUT", "OUT_BY")]
        attributed.append({
            **p, "deal_count": len(deals), "entry_deal_count": len(entry_deals),
            "closing_deal_count": len(closing_deals),
            "partial_fill_or_multi_close": len(entry_deals) > 1 or len(closing_deals) > 1,
            "gross_pnl": gross_pnl, "costs": costs, "net_pnl": round(gross_pnl + costs, 2),
            "deals": deals,
        })
    unattributed_clause = (
        "WHERE broker_position_id IS NULL" if not known_position_ids else
        "WHERE broker_position_id IS NULL OR broker_position_id NOT IN (" +
        ",".join("?" * len(known_position_ids)) + ")"
    )
    unattributed = _rows(
        conn,
        "SELECT broker_deal_id, broker_position_id, price, volume, commission, swap, profit, fee, "  # nosec B608 - placeholders only
        f"entry_type, deal_type, occurred_at_utc, comment FROM deals {unattributed_clause} "
        "ORDER BY occurred_at_utc DESC LIMIT 200",
        tuple(known_position_ids),
    )
    return {
        "attributed_positions": attributed, "unattributed_deals": unattributed,
        "note": "UNATTRIBUTED means a broker deal with no matching locally-tracked position -- "
                "never guessed onto a strategy.",
    }


def strategy_performance(conn: sqlite3.Connection, now: int) -> dict:
    """Strategy Lab: separate DEMO / PAPER / BACKTEST row-level trade
    evidence (never pooled -- each is a distinct evidence class with its
    own currency of truth). The UI aggregates and filters client-side by
    strategy/symbol/date/regime/direction/version/provenance; every metric
    it derives carries its own sample size from the row count actually
    shipped here."""
    demo = _rows(
        conn,
        "SELECT id, broker_position_id, strategy_key, canonical_symbol, direction, volume, "
        "initial_monetary_risk, entry_price, opened_at_utc, closed_at_utc FROM positions "
        "WHERE status = 'CLOSED' ORDER BY closed_at_utc DESC LIMIT 300",
    )
    for p in demo:
        deals = _rows(
            conn, "SELECT profit, commission, swap, fee FROM deals WHERE broker_position_id = ?",
            (p["broker_position_id"],),
        )
        p["gross_pnl"] = round(sum(d["profit"] or 0.0 for d in deals), 2)
        p["costs"] = round(sum((d["commission"] or 0.0) + (d["swap"] or 0.0) + (d["fee"] or 0.0) for d in deals), 2)
        p["net_pnl"] = round(p["gross_pnl"] + p["costs"], 2)
        p["deal_count"] = len(deals)
    paper = _rows(
        conn,
        "SELECT canonical_symbol, strategy_key, strategy_version, direction, entry_regime, exit_regime, "
        "entry_time_utc, exit_time_utc, realized_pnl, realized_r, gross_pnl, total_cost, cost_provenance, "
        "origin, exit_reason FROM paper_trades ORDER BY exit_time_utc DESC LIMIT 300",
    )
    backtest = _rows(
        conn,
        "SELECT run_id, canonical_symbol, strategy_key, strategy_version, direction, entry_regime, exit_regime, "
        "entry_time_utc, exit_time_utc, realized_pnl, realized_r, gross_pnl, total_cost, cost_provenance, "
        "exit_reason FROM backtest_trades ORDER BY exit_time_utc DESC LIMIT 300",
    )
    return {
        "demo_closed_trades": demo, "paper_trades": paper, "backtest_trades": backtest,
        "sample_sizes": {"demo": len(demo), "paper": len(paper), "backtest": len(backtest)},
        "note": "DEMO, PAPER and BACKTEST are separate evidence classes and are never pooled into one figure. "
                "Row-level trades only, capped at the 300 most recent per class to keep this panel cheap on "
                "every 2s dashboard refresh; no unavailable cost or performance figure is invented.",
    }


PANELS: dict[str, Callable[[sqlite3.Connection, int], dict]] = {
    "overview": overview, "components": components, "symbols": symbols, "positions": positions, "orders": orders,
    "decisions": decisions, "risk": risk, "news": news, "events": events, "research": research,
    "learning": learning, "memory": memory, "knowledge": knowledge, "costs": costs, "history": history,
    "market": market, "performance": performance,
    "strategy_registry": strategy_registry, "strategy_activity": strategy_activity,
    "strategy_attribution": strategy_attribution, "strategy_performance": strategy_performance,
    "multi_position": multi_position, "microstructure": microstructure,
}


def compute_panel(conn: sqlite3.Connection, name: str, now: int | None = None) -> dict:
    now = now if now is not None else int(time.time())
    try:
        return {"status": "OK", **PANELS[name](conn, now)}
    except Exception as exc:  # one panel's failure is shown, never propagated
        return {"status": "UNAVAILABLE", "detail": f"{type(exc).__name__}: {exc}"}


def compute_all(conn: sqlite3.Connection, now: int | None = None) -> dict:
    """Every panel from ONE read snapshot. The snapshot is pinned first and
    `now` taken after it, so nothing read can be newer than `now` (before,
    rows the runtime wrote mid-computation showed negative ages)."""
    if now is not None:
        return {"generated_at_utc": now, "panels": {name: compute_panel(conn, name, now) for name in PANELS}}
    conn.execute("BEGIN")
    try:
        conn.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchall()  # starts the read snapshot
        now = int(time.time())
        return {"generated_at_utc": now, "panels": {name: compute_panel(conn, name, now) for name in PANELS}}
    finally:
        conn.execute("COMMIT")
