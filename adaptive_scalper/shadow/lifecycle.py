"""Explicit strategy lifecycle contract (observer form; audit section 8).

Audit finding (docs/audits/PROFITABILITY_ROOT_CAUSE_FINAL.md, D5): the V1
position reviews decide "thesis still valid" by re-running the strategy's
ENTRY trigger (`strategy.evaluate(...) is not None`). For event-like triggers
(acceleration, breakout) the trigger normally vanishes one bar after entry, so
the position is closed as "original strategy thesis invalidated" -- 161 of 408
DEMO positions, median hold 301 s, -759.89 USD.

This module makes the seven lifecycle concepts separate, named fields. It is
an OBSERVER contract: nothing in the executable exit path reads it, and the
V1 behaviour is NOT changed here (that needs forward evidence or a
correctness-only equivalence argument). What it does enforce:

- every active strategy declares a lifecycle;
- the V1 strategies are recorded honestly as THESIS_COUPLED_TO_ENTRY_TRIGGER
  with their unvalidated horizon defaults, so no report can present the
  coupling as a designed thesis;
- a strategy registered in the future cannot declare a decoupled thesis
  without defining both its thesis-valid and thesis-invalidated conditions,
  and it must state ONE horizon that every component uses.
"""

from __future__ import annotations

from dataclasses import dataclass

THESIS_COUPLED_TO_ENTRY_TRIGGER = "THESIS_COUPLED_TO_ENTRY_TRIGGER"   # V1: hold == entry trigger still firing
THESIS_EXPLICIT = "THESIS_EXPLICIT"
THESIS_MODES = (THESIS_COUPLED_TO_ENTRY_TRIGGER, THESIS_EXPLICIT)

HORIZON_UNVALIDATED_DEFAULT = "UNVALIDATED_DEFAULT"
HORIZON_PREREGISTERED = "PREREGISTERED"


@dataclass(frozen=True)
class StrategyLifecycle:
    strategy_key: str
    strategy_version: int
    eligibility: str                  # 1. context in which the strategy may propose at all
    entry_trigger: str                # 2. the event that creates a candidate
    initial_risk: str                 # 3. stop definition
    payoff_and_horizon: str           # 4. intended payoff and holding horizon
    horizon_seconds: int              # 4. the ONE horizon every component must use
    horizon_status: str
    thesis_mode: str
    thesis_valid_condition: str | None       # 5. NOT derived from the entry trigger when THESIS_EXPLICIT
    thesis_invalidated_condition: str | None  # 6.
    profit_management: str | None            # 7. optional

    def __post_init__(self) -> None:
        if self.thesis_mode not in THESIS_MODES:
            raise ValueError(f"thesis_mode must be one of {THESIS_MODES}")
        if self.thesis_mode == THESIS_EXPLICIT and not (self.thesis_valid_condition and self.thesis_invalidated_condition):
            raise ValueError(f"{self.strategy_key}: an explicit thesis needs both a valid and an invalidated condition")
        if self.horizon_seconds <= 0:
            raise ValueError("horizon_seconds must be positive")


_V1_EXIT_NOTE = ("V1 adaptive exits: +1.0R early close, breakeven +0.05R after +0.4R, 0.2R giveback after +0.6R, "
                 "regime reversal, 600 s global max hold, and 'thesis invalidated' = entry trigger no longer fires "
                 "or distance-to-target minus remaining cost < 0")


def _v1(key: str, trigger: str, stop_atr: float, target_atr: float, duration: int, eligibility: str):
    return StrategyLifecycle(
        strategy_key=key, strategy_version=1, eligibility=eligibility, entry_trigger=trigger,
        initial_risk=f"{stop_atr} x ATR stop",
        payoff_and_horizon=(f"configured target {target_atr} x ATR ({target_atr / stop_atr:.2f}R); strategy default "
                            f"duration {duration} s has no decision authority; global max hold 600 s"),
        horizon_seconds=600, horizon_status=HORIZON_UNVALIDATED_DEFAULT,
        thesis_mode=THESIS_COUPLED_TO_ENTRY_TRIGGER, thesis_valid_condition=None, thesis_invalidated_condition=None,
        profit_management=_V1_EXIT_NOTE,
    )


V1_LIFECYCLES: dict[str, StrategyLifecycle] = {lc.strategy_key: lc for lc in (
    _v1("microstructure_acceleration", "|acceleration| / ATR above threshold (event)", 1.0, 1.5, 180,
        "any regime; 93-97 % of historical entries in RANGE"),
    _v1("momentum_continuation", "trend regime with efficiency ratio above threshold", 1.5, 2.5, 600,
        "TRENDING_UP / TRENDING_DOWN"),
    _v1("pullback_continuation", "pullback toward trend mean inside a trend regime", 1.2, 2.0, 480,
        "TRENDING_UP / TRENDING_DOWN"),
    _v1("range_breakout", "close beyond the recent range (event)", 2.0, 3.0, 600, "RANGE / COMPRESSION"),
    _v1("statistical_reversion", "price near a range extreme", 1.0, 1.5, 300, "RANGE"),
    _v1("volatility_expansion", "range expansion with a directional wick imbalance (event)", 2.0, 2.5, 420,
        "VOLATILITY_EXPANSION"),
)}


def trigger_loss_invalidates_thesis(lifecycle: StrategyLifecycle) -> bool:
    """Whether "the entry trigger no longer fires" may, BY ITSELF, mean the
    holding thesis is invalid. Only the V1 coupled mode says yes -- and that is
    the audited defect, recorded rather than endorsed. An explicit thesis is
    judged by its own invalidation condition, never by the trigger."""
    return lifecycle.thesis_mode == THESIS_COUPLED_TO_ENTRY_TRIGGER


def lifecycle_for(strategy_key: str) -> StrategyLifecycle:
    try:
        return V1_LIFECYCLES[strategy_key]
    except KeyError:
        raise KeyError(f"strategy {strategy_key!r} has no declared lifecycle (audit section 8)") from None
