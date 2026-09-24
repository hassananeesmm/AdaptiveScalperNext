"""PAPER cycle driver (directive section 132).

`run_paper_cycle()` is the ONLY entry point: reuses `backtest.engine
.run_backtest()`'s exact production decision cores in its incremental
(`resume_*`/`force_close_at_range_end=False`) mode, adds no decision logic
of its own. Its whole job is correct, safe WINDOWING and STATE
PERSISTENCE across repeated calls:

- PAPER starts NOW. A new session treats only the most recent supplied
  bar as new; everything before it is feature context. Deep history is a
  backtest's job -- replaying it here would record historical simulated
  trades as `PAPER_LIVE_DATA`, mixing evidence classes (directive
  section 82).
- Computes exactly the bar slice `run_backtest()`'s resume contract
  needs ([trailing feature_lookback+1 context] + [only bars strictly newer
  than the session's cursor]) from whatever bar history the caller
  supplies -- one new bar is enough.
- A session is bound to the configuration fingerprint it was created
  under (strategy versions, risk policy, fill model, exit/regime/feature
  settings; see `backtest.fingerprint`). Resuming it under a different
  configuration raises `PaperSessionConfigMismatchError`: the caller must
  start a NEW session, never silently continue incompatible state.
- Persists newly-closed trades and every piece of resumable state (open
  position incl. peak R and any pending exit, pending entry, regime
  tracker, risk state) atomically, so a crash between "decide" and
  "persist" is always safely retryable (the same unprocessed window is
  re-derived deterministically next call; `paper.state.record_paper_
  trades()`'s idempotent INSERT OR IGNORE makes a retry a safe no-op).

NEVER calls the broker order-submission/order-verification gateway calls
(directive section 132: "no broker order_send") -- there is no
`Gateway` parameter here at all. The runtime supplies `bars` (fetched
live from the real MT5 terminal, per directive section 132: "PAPER uses
real MT5 market data when available") and this module never reaches into
gateway/broker concerns itself.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass, replace

from adaptive_scalper.backtest import engine as _engine
from adaptive_scalper.backtest.fingerprint import compute_config_fingerprint
from adaptive_scalper.backtest.types import BacktestConfig, EntryRejection, OpenPositionState, SimulatedTrade
from adaptive_scalper.gateway.types import Bar, SymbolSpec
from adaptive_scalper.paper.state import (
    bind_session_config,
    get_or_create_session,
    record_paper_trades,
    save_session_state,
)
from adaptive_scalper.portfolio.correlation import CorrelationResult
from adaptive_scalper.portfolio.exposure import PositionExposure
from adaptive_scalper.simulation.types import EvidenceOrigin


class PaperSessionConfigMismatchError(RuntimeError):
    """The session's persisted state was produced under a different
    configuration. Start a new session key instead of resuming."""


@dataclass(frozen=True)
class PaperCycleResult:
    ran: bool  # False = no new bars to process yet -- a safe, expected no-op
    session_key: str
    new_trades: tuple[SimulatedTrade, ...]
    equity: float
    open_position: OpenPositionState | None
    last_processed_bar_time_utc: int | None
    entry_rejections: tuple[EntryRejection, ...] = ()
    risk_halted_scans: int = 0
    config_fingerprint: str | None = None


def _slice_resume_window(
    bars: list[Bar], *, last_processed_bar_time_utc: int | None, feature_lookback: int,
) -> list[Bar]:
    """Returns exactly the slice `run_backtest(..., resume_*=...)`'s
    contract needs. A `None` cursor (the session's first-ever cycle)
    returns the trailing `feature_lookback + 2` bars: context plus the
    single most recent bar, which is the first bar PAPER decides on."""
    if last_processed_bar_time_utc is None:
        return bars[-(feature_lookback + 2):]
    new_start = next((i for i, b in enumerate(bars) if b.time > last_processed_bar_time_utc), len(bars))
    if new_start < len(bars) and new_start < feature_lookback + 1:
        # Too little context before the first new bar: run_backtest() would
        # start deciding PAST the new bars and the cursor would skip them.
        raise ValueError(
            f"bar history must include at least feature_lookback+1 ({feature_lookback + 1}) already-processed "
            f"bars before the first new bar; got {new_start}"
        )
    return bars[max(0, new_start - feature_lookback - 1):]


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
    external_open_positions: tuple[PositionExposure, ...] = (),
    correlation_matrix: dict[tuple[str, str], CorrelationResult] | None = None,
) -> PaperCycleResult:
    """`external_open_positions`/`correlation_matrix`: the OTHER symbols'
    current PAPER exposure, so this symbol's deferred entries pass the
    same max-positions/total-risk/portfolio-heat/correlation gates a DEMO
    entry would."""
    key = session_key or f"PAPER:{canonical_symbol}:{resolution}"
    now = now_utc if now_utc is not None else int(time.time())

    strategies = tuple((s.key, s.version) for s in _engine.build_active_registry().all_active())
    fingerprint, config_json = compute_config_fingerprint(
        config, canonical_symbol=canonical_symbol, resolutions=(resolution,), strategies=strategies,
    )
    session = get_or_create_session(
        conn, key, canonical_symbol, resolution, initial_equity=config.initial_equity,
        config_fingerprint=fingerprint, config_json=config_json, now_utc=now,
    )
    if session.canonical_symbol != canonical_symbol or session.resolution != resolution:
        raise PaperSessionConfigMismatchError(
            f"session {key!r} is {session.canonical_symbol}/{session.resolution}, "
            f"not {canonical_symbol}/{resolution}"
        )
    if session.config_fingerprint != fingerprint:
        if session.config_fingerprint is None and session.last_processed_bar_time_utc is None:
            bind_session_config(conn, key, config_fingerprint=fingerprint, config_json=config_json)
        else:
            raise PaperSessionConfigMismatchError(
                f"session {key!r} was created under configuration {session.config_fingerprint!r} and cannot be "
                f"resumed under {fingerprint!r} -- start a new PAPER session key instead"
            )

    window = _slice_resume_window(
        bars, last_processed_bar_time_utc=session.last_processed_bar_time_utc,
        feature_lookback=config.feature_lookback,
    )
    # One genuinely new bar is enough: live closed bars arrive one at a time.
    if len(window) < config.feature_lookback + 2:
        return PaperCycleResult(
            ran=False, session_key=key, new_trades=(), equity=session.equity,
            open_position=session.open_position, last_processed_bar_time_utc=session.last_processed_bar_time_utc,
            config_fingerprint=fingerprint,
        )

    # Continue from the REAL current simulated equity, never the config's
    # static default -- otherwise every cycle would re-size positions as
    # if starting fresh from `config.initial_equity` again.
    run_config = replace(config, initial_equity=session.equity)
    result = _engine.run_backtest(
        window, canonical_symbol, resolution, symbol_spec, config=run_config, now_utc=now,
        resume_open_position=session.open_position, resume_pending_entry=session.pending_entry,
        resume_regime_tracker=session.regime_tracker_state, resume_risk_state=session.risk_state,
        force_close_at_range_end=False, origin=EvidenceOrigin.PAPER_LIVE_DATA,
        external_open_positions=external_open_positions, correlation_matrix=correlation_matrix,
    )

    conn.execute("BEGIN IMMEDIATE")
    try:
        record_paper_trades(conn, key, canonical_symbol, result.trades, now_utc=now)
        save_session_state(
            conn, key, equity=result.metrics.final_equity, last_processed_bar_time_utc=window[-1].time,
            open_position=result.open_position, regime_tracker_state=result.final_regime_tracker_state,
            pending_entry=result.pending_entry, risk_state=result.final_risk_state, now_utc=now,
        )
        conn.execute("COMMIT")
    except sqlite3.Error:
        conn.execute("ROLLBACK")
        raise

    return PaperCycleResult(
        ran=True, session_key=key, new_trades=result.trades, equity=result.metrics.final_equity,
        open_position=result.open_position, last_processed_bar_time_utc=window[-1].time,
        entry_rejections=result.entry_rejections, risk_halted_scans=result.risk_halted_scans,
        config_fingerprint=fingerprint,
    )
