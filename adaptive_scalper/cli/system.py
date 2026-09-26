"""System and observability commands: preflight, doctor, status, health, symbols,
strategies, why-no-trade, journal recent, costs observed. Everything
except `doctor`'s terminal probe and `symbols` reads SQLite only."""

from __future__ import annotations

import argparse
import json
import time

from adaptive_scalper.cli.common import CliError, add_symbol_arg, open_db, open_gateway, print_json
from adaptive_scalper.config.constants import ALLOWED_CANONICAL_SYMBOLS, ALLOWED_MODES, RETIRED_STRATEGY_KEYS
from adaptive_scalper.config.loader import ConfigError
from adaptive_scalper.core import kill_switch
from adaptive_scalper.costs.observations import summarize_observations
from adaptive_scalper.dashboard.health import compute_health
from adaptive_scalper.gateway.spec_store import save_symbol_spec
from adaptive_scalper.gateway.symbol_resolver import persist_all, resolve_all
from adaptive_scalper.persistence.database import integrity_check, quick_check
from adaptive_scalper.preflight import run_preflight
from adaptive_scalper.runtime.state import decision_counts, get_state_with_age, recent_events
from adaptive_scalper.strategies import build_active_registry


def cmd_doctor(args: argparse.Namespace) -> int:
    """Config, database and (best-effort, never fatal) terminal checks."""
    try:
        cfg, conn = open_db(args.config)
    except ConfigError as exc:
        print(f"FAIL: config: {exc}")
        return 1
    problems = []
    integrity = integrity_check(conn)
    if integrity != "ok":
        problems.append(f"database integrity: {integrity}")
    version = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
    ks = kill_switch.get_state(conn)
    from adaptive_scalper.history.time_basis import get_basis

    time_basis = get_basis(conn)["basis"]
    if time_basis != "UTC":
        problems.append("stored MT5 rows are in broker server time: run `history convert-server-time` "
                        "(BUG_BACKLOG #14)")
    conn.close()

    mt5 = "not checked"
    server_clock = "not checked"
    try:
        gw = open_gateway(cfg, require_demo=False)
        account = gw.account_info()
        mt5 = f"reachable, account trade mode {account.trade_mode.name if account else 'UNKNOWN'}"
        if account is not None and account.trade_mode.name != "DEMO":
            problems.append("connected account is not DEMO: REAL-MONEY EXECUTION IS DISABLED, runtime will refuse")
        from adaptive_scalper.gateway.server_time import MISMATCH, classify_quote_clock
        from adaptive_scalper.gateway.symbol_resolver import resolve_all

        verdicts = {}
        for canonical, result in sorted(resolve_all(gw.symbols_get()).items()):
            tick = gw.symbol_info_tick(result.broker_symbol) if result.resolved else None
            if tick is not None and tick.time:
                verdict, residual = classify_quote_clock(tick.time, time.time())
                verdicts[canonical] = f"{verdict} ({residual:+.0f}s)"
        server_clock = ", ".join(f"{k} {v}" for k, v in verdicts.items()) or "no quotes"
        if any(v.startswith(MISMATCH) for v in verdicts.values()):
            problems.append(f"quotes are in the future under server_time_rule={cfg.mt5.server_time_rule}: "
                            "the runtime will refuse to start")
        gw.shutdown()
    except CliError as exc:
        mt5 = f"unavailable: {exc}"

    print(f"config: OK ({args.config}, mode={cfg.mode}; allowed modes {sorted(ALLOWED_MODES)})")
    print(f"database: integrity={integrity} schema={version} ({cfg.database.path})")
    print(f"kill switch: {ks.status.value} (blocks new entries: {ks.blocks_new_entries})")
    print(f"mt5 time: server_time_rule={cfg.mt5.server_time_rule}, stored rows {time_basis}")
    print(f"mt5: {mt5}")
    print(f"mt5 server clock vs UTC: {server_clock}")
    print("real-money execution: DISABLED")
    for problem in problems:
        print(f"PROBLEM: {problem}")
    if problems:
        return 1
    print("doctor: OK")
    return 0


