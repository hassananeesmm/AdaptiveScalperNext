"""AI Trading Desk dashboard panels (observer-only).

Computed from the desk journal opened with `connect_readonly` (SQLite rejects
writes), on demand, inside the existing dashboard process. Nothing here can
reach MT5, the kill switch, the risk governor or the trading database's write
path. Every panel labels what it shows: AI ADVICE versus the ENGINE's actual
decision, DEMO versus SHADOW/HISTORICAL SIMULATION, and UNVERIFIED research.
"""

from __future__ import annotations

import contextlib
import json
import sqlite3
import time
from pathlib import Path

from adaptive_scalper.ai_desk import ADVISORY_NOTICE
from adaptive_scalper.ai_desk.configuration import AiDeskConfig, load_ai_desk_config
from adaptive_scalper.ai_desk.health import agent_overview
from adaptive_scalper.persistence.database import connect_readonly

AI_PANELS = ("ai_overview", "ai_market", "ai_research", "ai_learning", "ai_health")


def _latest(conn, agent: str, symbol: str | None = None) -> dict | None:
    q = "SELECT result_json FROM agent_results WHERE agent=?"
    args: tuple = (agent,)
    if symbol is not None:
        q += " AND symbol=?"
        args += (symbol,)
    row = conn.execute(q + " ORDER BY as_of_utc DESC, created_at_utc DESC LIMIT 1", args).fetchone()
    return json.loads(row[0]) if row else None


def _overview(conn, cfg: AiDeskConfig, now: int) -> dict:
    service = conn.execute("SELECT value_json, updated_at_utc FROM service_state WHERE key='service'").fetchone()
    hb = json.loads(service[0]).get("heartbeat_utc") if service else None
    agents = agent_overview(conn, now=now)
    return {"notice": ADVISORY_NOTICE, "research_service_enabled": cfg.enabled, "provider": cfg.provider,
            "service_heartbeat_age_seconds": None if hb is None else now - hb,
            "agents": [{"agent": a, "status": i["last_status"], "last_run_age_s": i["last_run_age_seconds"],
                        "last_success_utc": i["last_success_utc"], "last_error": i["last_error"],
                        "provider": i["provider"], "avg_latency_ms": i["avg_latency_ms_last20"]}
                       for a, i in agents.items()]}


def _market(conn, cfg: AiDeskConfig) -> dict:
    out = {"label": "AI ADVICE - market conditions (descriptive, not a price forecast)", "symbols": {}}
    for sym in cfg.symbols:
        r = _latest(conn, "market_analyst", sym)
        if r is None:
            continue
        f = r["findings"]
        changes = []
        for row in conn.execute("SELECT result_json FROM agent_results WHERE agent='market_analyst' AND symbol=? "
                                "ORDER BY as_of_utc DESC LIMIT 50", (sym,)):
            ch = json.loads(row[0])["findings"].get("change")
            if ch:
                changes.append({**ch, "as_of": json.loads(row[0])["as_of_utc"]})
            if len(changes) >= 8:
                break
        out["symbols"][sym] = {"as_of": r["as_of_utc"], "status": r["status"], "condition": f.get("condition"),
                               "regime": f.get("regime"), "trend": f.get("trend"), "adx14": f.get("adx14"),
                               "volatility": f.get("vol_bucket"), "basis": f.get("basis"),
                               "freshness": f.get("data_freshness"), "conflicts": f.get("conflicts"),
                               "recent_changes": changes, "limitations": r["limitations"]}
    return out


def _research(conn) -> dict:
    coord = _latest(conn, "coordinator")
    skeptic = _latest(conn, "skeptic")
    risk = _latest(conn, "risk_analyst")
    strat = _latest(conn, "strategy_analyst")
    news = _latest(conn, "news_analyst")
    summary = (coord or {}).get("findings", {}).get("summary", {})
    disagreements = [{"topic": r["topic"], "parties": json.loads(r["parties_json"]),
                      "positions": json.loads(r["positions_json"]), "severity": r["severity"],
                      "resolution": r["resolution"]}
                     for r in conn.execute("SELECT * FROM disagreements ORDER BY created_at_utc DESC LIMIT 20")]
    return {
        "ai_advice": {"label": "AI ADVICE (no execution authority)", "as_of": (coord or {}).get("as_of_utc"),
                      "summary_lines": summary.get("lines"), "overall": summary.get("overall"),
                      "llm_narrative": (coord or {}).get("llm_narrative"),
                      "narrative_status": (coord or {}).get("llm_narrative_status")},
        "engine_decision": {"label": "ACTUAL TRADING-ENGINE DECISION (authoritative)",
                            "latest_global_block": summary.get("engine_decision_reference")},
        "signal_reference": {"recent_proposals": ((risk or {}).get("findings") or {}).get("recent_proposals")},
        "risk_warnings": ((risk or {}).get("findings") or {}).get("warnings", [])[:12],
        "skeptic": {"verdict_counts": ((skeptic or {}).get("findings") or {}).get("verdict_counts"),
                    "objections": (skeptic or {}).get("counterarguments", [])[:8]},
        "supported_claims": summary.get("supported_claims", [])[:15],
        "unsupported_claims": summary.get("unsupported_claims", [])[:15],
        "strategy_hypotheses": {"label": "UNVERIFIED RESEARCH - untested hypotheses",
                                "items": ((strat or {}).get("findings") or {}).get("hypotheses", [])[:10]},
        "news": {"imminent_high_impact": ((news or {}).get("findings") or {}).get("imminent_high_impact"),
                 "engine_news_decision": ((news or {}).get("findings") or {}).get("engine_news_decision")},
        "disagreements": disagreements,
    }


