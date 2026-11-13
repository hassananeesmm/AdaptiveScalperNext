"""`ai-desk`: the AI Trading Desk research service (ADVISORY ONLY).

    ai-desk doctor     configuration, databases, providers (no secrets printed)
    ai-desk status     service state, last run, agent statuses
    ai-desk agents     per-agent last run / success / error / latency
    ai-desk run-once   one analysis cycle now (manual; works while the service is disabled)
    ai-desk serve      the autonomous event-driven service (requires enabled = true)
    ai-desk report     the latest Coordinator summary and key findings
    ai-desk evaluate   forward scoring, ablation vs the unchanged baseline, as-of replay
    ai-desk health     full health JSON

None of these commands can place an order, touch the kill switch, change a
risk limit or a strategy. The trading database is opened READ-ONLY (never
migrated here); the desk writes only its own journal.
"""

from __future__ import annotations

import argparse
import json
import time

from adaptive_scalper.cli.common import CliError, parse_utc, print_json
from adaptive_scalper.config.loader import load_config


def _load(args):
    from adaptive_scalper.ai_desk.configuration import (
        AiDeskConfigError,
        load_ai_desk_config,
    )

    try:
        desk = load_ai_desk_config(args.ai_config)
    except AiDeskConfigError as exc:
        raise CliError(str(exc)) from exc
    trading = load_config(args.config)
    return desk, trading


def _journal(desk, trading):
    from adaptive_scalper.ai_desk.journal import Journal, JournalError

    try:
        return Journal(desk.journal_path, trading_db_path=trading.database.path)
    except JournalError as exc:
        raise CliError(str(exc)) from exc


def _orchestrator(desk, trading, journal):
    from adaptive_scalper.ai_desk.orchestration import DeskOrchestrator

    return DeskOrchestrator(desk, trading_db_path=trading.database.path, journal=journal, trading_config=trading)


def cmd_doctor(args) -> int:
    import os
    from pathlib import Path

    from adaptive_scalper.ai_desk.snapshot_builder import open_trading_db_readonly

    desk, trading = _load(args)
    checks = []

    def check(name, ok, detail=""):
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    check("config", True, f"{args.ai_config} (fingerprint {desk.fingerprint()})")
    check("research_service_enabled", True, "enabled" if desk.enabled else
          "DISABLED (default) - `run-once` works; `serve` needs enabled = true")
    try:
        conn = open_trading_db_readonly(trading.database.path)
        try:
            conn.execute("SELECT COUNT(*) FROM runtime_state").fetchone()
            write_refused = False
            try:
                conn.execute("CREATE TABLE ai_desk_write_probe (x)")
            except Exception:  # noqa: BLE001 - expected: SQLite refuses writes on a read-only connection
                write_refused = True
            check("trading_db_read_only", write_refused, trading.database.path)
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        check("trading_db_read_only", False, f"{trading.database.path}: {exc}")
    same = Path(desk.journal_path).resolve() == Path(trading.database.path).resolve()
    check("journal_separate_from_trading_db", not same, desk.journal_path)
    try:
        j = _journal(desk, trading)
        check("journal_writable_and_migrated", True, desk.journal_path)
        j.close()
    except CliError as exc:
        check("journal_writable_and_migrated", False, str(exc))
    for p in desk.provider_chain():
        if p.kind == "deterministic":
            check(f"provider:{p.name}", True, "deterministic (offline, no cost)")
        else:
            present = bool(p.api_key_env and os.environ.get(p.api_key_env))
            check(f"provider:{p.name}", present or (p.kind == "openai_compatible" and not p.api_key_env),
                  f"{p.kind} model={p.model} key env {p.api_key_env or '(none)'} "
                  f"{'present' if present else 'NOT set'} (value never printed)")
    check("external_calls_permitted", True, str(desk.external_calls_permitted()) +
          " (needs an external provider, allowed data categories and a positive token budget)")
    ok = all(c["ok"] for c in checks)
    print_json({"ok": ok, "execution_authority": "NONE (advisory research only)", "checks": checks})
    return 0 if ok else 1


