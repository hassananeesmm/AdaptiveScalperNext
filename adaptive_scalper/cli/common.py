"""Shared CLI plumbing: config/DB opening, JSON output, the live gateway
(always `gateway.factory.create_live_gateway()`, i.e. one
`SynchronizedGateway`), and the DEMO-account check every command that
touches a broker terminal performs first."""

from __future__ import annotations

import argparse
import json
import sqlite3

from adaptive_scalper.config.loader import AppConfig, load_config
from adaptive_scalper.gateway.factory import create_live_gateway
from adaptive_scalper.gateway.mt5_gateway import Mt5NotAvailableError
from adaptive_scalper.gateway.types import TradeMode
from adaptive_scalper.persistence.database import connect, migrate

DEFAULT_CONFIG_PATH = "config/default.toml"


class CliError(Exception):
    """A command refused or could not run; printed as FAIL, exit code 1."""


def open_db(config_path: str) -> tuple[AppConfig, sqlite3.Connection]:
    cfg = load_config(config_path)
    conn = connect(cfg.database.path)
    migrate(conn)
    return cfg, conn


def print_json(obj) -> None:
    print(json.dumps(obj, indent=2, default=str))


def open_gateway(*, require_demo: bool = True):
    """Initialized live gateway. Every broker-facing command refuses a
    non-DEMO account (REAL-MONEY EXECUTION IS DISABLED) before doing
    anything else; the caller must `shutdown()`."""
    try:
        gw = create_live_gateway()
        initialized = gw.initialize()
    except Mt5NotAvailableError as exc:
        raise CliError(f"MetaTrader5 unavailable: {exc}") from exc
    if not initialized:
        raise CliError("could not initialize the MT5 terminal")
    if require_demo:
        account = gw.account_info()
        if account is None or account.trade_mode != TradeMode.DEMO:
            gw.shutdown()
            mode = account.trade_mode.name if account is not None else "UNKNOWN"
            raise CliError(f"account trade mode is {mode}: REAL-MONEY EXECUTION IS DISABLED -- DEMO account required")
    return gw


def add_symbol_arg(parser: argparse.ArgumentParser, *, required: bool = True) -> None:
    parser.add_argument("--symbol", required=required, choices=("XAUUSD", "GBPJPY", "BTCUSD"))


def parse_utc(value: str) -> int:
    """Epoch seconds or an ISO-8601 date/datetime (UTC assumed if naive)."""
    from datetime import datetime, timezone

    if value.isdigit():
        return int(value)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp())
