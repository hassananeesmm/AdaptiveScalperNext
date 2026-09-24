"""Runtime and market commands: paper, demo, scan, analyse, reconcile,
news status/refresh/upcoming.

`paper` / `demo` start the `RuntimeEngine` in the foreground (the Windows
launchers call these). They never bootstrap or clear the kill switch: a
fresh system starts with new entries blocked until the operator runs
`kill-switch bootstrap`. A second runtime against the same database is
refused while the first one's heartbeat is fresh.
"""

from __future__ import annotations

import argparse
import contextlib
import time

from adaptive_scalper.cli.common import CliError, add_symbol_arg, open_db, open_gateway, print_json
from adaptive_scalper.config.loader import AppConfig
from adaptive_scalper.gateway.spec_store import load_symbol_spec
from adaptive_scalper.history.resolutions import resolution_seconds
from adaptive_scalper.history.store import get_bars
from adaptive_scalper.runtime.analysis import analyze_bars
from adaptive_scalper.runtime.state import get_state, get_state_with_age

BANNERS = {
    "PAPER": ("PAPER MODE: real market data, SIMULATED fills -- no order is ever sent",
              "REAL-MONEY EXECUTION: DISABLED"),
    "DEMO": ("REAL-MONEY EXECUTION: DISABLED", "DEMO ACCOUNT REQUIRED",
             "NEW ENTRIES REQUIRE KILL SWITCH DISENGAGED (operator: kill-switch bootstrap / clear)"),
}


def _refuse_if_running(conn, cfg: AppConfig) -> None:
    engine, age = get_state_with_age(conn, "engine")
    if engine and engine.get("state") != "STOPPED" and age is not None \
            and age < max(10, 5 * cfg.runtime.heartbeat_seconds):
        raise CliError(f"a {engine.get('mode')} runtime is already running against this database "
                       f"(heartbeat {age}s ago); stop it first")


def _run_engine(args: argparse.Namespace, mode: str) -> int:
    from adaptive_scalper.gateway.factory import create_live_gateway
    from adaptive_scalper.gateway.mt5_gateway import Mt5NotAvailableError
    from adaptive_scalper.runtime.engine import RuntimeEngine, RuntimeStartupError
    from adaptive_scalper.runtime.logging_setup import configure_logging

    cfg, conn = open_db(args.config)
    cfg = cfg.model_copy(update={"mode": mode})
    _refuse_if_running(conn, cfg)
    log_path = configure_logging(cfg.runtime.log_dir)
    for line in BANNERS[mode]:
        print(f"*** {line} ***")
    try:
        gateway = create_live_gateway()
    except Mt5NotAvailableError as exc:
        raise CliError(f"MetaTrader5 unavailable: {exc}") from exc
    engine = RuntimeEngine(cfg, conn, gateway)
    try:
        summary = engine.startup()
    except (RuntimeStartupError, Mt5NotAvailableError) as exc:
        with contextlib.suppress(Exception):
            gateway.shutdown()
        conn.close()
        raise CliError(f"runtime refused to start: {exc}") from exc
    print_json({**summary, "logs": str(log_path)})
    if summary["kill_switch_blocks_new_entries"]:
        print(f"NOTE: kill switch is {summary['kill_switch']}: new entries are blocked; the runtime keeps "
              "managing positions and recording why-no-trade")
    try:
        engine.run(max_iterations=args.max_iterations)
    except KeyboardInterrupt:
        print("stopping (Ctrl+C): no positions are closed by stopping the runtime")
    finally:
        engine.stop()
        gateway.shutdown()
        conn.close()
    return 0


def cmd_paper(args: argparse.Namespace) -> int:
    return _run_engine(args, "PAPER")


def cmd_demo(args: argparse.Namespace) -> int:
    return _run_engine(args, "DEMO")


