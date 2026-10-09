"""Observer-only rolling VWAP + standard-deviation bands (config `[microstructure]`).

Shipped (2026-10-09, operator spec "Asset B: BTCUSD"): 10-minute VWAP with
+/- 2.0 sigma bands over closed M1 bars, BTCUSD, published to
runtime_state["microstructure"]. Nothing reads it for a trading decision.
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest
from pydantic import ValidationError

import adaptive_scalper.runtime.microstructure as micro
from adaptive_scalper.config.loader import MicrostructureConfig, load_config
from adaptive_scalper.core.kill_switch import bootstrap as bootstrap_kill_switch
from adaptive_scalper.core.operator_authority import OperatorAuthority
from adaptive_scalper.features.vwap import rolling_vwap_bands
from adaptive_scalper.gateway.types import Bar
from adaptive_scalper.runtime.state import get_state
from runtime_helpers import STEP, T0, FakeClock, build_engine, step

REPO_ROOT = Path(__file__).resolve().parent.parent
M1 = 60


def _bar(i: int, h: float, low: float, c: float, vol: int) -> Bar:
    return Bar(time=T0 + M1 * i, open=c, high=h, low=low, close=c, tick_volume=vol, spread=5, real_volume=0)


# --------------------------------------------------------------------------
# the pure computation
# --------------------------------------------------------------------------

def test_hand_computed_vwap_and_bands():
    # typical prices 100, 103, 106 with volumes 1, 2, 1
    bars = [_bar(0, 101, 99, 100, 1), _bar(1, 104, 102, 103, 2), _bar(2, 107, 105, 106, 1)]
    b = rolling_vwap_bands(bars, bar_seconds=M1, window_seconds=600, band_std=2.0)
    assert b.vwap == pytest.approx(103.0)
    sigma = math.sqrt((1 * 9 + 2 * 0 + 1 * 9) / 4)  # 2.1213...
    assert b.sigma == pytest.approx(sigma)
    assert b.upper == pytest.approx(103.0 + 2 * sigma)
    assert b.lower == pytest.approx(103.0 - 2 * sigma)
    assert (b.bar_count, b.total_volume) == (3, 4)


def test_window_is_the_last_ten_minutes_of_closed_bars():
    old = [_bar(i, 1001, 999, 1000, 50) for i in range(5)]          # outside the window
    recent = [_bar(i, 101, 99, 100, 1) for i in range(5, 15)]       # last 10 one-minute bars
    b = rolling_vwap_bands(old + recent, bar_seconds=M1, window_seconds=600, band_std=2.0)
    assert b.vwap == pytest.approx(100.0)
    assert b.bar_count == 10
    assert b.window_end_utc == recent[-1].time + M1
    assert b.window_start_utc == b.window_end_utc - 600


def test_zero_volume_bars_carry_no_weight():
    bars = [_bar(0, 1001, 999, 1000, 0), _bar(1, 101, 99, 100, 3)]
    assert rolling_vwap_bands(bars, bar_seconds=M1, window_seconds=600, band_std=2.0).vwap == pytest.approx(100.0)


def test_constant_price_has_zero_width_bands():
    bars = [_bar(i, 100, 100, 100, 2) for i in range(10)]
    b = rolling_vwap_bands(bars, bar_seconds=M1, window_seconds=600, band_std=2.0)
    assert b.sigma == 0.0 and b.upper == b.lower == b.vwap == 100.0


@pytest.mark.parametrize("bars", [[], [_bar(0, 101, 99, 100, 0)]])
def test_no_traded_bars_is_none_not_a_guess(bars):
    assert rolling_vwap_bands(bars, bar_seconds=M1, window_seconds=600, band_std=2.0) is None


@pytest.mark.parametrize("kwargs", [{"bar_seconds": 0}, {"window_seconds": 30}, {"band_std": 0.0},
                                    {"band_std": math.inf}])
def test_invalid_arguments_raise(kwargs):
    args = {"bar_seconds": M1, "window_seconds": 600, "band_std": 2.0, **kwargs}
    with pytest.raises(ValueError):
        rolling_vwap_bands([_bar(0, 101, 99, 100, 1)], **args)


# --------------------------------------------------------------------------
# config
# --------------------------------------------------------------------------

def test_code_default_tracks_nothing():
    assert MicrostructureConfig().vwap_symbols == []


def test_shipped_config_tracks_btcusd_10_minute_m1_vwap_with_2_sigma_bands():
    cfg = load_config(REPO_ROOT / "config" / "default.toml").microstructure
    assert (cfg.vwap_symbols, cfg.vwap_resolution, cfg.vwap_window_minutes, cfg.vwap_band_std) == \
        (["BTCUSD"], "M1", 10, 2.0)


@pytest.mark.parametrize("kwargs", [{"vwap_symbols": ["EURUSD"]}, {"vwap_resolution": "H1"},
                                    {"vwap_resolution": "M5", "vwap_window_minutes": 4},
                                    {"vwap_band_std": -2.0}])
def test_invalid_settings_are_rejected(kwargs):
    with pytest.raises(ValidationError):
        MicrostructureConfig(**kwargs)


# --------------------------------------------------------------------------
# runtimes (observer only)
# --------------------------------------------------------------------------

START_AT = T0 + 60 * STEP + 10


def _started(tmp_path, mode: str):
    clock = FakeClock(START_AT)
    engine, conn, gateway = build_engine(tmp_path, mode=mode, clock=clock)
    bootstrap_kill_switch(conn, OperatorAuthority("test-operator"), reason="test setup")
    engine.startup()
    runtime = engine.demo if mode == "DEMO" else engine.paper
    runtime.config = runtime.config.model_copy(update={"microstructure": MicrostructureConfig(vwap_symbols=["BTCUSD"])})
    return clock, engine, conn, gateway


def _m1_bars(now):
    end = (now // M1) * M1
    return [_bar((end - M1 * (10 - k) - T0) // M1, 101 + k, 99 + k, 100 + k, 1 + k) for k in range(10)]


@pytest.mark.parametrize("mode", ["DEMO", "PAPER"])
def test_runtime_publishes_btcusd_bands(tmp_path, monkeypatch, mode):
    clock, engine, conn, gateway = _started(tmp_path, mode)
    monkeypatch.setattr(micro, "closed_bars", lambda gw, name, res, *, now_utc, count, tick: _m1_bars(now_utc))

    step(engine, clock, seconds=STEP, tick=4)

    state = get_state(conn, micro.STATE_KEY)
    btc = state["symbols"]["BTCUSD"]
    assert set(state["symbols"]) == {"BTCUSD"}
    assert btc["bar_count"] == 10 and btc["resolution"] == "M1" and btc["band_std"] == 2.0
    assert btc["lower"] < btc["vwap"] < btc["upper"]
    assert btc["position"] in {"ABOVE_UPPER", "BELOW_LOWER", "ABOVE_VWAP", "BELOW_VWAP"}


@pytest.mark.parametrize("mode", ["DEMO", "PAPER"])
def test_observer_failure_never_interrupts_the_cycle(tmp_path, monkeypatch, mode):
    clock, engine, conn, gateway = _started(tmp_path, mode)

    def boom(*args, **kwargs):
        raise RuntimeError("M1 history unavailable")

    monkeypatch.setattr(micro, "closed_bars", boom)
    step(engine, clock, seconds=STEP, tick=4)

    assert get_state(conn, micro.STATE_KEY)["symbols"]["BTCUSD"]["status"] == micro.UNAVAILABLE
    assert get_state(conn, "why_no_trade")["at"] >= START_AT  # the entry cycle still completed
    assert gateway.calls["order_send"] == 0


def test_untracked_by_default(tmp_path):
    clock = FakeClock(START_AT)
    engine, conn, gateway = build_engine(tmp_path, mode="DEMO", clock=clock)
    bootstrap_kill_switch(conn, OperatorAuthority("test-operator"), reason="test setup")
    engine.startup()
    step(engine, clock, seconds=STEP, tick=4)
    assert get_state(conn, micro.STATE_KEY) is None
