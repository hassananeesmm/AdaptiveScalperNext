"""Local real-time dashboard — FastAPI app.

Per MASTER_BUILD_DIRECTIVE.md section 83: binds to 127.0.0.1 by default,
never requires cloud hosting. This is Phase 1's minimal health endpoint;
the full panel set (sections 84-104) is built out as each subsystem it
displays actually exists — never faked ("No fake demo values in actual
dashboard").

The dashboard is an OBSERVER (section 104): it reads from the database
and an optional gateway, but never drives trading logic itself. If this
process dies, nothing that depends on trading safety dies with it —
there is no trading loop running inside it.

Takes a DB PATH, not a live connection: FastAPI dispatches each request
onto a worker thread (via anyio's run_in_threadpool), and sqlite3
connections are thread-affine — sharing one connection object across
requests raised `sqlite3.ProgrammingError: SQLite objects created in a
thread can only be used in that same thread` under TestClient. Opening a
short-lived connection per request avoids this; SQLite/WAL mode is cheap
to open and supports concurrent readers.
"""

from __future__ import annotations

import contextlib
from pathlib import Path

from fastapi import FastAPI

from adaptive_scalper.dashboard.health import compute_health
from adaptive_scalper.gateway.protocol import Gateway
from adaptive_scalper.persistence.database import connect

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765


def create_app(db_path: str | Path, gateway: Gateway | None = None) -> FastAPI:
    """Build the dashboard FastAPI app.

    `gateway` is injected (not constructed here) so tests can point the
    app at a FakeGateway. It's read-only from the dashboard's perspective
    and, like the connection, must itself be safe to call from a worker
    thread — FastAPI dispatches each request handler onto its own worker
    thread, and MetaTrader5's underlying calls are not documented as safe
    for concurrent multi-thread use. When `gateway` wraps a real
    `Mt5Gateway`, callers MUST pass a `SynchronizedGateway`
    (`adaptive_scalper/gateway/synchronized_gateway.py`), not the raw
    `Mt5Gateway`, so concurrent dashboard requests serialize through one
    lock instead of racing each other. A `FakeGateway` in tests needs no
    such wrapping (it's already safe — pure in-memory reads).
    """
    app = FastAPI(title="Adaptive Scalper Next — Dashboard")

    @app.get("/api/health")
    def get_health() -> dict:
        with contextlib.closing(connect(db_path)) as conn:
            report = compute_health(conn, gateway)
        return {
            "state": report.state.value,
            "reasons": list(report.reasons),
            "database_integrity": report.database_integrity,
            "kill_switch_status": report.kill_switch_status,
            "kill_switch_blocks_new_entries": report.kill_switch_blocks_new_entries,
            "kill_switch_reason": report.kill_switch_reason,
            "mt5_connected": report.mt5_connected,
        }

    return app
