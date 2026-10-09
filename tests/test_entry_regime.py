"""Operator directional-momentum entry gate (config `[entry_regime]`).

Shipped policy (2026-10-09): NEW entries only while Wilder ADX(14) on the
entry-resolution closed bars is STRICTLY above 20, PAPER and DEMO. Blocks
only; disabled (the code default) it changes nothing.
"""

from __future__ import annotations

import math
import random
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

import adaptive_scalper.runtime.demo as demo_runtime
import adaptive_scalper.runtime.paper as paper_runtime
from adaptive_scalper.config.loader import EntryRegimeConfig, load_config
from adaptive_scalper.core.final_permission import ALLOW, evaluate_final_permission
from adaptive_scalper.core.kill_switch import bootstrap as bootstrap_kill_switch
from adaptive_scalper.core.operator_authority import OperatorAuthority
from adaptive_scalper.features.adx import wilder_adx
from adaptive_scalper.gateway.types import Bar
from adaptive_scalper.risk.entry_regime import BLOCK_REGIME, adx_entry_block
from runtime_helpers import STEP, T0, FakeClock, build_engine, step
from test_final_permission import _full_allow_input

REPO_ROOT = Path(__file__).resolve().parent.parent


def _bar(i: int, o: float, h: float, low: float, c: float) -> Bar:
    return Bar(time=T0 + 300 * i, open=o, high=h, low=low, close=c, tick_volume=1, spread=10, real_volume=0)


def _reference_adx(bars, p=14):
    """Independent formulation: Wilder RMA seeded with the simple MEAN (the
    module seeds with the SUM; the DI ratios are identical)."""
    tr, pdm, mdm = [], [], []
    for a, b in zip(bars, bars[1:]):
        tr.append(max(b.high - b.low, abs(b.high - a.close), abs(b.low - a.close)))
        up, dn = b.high - a.high, a.low - b.low
        pdm.append(up if up > dn and up > 0 else 0.0)
        mdm.append(dn if dn > up and dn > 0 else 0.0)

    def rma(xs):
        out = [sum(xs[:p]) / p]
        for x in xs[p:]:
            out.append(out[-1] + (x - out[-1]) / p)
        return out

    dx = []
    for t, pl, mi in zip(rma(tr), rma(pdm), rma(mdm)):
        pdi, mdi = 100 * pl / t, 100 * mi / t
        dx.append(0.0 if pdi + mdi == 0 else 100 * abs(pdi - mdi) / (pdi + mdi))
    return rma(dx)[-1]


# --------------------------------------------------------------------------
# Wilder ADX
# --------------------------------------------------------------------------

def test_adx_needs_two_periods_plus_one_bars():
    bars = [_bar(i, 100 + i, 101 + i, 99 + i, 100.5 + i) for i in range(29)]
    assert wilder_adx(bars[:28]) is None
    assert wilder_adx(bars) is not None


def test_steady_one_way_trend_has_adx_100():
    bars = [_bar(i, 100 + i, 101 + i, 99 + i, 100.5 + i) for i in range(60)]
    assert wilder_adx(bars) == pytest.approx(100.0)


def test_alternating_chop_has_low_adx():
    bars = [_bar(i, 100, 101 + (i % 2), 99 + (i % 2), 100) for i in range(80)]
    assert wilder_adx(bars) < 20.0


def test_matches_an_independent_reference_on_a_random_walk():
    rng = random.Random(20261009)
    bars, price = [], 2000.0
    for i in range(200):
        o = price
        price += rng.gauss(0.0, 1.5)
        h, low = max(o, price) + abs(rng.gauss(0, 0.5)), min(o, price) - abs(rng.gauss(0, 0.5))
        bars.append(_bar(i, o, h, low, price))
    assert wilder_adx(bars) == pytest.approx(_reference_adx(bars), rel=1e-9)


def test_value_depends_only_on_bars_passed_in():
    bars = [_bar(i, 100 + i, 101 + i, 99 + i, 100.5 + i) for i in range(40)]
    before = wilder_adx(bars[:35])
    assert wilder_adx(bars[:35]) == before  # deterministic
    assert wilder_adx(bars[:35] + [_bar(35, 0.0, 1e6, -1e6, 0.0)]) != before  # the next bar is not pre-read


def test_flat_market_is_zero_not_an_error():
    bars = [_bar(i, 100, 100, 100, 100) for i in range(40)]
    assert wilder_adx(bars) == 0.0


# --------------------------------------------------------------------------
# the gate
# --------------------------------------------------------------------------

def test_strictly_above_threshold_allows():
    assert adx_entry_block(20.01, 20.0) is None
    assert adx_entry_block(35.0, 20.0) is None


@pytest.mark.parametrize("adx", [20.0, 19.99, 0.0])
def test_at_or_below_threshold_blocks(adx):
    decision, reason = adx_entry_block(adx, 20.0)
    assert decision == BLOCK_REGIME
    assert "not above 20" in reason


