"""PAPER cycle driver (directive section 132).

`run_paper_cycle()` is the ONLY entry point: reuses `backtest.engine
.run_backtest()`'s exact production decision cores in its incremental
(`resume_open_position`/`resume_pending_entry`/
`force_close_at_range_end=False`) mode, adds no
decision logic of its own. Its whole job is correct, safe WINDOWING and
STATE PERSISTENCE across repeated calls:

- Computes exactly the bar slice `run_backtest()`'s resume contract
  needs ([trailing feature_lookback context] + [only bars strictly newer
  than the session's cursor]) from whatever full bar history the caller
  supplies -- callers never have to get this right by hand (see
  `backtest.engine.run_backtest()`'s docstring for why re-passing already-
  processed bars is a real, dangerous bug, not just wasted work).
- Persists newly-closed trades plus resumable open-position, pending-entry
  and regime state atomically, so a crash between "decide" and "persist" is
  always safely retryable (the same unprocessed window is re-derived
  deterministically next call; `paper.state.record_paper_trades()`'s
  idempotent INSERT OR IGNORE makes a retry a safe no-op).

NEVER calls the broker order-submission/order-verification gateway calls
(directive section 132: "no broker order_send") -- there is no
`Gateway` parameter here at all. A future runtime-engine caller supplies
`bars` (fetched live from the real MT5 terminal, per directive section
132: "PAPER uses real MT5 market data when available") and this module
never reaches into gateway/broker concerns itself.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass, replace

from adaptive_scalper.backtest.engine import run_backtest
from adaptive_scalper.backtest.types import (
    BacktestConfig,
    OpenPositionState,
    PendingEntryState,
    RegimeTrackerState,
    SimulatedTrade,
)
from adaptive_scalper.gateway.types import Bar, SymbolSpec
from adaptive_scalper.paper.state import (
    PaperStateError,
    get_or_create_session,
    record_paper_trades,
    save_session_state,
)


@dataclass(frozen=True)
class PaperCycleResult:
    ran: bool  # False = no new bars to process yet -- a safe, expected no-op
    session_key: str
    new_trades: tuple[SimulatedTrade, ...]
    equity: float
    open_position: OpenPositionState | None
    pending_entry: PendingEntryState | None
    last_processed_bar_time_utc: int | None


def _slice_resume_window(
    bars: list[Bar], *, last_processed_bar_time_utc: int | None, feature_lookback: int,
) -> list[Bar]:
    """Returns exactly the slice `run_backtest(..., resume_open_position=
    ...)`'s contract needs. `None` cursor (the session's first-ever
    cycle) returns `bars` unchanged -- nothing has been processed yet, so
    the entire supplied history is legitimately "new"."""
    if last_processed_bar_time_utc is None:
        return bars
    new_start = next((i for i, b in enumerate(bars) if b.time > last_processed_bar_time_utc), len(bars))
    context_start = max(0, new_start - feature_lookback - 1)
    return bars[context_start:]


def run_paper_cycle(
    conn: sqlite3.Connection,
    bars: list[Bar],
    canonical_symbol: str,
    resolution: str,
    symbol_spec: SymbolSpec,
    *,
    config: BacktestConfig = BacktestConfig(),
    session_key: str | None = None,
    now_utc: int | None = None,
) -> PaperCycleResult:
    key = session_key or f"PAPER:{canonical_symbol}:{resolution}"
    now = now_utc if now_utc is not None else int(time.time())

    session = get_or_create_session(
        conn, key, canonical_symbol, resolution, initial_equity=config.initial_equity, now_utc=now,
    )

    window = _slice_resume_window(
        bars, last_processed_bar_time_utc=session.last_processed_bar_time_utc,
        feature_lookback=config.feature_lookback,
    )
    if len(window) < config.feature_lookback + 3:
        return PaperCycleResult(
            ran=False, session_key=key, new_trades=(), equity=session.equity,
            open_position=session.open_position, pending_entry=session.pending_entry,
            last_processed_bar_time_utc=session.last_processed_bar_time_utc,
        )

    # Continue from the REAL current simulated equity, never the config's
    # static default -- otherwise every cycle would re-size positions as
    # if starting fresh from `config.initial_equity` again.
    run_config = replace(config, initial_equity=session.equity)
    result = run_backtest(
        window, canonical_symbol, resolution, symbol_spec, config=run_config, now_utc=now,
        resume_open_position=session.open_position, resume_pending_entry=session.pending_entry,
        resume_regime_tracker=session.regime_tracker_state, force_close_at_range_end=False,
    )

    conn.execute("BEGIN IMMEDIATE")
    try:
        record_paper_trades(conn, key, canonical_symbol, result.trades, now_utc=now)
        save_session_state(
            conn, key, equity=result.metrics.final_equity, last_processed_bar_time_utc=window[-1].time,
            open_position=result.open_position, pending_entry=result.pending_entry,
            regime_tracker_state=result.final_regime_tracker_state, now_utc=now,
        )
        conn.execute("COMMIT")
    except (sqlite3.Error, PaperStateError):
        conn.execute("ROLLBACK")
        raise

    return PaperCycleResult(
        ran=True, session_key=key, new_trades=result.trades, equity=result.metrics.final_equity,
        open_position=result.open_position, pending_entry=result.pending_entry,
        last_processed_bar_time_utc=window[-1].time,
    )
