"""AI Trading Desk: an isolated, ADVISORY-ONLY multi-agent research subsystem.

Seven agents (market, strategy, news, risk, skeptic, learning, coordinator)
analyse immutable, read-only snapshots of the trading database and write
their structured, validated findings to a SEPARATE journal
(`data/ai_desk.sqlite3`).

Hard boundaries (proven by `tests/test_ai_desk_safety.py`):

- The desk runs in its own process (`ai-desk serve`); the trading runtime
  never imports this package, so a slow, hung or crashed desk cannot delay
  position management, reconciliation or any entry decision.
- The trading database is opened ONLY with `connect_readonly` (SQLite
  rejects every write). The desk never constructs a gateway, never imports
  `execution/`, `gateway/` or `MetaTrader5`, never engages, bootstraps or
  clears the kill switch, and never constructs `OperatorAuthority`.
- No agent output carries an order, a direction to trade, a size, a stop,
  a target, a risk limit or a certificate. Agreement between agents is
  never evidence of an edge; the Skeptic may contradict everyone.
- Works fully offline (deterministic provider). External model providers
  are optional, disabled by default, budgeted and their text is untrusted.

REAL-MONEY EXECUTION IS DISABLED. Nothing here can change that.
"""

DESK_VERSION = "1.0.0"
SCHEMA_VERSION = "1"
ADVISORY_NOTICE = "ADVISORY ONLY - NO EXECUTION AUTHORITY. The trading engine's deterministic gates decide."
