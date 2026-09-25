"""Local observer-only dashboard (FastAPI).

Binds 127.0.0.1 by default (directive section 83). It is an OBSERVER
(section 104):

- it opens the database READ-ONLY (`connect_readonly`: SQLite rejects any
  write), per request, so an observer bug cannot change trading state;
- it never connects to MT5 -- the runtime process is the only terminal
  client; broker/terminal state arrives through `runtime_state`;
- it exposes no endpoint that changes anything (no kill-switch clear, no
  order, no config) -- operator actions are CLI commands;
- if this process dies, nothing that trading safety depends on dies.

Endpoints: `/` (page), `/api/health`, `/api/panels`, `/api/panels/{name}`
and the `/ws` WebSocket, which pushes all panels every `push_seconds`;
the page falls back to polling `/api/panels` when the socket drops.
"""

from __future__ import annotations

import asyncio
import contextlib
import sqlite3
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from starlette.concurrency import run_in_threadpool

from adaptive_scalper.dashboard.health import DASHBOARD_INTEGRITY, compute_health
from adaptive_scalper.dashboard.page import PAGE_HTML
from adaptive_scalper.dashboard.panels import PANELS, compute_all, compute_panel
from adaptive_scalper.persistence.database import connect_readonly

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DEFAULT_PUSH_SECONDS = 2.0


def _unavailable(exc: Exception) -> dict:
    return {"status": "UNAVAILABLE", "detail": f"database not readable: {type(exc).__name__}: {exc}"}


def create_app(db_path: str | Path, *, push_seconds: float = DEFAULT_PUSH_SECONDS) -> FastAPI:
    app = FastAPI(title="Adaptive Scalper Next - Dashboard (observer only)")

    def _all() -> dict:
        try:
            with contextlib.closing(connect_readonly(db_path)) as conn:
                return compute_all(conn)
        except sqlite3.Error as exc:
            return {"generated_at_utc": None, "panels": {}, **_unavailable(exc)}

    @app.get("/", response_class=HTMLResponse)
    def page() -> str:
        return PAGE_HTML

    @app.get("/api/health")
    def get_health() -> dict:
        try:
            with contextlib.closing(connect_readonly(db_path)) as conn:
                report = compute_health(conn, integrity=DASHBOARD_INTEGRITY.status(db_path)[0])
        except sqlite3.Error as exc:
            return {"state": "UNAVAILABLE", **_unavailable(exc)}
        return {
            "state": report.state.value, "reasons": list(report.reasons),
            "database_integrity": report.database_integrity, "kill_switch_status": report.kill_switch_status,
            "kill_switch_blocks_new_entries": report.kill_switch_blocks_new_entries,
            "kill_switch_reason": report.kill_switch_reason,
            "mt5_connected": report.mt5_connected,  # always None: the dashboard never talks to MT5
            "real_money_execution": "DISABLED",
        }

    @app.get("/api/panels")
    def get_panels() -> dict:
        return _all()

    @app.get("/api/panels/{name}")
    def get_panel(name: str) -> dict:
        if name not in PANELS:
            raise HTTPException(status_code=404, detail=f"unknown panel {name!r}")
        try:
            with contextlib.closing(connect_readonly(db_path)) as conn:
                return compute_panel(conn, name)
        except sqlite3.Error as exc:
            return _unavailable(exc)

    @app.websocket("/ws")
    async def feed(ws: WebSocket) -> None:
        await ws.accept()
        try:
            while True:
                await ws.send_json(await run_in_threadpool(_all))
                await asyncio.sleep(push_seconds)
        except (WebSocketDisconnect, RuntimeError):
            return

    return app
