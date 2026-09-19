"""Command-line interface.

Per MASTER_BUILD_DIRECTIVE.md section 108. Only commands backed by a
real, implemented subsystem exist here — no stub command that prints a
placeholder, per section 118 "no showpiece modules". As each subsystem
lands (news, strategies, RAG, ML, backtesting, ...) its commands are
added here, not before.

Currently implemented: doctor, status, health, symbols, kill-switch
status/engage/clear, dashboard, history bootstrap/status.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from adaptive_scalper.config.loader import ConfigError, load_config
from adaptive_scalper.core import kill_switch
from adaptive_scalper.core.operator_authority import OperatorAuthority
from adaptive_scalper.dashboard.app import DEFAULT_HOST, DEFAULT_PORT, create_app
from adaptive_scalper.dashboard.health import compute_health
from adaptive_scalper.gateway.mt5_gateway import Mt5Gateway, Mt5NotAvailableError
from adaptive_scalper.gateway.symbol_resolver import resolve_all
from adaptive_scalper.history import account_history
from adaptive_scalper.history import bootstrap as history_bootstrap
from adaptive_scalper.history import jobs as history_jobs
from adaptive_scalper.history.coverage import get_bar_coverage, get_tick_coverage
from adaptive_scalper.history.resolutions import SUPPORTED_BAR_RESOLUTIONS
from adaptive_scalper.persistence.database import connect, integrity_check, migrate

DEFAULT_CONFIG_PATH = "config/default.toml"


def _open_db(config_path: str):
    cfg = load_config(config_path)
    conn = connect(cfg.database.path)
    migrate(conn)
    return cfg, conn


def _print_json(obj) -> None:
    print(json.dumps(obj, indent=2, default=str))


def cmd_doctor(args: argparse.Namespace) -> int:
    """Verify the environment: config loads, DB opens/migrates/integrity-
    checks, and (best-effort, never fatal) whether an MT5 terminal is
    reachable right now."""
    problems: list[str] = []

    try:
        cfg, conn = _open_db(args.config)
    except ConfigError as exc:
        print(f"FAIL: config: {exc}")
        return 1

    integrity = integrity_check(conn)
    if integrity != "ok":
        problems.append(f"database integrity: {integrity}")

    mt5_status = "not checked"
    try:
        gw = Mt5Gateway()
        if gw.initialize():
            mt5_status = "reachable"
            gw.shutdown()
        else:
            mt5_status = "initialize() returned False"
    except Mt5NotAvailableError as exc:
        mt5_status = f"unavailable: {exc}"

    conn.close()

    print(f"config: OK ({args.config}, mode={cfg.mode})")
    print(f"database: integrity={integrity} ({cfg.database.path})")
    print(f"mt5: {mt5_status}")
    if problems:
        for p in problems:
            print(f"PROBLEM: {p}")
        return 1
    print("doctor: OK")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    cfg, conn = _open_db(args.config)
    ks = kill_switch.get_state(conn)
    report = compute_health(conn)
    conn.close()
    _print_json({
        "mode": cfg.mode,
        "symbols": cfg.market.symbols,
        "health": report.state.value,
        "kill_switch_status": ks.status.value,
        "kill_switch_reason": ks.reason,
    })
    return 0


def cmd_health(args: argparse.Namespace) -> int:
    _, conn = _open_db(args.config)
    report = compute_health(conn)
    conn.close()
    _print_json({
        "state": report.state.value,
        "reasons": list(report.reasons),
        "database_integrity": report.database_integrity,
        "kill_switch_status": report.kill_switch_status,
        "kill_switch_blocks_new_entries": report.kill_switch_blocks_new_entries,
    })
    return 0 if report.state.value == "HEALTHY" else 1


def cmd_symbols(args: argparse.Namespace) -> int:
    try:
        gw = Mt5Gateway()
        if not gw.initialize():
            print("FAIL: could not initialize MT5 terminal")
            return 1
    except Mt5NotAvailableError as exc:
        print(f"FAIL: {exc}")
        return 1

    broker_symbols = gw.symbols_get()
    gw.shutdown()
    results = resolve_all(broker_symbols)
    _print_json({
        canonical: {
            "resolved": r.resolved,
            "broker_symbol": r.broker_symbol,
            "reason": r.reason,
        }
        for canonical, r in results.items()
    })
    return 0 if all(r.resolved for r in results.values()) else 1


def cmd_kill_switch_status(args: argparse.Namespace) -> int:
    _, conn = _open_db(args.config)
    state = kill_switch.get_state(conn)
    conn.close()
    _print_json({
        "status": state.status.value,
        "reason": state.reason,
        "changed_at": state.changed_at,
        "changed_by": state.changed_by,
        "blocks_new_entries": state.blocks_new_entries,
    })
    return 0


def cmd_kill_switch_engage(args: argparse.Namespace) -> int:
    _, conn = _open_db(args.config)
    state = kill_switch.engage(conn, reason=args.reason, actor=args.actor)
    conn.close()
    print(f"kill switch ENGAGED: {state.reason} (by {state.changed_by})")
    return 0


def cmd_kill_switch_clear(args: argparse.Namespace) -> int:
    _, conn = _open_db(args.config)
    authority = OperatorAuthority(args.operator_id)
    state = kill_switch.clear(conn, reason=args.reason, authority=authority)
    conn.close()
    print(f"kill switch CLEARED: {state.reason} (by {state.changed_by})")
    return 0


def cmd_history_bootstrap(args: argparse.Namespace) -> int:
    """Chunked, resumable bar (+ optional tick) download for every
    canonical symbol that resolves against the live broker right now.
    An unresolved symbol is reported as an error for that symbol only —
    it never aborts the run for the other, resolved symbols."""
    cfg, conn = _open_db(args.config)
    try:
        gw = Mt5Gateway()
        if not gw.initialize():
            print("FAIL: could not initialize MT5 terminal")
            return 1
    except Mt5NotAvailableError as exc:
        print(f"FAIL: {exc}")
        return 1

    broker_symbols = gw.symbols_get()
    resolutions_result = resolve_all(broker_symbols)
    now_utc = int(time.time())
    resolutions = tuple(args.resolutions) if args.resolutions else SUPPORTED_BAR_RESOLUTIONS
    bar_start = now_utc - args.years * 365 * 86400
    tick_start = now_utc - args.tick_days * 86400

    report: dict[str, dict] = {}
    ok = True
    for canonical, result in sorted(resolutions_result.items()):
        if not result.resolved:
            report[canonical] = {"error": f"unresolved: {result.reason}"}
            ok = False
            continue
        symbol_report: dict[str, dict] = {}
        for resolution in resolutions:
            try:
                job = history_bootstrap.bootstrap_bars(
                    conn, gw, canonical, result.broker_symbol, resolution, bar_start, now_utc
                )
                symbol_report[resolution] = {"status": job.status, "cursor_utc": job.cursor_utc}
            except Exception as exc:  # noqa: BLE001 - one symbol/resolution's failure must not abort the rest
                symbol_report[resolution] = {"status": "ERROR", "error": str(exc)}
                ok = False
        if not args.no_ticks:
            try:
                job = history_bootstrap.bootstrap_ticks(conn, gw, canonical, result.broker_symbol, tick_start, now_utc)
                symbol_report["TICK"] = {"status": job.status, "cursor_utc": job.cursor_utc}
            except Exception as exc:  # noqa: BLE001
                symbol_report["TICK"] = {"status": "ERROR", "error": str(exc)}
                ok = False
        report[canonical] = symbol_report

    gw.shutdown()
    conn.close()
    _print_json(report)
    return 0 if ok else 1


def cmd_history_status(args: argparse.Namespace) -> int:
    """Coverage summary per configured symbol/resolution (directive
    section 97's dashboard historical-data panel, as text/JSON for now —
    no dashboard panel exists yet)."""
    cfg, conn = _open_db(args.config)
    report: dict[str, dict] = {}
    for canonical in sorted(cfg.market.symbols):
        bar_report: dict[str, dict] = {}
        for resolution in SUPPORTED_BAR_RESOLUTIONS:
            cov = get_bar_coverage(conn, canonical, resolution)
            job = history_jobs.get_job(conn, canonical, history_jobs.BAR, resolution)
            bar_report[resolution] = {
                "job_status": job.status if job else "NOT_STARTED",
                "earliest_utc": cov.earliest_utc if cov else None,
                "latest_utc": cov.latest_utc if cov else None,
                "bar_count": cov.bar_count if cov else 0,
                "gap_count": cov.gap_count if cov else 0,
            }
        tick_cov = get_tick_coverage(conn, canonical)
        tick_job = history_jobs.get_job(conn, canonical, history_jobs.TICK, "")
        report[canonical] = {
            "bars": bar_report,
            "ticks": {
                "job_status": tick_job.status if tick_job else "NOT_STARTED",
                "earliest_utc": tick_cov.earliest_utc if tick_cov else None,
                "latest_utc": tick_cov.latest_utc if tick_cov else None,
                "tick_count": tick_cov.tick_count if tick_cov else 0,
            },
        }
    conn.close()
    _print_json(report)
    return 0


def cmd_broker_history_import(args: argparse.Namespace) -> int:
    """Import the connected account's order/deal history (directive
    sections 51-52). Idempotent — safe to re-run as a periodic refresh."""
    _, conn = _open_db(args.config)
    try:
        gw = Mt5Gateway()
        if not gw.initialize():
            print("FAIL: could not initialize MT5 terminal")
            return 1
    except Mt5NotAvailableError as exc:
        print(f"FAIL: {exc}")
        return 1

    account = gw.account_info()
    if account is None:
        print("FAIL: could not read account info")
        gw.shutdown()
        return 1

    now_utc = int(time.time())
    start_utc = now_utc - args.days * 86400
    orders_inserted, deals_inserted = account_history.import_account_history(conn, gw, account, start_utc, now_utc)
    gw.shutdown()
    conn.close()
    _print_json({
        "login": account.login,
        "server": account.server,
        "orders_inserted": orders_inserted,
        "deals_inserted": deals_inserted,
    })
    return 0


def cmd_broker_history_status(args: argparse.Namespace) -> int:
    """Coverage summary for the connected account's imported history."""
    _, conn = _open_db(args.config)
    try:
        gw = Mt5Gateway()
        if not gw.initialize():
            print("FAIL: could not initialize MT5 terminal")
            return 1
    except Mt5NotAvailableError as exc:
        print(f"FAIL: {exc}")
        return 1

    account = gw.account_info()
    gw.shutdown()
    if account is None:
        print("FAIL: could not read account info")
        return 1

    cov = account_history.coverage(conn, account.login, account.server)
    conn.close()
    _print_json({"login": account.login, "server": account.server, **cov})
    return 0