def _status_integrity(conn, args: argparse.Namespace) -> tuple[str, str]:
    """ASN-008: interactive status uses the fast structural quick_check by
    default; `--full-integrity` runs the full (slow) integrity_check."""
    if getattr(args, "full_integrity", False):
        return integrity_check(conn), "integrity_check"
    return quick_check(conn), "quick_check"


def cmd_status(args: argparse.Namespace) -> int:
    cfg, conn = open_db(args.config)
    ks = kill_switch.get_state(conn)
    integrity, kind = _status_integrity(conn, args)
    report = compute_health(conn, integrity=integrity)
    engine, age = get_state_with_age(conn, "engine")
    conn.close()
    print_json({
        # `mode` is the CONFIGURED default; `runtime_mode` is what the running engine reports.
        "mode": cfg.mode, "runtime_mode": (engine or {}).get("mode"), "symbols": cfg.market.symbols,
        "health": report.state.value, "database_integrity": report.database_integrity,
        "database_integrity_check": kind,
        "kill_switch_status": ks.status.value, "kill_switch_reason": ks.reason,
        "engine": None if engine is None else {**engine, "heartbeat_age_seconds": age},
        "real_money_execution": "DISABLED",
    })
    return 0


def cmd_health(args: argparse.Namespace) -> int:
    _, conn = open_db(args.config)
    integrity, kind = _status_integrity(conn, args)
    report = compute_health(conn, integrity=integrity)
    components, age = get_state_with_age(conn, "components")
    conn.close()
    print_json({
        "state": report.state.value, "reasons": list(report.reasons),
        "database_integrity": report.database_integrity, "database_integrity_check": kind,
        "kill_switch_status": report.kill_switch_status,
        "kill_switch_blocks_new_entries": report.kill_switch_blocks_new_entries,
        "runtime_components": components, "runtime_components_age_seconds": age,
    })
    return 0 if report.state.value == "HEALTHY" else 1


def cmd_symbols(args: argparse.Namespace) -> int:
    cfg, conn = open_db(args.config)
    gw = open_gateway(cfg, require_demo=False)  # read-only resolution; the account check is reported by `doctor`
    try:
        results = resolve_all(gw.symbols_get())
        persist_all(conn, results)
        out = {}
        for canonical, r in results.items():
            spec = gw.symbol_info(r.broker_symbol) if r.resolved else None
            if spec is not None:
                save_symbol_spec(conn, canonical, spec)
            out[canonical] = {"resolved": r.resolved, "broker_symbol": r.broker_symbol, "reason": r.reason,
                              "spec_captured": spec is not None}
    finally:
        gw.shutdown()
        conn.close()
    print_json(out)
    return 0 if all(v["resolved"] for v in out.values()) else 1


def cmd_strategies(args: argparse.Namespace) -> int:
    registry = build_active_registry()
    print_json({
        "active": [{"key": s.key, "version": s.version} for s in registry.all_active()],
        "retired_permanently": sorted(RETIRED_STRATEGY_KEYS),
        "executable_symbols": sorted(ALLOWED_CANONICAL_SYMBOLS),
    })
    return 0


def cmd_why_no_trade(args: argparse.Namespace) -> int:
    _, conn = open_db(args.config)
    now = int(time.time())
    snapshot, age = get_state_with_age(conn, "why_no_trade", now_utc=now)
    ks = kill_switch.get_state(conn)
    recent = [dict(r) for r in conn.execute(
        "SELECT decided_at_utc, mode, canonical_symbol, stage, decision, reason, strategy_key FROM entry_decisions "
        "ORDER BY id DESC LIMIT ?", (args.limit,))]
    blocking = [e for e in recent_events(conn, 50) if e["severity"] in ("BLOCKED", "CRITICAL") and not e["cleared_at_utc"]]
    counts = decision_counts(conn, since_utc=now - 86400)
    conn.close()
    print_json({
        "kill_switch": {"status": ks.status.value, "blocks_new_entries": ks.blocks_new_entries, "reason": ks.reason},
        "latest_cycle": snapshot, "latest_cycle_age_seconds": age,
        "open_blocking_events": [{k: e[k] for k in ("severity", "component", "event", "detail", "canonical_symbol")}
                                 for e in blocking],
        "decision_counts_24h": counts, "recent_decisions": recent,
        "note": None if snapshot is not None else "no runtime cycle recorded yet -- is START PAPER/DEMO running?",
    })
    return 0


