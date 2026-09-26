"""Read-only operational preflight for PAPER and controlled DEMO.

The probe deliberately never calls ``migrate``, ``order_check``,
``order_send`` or reconciliation repair.  It reads the existing database,
runtime snapshots, local dashboard and MT5 terminal, then reports the highest
safe readiness level supported by current evidence.
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from adaptive_scalper.config.loader import AppConfig, load_config
from adaptive_scalper.core.kill_switch import get_state
from adaptive_scalper.gateway.factory import create_live_gateway
from adaptive_scalper.gateway.server_time import VERIFIED, classify_quote_clock
from adaptive_scalper.gateway.symbol_resolver import resolve_all
from adaptive_scalper.gateway.types import TradeMode
from adaptive_scalper.history.time_basis import get_basis
from adaptive_scalper.persistence.database import MIGRATIONS_DIR, connect_readonly, integrity_check
from adaptive_scalper.runtime.state import get_state_with_age

_IMPORTS = ("pydantic", "fastapi", "uvicorn", "websockets", "httpx", "MetaTrader5", "numpy", "sklearn", "scipy", "joblib", "yaml")
_DANGEROUS_ORDER_STATES = ("UNKNOWN", "PENDING_RECONCILIATION")


def _check(checks: list[dict[str, Any]], name: str, status: str, detail: str) -> None:
    checks.append({"name": name, "status": status, "detail": detail})


def _expected_schema_version() -> int:
    return max(int(path.stem.split("_", 1)[0]) for path in MIGRATIONS_DIR.glob("*.sql"))


def _read_json_state(conn: sqlite3.Connection, key: str, now: int) -> tuple[Any, float | None]:
    return get_state_with_age(conn, key, now_utc=now)


def run_preflight(
    config_path: str,
    *,
    dashboard_url: str = "http://127.0.0.1:8765",
    now_utc: int | None = None,
) -> dict[str, Any]:
    """Return a non-mutating readiness report.

    ``READY_FOR_PAPER`` means the data runtime is safe to start, even when the
    operator kill switch intentionally blocks simulated entries.
    ``READY_FOR_DEMO`` additionally requires every execution gate represented
    here.  ``BLOCKED`` means even PAPER should not be started.
    """
    now = now_utc if now_utc is not None else int(time.time())
    checks: list[dict[str, Any]] = []
    paper_blockers: list[str] = []
    demo_blockers: list[str] = []
    warnings: list[str] = []

    if sys.version_info[:2] != (3, 13):
        paper_blockers.append(f"Python {sys.version_info.major}.{sys.version_info.minor} is not the required 3.13.x")
        _check(checks, "python", "FAIL", paper_blockers[-1])
    else:
        _check(checks, "python", "PASS", sys.version.split()[0])

    missing = [name for name in _IMPORTS if importlib.util.find_spec(name) is None]
    if missing:
        paper_blockers.append(f"missing Python dependencies: {', '.join(missing)}")
        _check(checks, "dependencies", "FAIL", paper_blockers[-1])
    else:
        _check(checks, "dependencies", "PASS", "required imports are available")

    try:
        cfg: AppConfig = load_config(config_path)
        _check(checks, "configuration", "PASS", f"mode={cfg.mode}; risk ceilings validated")
    except Exception as exc:
        paper_blockers.append(f"configuration invalid: {exc}")
        _check(checks, "configuration", "FAIL", paper_blockers[-1])
        return {
            "result": "BLOCKED", "paper_blockers": paper_blockers,
            "demo_blockers": demo_blockers, "warnings": warnings, "checks": checks,
            "non_mutating": True, "real_money_execution": "DISABLED",
        }

    db_path = Path(cfg.database.path)
    if not db_path.exists():
        paper_blockers.append(f"database does not exist: {db_path}")
        _check(checks, "database", "FAIL", paper_blockers[-1])
    else:
        try:
            with connect_readonly(db_path) as conn:
                integrity = integrity_check(conn)
                version_row = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
                version = int(version_row[0] or 0)
                expected = _expected_schema_version()
                basis = get_basis(conn)["basis"]
                if integrity != "ok":
                    paper_blockers.append(f"database integrity is {integrity}")
                if version != expected:
                    paper_blockers.append(f"schema version {version}, expected {expected}")
                if basis != "UTC":
                    paper_blockers.append(f"stored MT5 time basis is {basis}, expected UTC")
                status = "PASS" if integrity == "ok" and version == expected and basis == "UTC" else "FAIL"
                _check(checks, "database", status, f"integrity={integrity}; schema={version}/{expected}; time_basis={basis}")

                ks = get_state(conn)
                _check(checks, "kill_switch", "PASS" if not ks.blocks_new_entries else "BLOCKED",
                       f"status={ks.status.value}; blocks_new_entries={ks.blocks_new_entries}")
                if ks.blocks_new_entries:
                    demo_blockers.append(f"kill switch status={ks.status.value}; only the human operator may change it")

                placeholders = ",".join("?" for _ in _DANGEROUS_ORDER_STATES)
                unknown_count = conn.execute(
                    f"SELECT COUNT(*) FROM orders WHERE state IN ({placeholders})", _DANGEROUS_ORDER_STATES,
                ).fetchone()[0]
                incident_count = conn.execute(
                    "SELECT COUNT(*) FROM execution_incidents WHERE resolved_at_utc IS NULL"
                ).fetchone()[0]
                if unknown_count or incident_count:
                    demo_blockers.append(
                        f"unresolved execution state: {unknown_count} dangerous order(s), {incident_count} incident(s)"
                    )
                    _check(checks, "execution_state", "FAIL", demo_blockers[-1])
                else:
                    _check(checks, "execution_state", "PASS", "no dangerous UNKNOWN orders or open incidents")

                news, news_age = _read_json_state(conn, "news", now)
                news_ok = bool(news and news.get("health") == "HEALTHY" and news_age is not None
                               and news_age <= cfg.runtime.news_stale_after_seconds)
                if not news_ok:
                    demo_blockers.append("news health is missing, unhealthy or stale")
                _check(checks, "news", "PASS" if news_ok else "FAIL",
                       f"health={news.get('health') if news else None}; age_seconds={news_age}")

                reconciliation, recon_age = _read_json_state(conn, "reconciliation", now)
                recon_ok = bool(reconciliation and reconciliation.get("status") == "CLEAN" and recon_age is not None
                                and recon_age <= 15)
                if not recon_ok:
                    demo_blockers.append("no fresh CLEAN reconciliation snapshot")
                _check(checks, "reconciliation", "PASS" if recon_ok else "FAIL",
                       f"status={reconciliation.get('status') if reconciliation else None}; age_seconds={recon_age}")
        except sqlite3.Error as exc:
            paper_blockers.append(f"database cannot be read safely: {type(exc).__name__}: {exc}")
            _check(checks, "database", "FAIL", paper_blockers[-1])

    unknown_costs = [symbol for symbol in cfg.market.symbols if not cfg.cost_for(symbol).fully_known]
    if unknown_costs:
        demo_blockers.append(f"execution costs are not fully evidenced for: {', '.join(sorted(unknown_costs))}")
        _check(checks, "execution_costs", "FAIL", demo_blockers[-1])
    else:
        _check(checks, "execution_costs", "PASS", "all configured symbols have commission and slippage evidence")

    gateway = None
    try:
        gateway = create_live_gateway(cfg.mt5.server_time_rule, cfg.mt5.terminal_path)
        if not gateway.initialize():
            raise RuntimeError("MT5 initialize returned false")
        terminal = gateway.terminal_info()
        account = gateway.account_info()
        connected = bool(terminal and terminal.connected)
        if not connected:
            paper_blockers.append("MT5 terminal is not connected")
        if account is None or account.trade_mode != TradeMode.DEMO:
            mode = account.trade_mode.name if account is not None else "UNKNOWN"
            paper_blockers.append(f"account trade mode is {mode}; DEMO is required even for PAPER data access")
        _check(checks, "mt5_account", "PASS" if connected and account and account.trade_mode == TradeMode.DEMO else "FAIL",
               f"connected={connected}; trade_mode={account.trade_mode.name if account else 'UNKNOWN'}")

        if not terminal or not terminal.trade_allowed or not account or not account.trade_allowed or not account.trade_expert:
            demo_blockers.append("terminal/account expert trading permission is not enabled")
            permissions_ok = False
        else:
            permissions_ok = True
        _check(checks, "terminal_permissions", "PASS" if permissions_ok else "FAIL",
               f"terminal_trade_allowed={getattr(terminal, 'trade_allowed', None)}; "
               f"account_trade_allowed={getattr(account, 'trade_allowed', None)}; "
               f"account_trade_expert={getattr(account, 'trade_expert', None)}")

        resolutions = resolve_all(gateway.symbols_get())
        unresolved = sorted(symbol for symbol in cfg.market.symbols if not resolutions[symbol].resolved)
        stale: list[str] = []
        bad_clock: list[str] = []
        for symbol in cfg.market.symbols:
            resolved = resolutions[symbol]
            if not resolved.resolved:
                continue
            tick = gateway.symbol_info_tick(resolved.broker_symbol)
            if tick is None or not tick.time or now - tick.time > 15:
                stale.append(symbol)
                continue
            verdict, _ = classify_quote_clock(tick.time, now)
            if verdict != VERIFIED:
                bad_clock.append(f"{symbol}:{verdict}")
        if unresolved:
            paper_blockers.append(f"unresolved configured symbols: {', '.join(unresolved)}")
        if bad_clock:
            paper_blockers.append(f"broker clock verification failed: {', '.join(bad_clock)}")
        if stale:
            demo_blockers.append(f"no fresh quote for: {', '.join(stale)}")
        _check(checks, "symbols_and_quotes", "PASS" if not unresolved and not bad_clock and not stale else "FAIL",
               f"unresolved={unresolved}; stale={stale}; clock_failures={bad_clock}")
    except Exception as exc:
        paper_blockers.append(f"MT5 probe failed: {type(exc).__name__}: {exc}")
        _check(checks, "mt5_account", "FAIL", paper_blockers[-1])
    finally:
        if gateway is not None:
            try:
                gateway.shutdown()
            except Exception:
                pass

    dashboard_ok = False
    dashboard_detail = "unavailable"
    try:
        with urllib.request.urlopen(dashboard_url.rstrip("/") + "/", timeout=2.0) as response:
            page = response.read(200_000).decode("utf-8", errors="replace")
        with urllib.request.urlopen(dashboard_url.rstrip("/") + "/api/health", timeout=2.0) as response:
            health = json.loads(response.read().decode("utf-8"))
        dashboard_ok = "AdaptiveScalperNext · Monitor" in page and health.get("state") != "UNAVAILABLE"
        dashboard_detail = f"integrated_17_panel_build={dashboard_ok}; health={health.get('state')}"
    except (OSError, urllib.error.URLError, ValueError, json.JSONDecodeError) as exc:
        dashboard_detail = f"{type(exc).__name__}: {exc}"
    if not dashboard_ok:
        demo_blockers.append("integrated local dashboard is unavailable or a stale build is running")
        warnings.append("PAPER can run without the dashboard, but monitoring must be restored before DEMO")
    _check(checks, "dashboard", "PASS" if dashboard_ok else "FAIL", dashboard_detail)

    result = "BLOCKED" if paper_blockers else ("READY_FOR_DEMO" if not demo_blockers else "READY_FOR_PAPER")
    return {
        "result": result,
        "paper_blockers": paper_blockers,
        "demo_blockers": demo_blockers,
        "warnings": warnings,
        "checks": checks,
        "non_mutating": True,
        "real_money_execution": "DISABLED",
    }
