"""Operator command-line interface: `python -m adaptive_scalper.cli ...`
(the Windows launchers call exactly this).

Every command is backed by a real subsystem (directive section 118: no
placeholder commands). Modules:

    system     doctor, status, health, symbols, strategies, why-no-trade,
               journal recent, costs observed
    operator   kill-switch status/engage/bootstrap/clear (the ONLY place an
               OperatorAuthority is constructed)
    data       history bootstrap/status, broker-history import/status
    runtime    paper, demo, scan, analyse, reconcile, news status/refresh/upcoming
    research   backtest, walk-forward, oos, path-stress, purged-validation
    learning   models, learning status/train/scores, model-walk-forward
    knowledge  rag status/stats/similar/rebuild-index/verify-index/ingest,
               okf status/validate/search/benchmark
    dashboard  dashboard

REAL-MONEY EXECUTION IS DISABLED: every broker-facing command refuses a
non-DEMO account, and no command can send an order except through the
DEMO runtime's guarded execution service.
"""

from __future__ import annotations

import argparse
import sys

from adaptive_scalper.cli import data, dashboard, knowledge, learning, operator, research, runtime, system
from adaptive_scalper.cli.common import DEFAULT_CONFIG_PATH, CliError
from adaptive_scalper.config.loader import ConfigError

_MODULES = (system, operator, data, runtime, research, learning, knowledge, dashboard)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="adaptive-scalper",
                                     description="Adaptive Scalper Next -- PAPER + MT5 DEMO only")
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH, help="path to TOML config")
    sub = parser.add_subparsers(dest="command", required=True)
    for module in _MODULES:
        module.register(sub)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (ConfigError, CliError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1


__all__ = ["build_parser", "main"]