def cmd_journal_recent(args: argparse.Namespace) -> int:
    _, conn = open_db(args.config)
    sql = ("SELECT je.id, je.event_type, je.event_timestamp_utc, je.canonical_symbol, je.strategy_key, dc.chain_key, "
           "je.payload_json FROM journal_events je JOIN decision_chains dc ON dc.id = je.chain_id")
    params: list = []
    if args.symbol:
        sql += " WHERE je.canonical_symbol = ?"
        params.append(args.symbol)
    rows = conn.execute(sql + " ORDER BY je.id DESC LIMIT ?", (*params, args.limit)).fetchall()
    conn.close()
    print_json([{**{k: r[k] for k in r.keys() if k != "payload_json"}, "payload": json.loads(r["payload_json"])}
                for r in rows])
    return 0


def cmd_costs_observed(args: argparse.Namespace) -> int:
    cfg, conn = open_db(args.config)
    symbols = [args.symbol] if args.symbol else sorted(cfg.market.symbols)
    out = {}
    for symbol in symbols:
        summary = summarize_observations(conn, symbol)
        configured = cfg.cost_for(symbol)
        out[symbol] = {**summary.__dict__, "configured": configured.model_dump()}
    conn.close()
    print_json({"observations": out, "note": "evidence only: review and edit [costs.SYMBOL] yourself; "
                                             "nothing is changed automatically"})
    return 0


def cmd_preflight(args: argparse.Namespace) -> int:
    report = run_preflight(args.config, dashboard_url=args.dashboard_url)
    print_json(report)
    return 1 if report["result"] == "BLOCKED" else 0


def register(sub) -> None:
    preflight = sub.add_parser("preflight", help="non-mutating PAPER/DEMO readiness diagnostic")
    preflight.add_argument("--dashboard-url", default="http://127.0.0.1:8765")
    preflight.set_defaults(func=cmd_preflight)
    sub.add_parser("doctor", help="verify config, database, kill switch and MT5 reachability").set_defaults(func=cmd_doctor)
    for name, func, help_text in (("status", cmd_status, "mode, symbols, health, kill switch, engine heartbeat"),
                                  ("health", cmd_health, "health report (exit 1 unless HEALTHY)")):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--full-integrity", action="store_true",
                       help="run the full (slow) PRAGMA integrity_check instead of quick_check")
        p.set_defaults(func=func)
    sub.add_parser("symbols", help="resolve canonical symbols on the broker and capture their specs").set_defaults(
        func=cmd_symbols)
    sub.add_parser("strategies", help="active strategies, retired strategies, executable symbols").set_defaults(
        func=cmd_strategies)

    why = sub.add_parser("why-no-trade", help="why the runtime is not entering: blocks, gates, recent decisions")
    why.add_argument("--limit", type=int, default=20)
    why.set_defaults(func=cmd_why_no_trade)

    journal = sub.add_parser("journal", help="decision journal")
    journal_sub = journal.add_subparsers(dest="journal_command", required=True)
    recent = journal_sub.add_parser("recent", help="most recent journal events")
    recent.add_argument("--limit", type=int, default=50)
    add_symbol_arg(recent, required=False)
    recent.set_defaults(func=cmd_journal_recent)

    costs = sub.add_parser("costs", help="transaction cost evidence")
    costs_sub = costs.add_subparsers(dest="costs_command", required=True)
    observed = costs_sub.add_parser("observed", help="DEMO execution cost observations vs configured costs")
    add_symbol_arg(observed, required=False)
    observed.set_defaults(func=cmd_costs_observed)
