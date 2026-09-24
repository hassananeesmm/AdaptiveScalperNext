"""Historical market data and broker account history commands."""

from __future__ import annotations

import argparse
import time

from adaptive_scalper.cli.common import open_db, open_gateway, print_json
from adaptive_scalper.gateway.spec_store import save_symbol_spec
from adaptive_scalper.gateway.symbol_resolver import resolve_all
from adaptive_scalper.history import account_history
from adaptive_scalper.history import bootstrap as history_bootstrap
from adaptive_scalper.history import jobs as history_jobs
from adaptive_scalper.history.coverage import get_bar_coverage, get_tick_coverage
from adaptive_scalper.history.resolutions import SUPPORTED_BAR_RESOLUTIONS


def _mask(login) -> str:
    text = str(login)
    return "*" * max(0, len(text) - 3) + text[-3:]


def cmd_history_bootstrap(args: argparse.Namespace) -> int:
    """Chunked, resumable bar (+ optional tick) download for every
    canonical symbol that resolves right now. One symbol's failure never
    aborts the others."""
    cfg, conn = open_db(args.config, require_utc_history=True)
    gw = open_gateway(cfg)
    resolutions_result = resolve_all(gw.symbols_get())
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
        spec = gw.symbol_info(result.broker_symbol)
        if spec is not None:
            save_symbol_spec(conn, canonical, spec, now_utc=now_utc)
        symbol_report: dict[str, dict] = {}
        for resolution in resolutions:
            try:
                job = history_bootstrap.bootstrap_bars(conn, gw, canonical, result.broker_symbol, resolution,
                                                       bar_start, now_utc)
                symbol_report[resolution] = {"status": job.status, "cursor_utc": job.cursor_utc}
            except Exception as exc:  # noqa: BLE001 - one symbol/resolution must not abort the rest
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
    print_json(report)
    return 0 if ok else 1


def cmd_history_status(args: argparse.Namespace) -> int:
    cfg, conn = open_db(args.config)
    report: dict[str, dict] = {}
    for canonical in sorted(cfg.market.symbols):
        bar_report: dict[str, dict] = {}
        for resolution in SUPPORTED_BAR_RESOLUTIONS:
            cov = get_bar_coverage(conn, canonical, resolution)
            job = history_jobs.get_job(conn, canonical, history_jobs.BAR, resolution)
            bar_report[resolution] = {
                "job_status": job.status if job else "NOT_STARTED",
                "earliest_utc": cov.earliest_utc if cov else None, "latest_utc": cov.latest_utc if cov else None,
                "bar_count": cov.bar_count if cov else 0, "gap_count": cov.gap_count if cov else 0,
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
    print_json(report)
    return 0


def cmd_broker_history_import(args: argparse.Namespace) -> int:
    cfg, conn = open_db(args.config, require_utc_history=True)
    gw = open_gateway(cfg)
    account = gw.account_info()
    now_utc = int(time.time())
    orders, deals = account_history.import_account_history(conn, gw, account, now_utc - args.days * 86400, now_utc)
    gw.shutdown()
    conn.close()
    print_json({"login": _mask(account.login), "server": account.server, "orders_inserted": orders,
                "deals_inserted": deals})
    return 0


def cmd_broker_history_status(args: argparse.Namespace) -> int:
    cfg, conn = open_db(args.config)
    gw = open_gateway(cfg)
    account = gw.account_info()
    gw.shutdown()
    cov = account_history.coverage(conn, account.login, account.server)
    conn.close()
    print_json({"login": _mask(account.login), "server": account.server, **cov})
    return 0


def cmd_history_convert_server_time(args: argparse.Namespace) -> int:
    """One-shot conversion of rows stored before schema 27 from the broker
    server clock to UTC (BUG_BACKLOG #14). Backs the database up first;
    refuses a database that is already UTC. Never touches MT5."""
    from pathlib import Path

    from adaptive_scalper.cli.common import CliError
    from adaptive_scalper.history.time_basis import TimeBasisError, convert_server_time_to_utc

    cfg, conn = open_db(args.config)
    rule = args.rule or cfg.mt5.server_time_rule
    try:
        report = convert_server_time_to_utc(conn, rule, db_path=cfg.database.path,
                                            backup_dir=Path(cfg.database.path).parent / "backups",
                                            now_utc=int(time.time()))
    except (TimeBasisError, ValueError) as exc:
        raise CliError(str(exc)) from exc
    finally:
        conn.close()
    print_json(report)
    return 0


def register(sub) -> None:
    hist = sub.add_parser("history", help="historical MT5 market data")
    hist_sub = hist.add_subparsers(dest="history_command", required=True)
    boot = hist_sub.add_parser("bootstrap", help="chunked, resumable bar+tick download for every resolved symbol")
    boot.add_argument("--years", type=int, default=history_bootstrap.DEFAULT_BAR_YEARS)
    boot.add_argument("--tick-days", type=int, default=history_bootstrap.DEFAULT_TICK_DAYS)
    boot.add_argument("--no-ticks", action="store_true")
    boot.add_argument("--resolutions", nargs="+", choices=SUPPORTED_BAR_RESOLUTIONS)
    boot.set_defaults(func=cmd_history_bootstrap)
    hist_sub.add_parser("status", help="coverage per configured symbol/resolution").set_defaults(func=cmd_history_status)
    conv = hist_sub.add_parser("convert-server-time",
                               help="one-shot: back up, then convert pre-schema-27 MT5 rows from server time to UTC")
    conv.add_argument("--rule", default=None, help="server clock rule (default: [mt5] server_time_rule)")
    conv.set_defaults(func=cmd_history_convert_server_time)

    bh = sub.add_parser("broker-history", help="the DEMO account's own order/deal history")
    bh_sub = bh.add_subparsers(dest="broker_history_command", required=True)
    imp = bh_sub.add_parser("import", help="idempotently import order/deal history")
    imp.add_argument("--days", type=int, default=3650)
    imp.set_defaults(func=cmd_broker_history_import)
    bh_sub.add_parser("status", help="coverage of the imported history").set_defaults(func=cmd_broker_history_status)
