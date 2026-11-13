"""Build immutable evidence snapshots from the trading database, READ-ONLY.

The trading database is opened exclusively through `connect_readonly` (SQLite
itself rejects every write), queries are fixed SQL in this file (never model
generated), and every query is bounded by the snapshot's `as_of_utc`:

- bars are included only once CLOSED at as-of (open time + bar length <= as-of);
- outcomes (shadow outcomes, closed trades, deals) only when resolved at or
  before as-of, so a historical replay cannot see the future;
- `runtime_state` has no history, so it is used only in CURRENT mode (and is
  flagged as last-known when the runtime heartbeat is not fresh).

Account identity (login, server, company) never enters a snapshot.
Missing tables (an older production schema) are a recorded limitation, never
an error.
"""

from __future__ import annotations

import json
import sqlite3
import statistics
import time
from pathlib import Path

from adaptive_scalper.ai_desk.contracts import EvidenceItem, Snapshot
from adaptive_scalper.ai_desk.security import sanitize_untrusted
from adaptive_scalper.config.constants import HARD_RISK_CEILINGS, RETIRED_STRATEGY_KEYS
from adaptive_scalper.persistence.database import connect_readonly

BAR_SECONDS = {"M1": 60, "M2": 120, "M3": 180, "M5": 300, "M15": 900}
MARKET_BAR_COUNT = 300
M1_BAR_COUNT = 30
_ACCOUNT_KEYS_KEPT = ("balance", "equity", "currency", "trade_mode", "margin_free", "trade_allowed")
_IDENTITY_KEYS = frozenset({"account_login", "login", "server", "account_server", "company", "name"})


def open_trading_db_readonly(db_path: str | Path) -> sqlite3.Connection:
    """The ONLY way the desk touches the trading database."""
    return connect_readonly(db_path)


class _ReadOnly:
    def __init__(self, path):
        self.path = path
        self.conn: sqlite3.Connection | None = None

    def __enter__(self) -> sqlite3.Connection:
        self.conn = open_trading_db_readonly(self.path)
        return self.conn

    def __exit__(self, *exc):
        if self.conn is not None:
            self.conn.close()
        return False


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def _state(conn, key):
    row = conn.execute("SELECT value_json, updated_at_utc FROM runtime_state WHERE key=?", (key,)).fetchone()
    if row is None:
        return None, None
    try:
        return json.loads(row["value_json"]), int(row["updated_at_utc"])
    except (ValueError, TypeError):
        return None, int(row["updated_at_utc"])


def _quantile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    return s[min(len(s) - 1, round(q * (len(s) - 1)))]


