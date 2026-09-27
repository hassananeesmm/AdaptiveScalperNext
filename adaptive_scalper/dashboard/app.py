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

Strategy Lab (`/api/strategy-lab/...`, GET only) is computed on demand
when that view asks for it -- never on the 2 s push -- from the same
read-only connection (see `dashboard/strategy_lab.py`).
"""

from __future__ import annotations

import asyncio
import calendar
import contextlib
import csv
import io
import sqlite3
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, Response
from starlette.concurrency import run_in_threadpool

from adaptive_scalper.dashboard import strategy_lab
from adaptive_scalper.dashboard.health import DASHBOARD_INTEGRITY, compute_health
from adaptive_scalper.dashboard.page import PAGE_HTML
from adaptive_scalper.dashboard.panels import PANELS, compute_all, compute_panel
from adaptive_scalper.persistence.database import connect_readonly

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DEFAULT_PUSH_SECONDS = 2.0

_LAB_TEXT_FILTERS = ("strategy", "symbol", "version", "regime", "direction", "session", "exit_reason",
                     "cost_provenance", "source_class", "status", "q")


def _unavailable(exc: Exception) -> dict:
    return {"status": "UNAVAILABLE", "detail": f"database not readable: {type(exc).__name__}: {exc}"}


def _parse_day_or_epoch(value: str | None, *, end: bool) -> int | None:
    """`YYYY-MM-DD` (a UTC day; an END date is inclusive, so it maps to the
    next midnight) or integer epoch seconds."""
    if value in (None, ""):
        return None
    if value.isdigit():
        return int(value)
    try:
        day = time.strptime(value, "%Y-%m-%d")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"bad date {value!r}: use YYYY-MM-DD") from exc
    return calendar.timegm(day) + (86400 if end else 0)


def _lab_query(request: Request) -> tuple[str, dict, dict]:
    q = request.query_params
    evidence = (q.get("evidence") or "DEMO").upper()
    if evidence not in strategy_lab.EVIDENCE_SOURCES:
        raise HTTPException(status_code=400, detail=f"evidence must be one of {strategy_lab.EVIDENCE_SOURCES}")
    filters: dict = {k: q.get(k) for k in _LAB_TEXT_FILTERS if q.get(k)}
    filters["date_from"] = _parse_day_or_epoch(q.get("date_from"), end=False)
    filters["date_to"] = _parse_day_or_epoch(q.get("date_to"), end=True)
    params: dict = {"run_id": q.get("run_id") or None, "session_key": q.get("session_key") or None}
    if q.get("login"):
        if not q["login"].isdigit():
            raise HTTPException(status_code=400, detail="login must be an integer")
        params["login"] = int(q["login"])
    return evidence, filters, params


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

    def _lab(fn, *args):
        """Run one Strategy Lab computation on a fresh read-only connection
        inside ONE read snapshot (so every figure in a response is mutually
        consistent). Errors are returned as data, never raised into the
        server loop."""
        try:
            with contextlib.closing(connect_readonly(db_path)) as conn:
                conn.execute("BEGIN")
                try:
                    return fn(conn, *args)
                finally:
                    conn.execute("COMMIT")
        except sqlite3.Error as exc:
            return _unavailable(exc)

    @app.get("/api/strategy-lab/summary")
    def lab_summary(request: Request) -> dict:
        evidence, filters, params = _lab_query(request)
        return _lab(strategy_lab.summary, evidence, filters, params)

    @app.get("/api/strategy-lab/trades")
    def lab_trades(request: Request, page: int = 1, page_size: int = 50) -> dict:
        evidence, filters, params = _lab_query(request)
        return _lab(strategy_lab.trades_page, evidence, filters, params, page, page_size)

    @app.get("/api/strategy-lab/trade/{evidence}/{trade_id}")
    def lab_trade(evidence: str, trade_id: str, request: Request) -> dict:
        if evidence.upper() not in strategy_lab.EVIDENCE_SOURCES:
            raise HTTPException(status_code=400, detail="unknown evidence source")
        _, _, params = _lab_query(request)
        result = _lab(strategy_lab.trade_lifecycle, evidence, trade_id, params)
        if result is None:
            raise HTTPException(status_code=404, detail=f"no {evidence.upper()} trade {trade_id!r}")
        return result

    @app.get("/api/strategy-lab/strategy/{strategy_key}")
    def lab_strategy(strategy_key: str) -> dict:
        result = _lab(strategy_lab.strategy_detail, strategy_key)
        if result is None:
            raise HTTPException(status_code=404, detail=f"unknown strategy {strategy_key!r}")
        return result

    @app.get("/api/strategy-lab/research")
    def lab_research() -> dict:
        """Independent per-strategy research reports (files next to the DB
        in `research/`). Read-only; never the live evidence tabs."""
        return strategy_lab.research_reports(Path(db_path).resolve().parent / "research")

    @app.get("/api/strategy-lab/export.csv")
    def lab_export(request: Request, keys: str = "") -> Response:
        """Review export of the comparison table (same filters). Exporting
        is a read; it changes nothing about live execution."""
        evidence, filters, params = _lab_query(request)
        payload = _lab(strategy_lab.summary, evidence, filters, params)
        if payload.get("status") == "UNAVAILABLE":
            raise HTTPException(status_code=503, detail=payload["detail"])
        rows = strategy_lab.comparison_csv_rows(payload, [k for k in keys.split(",") if k])
        buf = io.StringIO()
        if rows:
            writer = csv.DictWriter(buf, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        return Response(buf.getvalue(), media_type="text/csv", headers={
            "Content-Disposition": f'attachment; filename="strategy-lab-{evidence.lower()}.csv"'})

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
