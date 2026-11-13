"""Event detection over the trading database (read-only polling).

The trading runtime is never modified to publish events for the desk; the
desk detects them from data the runtime already persists:

- NEW_CLOSED_BAR      runtime regime/microstructure bar time advanced  -> Market
- NEW_SIGNAL          new shadow-observed strategy candidate           -> Strategy, Risk
- NEW_TRADE_OUTCOME   a position closed                                -> Learning, Strategy
- NEWS_UPDATE         calendar rows retrieved or revised               -> News
- RISK_STATE_CHANGE   kill switch / reconciliation / broker truth /
                      engine block / open-position count changed       -> Risk
- SCHEDULED_REPORT    the periodic consolidated review                  -> all agents

Each event has a deterministic key; the journal's `processed_events` table
makes processing idempotent across restarts (a duplicate is skipped).
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from adaptive_scalper.ai_desk.contracts import canonical_json, sha
from adaptive_scalper.ai_desk.snapshot_builder import (
    _table_exists,
    open_trading_db_readonly,
)

EVENT_AGENTS = {
    "NEW_CLOSED_BAR": ("market_analyst",),
    "NEW_SIGNAL": ("strategy_analyst", "risk_analyst"),
    "NEW_TRADE_OUTCOME": ("learning_analyst", "strategy_analyst"),
    "NEWS_UPDATE": ("news_analyst",),
    "RISK_STATE_CHANGE": ("risk_analyst",),
    "SCHEDULED_REPORT": ("market_analyst", "news_analyst", "risk_analyst", "strategy_analyst", "learning_analyst"),
}


@dataclass(frozen=True)
class DeskEvent:
    event_type: str
    key: str
    detail: str = ""

    @property
    def agents(self) -> tuple[str, ...]:
        return EVENT_AGENTS[self.event_type]


def _state(conn, key):
    row = conn.execute("SELECT value_json FROM runtime_state WHERE key=?", (key,)).fetchone()
    if row is None:
        return None
    try:
        return json.loads(row["value_json"])
    except (TypeError, ValueError):
        return None


class EventBus:
    def __init__(self, trading_db_path: str, *, symbols: list[str], scheduled_report_seconds: float) -> None:
        self.db_path = trading_db_path
        self.symbols = symbols
        self.scheduled_report_seconds = max(60.0, scheduled_report_seconds)

    def poll(self, now: int) -> list[DeskEvent]:
        events: list[DeskEvent] = []
        conn = open_trading_db_readonly(self.db_path)
        try:
            conn.execute("BEGIN")
            micro = _state(conn, "microstructure") or {}
            for sym in self.symbols:
                regime = _state(conn, f"regime:{sym}") or {}
                bar_time = regime.get("bar_time") or ((micro.get("candles") or {}).get(sym) or {}).get("bar_time_utc")
                if bar_time:
                    events.append(DeskEvent("NEW_CLOSED_BAR", f"bar:{sym}:{bar_time}", sym))
            if _table_exists(conn, "shadow_candidates"):
                row = conn.execute("SELECT MAX(id) FROM shadow_candidates").fetchone()
                if row[0] is not None:
                    events.append(DeskEvent("NEW_SIGNAL", f"signal:{row[0]}"))
            row = conn.execute("SELECT MAX(id), MAX(closed_at_utc) FROM positions WHERE status='CLOSED'").fetchone()
            if row[0] is not None:
                events.append(DeskEvent("NEW_TRADE_OUTCOME", f"trade:{row[0]}:{row[1]}"))
            row = conn.execute("SELECT COUNT(*), MAX(retrieved_at_utc), COALESCE(SUM(revision), 0) FROM news_events"
                               ).fetchone()
            if row[0]:
                events.append(DeskEvent("NEWS_UPDATE", f"news:{row[0]}:{row[1]}:{row[2]}"))
            ks = conn.execute("SELECT value FROM app_state WHERE key='kill_switch'").fetchone()
            why = _state(conn, "why_no_trade") or {}
            tel = _state(conn, "live_telemetry") or {}
            risk_view = {
                "kill_switch": json.loads(ks["value"]).get("status") if ks else None,
                "reconciliation": (_state(conn, "reconciliation") or {}).get("status"),
                "broker_truth": (_state(conn, "broker_truth") or {}).get("status"),
                "engine_block": (why.get("global_block") or {}).get("decision"),
                "open_positions": len(tel.get("positions") or []),
            }
            events.append(DeskEvent("RISK_STATE_CHANGE", "risk:" + sha(canonical_json(risk_view), 16),
                                    canonical_json(risk_view)))
            conn.execute("COMMIT")
        finally:
            conn.close()
        bucket = int(now // self.scheduled_report_seconds)
        events.append(DeskEvent("SCHEDULED_REPORT", f"scheduled:{int(self.scheduled_report_seconds)}:{bucket}"))
        return events