def _bars_and_spec(args, cfg: AppConfig, conn, symbol: str, gw):
    resolution = cfg.runtime.entry_resolution
    if gw is None:
        stored = load_symbol_spec(conn, symbol)
        if stored is None:
            raise CliError(f"no stored symbol spec for {symbol}: run `symbols` or `history bootstrap` on the laptop")
        last = conn.execute("SELECT MAX(ts_utc) FROM bars WHERE canonical_symbol = ? AND resolution = ?",
                            (symbol, resolution)).fetchone()[0]
        if last is None:
            raise CliError(f"no stored {resolution} bars for {symbol}")
        start = last - cfg.runtime.bar_history_count * resolution_seconds(resolution)
        return get_bars(conn, symbol, resolution, start, last), stored[0]
    from adaptive_scalper.gateway.symbol_resolver import resolve_all
    from adaptive_scalper.runtime.market_data import closed_bars

    result = resolve_all(gw.symbols_get()).get(symbol)
    if result is None or not result.resolved:
        raise CliError(f"{symbol} does not resolve on this broker")
    spec = gw.symbol_info(result.broker_symbol)
    tick = gw.symbol_info_tick(result.broker_symbol)
    if spec is None or tick is None:
        raise CliError(f"no spec/quote for {result.broker_symbol}")
    return closed_bars(gw, result.broker_symbol, resolution, now_utc=int(time.time()),
                       count=cfg.runtime.bar_history_count, tick=tick), spec


def _analyse(args, symbols: list[str]) -> dict:
    cfg, conn = open_db(args.config)
    gw = open_gateway() if args.source == "mt5" else None
    out = {}
    try:
        for symbol in symbols:
            try:
                bars, spec = _bars_and_spec(args, cfg, conn, symbol, gw)
                analysis = analyze_bars(symbol, cfg.runtime.entry_resolution, bars, spec)
                runtime_regime = get_state(conn, f"regime:{symbol}")
                analysis["runtime_confirmed_regime"] = runtime_regime.get("confirmed") if runtime_regime else None
                analysis["source"] = args.source
                out[symbol] = analysis
            except CliError as exc:
                out[symbol] = {"symbol": symbol, "status": "UNAVAILABLE", "detail": str(exc)}
    finally:
        if gw is not None:
            gw.shutdown()
        conn.close()
    return out


def cmd_scan(args: argparse.Namespace) -> int:
    cfg, _ = open_db(args.config)
    results = _analyse(args, sorted(cfg.market.symbols))
    print_json({s: {k: r.get(k) for k in ("status", "bar_time_utc", "raw_regime", "runtime_confirmed_regime",
                                          "detail")}
                   | {"signals": [x for x in r.get("signals", []) if x["signal"] is not None]}
                for s, r in results.items()})
    return 0


def cmd_analyse(args: argparse.Namespace) -> int:
    result = _analyse(args, [args.symbol])[args.symbol]
    if args.with_knowledge and result.get("status") == "OK":
        from adaptive_scalper.knowledge.advisor import KnowledgeAdvisor

        advisor = KnowledgeAdvisor.from_path()
        result["knowledge"] = {s["strategy"]: advisor.advise(canonical_symbol=args.symbol, strategy_key=s["strategy"])
                               for s in result["signals"] if s["signal"] is not None}
    print_json(result)
    return 0 if result.get("status") == "OK" else 1


def cmd_reconcile(args: argparse.Namespace) -> int:
    from adaptive_scalper.execution.reconciliation import run_reconciliation
    from adaptive_scalper.execution.recovery import apply_unknown_resolutions

    _, conn = open_db(args.config)
    gw = open_gateway()
    now = int(time.time())
    try:
        report = run_reconciliation(conn, gw, f"cli-reconcile:{now}", now_utc=now)
        resolutions = apply_unknown_resolutions(conn, gw, now_utc=now)
    finally:
        gw.shutdown()
        conn.close()
    print_json({"status": report.status, "unrepaired_positions": report.unrepaired_position_ids,
                "unrepaired_orders": report.unrepaired_order_ids,
                "unknown_resolutions": [o.detail for o in resolutions],
                "note": "reads broker truth and repairs LOCAL state only; never sends an order"})
    return 0 if report.status == "CLEAN" else 1


def cmd_order_check_probe(args: argparse.Namespace) -> int:
    """LOCAL_MT5_HANDOFF step L: one real DEMO order_check, never sent."""
    from adaptive_scalper.execution.order_check_probe import CHECKED, result_dict, run_order_check_probe

    cfg, conn = open_db(args.config)
    gw = open_gateway()
    try:
        result = run_order_check_probe(conn, gw, canonical_symbol=args.symbol, direction=args.direction,
                                       risk_per_trade_pct=cfg.risk.risk_per_trade_pct, magic=cfg.runtime.magic)
    finally:
        gw.shutdown()
        conn.close()
    print_json({**result_dict(result), "order_sent": False,
                "next": "record this retcode against BUG_BACKLOG #5 before any controlled DEMO order_send"})
    return 0 if result.status == CHECKED else 1


