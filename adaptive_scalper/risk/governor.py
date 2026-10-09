"""Risk governor (directive section 32-33).

`calculate_safe_volume()` is the ONLY function in this codebase that may
compute a position's monetary size. It takes CURRENT equity, CURRENT
stop distance, and CURRENT broker contract data — nothing else. In
particular it has no "previous volume," "loss streak," or "multiplier"
parameter, which makes martingale/grid/revenge-sizing structurally
impossible to express through this API, not merely discouraged by
convention (directive section 33: "No martingale. No revenge trading.
No doubling losses. No grid-loss recovery. No uncontrolled averaging
down."). `tests/test_risk_governor.py` asserts this by inspecting the
function's actual signature, so the guarantee can't silently erode if
someone edits this file later without reading this docstring.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from adaptive_scalper.config.constants import check_hard_risk_ceilings
from adaptive_scalper.gateway.types import SymbolSpec

ALLOW = "ALLOW"
BLOCK_RISK = "BLOCK_RISK"


@dataclass(frozen=True)
class RiskLimits:
    risk_per_trade_pct: float
    max_total_open_risk_pct: float
    max_daily_loss_pct: float
    max_drawdown_pct: float
    max_open_positions: int
    max_positions_per_symbol: int

    def __post_init__(self) -> None:
        # Every construction path, not only the config loader, is bound by the
        # documented hard ceilings (they can be lowered, never raised).
        check_hard_risk_ceilings(
            risk_per_trade_pct=self.risk_per_trade_pct, max_total_open_risk_pct=self.max_total_open_risk_pct,
            max_daily_loss_pct=self.max_daily_loss_pct, max_drawdown_pct=self.max_drawdown_pct,
            max_open_positions=self.max_open_positions, max_positions_per_symbol=self.max_positions_per_symbol,
        )


def risk_limits_from_config(risk_config) -> RiskLimits:
    """The ONLY intended construction path for `RiskLimits` in real
    operation: directly from the validated, fail-closed `RiskConfig`
    (adaptive_scalper.config.loader). Directive section 32: "Learning
    may NEVER raise these automatically" — there is currently no
    ML/learning module in this codebase (Stage 0, directive section 61),
    so there is nothing yet that COULD raise them; when one is built, it
    must never gain access to construct or mutate a `RiskLimits` — that
    boundary is enforced by code review and import discipline (which
    packages ever import `RiskLimits`'s constructor), the same honestly-
    documented boundary `core/operator_authority.py` uses for the kill
    switch."""
    return RiskLimits(
        risk_per_trade_pct=risk_config.risk_per_trade_pct,
        max_total_open_risk_pct=risk_config.max_total_open_risk_pct,
        max_daily_loss_pct=risk_config.max_daily_loss_pct,
        max_drawdown_pct=risk_config.max_drawdown_pct,
        max_open_positions=risk_config.max_open_positions,
        max_positions_per_symbol=risk_config.max_positions_per_symbol,
    )


@dataclass(frozen=True)
class SafeVolumeResult:
    approved: bool
    volume: float | None
    monetary_risk: float | None
    reason: str


def calculate_safe_volume(
    *, equity: float, risk_per_trade_pct: float, stop_distance_price: float, symbol_spec: SymbolSpec,
) -> SafeVolumeResult:
    """Safe position volume from CURRENT equity/stop/contract data only.

    Rounds DOWN to the broker's `volume_step` (directive section 33 —
    never round up). If the rounded-down volume is still below
    `volume_min`, REJECTS rather than raising to the minimum, since
    doing so would risk more than `risk_per_trade_pct` of equity.
    """
    if equity <= 0:
        return SafeVolumeResult(False, None, None, f"equity must be positive, got {equity!r}")
    if stop_distance_price <= 0:
        return SafeVolumeResult(False, None, None, f"stop_distance_price must be positive, got {stop_distance_price!r}")
    if symbol_spec.trade_tick_size <= 0 or symbol_spec.trade_tick_value <= 0:
        return SafeVolumeResult(False, None, None, "invalid contract spec: trade_tick_size/trade_tick_value must be positive")
    if symbol_spec.volume_step <= 0:
        return SafeVolumeResult(False, None, None, "invalid contract spec: volume_step must be positive")

    risk_amount = equity * (risk_per_trade_pct / 100.0)
    risk_per_lot = (stop_distance_price / symbol_spec.trade_tick_size) * symbol_spec.trade_tick_value
    if risk_per_lot <= 0:
        return SafeVolumeResult(False, None, None, "computed monetary risk per lot is non-positive")

    raw_volume = risk_amount / risk_per_lot
    steps = math.floor(raw_volume / symbol_spec.volume_step + 1e-9)  # tiny epsilon guards float round-trip error
    safe_volume = round(steps * symbol_spec.volume_step, 8)
    safe_volume = min(safe_volume, symbol_spec.volume_max)

    if safe_volume < symbol_spec.volume_min:
        return SafeVolumeResult(
            False, None, None,
            f"safe volume {safe_volume} (from risk_amount={risk_amount:.2f}, risk_per_lot={risk_per_lot:.4f}) "
            f"is below broker minimum {symbol_spec.volume_min} — rejected, never rounded up",
        )

    actual_monetary_risk = safe_volume * risk_per_lot
    return SafeVolumeResult(True, safe_volume, actual_monetary_risk, "ok")


@dataclass(frozen=True)
class RiskGateInput:
    proposed_symbol: str
    proposed_monetary_risk: float
    equity: float
    current_total_open_risk: float
    current_total_pending_risk: float
    current_positions_count: int
    current_positions_for_symbol: int
    daily_realized_pnl: float   # negative = net loss today
    peak_equity: float
    # Floating P&L of open positions now (broker equity - balance). Only a
    # floating LOSS counts toward the daily loss limit; a floating gain never
    # offsets a realized loss. 0.0 = no open positions (the backtest marks
    # its own open positions separately and leaves this at 0.0).
    daily_floating_pnl: float = 0.0
    # Account equity at the start of the UTC day; None = unknown, in which
    # case current equity is the denominator (the pre-2026-10-06 behaviour).
    day_start_equity: float | None = None


def effective_daily_loss_pct(
    *, realized_pnl: float, floating_pnl: float, day_start_equity: float | None, equity: float,
) -> float:
    """Today's loss in PERCENT (2.00 == 2 %, the `max_daily_loss_pct` unit):

        -100 * (realized + min(0, floating)) / denominator, floored at 0

    The denominator is the SMALLER of start-of-day equity and current equity,
    so this is never more lenient than the realized-only, current-equity
    figure it replaced. Anything non-finite or a non-positive denominator
    returns +inf: an unknowable loss is treated as a breached limit."""
    values = (realized_pnl, floating_pnl, equity) + (() if day_start_equity is None else (day_start_equity,))
    if not all(math.isfinite(v) for v in values):
        return math.inf
    denominator = equity if day_start_equity is None else min(day_start_equity, equity)
    if denominator <= 0:
        return math.inf
    return max(0.0, -100.0 * (realized_pnl + min(0.0, floating_pnl)) / denominator)


def evaluate_risk_gate(inp: RiskGateInput, limits: RiskLimits) -> tuple[str, str]:
    """The hard portfolio ceilings (directive section 32). Checked in a
    fixed order so the reported reason is always the FIRST limit that
    would be breached, not an arbitrary one.

    Per external review: this gate must independently re-verify the
    per-trade risk ceiling rather than trust that
    `proposed_monetary_risk` was correctly derived from
    `calculate_safe_volume()` — `calculate_safe_volume()` remains the
    SOLE function that may compute a volume/monetary-risk figure in the
    first place, but this gate is the last line of defense if a
    tampered, miscalculated, or otherwise bypassed proposal ever reaches
    it. Total open risk also now includes PENDING risk (resting orders
    not yet filled) alongside open positions — a pending order is real
    exposure the moment it could fill, not exposure that only counts
    once filled.
    """
    if inp.proposed_monetary_risk <= 0 or not math.isfinite(inp.proposed_monetary_risk):
        return BLOCK_RISK, f"proposed_monetary_risk must be a positive, finite number, got {inp.proposed_monetary_risk!r}"
    if inp.equity <= 0 or not math.isfinite(inp.equity):
        return BLOCK_RISK, f"equity must be a positive, finite number, got {inp.equity!r}"

    max_per_trade_risk = inp.equity * (limits.risk_per_trade_pct / 100.0)
    if inp.proposed_monetary_risk > max_per_trade_risk:
        return BLOCK_RISK, (
            f"proposed_monetary_risk ({inp.proposed_monetary_risk:.2f}) exceeds the independent "
            f"per-trade ceiling ({max_per_trade_risk:.2f} = {limits.risk_per_trade_pct}% of equity) "
            f"— rejected regardless of how it was computed upstream"
        )

    if inp.current_positions_count >= limits.max_open_positions:
        return BLOCK_RISK, (
            f"max_open_positions reached ({inp.current_positions_count}/{limits.max_open_positions})"
        )
    if inp.current_positions_for_symbol >= limits.max_positions_per_symbol:
        return BLOCK_RISK, (
            f"max_positions_per_symbol reached for {inp.proposed_symbol} "
            f"({inp.current_positions_for_symbol}/{limits.max_positions_per_symbol})"
        )

    max_total_risk = inp.equity * (limits.max_total_open_risk_pct / 100.0)
    total_risk_after = inp.current_total_open_risk + inp.current_total_pending_risk + inp.proposed_monetary_risk
    if total_risk_after > max_total_risk:
        return BLOCK_RISK, (
            f"total risk after this trade ({total_risk_after:.2f} = open "
            f"{inp.current_total_open_risk:.2f} + pending {inp.current_total_pending_risk:.2f} + "
            f"proposed {inp.proposed_monetary_risk:.2f}) would exceed max_total_open_risk_pct "
            f"limit ({max_total_risk:.2f})"
        )

    daily_loss_pct = effective_daily_loss_pct(
        realized_pnl=inp.daily_realized_pnl, floating_pnl=inp.daily_floating_pnl,
        day_start_equity=inp.day_start_equity, equity=inp.equity,
    )
    if daily_loss_pct >= limits.max_daily_loss_pct:
        return BLOCK_RISK, (
            f"daily loss {daily_loss_pct:.2f}% (realized {inp.daily_realized_pnl:.2f} + floating loss "
            f"{min(0.0, inp.daily_floating_pnl):.2f}) has reached/exceeded "
            f"max_daily_loss_pct {limits.max_daily_loss_pct}%"
        )

    # ASN-034: an unknown baseline (NaN, inf, zero, negative) used to skip the
    # drawdown check entirely; it now blocks like an unknown daily loss does.
    if not math.isfinite(inp.peak_equity) or inp.peak_equity <= 0:
        return BLOCK_RISK, (
            f"peak equity (drawdown baseline) is unknown or invalid ({inp.peak_equity!r}) -- the drawdown limit "
            f"cannot be evaluated"
        )
    drawdown_pct = max(0.0, (inp.peak_equity - inp.equity) / inp.peak_equity * 100.0)
    if drawdown_pct >= limits.max_drawdown_pct:
        return BLOCK_RISK, (
            f"drawdown {drawdown_pct:.2f}% has reached/exceeded max_drawdown_pct {limits.max_drawdown_pct}%"
        )

    return ALLOW, "within all risk limits"