@pytest.mark.parametrize("adx", [None, math.nan, math.inf])
def test_unknown_adx_blocks_when_configured(adx):
    assert adx_entry_block(adx, 20.0)[0] == BLOCK_REGIME


def test_unconfigured_never_blocks():
    assert adx_entry_block(None, None) is None
    assert adx_entry_block(5.0, None) is None


# --------------------------------------------------------------------------
# config
# --------------------------------------------------------------------------

def test_code_default_is_disabled():
    assert EntryRegimeConfig().min_adx is None


def test_shipped_config_requires_adx14_above_20():
    config = load_config(REPO_ROOT / "config" / "default.toml")
    assert config.entry_regime.min_adx == 20.0
    assert config.entry_regime.adx_period == 14


@pytest.mark.parametrize("kwargs", [{"min_adx": -1.0}, {"min_adx": 100.0}, {"min_adx": math.nan},
                                    {"adx_period": 1}])
def test_invalid_settings_are_rejected(kwargs):
    with pytest.raises(ValidationError):
        EntryRegimeConfig(**kwargs)


# --------------------------------------------------------------------------
# final permission
# --------------------------------------------------------------------------

def test_final_permission_blocks_weak_momentum():
    result = evaluate_final_permission(_full_allow_input(entry_adx=15.0, min_entry_adx=20.0))
    assert result.decision == BLOCK_REGIME


def test_final_permission_allows_strong_momentum():
    result = evaluate_final_permission(_full_allow_input(entry_adx=25.0, min_entry_adx=20.0))
    assert result.decision == ALLOW, result.reason


def test_final_permission_blocks_unknown_adx_when_configured():
    assert evaluate_final_permission(_full_allow_input(min_entry_adx=20.0)).decision == BLOCK_REGIME


def test_final_permission_without_threshold_is_unchanged():
    assert evaluate_final_permission(_full_allow_input(entry_adx=1.0)).decision == ALLOW


# --------------------------------------------------------------------------
# runtimes
# --------------------------------------------------------------------------

START_AT = T0 + 60 * STEP + 10


def _with_min_adx(runtime, min_adx):
    runtime.config = runtime.config.model_copy(update={"entry_regime": EntryRegimeConfig(min_adx=min_adx)})


def _started(tmp_path, mode: str):
    clock = FakeClock(START_AT)
    engine, conn, gateway = build_engine(tmp_path, mode=mode, clock=clock)
    bootstrap_kill_switch(conn, OperatorAuthority("test-operator"), reason="test setup")
    engine.startup()
    return clock, engine, conn, gateway


def _regime_rows(conn):
    return conn.execute("SELECT decision, reason FROM entry_decisions WHERE stage = 'REGIME'").fetchall()


# The runtime helpers' synthetic market trends perfectly (ADX == 100), so the
# runtimes' ADX is replaced by a weak value to exercise the block.
WEAK_ADX = 10.0


def test_synthetic_market_is_a_strong_trend():
    from runtime_helpers import default_market

    bars = next(iter(default_market().values()))
    assert wilder_adx(bars[:120]) > 20.0


def test_demo_records_regime_block_before_strategies_and_sends_nothing(tmp_path, monkeypatch):
    clock, engine, conn, gateway = _started(tmp_path, "DEMO")
    monkeypatch.setattr(demo_runtime, "wilder_adx", lambda bars, period: WEAK_ADX)
    _with_min_adx(engine.demo, 20.0)

    step(engine, clock, seconds=STEP * 3, tick=4)

    rows = _regime_rows(conn)
    assert rows and all(r["decision"] == BLOCK_REGIME for r in rows)
    assert all("ADX 10.00 is not above 20" in r["reason"] for r in rows)
    assert all(a.adx == WEAK_ADX for a in engine.demo.latest.values())
    assert gateway.calls["order_send"] == 0


def test_demo_without_threshold_records_no_regime_block(tmp_path):
    clock, engine, conn, gateway = _started(tmp_path, "DEMO")
    step(engine, clock, seconds=STEP * 3, tick=4)
    assert _regime_rows(conn) == []


def test_paper_passes_regime_block_as_the_entry_block(tmp_path, monkeypatch):
    clock, engine, conn, gateway = _started(tmp_path, "PAPER")
    seen = []

    def fake_cycle(*args, **kwargs):
        seen.append(kwargs["entry_block_reason"])
        return SimpleNamespace(ran=False)

    monkeypatch.setattr(paper_runtime, "run_paper_cycle", fake_cycle)
    monkeypatch.setattr(paper_runtime, "wilder_adx", lambda bars, period: WEAK_ADX)

    _with_min_adx(engine.paper, 20.0)
    engine.paper.cycle()
    assert seen and all(reason == BLOCK_REGIME for reason in seen)

    seen.clear()
    _with_min_adx(engine.paper, None)
    engine.paper.cycle()
    assert seen and all(reason is None for reason in seen)
