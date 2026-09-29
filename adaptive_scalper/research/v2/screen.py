"""H6 -- gross-edge screening of slower, cost-efficient ENTRIES (research only).

Entries are produced two ways and then replayed with the H4 exit engine
(`counterfactual.replay_entry`):

- V1 entry logic on another resolution: `counterfactual.cohort_from_run`
  on a frozen-V1 `run_backtest` over M15 bars (unchanged strategy code).
- H6c Donchian breakout (`donchian_entries`): a research signal at a
  closed bar, turned into an entry by the ENGINE's own pending-entry path
  (`_pending_from_signal` -> `_revalidate_and_open`: staleness, cost gate,
  safe sizing, risk gates, next-bar-open fill with spread and slippage).
  Every entry is sized from the fold's initial equity and entries may
  overlap in time: this is a SCREEN, and a passer needs a causal flat-only
  confirmation run before it means anything.

`cost_to_stop` is knowable at decision time (engine cost estimate at the
signal bar / stop distance). Nothing here touches a broker, a database or
the reserved OOS.
"""

from __future__ import annotations

from adaptive_scalper.backtest.engine import _estimate_cost, _pending_from_signal, _revalidate_and_open
from adaptive_scalper.backtest.reserved_oos import assert_outside_reserved_oos
from adaptive_scalper.backtest.types import RiskState
from adaptive_scalper.features.bar_features import FEATURE_SCHEMA_VERSION
from adaptive_scalper.research.v2.counterfactual import CohortEntry, FoldContext
from adaptive_scalper.strategies.base import StrategySignal

DONCHIAN_STOP_ATR = 2.0
DONCHIAN_TARGET_ATR = 4.0
DONCHIAN_MAX_COST_TO_STOP = 0.10
SCREEN_MIN_TRADES = 100
SCREEN_MIN_GROSS_TO_COST = 3.0


def cost_to_stop(bar, symbol_spec, config, stop_distance: float) -> float | None:
    cost = _estimate_cost(bar, symbol_spec, config)
    if cost is None or stop_distance <= 0:
        return None
    return cost.total_cost / stop_distance


def gate_by_cost_to_stop(entries: list[CohortEntry], ctx_by_fold: dict[int, FoldContext], symbol_spec, config,
                         max_ratio: float) -> list[CohortEntry]:
    """H6b: keep entries whose estimated round-trip cost at the SIGNAL bar is
    <= max_ratio x the stop distance. An entry whose ratio cannot be
    computed is dropped (unknown cost never passes a cost gate)."""
    kept = []
    for e in entries:
        ctx = ctx_by_fold[e.fold]
        signal_time = e.original.signal_time_utc
        index = next((i for i in range(e.entry_index, -1, -1) if ctx.bars[i].time == signal_time), None)
        if index is None:
            continue
        ratio = cost_to_stop(ctx.bars[index], symbol_spec, config, e.stop_distance)
        if ratio is not None and ratio <= max_ratio:
            kept.append(e)
    return kept


def donchian_entries(ctx: FoldContext, canonical_symbol: str, symbol_spec, config, *, lookback: int, fold: int,
                     cohort: str, bar_seconds: int, fingerprint: str) -> tuple[list[CohortEntry], dict]:
    """Breakout of the previous `lookback` bars' high/low at a closed bar,
    fresh only (the previous close was inside the channel). Stop/target are
    ATR multiples of the causal `atr` feature; the signal is dropped unless
    the estimated round-trip cost is <= 10 % of the stop."""
    bars = ctx.bars
    assert_outside_reserved_oos(bars[0].time, bars[-1].time)
    entries: list[CohortEntry] = []
    counts = {"signals": 0, "cost_gate_rejected": 0, "engine_rejected": 0}
    first = max(ctx.start_index, lookback + 1)
    for i in range(first, len(bars) - 1):
        atr = ctx.atr[i]
        if atr is None or atr <= 0:
            continue
        window = bars[i - lookback:i]
        high, low = max(b.high for b in window), min(b.low for b in window)
        prev = bars[i - 1].close
        direction = None
        if bars[i].close > high and prev <= high:
            direction = "BUY"
        elif bars[i].close < low and prev >= low:
            direction = "SELL"
        if direction is None:
            continue
        counts["signals"] += 1
        stop, target = DONCHIAN_STOP_ATR * atr, DONCHIAN_TARGET_ATR * atr
        ratio = cost_to_stop(bars[i], symbol_spec, config, stop)
        if ratio is None or ratio > DONCHIAN_MAX_COST_TO_STOP:
            counts["cost_gate_rejected"] += 1
            continue
        signal = StrategySignal(
            strategy_key=cohort, strategy_version=1, canonical_symbol=canonical_symbol, direction=direction,
            raw_confidence=0.5, stop_distance=stop, target_distance=target,
            expected_duration_seconds=16 * bar_seconds, entry_method="market", regime=ctx.regimes[i] or "UNKNOWN",
            rationale=f"research donchian breakout N={lookback}", feature_schema_version=FEATURE_SCHEMA_VERSION,
            data_timestamp=bars[i].time,
        )
        cost = _estimate_cost(bars[i], symbol_spec, config)
        pending = _pending_from_signal(signal, {"atr": atr}, bars[i].time, canonical_symbol=canonical_symbol,
                                       estimated_cost_price=cost.total_cost if cost is not None else None,
                                       expected_net_edge_price=None)
        opened = _revalidate_and_open(
            pending, bars[i + 1], equity=config.initial_equity,
            risk_state=RiskState(peak_equity=config.initial_equity, day_utc=bars[i + 1].time // 86_400,
                                 day_realized_pnl=0.0),
            symbol_spec=symbol_spec, config=config, canonical_symbol=canonical_symbol,
            max_fill_delay_seconds=2 * bar_seconds, external_open_positions=(), correlation_matrix=None,
            config_fingerprint=fingerprint,
        )
        if isinstance(opened, tuple):
            counts["engine_rejected"] += 1
            continue
        entries.append(CohortEntry(cohort, fold, i + 1, stop, target, opened))
    return entries, counts


def screen_verdict(m: dict) -> dict:
    """The pre-registered H6 screen: gross first."""
    ci = m.get("gross_r_ci95")
    checks = {
        "gross_ci_above_zero": bool(ci) and ci[0] > 0,
        "gross_ge_3x_cost": m.get("gross_r") is not None and bool(m.get("cost_r"))
        and m["gross_r"] >= SCREEN_MIN_GROSS_TO_COST * m["cost_r"],
        "trades_ge_100": m.get("trades", 0) >= SCREEN_MIN_TRADES,
    }
    return {"checks": checks, "passes": all(checks.values())}
