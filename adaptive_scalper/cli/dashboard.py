"""`dashboard`: the local observer-only web dashboard. It never connects
to MT5 and opens the database read-only."""

from __future__ import annotations

import argparse

from adaptive_scalper.cli.common import CliError
from adaptive_scalper.config.loader import load_config
from adaptive_scalper.dashboard.app import DEFAULT_HOST, DEFAULT_PORT, create_app


def cmd_dashboard(args: argparse.Namespace) -> int:
    import uvicorn

    if args.host not in ("127.0.0.1", "localhost", "::1") and not args.allow_remote_bind:
        raise CliError(f"refusing to bind {args.host}: the dashboard is local-only (use --allow-remote-bind to override)")
    cfg = load_config(args.config)
    print(f"Adaptive Scalper Next dashboard (observer only, read-only DB, no MT5): http://{args.host}:{args.port}/")
    uvicorn.run(create_app(cfg.database.path), host=args.host, port=args.port, log_level="warning")
    return 0


def register(sub) -> None:
    dash = sub.add_parser("dashboard", help="local observer-only dashboard (127.0.0.1)")
    dash.add_argument("--host", default=DEFAULT_HOST)
    dash.add_argument("--port", type=int, default=DEFAULT_PORT)
    dash.add_argument("--allow-remote-bind", action="store_true", help=argparse.SUPPRESS)
    dash.set_defaults(func=cmd_dashboard)