def cmd_dashboard(args: argparse.Namespace) -> int:
    import uvicorn

    cfg = load_config(args.config)
    app = create_app(cfg.database.path)
    print(f"Adaptive Scalper Next dashboard: http://{args.host}:{args.port}/api/health")
    uvicorn.run(app, host=args.host, port=args.port)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="adaptive-scalper")
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH, help="path to TOML config")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor", help="verify config/database/MT5 reachability").set_defaults(func=cmd_doctor)
    sub.add_parser("status", help="mode, symbols, health, kill switch summary").set_defaults(func=cmd_status)
    sub.add_parser("health", help="dashboard health report as JSON").set_defaults(func=cmd_health)
    sub.add_parser("symbols", help="resolve canonical symbols against the live broker").set_defaults(func=cmd_symbols)

    ks = sub.add_parser("kill-switch", help="kill switch operations")
    ks_sub = ks.add_subparsers(dest="ks_command", required=True)
    ks_sub.add_parser("status", help="show kill switch state").set_defaults(func=cmd_kill_switch_status)

    ks_engage = ks_sub.add_parser("engage", help="engage the kill switch")
    ks_engage.add_argument("--reason", required=True)
    ks_engage.add_argument("--actor", required=True)
    ks_engage.set_defaults(func=cmd_kill_switch_engage)

    ks_clear = ks_sub.add_parser("clear", help="clear the kill switch (operator only)")
    ks_clear.add_argument("--reason", required=True)
    ks_clear.add_argument("--operator-id", required=True)
    ks_clear.set_defaults(func=cmd_kill_switch_clear)

    hist = sub.add_parser("history", help="historical MT5 data bootstrap")
    hist_sub = hist.add_subparsers(dest="history_command", required=True)

    hist_bootstrap = hist_sub.add_parser(
        "bootstrap", help="chunked, resumable bar+tick download for every broker-resolved symbol"
    )
    hist_bootstrap.add_argument("--years", type=int, default=history_bootstrap.DEFAULT_BAR_YEARS)
    hist_bootstrap.add_argument("--tick-days", type=int, default=history_bootstrap.DEFAULT_TICK_DAYS)
    hist_bootstrap.add_argument("--no-ticks", action="store_true")
    hist_bootstrap.add_argument("--resolutions", nargs="+", choices=SUPPORTED_BAR_RESOLUTIONS)
    hist_bootstrap.set_defaults(func=cmd_history_bootstrap)

    hist_status = hist_sub.add_parser("status", help="coverage summary per configured symbol/resolution")
    hist_status.set_defaults(func=cmd_history_status)

    bh = sub.add_parser("broker-history", help="connected account's order/deal history import")
    bh_sub = bh.add_subparsers(dest="broker_history_command", required=True)

    bh_import = bh_sub.add_parser("import", help="idempotently import order/deal history for the connected account")
    bh_import.add_argument("--days", type=int, default=3650, help="how many days of account history to request")
    bh_import.set_defaults(func=cmd_broker_history_import)

    bh_status = bh_sub.add_parser("status", help="coverage summary for the connected account's imported history")
    bh_status.set_defaults(func=cmd_broker_history_status)

    dash = sub.add_parser("dashboard", help="run the local dashboard (binds 127.0.0.1 by default)")
    dash.add_argument("--host", default=DEFAULT_HOST)
    dash.add_argument("--port", type=int, default=DEFAULT_PORT)
    dash.set_defaults(func=cmd_dashboard)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except ConfigError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
