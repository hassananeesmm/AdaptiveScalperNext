"""The reserved untouched out-of-sample interval, enforced below research.

The holdout was reserved before any research was run (docs/QA_REPORT.md,
docs/RESEARCH_VALIDATION.md): 2026-07-01 00:00 UTC to the end of
2026-09-18 UTC, all symbols, half-open [start, end). It is consumed ONCE,
for one locked candidate, via `backtest.oos.run_untouched_oos` after
explicit operator authorization -- never by research.

This module lives in `backtest` (not `research`) so the engine itself can
refuse research hooks over the holdout: a guard only in the research
wrappers is bypassed by calling `run_backtest(..., research_*=...)`
directly (defense in depth, master prompt section 18).
"""

from __future__ import annotations

RESERVED_OOS_INTERVALS: tuple[tuple[int, int], ...] = ((1782864000, 1789776000),)


class ReservedOosOverlapError(ValueError):
    """The requested research range overlaps the reserved untouched OOS
    interval. Research never reads it; use the one-shot `oos` command."""


def overlaps_reserved_oos(start_utc: int, end_utc: int) -> bool:
    return any(start_utc < oos_end and end_utc >= oos_start for oos_start, oos_end in RESERVED_OOS_INTERVALS)


def assert_outside_reserved_oos(start_utc: int, end_utc: int) -> None:
    for oos_start, oos_end in RESERVED_OOS_INTERVALS:
        if start_utc < oos_end and end_utc >= oos_start:
            raise ReservedOosOverlapError(
                f"research range [{start_utc}, {end_utc}] overlaps the reserved untouched OOS interval "
                f"[{oos_start}, {oos_end}) -- refused; OOS is consumed once via `oos`, never by research"
            )
