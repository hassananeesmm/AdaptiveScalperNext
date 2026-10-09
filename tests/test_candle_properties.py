"""Mathematical candle properties + EMA (observation only, 2026-10-09).

Operator spec: "Do not use naive candlestick pattern matching. Instead,
compute mathematical candle properties: Lower Wick Ratio =
(min(Open, Close) - Low) / (High - Low); Upper Wick Ratio =
(High - max(Open, Close)) / (High - Low)."
"""

from __future__ import annotations

import math
import random
from pathlib import Path

import pytest
from pydantic import ValidationError

from adaptive_scalper.config.loader import MicrostructureConfig, load_config
from adaptive_scalper.core.kill_switch import bootstrap as bootstrap_kill_switch
from adaptive_scalper.core.operator_authority import OperatorAuthority
from adaptive_scalper.features.candle import candle_properties
from adaptive_scalper.features.ema import ema_last
from adaptive_scalper.gateway.types import Bar
from adaptive_scalper.runtime import microstructure as micro
from adaptive_scalper.runtime.state import get_state
from runtime_helpers import STEP, T0, FakeClock, build_engine, step

REPO_ROOT = Path(__file__).resolve().parent.parent


def _bar(o, h, low, c, t=T0) -> Bar:
    return Bar(time=t, open=o, high=h, low=low, close=c, tick_volume=1, spread=1, real_volume=0)


# --------------------------------------------------------------------------
# candle properties
# --------------------------------------------------------------------------

def test_hammer_like_bar_matches_the_operator_formulas():
    # open 108, close 109, high 110, low 100: range 10
    p = candle_properties(_bar(108, 110, 100, 109))
    assert p.lower_wick_ratio == pytest.approx((min(108, 109) - 100) / 10)   # 0.8
    assert p.upper_wick_ratio == pytest.approx((110 - max(108, 109)) / 10)   # 0.1
    assert p.body_ratio == pytest.approx(0.1)
    assert p.close_location == pytest.approx(0.9)
    assert p.direction == "UP" and p.range == 10


def test_bearish_bar_uses_min_and_max_of_open_and_close():
    p = candle_properties(_bar(109, 110, 100, 102))
    assert p.lower_wick_ratio == pytest.approx(0.2)
    assert p.upper_wick_ratio == pytest.approx(0.1)
    assert p.body_ratio == pytest.approx(0.7)
    assert p.direction == "DOWN"


def test_ratios_always_sum_to_one_and_stay_in_unit_interval():
    rng = random.Random(7)
    for _ in range(500):
        o, c = rng.uniform(90, 110), rng.uniform(90, 110)
        low, h = min(o, c) - rng.uniform(0, 5), max(o, c) + rng.uniform(0, 5)
        p = candle_properties(_bar(o, h, low, c))
        if p is None:
            continue
        assert p.lower_wick_ratio + p.upper_wick_ratio + p.body_ratio == pytest.approx(1.0)
        for v in (p.lower_wick_ratio, p.upper_wick_ratio, p.body_ratio, p.close_location):
            assert -1e-12 <= v <= 1 + 1e-12


@pytest.mark.parametrize("bar", [
    _bar(100, 100, 100, 100),          # no range
    _bar(100, 101, 100.5, 100.8),      # low above the open: inconsistent
    _bar(100, 100.5, 99, 101),         # high below the close: inconsistent
    _bar(100, math.nan, 99, 100),
])
def test_undefined_bars_are_none_not_zero(bar):
    assert candle_properties(bar) is None


def test_doji_is_flat_with_zero_body():
    p = candle_properties(_bar(100, 101, 99, 100))
    assert p.direction == "FLAT" and p.body_ratio == 0.0
    assert p.lower_wick_ratio == pytest.approx(0.5) and p.upper_wick_ratio == pytest.approx(0.5)


# --------------------------------------------------------------------------
# EMA
# --------------------------------------------------------------------------

def test_ema_seeds_with_the_sma_then_smooths():
    values = [1.0, 2.0, 3.0, 4.0]
    alpha = 2 / 4
    expected = 2.0 + alpha * (4.0 - 2.0)  # seed = mean(1, 2, 3)
    assert ema_last(values, 3) == pytest.approx(expected)


def test_ema_of_a_constant_is_the_constant_and_short_input_is_none():
    assert ema_last([5.0] * 30, 20) == pytest.approx(5.0)
    assert ema_last([5.0] * 19, 20) is None
    assert ema_last([1.0, math.nan] * 20, 20) is None
    with pytest.raises(ValueError):
        ema_last([1.0], 0)


# --------------------------------------------------------------------------
# config + observer
# --------------------------------------------------------------------------

def test_shipped_config_observes_both_market_symbols():
    cfg = load_config(REPO_ROOT / "config" / "default.toml").microstructure
    assert cfg.candle_symbols == ["XAUUSD", "BTCUSD"] and cfg.candle_ema_period == 20
    assert MicrostructureConfig().candle_symbols == []


@pytest.mark.parametrize("kwargs", [{"candle_symbols": ["EURUSD"]}, {"candle_ema_period": 0}])
def test_invalid_candle_settings_are_rejected(kwargs):
    with pytest.raises(ValidationError):
        MicrostructureConfig(**kwargs)


def test_demo_cycle_publishes_candle_ema_and_adx_for_each_symbol(tmp_path):
    clock = FakeClock(T0 + 60 * STEP + 10)
    engine, conn, gateway = build_engine(tmp_path, mode="DEMO", clock=clock)
    bootstrap_kill_switch(conn, OperatorAuthority("test-operator"), reason="test setup")
    engine.startup()
    engine.demo.config = engine.demo.config.model_copy(update={
        "microstructure": MicrostructureConfig(candle_symbols=["XAUUSD", "BTCUSD"])})

    step(engine, clock, seconds=STEP, tick=4)

    candles = get_state(conn, micro.STATE_KEY)["candles"]
    assert set(candles) == {"XAUUSD", "BTCUSD"}
    for snap in candles.values():
        assert snap["resolution"] == "M5"
        assert snap["candle"] is not None
        c = snap["candle"]
        assert c["lower_wick_ratio"] + c["upper_wick_ratio"] + c["body_ratio"] == pytest.approx(1.0)
        assert snap["ema20"] is not None and snap["close_vs_ema"] in {"ABOVE", "BELOW", "AT"}
        assert snap["adx14"] is not None
    assert gateway.calls["order_send"] == 0