def cmd_news_status(args: argparse.Namespace) -> int:
    _, conn = open_db(args.config)
    providers = [dict(r) for r in conn.execute("SELECT * FROM news_provider_state ORDER BY provider")]
    snapshot, age = get_state_with_age(conn, "news")
    cached = conn.execute("SELECT COUNT(*), MAX(retrieved_at_utc) FROM news_events").fetchone()
    conn.close()
    print_json({"providers": providers, "runtime_snapshot": snapshot, "runtime_snapshot_age_seconds": age,
                "cached_events": cached[0], "cache_last_retrieved_utc": cached[1]})
    return 0


def cmd_news_refresh(args: argparse.Namespace) -> int:
    from adaptive_scalper.news.providers.cache import CacheProvider
    from adaptive_scalper.runtime.news_monitor import NewsMonitor, default_live_providers

    cfg, conn = open_db(args.config)
    monitor = NewsMonitor(
        conn, default_live_providers(), CacheProvider(conn, max_age_seconds=cfg.runtime.news_stale_after_seconds),
        pre_minutes=cfg.news.pre_high_impact_minutes, post_minutes=cfg.news.post_high_impact_minutes,
        refresh_seconds=cfg.runtime.news_refresh_seconds, stale_after_seconds=cfg.runtime.news_stale_after_seconds,
    )
    now = int(time.time())
    health = monitor.refresh(now)
    snapshot = monitor.snapshot(now)
    conn.close()
    print_json(snapshot)
    return 0 if health.value == "HEALTHY" else 1


def cmd_news_upcoming(args: argparse.Namespace) -> int:
    _, conn = open_db(args.config)
    now = int(time.time())
    rows = conn.execute(
        "SELECT scheduled_at_utc, currency, title, impact, provider FROM news_events "
        "WHERE scheduled_at_utc BETWEEN ? AND ? AND (? = 0 OR impact = 'HIGH') ORDER BY scheduled_at_utc",
        (now, now + args.hours * 3600, 0 if args.all_impacts else 1),
    ).fetchall()
    conn.close()
    print_json([dict(r) for r in rows])
    return 0


def register(sub) -> None:
    for name, func, text in (("paper", cmd_paper, "run the PAPER runtime (live data, simulated fills)"),
                             ("demo", cmd_demo, "run the DEMO runtime (MT5 DEMO account only)")):
        p = sub.add_parser(name, help=text)
        p.add_argument("--max-iterations", type=int, default=None, help=argparse.SUPPRESS)
        p.set_defaults(func=func)

    for name, func, text in (("scan", cmd_scan, "every symbol: raw regime and would-be strategy signals"),
                             ("analyse", cmd_analyse, "one symbol in detail (features, regime, signals)")):
        p = sub.add_parser(name, help=text)
        if name == "analyse":
            add_symbol_arg(p)
            p.add_argument("--with-knowledge", action="store_true", help="attach OKF advisory evidence")
        p.add_argument("--source", choices=("mt5", "db"), default="mt5",
                       help="mt5: live closed bars; db: newest stored history bars")
        p.set_defaults(func=func)

    sub.add_parser("reconcile", help="reconcile local state with the DEMO broker (never sends orders)").set_defaults(
        func=cmd_reconcile)

    probe = sub.add_parser("order-check-probe", help="ONE real DEMO order_check() that is never sent (handoff step L)")
    add_symbol_arg(probe)
    probe.add_argument("--direction", choices=("BUY", "SELL"), default="BUY")
    probe.set_defaults(func=cmd_order_check_probe)

    news = sub.add_parser("news", help="economic calendar")
    news_sub = news.add_subparsers(dest="news_command", required=True)
    news_sub.add_parser("status", help="provider health and cache").set_defaults(func=cmd_news_status)
    news_sub.add_parser("refresh", help="fetch the calendar now").set_defaults(func=cmd_news_refresh)
    upcoming = news_sub.add_parser("upcoming", help="upcoming events from the cache")
    upcoming.add_argument("--hours", type=int, default=24)
    upcoming.add_argument("--all-impacts", action="store_true")
    upcoming.set_defaults(func=cmd_news_upcoming)