def cmd_status(args) -> int:
    from adaptive_scalper.ai_desk.health import health_report

    desk, trading = _load(args)
    j = _journal(desk, trading)
    try:
        h = health_report(j, desk)
    finally:
        j.close()
    print_json({"state": h["state"], "service_heartbeat_age_seconds": h["service_heartbeat_age_seconds"],
                "last_run": h["last_run"], "runs": h["runs"], "stale_agents": h["stale_agents"],
                "agents": {a: i["last_status"] for a, i in h["agents"].items()}, "config": h["config"],
                "execution_authority": h["execution_authority"]})
    return 0


def cmd_agents(args) -> int:
    from adaptive_scalper.ai_desk.health import agent_overview

    desk, trading = _load(args)
    j = _journal(desk, trading)
    try:
        print_json(agent_overview(j.conn))
    finally:
        j.close()
    return 0


def cmd_run_once(args) -> int:
    desk, trading = _load(args)
    j = _journal(desk, trading)
    try:
        orch = _orchestrator(desk, trading, j)
        agents = set(args.agents.split(",")) if args.agents else None
        report = orch.run_cycle(agents=agents, trigger="manual:run-once",
                                as_of_utc=parse_utc(args.as_of) if args.as_of else None, mode=args.mode)
        out = report.summary()
        if not args.verbose:
            for a in out["agents"]:
                a["assessment"] = a["assessment"][:240]
        print_json(out)
        return 0 if report.status == "COMPLETED" else 1
    finally:
        j.close()


def cmd_serve(args) -> int:
    from adaptive_scalper.ai_desk.scheduler import DeskDisabledError, DeskService

    desk, trading = _load(args)
    j = _journal(desk, trading)
    try:
        service = DeskService(desk, trading_db_path=trading.database.path, journal=j, trading_config=trading)
        print("AI Trading Desk research service - ADVISORY ONLY, no execution authority. "
              f"Polling every {desk.poll_seconds}s; Ctrl+C stops it (trading is unaffected).", flush=True)
        try:
            ticks = service.serve(max_ticks=args.max_ticks)
        except DeskDisabledError as exc:
            raise CliError(str(exc)) from exc
        except KeyboardInterrupt:
            service.stop()
            ticks = None
        print_json({"stopped": True, "ticks": ticks})
        return 0
    finally:
        j.close()


def cmd_report(args) -> int:
    desk, trading = _load(args)
    j = _journal(desk, trading)
    try:
        coord = j.latest_results("coordinator", limit=1)
        if not coord:
            raise CliError("no desk analysis yet: run `ai-desk run-once`")
        c = coord[0]
        latest = {a: j.latest_results(a, limit=3 if a == "market_analyst" else 1) for a in
                  ("market_analyst", "risk_analyst", "strategy_analyst", "news_analyst", "learning_analyst", "skeptic")}
        out = {"as_of": c["as_of_utc"], "snapshot_id": c["snapshot_id"], "summary": c["findings"]["summary"]["lines"],
               "llm_narrative": c.get("llm_narrative"),
               "agents": {a: [{"symbol": r["symbol"], "status": r["status"], "assessment": r["assessment"],
                               "limitations": r["limitations"][:4]} for r in rs] for a, rs in latest.items()},
               "risk_warnings": (latest["risk_analyst"][0]["findings"].get("warnings") if latest["risk_analyst"]
                                 else None),
               "hypotheses": (latest["strategy_analyst"][0]["findings"].get("hypotheses") if latest["strategy_analyst"]
                              else None)}
        if args.json:
            print_json(out)
        else:
            print("\n".join(out["summary"]))
            for a, rs in out["agents"].items():
                for r in rs:
                    print(f"- {a}{'/' + r['symbol'] if r['symbol'] else ''} [{r['status']}]: {r['assessment']}")
        return 0
    finally:
        j.close()


