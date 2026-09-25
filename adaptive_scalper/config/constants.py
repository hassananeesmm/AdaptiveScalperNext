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


# Documented HARD risk ceilings (CLAUDE.md, MASTER_BUILD_DIRECTIVE.md; docs/SAFETY.md
# guarantee 5). Configuration may LOWER any of these but never raise them: both
# `config.loader.RiskConfig` and `risk.governor.RiskLimits` refuse larger values, so
# no config file, research run, model or knowledge source can exceed them.
HARD_RISK_CEILINGS: dict[str, float] = {
    "risk_per_trade_pct": 0.25,
    "max_total_open_risk_pct": 0.75,
    "max_daily_loss_pct": 2.00,
    "max_drawdown_pct": 5.00,
    "max_open_positions": 2,
    "max_positions_per_symbol": 1,
}


def check_hard_risk_ceilings(**limits: float) -> None:
    """Raise ValueError if any given limit is non-positive or above its hard ceiling."""
    for name, value in limits.items():
        ceiling = HARD_RISK_CEILINGS[name]
        if not (0 < value <= ceiling):
            raise ValueError(f"{name}={value} is outside (0, {ceiling}]: the hard ceiling can be lowered, never raised")

