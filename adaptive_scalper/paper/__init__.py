"""PAPER mode (directive section 132): real MT5 market data, simulated
fills, NEVER a broker `order_send`. All resulting evidence is labeled
`PAPER_LIVE_DATA` (directive section 82) and persisted in its own
`paper_session_state`/`paper_trades` tables (migrations `0018_paper` +
`0019_paper_pending_entry`),
deliberately separate from `positions`/`orders`/`deals` -- a simulated
PAPER position must never be reachable by `execution/reconciliation.py`'s
broker-truth recovery path, and vice versa.

- `state.py` -- session persistence (resumable cursor, current simulated
  equity, currently-open simulated position, and typed deferred entry).
- `engine.py` -- `run_paper_cycle()`: reuses `adaptive_scalper.backtest
  .engine.run_backtest()`'s exact same production decision cores via its
  `resume_open_position`/`resume_pending_entry`/
  `force_close_at_range_end=False` incremental
  mode, never a parallel reimplementation of the decision logic.
"""

from __future__ import annotations
