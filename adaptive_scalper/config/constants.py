"""Hard, non-negotiable safety constants.

Per MASTER_BUILD_DIRECTIVE.md sections 4, 5 and 8, and CLAUDE.md's
permanent project rules: these values are not configuration. They are not
read from TOML, not adjustable by learning/RAG/ML, and not meant to change
without a deliberate, reviewed source-code edit.

Every other component that needs to know the tradable universe, the
retired-strategy list, or the allowed operating modes must import from
here rather than re-declaring its own copy or reading it from config.
"""

from __future__ import annotations

# Section 5 — EXACT TRADING UNIVERSE. Exactly these three symbols may ever
# produce an active strategy signal, an executable proposal, or an order.
# A fourth symbol must be blocked with reason BLOCK_SYMBOL_NOT_ALLOWED.
ALLOWED_CANONICAL_SYMBOLS: frozenset[str] = frozenset({
    "XAUUSD",
    "GBPJPY",
    "BTCUSD",
})

# Section 8 — PERMANENTLY RETIRED STRATEGIES. These identifiers must never
# register, signal, propose, rank, train actively, promote, or execute,
# through restart, config reload, migration, model-registry restore, RAG,
# or packaging. Historical records mentioning them may remain; the keys
# themselves must never re-enter the active set.
RETIRED_STRATEGY_KEYS: frozenset[str] = frozenset({
    "failed_breakout_fade",
    "support_resistance_reaction",
})

# Section 3/4 — operating modes. There is no third value. A usable
# real-money/LIVE order path must never exist.
ALLOWED_MODES: frozenset[str] = frozenset({
    "PAPER",
    "DEMO",
})
