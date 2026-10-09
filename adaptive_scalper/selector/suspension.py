"""Entry suspension (config `strategies.entry_suspended`, release 0.2.8).

A suspended strategy stays ACTIVE (registered, evaluated, its signals
journaled as evidence) but its signals are removed BEFORE the frozen V1
selector ranks candidates, so it can never open a position. Applied
identically by the DEMO runtime and by `backtest.engine.run_backtest`
(hence PAPER). Narrow-only: it removes candidates, never adds or prefers
one, and the selector itself stays byte-identical V1 code.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

REJECTED_ENTRY_SUSPENDED = "strategy_entry_suspended"


def partition_suspended(signals: Sequence, suspended: Iterable[str]) -> tuple[list[int], list[int]]:
    """Indices of `signals` that remain eligible, and of those suspended."""
    blocked = frozenset(suspended)
    kept = [i for i, s in enumerate(signals) if s.strategy_key not in blocked]
    dropped = [i for i, s in enumerate(signals) if s.strategy_key in blocked]
    return kept, dropped