def cmd_evaluate(args) -> int:
    from adaptive_scalper.ai_desk.evaluator import run_full_evaluation
    from adaptive_scalper.research.independent import RESERVED_OOS_INTERVALS

    desk, trading = _load(args)
    j = _journal(desk, trading)
    try:
        if args.replay_start or args.replay_end:
            start, end = parse_utc(args.replay_start), parse_utc(args.replay_end)
        else:   # the 60 days immediately BEFORE the reserved untouched OOS interval
            end = min(s for s, _ in RESERVED_OOS_INTERVALS) - 1
            start = end - 60 * 86400
        symbols = args.symbols.split(",") if args.symbols else desk.symbols
        started = time.perf_counter()
        result = run_full_evaluation(j, trading.database.path, replay_ranges={s: (start, end) for s in symbols})
        result["duration_seconds"] = round(time.perf_counter() - started, 2)
        if args.json:
            print_json(result)
        else:
            ab = result["ablation"]
            print(ab["conclusion"])
            for ds, d in ab["datasets"].items():
                print(f"[{ab['labels'][ds]}] n={d['n']} baseline expectancy {d['baseline'].get('expectancy')} R "
                      f"(CI {d['baseline'].get('expectancy_ci95')})")
                for name, cfg in d["configs"].items():
                    print(f"  {name:22} kept {cfg['kept'].get('n', 0):4} exp {cfg['kept'].get('expectancy')} "
                          f"improvement CI {cfg['improvement_ci95']} -> {cfg['verdict']}")
            for sym, rep in result["market_replay"].items():
                print(f"replay {sym}: {json.dumps({k: rep.get(k) for k in ('n', 'hit_rate', 'hit_rate_ci95', 'majority_baseline', 'verdict', 'status', 'detail')})}")
            print(f"evaluation {result['evaluation_id']} trial #{result['trial_number']} "
                  f"({result['duration_seconds']}s); forward checks {result['forward_checks']}")
        return 0
    finally:
        j.close()


def cmd_health(args) -> int:
    from adaptive_scalper.ai_desk.health import health_report

    desk, trading = _load(args)
    j = _journal(desk, trading)
    try:
        print_json(health_report(j, desk))
    finally:
        j.close()
    return 0


def register(sub) -> None:
    from adaptive_scalper.ai_desk.configuration import DEFAULT_AI_DESK_CONFIG_PATH

    desk = sub.add_parser("ai-desk", help="AI Trading Desk research service (advisory only, no execution authority)")
    desk.add_argument("--ai-config", default=DEFAULT_AI_DESK_CONFIG_PATH, help="path to the AI desk TOML")
    dsub = desk.add_subparsers(dest="ai_desk_command", required=True)
    dsub.add_parser("doctor", help="check configuration, databases and providers").set_defaults(func=cmd_doctor)
    dsub.add_parser("status", help="service state and last run").set_defaults(func=cmd_status)
    dsub.add_parser("agents", help="per-agent status").set_defaults(func=cmd_agents)
    run = dsub.add_parser("run-once", help="run one analysis cycle now")
    run.add_argument("--agents", help="comma-separated agent keys (default: all)")
    run.add_argument("--as-of", help="as-of instant (epoch or ISO-8601, UTC); default now")
    run.add_argument("--mode", choices=("CURRENT", "REPLAY"), default="CURRENT")
    run.add_argument("--verbose", action="store_true")
    run.set_defaults(func=cmd_run_once)
    serve = dsub.add_parser("serve", help="autonomous event-driven research service (needs enabled = true)")
    serve.add_argument("--max-ticks", type=int, default=None, help=argparse.SUPPRESS)
    serve.set_defaults(func=cmd_serve)
    rep = dsub.add_parser("report", help="latest Coordinator summary")
    rep.add_argument("--json", action="store_true")
    rep.set_defaults(func=cmd_report)
    ev = dsub.add_parser("evaluate", help="compare the desk with the unchanged baseline (research only)")
    ev.add_argument("--replay-start", help="as-of replay start (must be outside the reserved OOS)")
    ev.add_argument("--replay-end", help="as-of replay end")
    ev.add_argument("--symbols", help="comma-separated symbols for the replay")
    ev.add_argument("--json", action="store_true")
    ev.set_defaults(func=cmd_evaluate)
    dsub.add_parser("health", help="full health report (JSON)").set_defaults(func=cmd_health)