class SnapshotBuilder:
    def __init__(self, trading_db_path: str | Path, *, symbols: list[str], resolution: str = "M5",
                 runtime_state_stale_seconds: int = 120, trading_config=None) -> None:
        if resolution not in BAR_SECONDS:
            raise ValueError(f"unsupported resolution {resolution}")
        self.db_path = trading_db_path
        self.symbols = list(symbols)
        self.resolution = resolution
        self.runtime_state_stale_seconds = runtime_state_stale_seconds
        self.trading_config = trading_config

    def build(self, *, as_of_utc: int | None = None, mode: str = "CURRENT",
              prior_assessments: dict[str, dict] | None = None) -> Snapshot:
        as_of = int(as_of_utc if as_of_utc is not None else time.time())
        items: list[EvidenceItem] = []
        limitations: list[str] = []
        runtime_live = False
        with _ReadOnly(self.db_path) as conn:
            conn.execute("BEGIN")       # one consistent read snapshot across every query
            try:
                if mode == "CURRENT":
                    runtime_live = self._runtime_state(conn, as_of, items, limitations)
                else:
                    limitations.append("REPLAY: runtime_state has no history and is excluded; kill switch and "
                                       "live telemetry are not reconstructable for past instants")
                for sym in self.symbols:
                    self._bars(conn, sym, self.resolution, MARKET_BAR_COUNT, "MARKET_BARS", as_of, items, limitations)
                    self._bars(conn, sym, "M1", M1_BAR_COUNT, "MARKET_BARS_M1", as_of, items, limitations)
                self._config(items)
                if mode == "CURRENT":
                    self._kill_switch(conn, items)
                self._drawdown(conn, as_of, items, limitations)
                self._incidents(conn, as_of, items)
                self._trades(conn, as_of, items, limitations)
                self._execution_costs(conn, as_of, items)
                self._shadow(conn, as_of, items, limitations)
                self._decisions(conn, as_of, items)
                self._news(conn, as_of, mode, items, limitations)
                self._research(conn, as_of, items)
            finally:
                conn.execute("COMMIT")
        for sym, prior in sorted((prior_assessments or {}).items()):
            ts = prior.get("as_of_epoch")
            if ts is not None and ts <= as_of:
                items.append(EvidenceItem.make(f"ai_desk_journal:market_analyst:{sym}", "PRIOR_DESK_ASSESSMENT",
                                               prior, ts, symbol=sym))
        return Snapshot(as_of_utc=as_of, mode=mode, evidence=tuple(items), limitations=tuple(limitations),
                        runtime_live=runtime_live)

    # ---------------------------------------------------------------- sources
    def _runtime_state(self, conn, as_of, items, limitations) -> bool:
        engine, engine_ts = _state(conn, "engine")
        heartbeat = (engine or {}).get("heartbeat_utc") or engine_ts
        live = (heartbeat is not None and 0 <= as_of - int(heartbeat) <= self.runtime_state_stale_seconds
                and (engine or {}).get("state") == "RUNNING")
        if not live:
            age = None if heartbeat is None else as_of - int(heartbeat)
            limitations.append(f"trading runtime not live (heartbeat age {age}s): runtime_state values are "
                               "last-known, not current")
        if engine is not None:
            items.append(EvidenceItem.make("runtime_state:engine", "RUNTIME_ENGINE", {
                "state": engine.get("state"), "mode": engine.get("mode"), "heartbeat_utc": heartbeat,
                "broker_truth": engine.get("broker_truth"), "degraded_components": engine.get("degraded_components"),
                "safety_critical_degraded": engine.get("safety_critical_degraded"), "runtime_fresh": live,
            }, heartbeat))
        for key, kind in (("reconciliation", "RECONCILIATION"), ("broker_truth", "BROKER_TRUTH"),
                          ("why_no_trade", "WHY_NO_TRADE"), ("risk_limits", "RISK_LIMITS_RUNTIME"),
                          ("multi_position_readiness", "MULTI_POSITION_READINESS"),
                          ("symbol_admission", "SYMBOL_ADMISSION"), ("news", "NEWS_RUNTIME")):
            value, ts = _state(conn, key)
            if value is not None and ts is not None and ts <= as_of:
                if isinstance(value, dict):
                    value = {k: v for k, v in value.items() if k not in _IDENTITY_KEYS}
                items.append(EvidenceItem.make(f"runtime_state:{key}", kind, value, ts))
        tel, ts = _state(conn, "live_telemetry")
        if tel is not None and ts is not None and ts <= as_of:
            account = {k: (tel.get("account") or {}).get(k) for k in _ACCOUNT_KEYS_KEPT}
            positions = [{k: p.get(k) for k in ("symbol", "canonical_symbol", "type", "direction", "volume",
                                                 "price_open", "sl", "tp", "profit", "magic")}
                         for p in tel.get("positions") or []]
            items.append(EvidenceItem.make("runtime_state:live_telemetry", "ACCOUNT_TELEMETRY", {
                "account": account, "positions": positions, "quotes": tel.get("quotes") or {},
                "sampled_at_utc": tel.get("sampled_at_utc"), "errors": tel.get("errors") or []}, ts))
        peak, ts = _state(conn, "peak_equity")
        if peak is not None and ts is not None and ts <= as_of:
            safe = {k: v for k, v in peak.items() if k not in _IDENTITY_KEYS} if isinstance(peak, dict) else peak
            items.append(EvidenceItem.make("runtime_state:peak_equity", "PEAK_EQUITY_RUNTIME", safe, ts))
        micro, ts = _state(conn, "microstructure")
        if isinstance(micro, dict) and ts is not None and ts <= as_of:
            for sym, candle in sorted((micro.get("candles") or {}).items()):
                if sym in self.symbols and isinstance(candle, dict):
                    items.append(EvidenceItem.make("runtime_state:microstructure", "RUNTIME_MICROSTRUCTURE",
                                                   candle, candle.get("bar_time_utc") or ts, symbol=sym))
            for sym, vwap in sorted((micro.get("vwap") or {}).items()):
                if sym in self.symbols:
                    items.append(EvidenceItem.make("runtime_state:microstructure.vwap", "RUNTIME_VWAP", vwap, ts,
                                                   symbol=sym))
        for sym in self.symbols:
            value, ts = _state(conn, f"regime:{sym}")
            if isinstance(value, dict) and ts is not None and ts <= as_of:
                items.append(EvidenceItem.make(f"runtime_state:regime:{sym}", "RUNTIME_REGIME", value,
                                               value.get("bar_time") or ts, symbol=sym))
        return live

    def _bars(self, conn, sym, resolution, count, kind, as_of, items, limitations) -> None:
        secs = BAR_SECONDS[resolution]
        rows = conn.execute(
            "SELECT ts_utc, open, high, low, close, tick_volume, spread, real_volume FROM bars "
            "WHERE canonical_symbol=? AND resolution=? AND ts_utc + ? <= ? ORDER BY ts_utc DESC LIMIT ?",
            (sym, resolution, secs, as_of, count)).fetchall()
        if not rows:
            limitations.append(f"no closed {resolution} bars for {sym} at or before as-of")
            return
        bars = [[r["ts_utc"], r["open"], r["high"], r["low"], r["close"], r["tick_volume"], r["spread"],
                 r["real_volume"]] for r in reversed(rows)]
        items.append(EvidenceItem.make(f"bars:{sym}:{resolution}", kind,
                                       {"resolution": resolution, "bar_seconds": secs, "bars": bars},
                                       bars[-1][0] + secs, symbol=sym))

    def _config(self, items) -> None:
        cfg = self.trading_config
        strategies: dict = {"retired": sorted(RETIRED_STRATEGY_KEYS), "entry_suspended": [], "costs": {}}
        risk = dict(HARD_RISK_CEILINGS)
        if cfg is not None:
            strategies["entry_suspended"] = sorted(cfg.strategies.entry_suspended)
            risk = {k: getattr(cfg.risk, k) for k in HARD_RISK_CEILINGS}
            for sym, c in sorted((cfg.costs or {}).items()):
                strategies["costs"][sym] = {"provenance": c.provenance, "max_spread_price": c.max_spread_price,
                                            "slippage_price": c.slippage_price,
                                            "commission_per_lot_round_trip": c.commission_per_lot_round_trip}
            strategies["entry_window"] = cfg.entry_window.model_dump()
            strategies["entry_regime"] = cfg.entry_regime.model_dump()
            strategies["news_windows"] = {"pre_high_impact_minutes": cfg.news.pre_high_impact_minutes,
                                          "post_high_impact_minutes": cfg.news.post_high_impact_minutes}
        items.append(EvidenceItem.make("config:strategies", "STRATEGY_CONFIG", strategies, None))
        items.append(EvidenceItem.make("config:risk", "RISK_LIMITS_CONFIG",
                                       {"configured": risk, "hard_ceilings": dict(HARD_RISK_CEILINGS)}, None))

    def _kill_switch(self, conn, items) -> None:
        from adaptive_scalper.core.kill_switch import (
            get_state,  # a pure SELECT, on a read-only connection
        )

        state = get_state(conn)
        items.append(EvidenceItem.make("app_state:kill_switch", "KILL_SWITCH",
                                       {"status": state.status.value, "reason": state.reason,
                                        "blocks_new_entries": state.blocks_new_entries,
                                        "changed_at": state.changed_at}, None))

    def _drawdown(self, conn, as_of, items, limitations) -> None:
        if _table_exists(conn, "peak_equity_history"):
            row = conn.execute(
                "SELECT event_type, peak_equity, observed_equity, recorded_at_utc FROM peak_equity_history "
                "WHERE recorded_at_utc <= ? ORDER BY id DESC LIMIT 1", (as_of,)).fetchone()
            if row is not None:
                items.append(EvidenceItem.make("peak_equity_history", "PEAK_EQUITY_HISTORY", dict(row),
                                               row["recorded_at_utc"]))
        else:
            limitations.append("peak_equity_history absent (pre-0033 schema)")
        if _table_exists(conn, "project_drawdown_events"):
            row = conn.execute(
                "SELECT event_type, latched, reason, recorded_at_utc FROM project_drawdown_events "
                "WHERE recorded_at_utc <= ? ORDER BY id DESC LIMIT 1", (as_of,)).fetchone()
            if row is not None:
                items.append(EvidenceItem.make("project_drawdown_events", "PROJECT_DRAWDOWN", dict(row),
                                               row["recorded_at_utc"]))

    def _incidents(self, conn, as_of, items) -> None:
        rows = conn.execute(
            "SELECT incident_type, COUNT(*) AS n, MAX(detected_at_utc) AS last FROM execution_incidents "
            "WHERE detected_at_utc <= ? AND (resolved_at_utc IS NULL OR resolved_at_utc > ?) GROUP BY incident_type",
            (as_of, as_of)).fetchall()
        payload = {r["incident_type"]: {"open": r["n"], "last_detected_utc": r["last"]} for r in rows}
        items.append(EvidenceItem.make("execution_incidents", "EXECUTION_INCIDENTS", payload,
                                       max((r["last"] for r in rows), default=None)))

    def _trades(self, conn, as_of, items, limitations) -> None:
        rows = conn.execute(
            "SELECT p.id, p.canonical_symbol, p.direction, p.strategy_key, p.initial_monetary_risk, "
            "p.opened_at_utc, p.closed_at_utc, e.entry_regime, e.strategy_version, "
            "(SELECT SUM(d.profit + d.commission + d.swap + COALESCE(d.fee, 0)) FROM deals d "
            "  WHERE d.broker_position_id = p.broker_position_id AND d.occurred_at_utc <= ?) AS net, "
            "(SELECT SUM(d.commission + d.swap + COALESCE(d.fee, 0)) FROM deals d "
            "  WHERE d.broker_position_id = p.broker_position_id AND d.occurred_at_utc <= ?) AS costs "
            "FROM positions p LEFT JOIN position_entry_context e ON e.broker_position_id = p.broker_position_id "
            "WHERE p.status = 'CLOSED' AND p.closed_at_utc IS NOT NULL AND p.closed_at_utc <= ? "
            "ORDER BY p.closed_at_utc, p.id", (as_of, as_of, as_of)).fetchall()
        trades = []
        for r in rows:
            risk, net = r["initial_monetary_risk"], r["net"]
            trades.append({
                "id": r["id"], "symbol": r["canonical_symbol"], "direction": r["direction"],
                "strategy": r["strategy_key"], "strategy_version": r["strategy_version"],
                "opened_utc": r["opened_at_utc"], "closed_utc": r["closed_at_utc"],
                "net_profit": None if net is None else round(net, 4),
                "commission_swap_fee": None if r["costs"] is None else round(r["costs"], 4),
                "initial_risk": risk,
                "r": None if (net is None or not risk) else round(net / risk, 4),
                "entry_regime": r["entry_regime"], "hour_utc": (r["opened_at_utc"] // 3600) % 24,
            })
        if not trades:
            limitations.append("no closed DEMO trades at or before as-of")
        items.append(EvidenceItem.make("positions+deals", "DEMO_TRADES", {"trades": trades},
                                       trades[-1]["closed_utc"] if trades else None))

    def _execution_costs(self, conn, as_of, items) -> None:
        rows = conn.execute(
            "SELECT canonical_symbol, spread_price, slippage_price, decided_at_utc FROM execution_cost_observations "
            "WHERE decided_at_utc <= ?", (as_of,)).fetchall()
        by: dict[str, list] = {}
        for r in rows:
            by.setdefault(r["canonical_symbol"], []).append(r)
        payload = {}
        for sym, rs in sorted(by.items()):
            spreads = [r["spread_price"] for r in rs if r["spread_price"] is not None]
            slips = [abs(r["slippage_price"]) for r in rs if r["slippage_price"] is not None]
            payload[sym] = {
                "n": len(rs), "spread_mean": statistics.fmean(spreads) if spreads else None,
                "spread_p95": _quantile(spreads, 0.95),
                "abs_slippage_mean": statistics.fmean(slips) if slips else None,
                "abs_slippage_p95": _quantile(slips, 0.95),
            }
        items.append(EvidenceItem.make("execution_cost_observations", "EXECUTION_COSTS", payload,
                                       max((r["decided_at_utc"] for r in rows), default=None)))

    def _shadow(self, conn, as_of, items, limitations) -> None:
        if not _table_exists(conn, "shadow_candidates"):
            limitations.append("shadow observer tables absent")
            return
        cands = conn.execute(
            "SELECT id, canonical_symbol, strategy_key, direction, decision_time_utc, confirmed_regime, session, "
            "news_status, spread_percentile, movement_to_cost, atr, estimated_round_trip_cost_price, cost_provenance, "
            "selector_disposition, rejection_reason, final_permission_result, raw_score "
            "FROM shadow_candidates WHERE decision_time_utc <= ? ORDER BY decision_time_utc DESC, id DESC LIMIT 1000",
            (as_of,)).fetchall()
        ids = [c["id"] for c in cands]
        outcomes: dict[int, dict] = {}
        if ids:
            q = ",".join("?" * len(ids))
            for o in conn.execute(
                    "SELECT candidate_id, horizon_seconds, net_r_estimated, gross_r, resolved_at_utc FROM shadow_outcomes "
                    f"WHERE status='RESOLVED' AND resolved_at_utc <= ? AND candidate_id IN ({q})", (as_of, *ids)):
                outcomes.setdefault(o["candidate_id"], {})[str(o["horizon_seconds"])] = {
                    "net_r": o["net_r_estimated"], "gross_r": o["gross_r"], "resolved_utc": o["resolved_at_utc"]}
        payload = [{**dict(c), "outcomes": outcomes.get(c["id"], {})} for c in reversed(cands)]
        items.append(EvidenceItem.make("shadow_candidates+outcomes", "SHADOW_CANDIDATES", {"candidates": payload},
                                       max((c["decision_time_utc"] for c in cands), default=None)))

    def _decisions(self, conn, as_of, items) -> None:
        summary: dict = {}
        for label, window in (("last_24h", 86400), ("last_7d", 7 * 86400)):
            rows = conn.execute(
                "SELECT decision, reason, COUNT(*) AS n FROM entry_decisions WHERE decided_at_utc <= ? "
                "AND decided_at_utc > ? GROUP BY decision, reason ORDER BY n DESC, decision, reason LIMIT 15",
                (as_of, as_of - window)).fetchall()
            summary[label] = [{"decision": r["decision"], "reason": r["reason"], "n": r["n"]} for r in rows]
        latest = {}
        for r in conn.execute(
                "SELECT canonical_symbol, decision, reason, MAX(decided_at_utc) AS t FROM entry_decisions "
                "WHERE decided_at_utc <= ? GROUP BY canonical_symbol", (as_of,)):
            latest[r["canonical_symbol"] or "GLOBAL"] = {"decision": r["decision"], "reason": r["reason"], "at": r["t"]}
        summary["latest"] = latest
        items.append(EvidenceItem.make("entry_decisions", "ENTRY_DECISIONS", summary,
                                       max((v["at"] for v in latest.values()), default=None)))

    def _news(self, conn, as_of, mode, items, limitations) -> None:
        rows = conn.execute(
            "SELECT event_id, provider, scheduled_at_utc, country, currency, title, normalized_event_type, impact, "
            "actual, forecast, previous, retrieved_at_utc, revision, source_reliability FROM news_events "
            "WHERE retrieved_at_utc <= ? AND scheduled_at_utc BETWEEN ? AND ? ORDER BY scheduled_at_utc, event_id",
            (as_of, as_of - 6 * 3600, as_of + 48 * 3600)).fetchall()
        events = [{**dict(r), "title": sanitize_untrusted(r["title"], 160)} for r in rows]
        items.append(EvidenceItem.make("news_events:window", "NEWS_EVENTS", {"events": events},
                                       max((r["retrieved_at_utc"] for r in rows), default=None)))
        if not rows:
            limitations.append("no calendar events retrieved for the [-6h, +48h] window around as-of")
        past = conn.execute(
            "SELECT scheduled_at_utc, currency, MAX(normalized_event_type) AS normalized_event_type, "
            "MAX(title) AS title FROM news_events WHERE impact='HIGH' AND retrieved_at_utc <= ? "
            "AND scheduled_at_utc <= ? GROUP BY scheduled_at_utc, currency ORDER BY scheduled_at_utc DESC LIMIT 300",
            (as_of, as_of - 3600)).fetchall()
        items.append(EvidenceItem.make("news_events:history", "NEWS_HISTORY",
                                       {"events": [{**dict(r), "title": sanitize_untrusted(r["title"], 160)}
                                                   for r in past]},
                                       max((r["scheduled_at_utc"] for r in past), default=None)))
        if mode == "CURRENT":
            prov = conn.execute("SELECT provider, last_success_at_utc, last_attempt_at_utc, health, last_error "
                                "FROM news_provider_state ORDER BY provider").fetchall()
            items.append(EvidenceItem.make("news_provider_state", "NEWS_PROVIDERS",
                                           [{**dict(p), "last_error": sanitize_untrusted(p["last_error"], 200)}
                                            for p in prov],
                                           max((p["last_attempt_at_utc"] or 0 for p in prov), default=0) or None))

    def _research(self, conn, as_of, items) -> None:
        rows = conn.execute(
            "SELECT trial_id, family, kind, status, sharpe, n_observations, "
            "(cost_model_json IS NOT NULL AND cost_model_json NOT IN ('', 'null', '{}')) AS has_cost_model, "
            "created_at_utc FROM research_trials WHERE created_at_utc <= ? ORDER BY created_at_utc, trial_id",
            (as_of,)).fetchall()
        items.append(EvidenceItem.make("research_trials", "RESEARCH_TRIALS", {"trials": [dict(r) for r in rows]},
                                       max((r["created_at_utc"] for r in rows), default=None)))
