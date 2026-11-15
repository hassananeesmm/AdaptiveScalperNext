"""Desk health and status, computed from the journal (read-only use)."""

from __future__ import annotations

import json
import time

from adaptive_scalper.ai_desk.configuration import AGENT_KEYS, AiDeskConfig


def agent_overview(conn, *, now: int | None = None) -> dict:
    now = int(now or time.time())
    out = {}
    for agent in AGENT_KEYS:
        last = conn.execute("SELECT status, created_at_utc, duration_ms, provider, assessment FROM agent_results "
                            "WHERE agent=? ORDER BY created_at_utc DESC LIMIT 1", (agent,)).fetchone()
        ok = conn.execute("SELECT MAX(created_at_utc) FROM agent_results WHERE agent=? AND status IN "
                          "('COMPLETE', 'INCOMPLETE')", (agent,)).fetchone()[0]
        err = conn.execute("SELECT created_at_utc, result_json FROM agent_results WHERE agent=? AND status='FAILED' "
                           "ORDER BY created_at_utc DESC LIMIT 1", (agent,)).fetchone()
        lat = conn.execute("SELECT AVG(duration_ms) FROM (SELECT duration_ms FROM agent_results WHERE agent=? "
                           "ORDER BY created_at_utc DESC LIMIT 20)", (agent,)).fetchone()[0]
        out[agent] = {
            "last_status": last["status"] if last else "NEVER_RUN",
            "last_run_utc": last["created_at_utc"] if last else None,
            "last_run_age_seconds": now - last["created_at_utc"] if last else None,
            "last_success_utc": ok,
            "last_error": (json.loads(err["result_json"]).get("limitations") or [None])[0] if err else None,
            "last_error_utc": err["created_at_utc"] if err else None,
            "provider": last["provider"] if last else None,
            "avg_latency_ms_last20": round(lat, 1) if lat is not None else None,
            "latest_assessment": last["assessment"][:300] if last else None,
        }
    return out


def health_report(journal, config: AiDeskConfig, *, now: int | None = None, router=None) -> dict:
    now = int(now or time.time())
    conn = journal.conn
    service = journal.get_state("service") or {}
    hb = service.get("heartbeat_utc") if isinstance(service, dict) else None
    hb_age = None if hb is None else now - hb
    agents = agent_overview(conn, now=now)
    stale = []
    for a, info in agents.items():
        interval = getattr(config.intervals, a, config.intervals.scheduled_report)
        if info["last_run_age_seconds"] is not None and info["last_run_age_seconds"] > 3 * max(interval, 60):
            stale.append(a)
    runs = {r[0]: r[1] for r in conn.execute("SELECT status, COUNT(*) FROM desk_runs GROUP BY status")}
    last_run = conn.execute("SELECT run_id, status, trigger, started_at_utc, error FROM desk_runs "
                            "ORDER BY started_at_utc DESC LIMIT 1").fetchone()
    errors = [dict(r) for r in conn.execute(
        "SELECT provider, status, error, created_at_utc FROM provider_calls WHERE status != 'OK' "
        "ORDER BY created_at_utc DESC LIMIT 10")]
    from adaptive_scalper.ai_desk.providers.base import utc_day_start

    usage = journal.usage_since(utc_day_start(now))
    pending = conn.execute("SELECT COUNT(*) FROM forward_checks WHERE status='PENDING'").fetchone()[0]
    cache = conn.execute("SELECT COUNT(*) FROM narrative_cache").fetchone()[0]
    if not config.enabled:
        state = "DISABLED"
    elif hb_age is None:
        state = "NOT_RUNNING"
    elif hb_age > max(120, 4 * config.poll_seconds):
        state = "STALLED"
    elif (service.get("consecutive_errors") or 0) > 0 or runs.get("FAILED", 0) and last_run and \
            last_run["status"] == "FAILED":
        state = "DEGRADED"
    else:
        state = "RUNNING"
    return {
        "state": state, "service_heartbeat_age_seconds": hb_age, "service": service,
        "config": {"enabled": config.enabled, "provider": config.provider, "fallbacks": config.fallback_providers,
                   "external_calls_permitted": config.external_calls_permitted(),
                   "allowed_external_data": config.allowed_external_data,
                   "daily_token_budget": config.budget.daily_token_budget,
                   "daily_cost_budget_usd": config.budget.daily_cost_budget_usd,
                   "fingerprint": config.fingerprint()},
        "runs": runs, "last_run": dict(last_run) if last_run else None, "agents": agents, "stale_agents": stale,
        "provider_errors": errors, "usage_today": usage, "providers": router.status() if router else None,
        "pending_forward_checks": pending, "narrative_cache_entries": cache,
        "execution_authority": "NONE - advisory research only",
    }