def _learning(conn) -> dict:
    ev = conn.execute("SELECT result_json, created_at_utc, trial_number FROM evaluations WHERE kind='FULL' "
                      "ORDER BY created_at_utc DESC LIMIT 1").fetchone()
    learn = _latest(conn, "learning_analyst")
    lf = (learn or {}).get("findings") or {}
    out = {"learning_analyst": {"as_of": (learn or {}).get("as_of_utc"),
                                "drift": lf.get("feature_drift"), "deterioration": lf.get("deterioration"),
                                "recommendations": lf.get("recommendations"),
                                "blocked_opportunities": lf.get("blocked_opportunities")}}
    if ev is None:
        out["evaluation"] = {"status": "NO_DATA", "detail": "run `ai-desk evaluate` to compare against the baseline"}
        return out
    res = json.loads(ev[0])
    abl = res.get("ablation", {})
    table = []
    for ds_key, ds in (abl.get("datasets") or {}).items():
        for name, c in ds.get("configs", {}).items():
            table.append({"dataset": (abl.get("labels") or {}).get(ds_key, ds_key), "config": name,
                          "kept_n": c["kept"].get("n"), "kept_expectancy": c["kept"].get("expectancy"),
                          "kept_ci95": c["kept"].get("expectancy_ci95"), "improvement_ci95": c.get("improvement_ci95"),
                          "false_rejection_rate": c.get("false_rejection_rate"), "verdict": c.get("verdict")})
    out["evaluation"] = {"label": "UNVERIFIED RESEARCH - shadow comparison, not a deployment permission",
                         "evaluated_at_utc": ev[1], "trial_number": ev[2], "conclusion": abl.get("conclusion"),
                         "ablation": table, "market_replay": res.get("market_replay"),
                         "forecast_track_record": (res.get("operations") or {}).get("forecast_track_record"),
                         "forward_checks": res.get("forward_checks")}
    return out


def _health(conn, cfg: AiDeskConfig, now: int) -> dict:
    from adaptive_scalper.ai_desk.providers.base import utc_day_start

    usage = conn.execute("SELECT COALESCE(SUM(input_tokens + output_tokens), 0), COALESCE(SUM(cost_usd), 0), COUNT(*) "
                         "FROM provider_calls WHERE created_at_utc >= ?", (utc_day_start(now),)).fetchone()
    errors = [dict(r) for r in conn.execute("SELECT provider, status, error, created_at_utc FROM provider_calls "
                                            "WHERE status != 'OK' ORDER BY created_at_utc DESC LIMIT 10")]
    runs = {r[0]: r[1] for r in conn.execute("SELECT status, COUNT(*) FROM desk_runs GROUP BY status")}
    stale = [dict(r) for r in conn.execute(
        "SELECT agent, MAX(created_at_utc) AS last FROM agent_results GROUP BY agent HAVING ? - last > 3 * 3600",
        (now,))]
    return {"runs": runs, "provider_errors": errors,
            "usage_today": {"tokens": usage[0], "cost_usd": round(usage[1], 6), "calls": usage[2]},
            "budget": {"daily_token_budget": cfg.budget.daily_token_budget,
                       "daily_cost_budget_usd": cfg.budget.daily_cost_budget_usd},
            "cache_entries": conn.execute("SELECT COUNT(*) FROM narrative_cache").fetchone()[0],
            "pending_forward_checks": conn.execute("SELECT COUNT(*) FROM forward_checks WHERE status='PENDING'"
                                                   ).fetchone()[0],
            "stale_agents_over_3h": stale,
            "configuration": {"enabled": cfg.enabled, "provider": cfg.provider,
                              "external_calls_permitted": cfg.external_calls_permitted(),
                              "allowed_external_data": cfg.allowed_external_data}}


def ai_desk_panels(journal_path: str | Path, *, config_path: str | Path = "config/ai_desk.toml",
                   now: int | None = None) -> dict:
    now = int(now or time.time())
    try:
        cfg = load_ai_desk_config(config_path)
    except Exception as exc:  # noqa: BLE001 - a bad desk config degrades only these panels
        return {name: {"status": "UNAVAILABLE", "detail": f"ai_desk config: {exc}"} for name in AI_PANELS}
    if not Path(journal_path).exists():
        return {name: {"status": "NO_DATA", "detail": "the AI desk journal does not exist yet - run "
                                                      "`ai-desk run-once` or start the research service"}
                for name in AI_PANELS}
    try:
        with contextlib.closing(connect_readonly(journal_path)) as conn:
            return {"ai_overview": _overview(conn, cfg, now), "ai_market": _market(conn, cfg),
                    "ai_research": _research(conn), "ai_learning": _learning(conn),
                    "ai_health": _health(conn, cfg, now)}
    except sqlite3.Error as exc:
        return {name: {"status": "UNAVAILABLE", "detail": f"AI desk journal not readable: {exc}"} for name in AI_PANELS}
