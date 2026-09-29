"""H6 screen: Donchian entries go through the engine's own pending-entry
path (next-bar-open fill, sizing, cost), are causal, respect the cost gate,
the H6b gate drops unknown or expensive entries, the screen verdict is gross
first, and the runner refuses the H7 holdout and the OOS before loading."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from adaptive_scalper.backtest.reserved_oos import RESERVED_OOS_INTERVALS, ReservedOosOverlapError
from adaptive_scalper.backtest.types import BacktestConfig
from adaptive_scalper.research.v2.counterfactual import ExitPolicy, build_fold_context, replay_entry
from adaptive_scalper.research.v2.screen import (
    DONCHIAN_MAX_COST_TO_STOP,
    cost_to_stop,
    donchian_entries,
    gate_by_cost_to_stop,
    screen_verdict,
)
from adaptive_scalper.strategies import build_active_registry, select_active_strategies
from sim_helpers import RES, SYMBOL, random_walk_bars, spec

ROOT = Path(__file__).resolve().parents[1]
BARS = random_walk_bars(1500, seed=11)
CONFIG = BacktestConfig()


@pytest.fixture(scope="module")
def ctx():
    return build_fold_context(BARS, SYMBOL, RES, spec(), CONFIG, index=0)


@pytest.fixture(scope="module")
def donchian(ctx):
    return donchian_entries(ctx, SYMBOL, spec(), CONFIG, lookback=20, fold=0, cohort="research_donchian_20",
                            bar_seconds=300, fingerprint="test")


def test_donchian_entries_are_causal_and_fill_at_the_next_open(ctx, donchian):
    entries, counts = donchian
    assert entries and counts["signals"] >= len(entries)
    for e in entries:
        signal_index = e.entry_index - 1
        t = e.original
        assert ctx.bars[signal_index].time == t.signal_time_utc   # decided at the prior bar's close
        assert ctx.bars[e.entry_index].time == t.entry_time_utc
        window = ctx.bars[signal_index - 20:signal_index]           # the channel excludes the signal bar
        close = ctx.bars[signal_index].close
        assert close > max(b.high for b in window) if t.direction == "BUY" else close < min(b.low for b in window)
        half = ctx.bars[e.entry_index].spread * spec().point / 2
        reference = ctx.bars[e.entry_index].open
        assert (t.entry_price > reference) if t.direction == "BUY" else (t.entry_price < reference)
        assert t.entry_price == pytest.approx(
            reference + (1 if t.direction == "BUY" else -1) * (half + CONFIG.fill_assumptions.slippage_price))
        assert t.initial_monetary_risk <= CONFIG.initial_equity * CONFIG.risk_per_trade_pct / 100 * 1.0001


def test_donchian_respects_its_cost_gate(ctx, donchian):
    entries, _ = donchian
    for e in entries:
        ratio = cost_to_stop(ctx.bars[e.entry_index - 1], spec(), CONFIG, e.stop_distance)
        assert ratio is not None and ratio <= DONCHIAN_MAX_COST_TO_STOP


def test_donchian_entries_replay_with_standing_stop_and_target(ctx, donchian):
    entries, _ = donchian
    st = ExitPolicy("ST", "STOP_TARGET")
    strategies = select_active_strategies(None, build_active_registry())
    for e in entries[:25]:
        r = replay_entry(e, ctx, st, symbol_spec=spec(), config=CONFIG, strategies=strategies, bar_seconds=300,
                         fingerprint="t")
        assert r.trade.exit_reason in ("STOP_LOSS_HIT", "TAKE_PROFIT_HIT", "BACKTEST_RANGE_ENDED")


def test_cost_gate_drops_expensive_and_unknown_entries(ctx, donchian):
    entries, _ = donchian
    assert gate_by_cost_to_stop(entries, {0: ctx}, spec(), CONFIG, 1e-9) == []
    assert len(gate_by_cost_to_stop(entries, {0: ctx}, spec(), CONFIG, 10.0)) == len(entries)


def test_screen_verdict_is_gross_first():
    ok = {"trades": 150, "gross_r": 0.30, "cost_r": 0.05, "gross_r_ci95": [0.05, 0.55]}
    assert screen_verdict(ok)["passes"]
    assert not screen_verdict(dict(ok, gross_r_ci95=[-0.01, 0.6]))["passes"]
    assert not screen_verdict(dict(ok, cost_r=0.11))["passes"]
    assert not screen_verdict(dict(ok, trades=99))["passes"]


def test_donchian_refuses_oos_bars():
    oos_bars = random_walk_bars(300, seed=3, start=RESERVED_OOS_INTERVALS[0][0] + 86_400)
    with pytest.raises(ReservedOosOverlapError):
        build_fold_context(oos_bars, SYMBOL, RES, spec(), CONFIG, index=0)


@pytest.mark.parametrize("start, end, needle", [
    ("2022-06-23", "2024-12-31", "H7 holdout"),
    ("2026-06-01", "2026-07-15", "reserved untouched OOS"),
])
def test_the_h6_runner_refuses_the_h7_holdout_and_the_oos_before_loading(start, end, needle):
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "research_v2_h6.py"), "--symbol", "XAUUSD", "--start", start,
         "--end", end, "--research-db", "does-not-exist.sqlite3", "--tag", "t", "--out", "x.json"],
        capture_output=True, text=True, cwd=ROOT,
    )
    assert proc.returncode != 0
    assert needle in (proc.stdout + proc.stderr)
