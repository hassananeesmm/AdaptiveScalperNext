"""Release 0.2.8: `strategies.entry_suspended` -- an active strategy keeps
signalling (journaled evidence) but can never open a position, in DEMO,
PAPER and backtests alike. Narrow-only: only active keys are accepted."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from adaptive_scalper.backtest.fingerprint import compute_config_fingerprint, describe_config
from adaptive_scalper.backtest.types import BacktestConfig
from adaptive_scalper.config.loader import AppConfig, ConfigError, load_config
from adaptive_scalper.core.kill_switch import bootstrap as bootstrap_kill_switch
from adaptive_scalper.core.operator_authority import OperatorAuthority
from adaptive_scalper.costs.model import estimate_cost
from adaptive_scalper.selector.selector import select_proposal
from adaptive_scalper.selector.suspension import REJECTED_ENTRY_SUSPENDED, partition_suspended
from adaptive_scalper.strategies import build_active_registry
from adaptive_scalper.strategies.base import StrategySignal
from runtime_helpers import STEP, T0, FakeClock, build_engine, step

ROOT = Path(__file__).resolve().parents[1]
ACTIVE = sorted(build_active_registry().active_keys())
SUSPENDED = "microstructure_acceleration"
COST = {"XAUUSD": estimate_cost(spread_price=0.1, commission_price_equivalent=0.0, expected_slippage_price=0.0,
                                 swap_price_equivalent=0.0)}


def _signal(key, confidence=0.9, target=5.0):
    return StrategySignal(
        strategy_key=key, strategy_version=1, canonical_symbol="XAUUSD", direction="BUY", raw_confidence=confidence,
        stop_distance=2.0, target_distance=target, expected_duration_seconds=600, entry_method="market",
        regime="RANGE", rationale="t", feature_schema_version=1, data_timestamp=T0,
    )


# ------------------------------------------------------------------ config

def test_shipped_config_suspends_only_microstructure_acceleration():
    cfg = load_config(ROOT / "config" / "default.toml")
    assert cfg.strategies.entry_suspended == [SUSPENDED]
    assert SUSPENDED in build_active_registry().active_keys()            # still an ACTIVE family (directive s9)
    assert len(build_active_registry().all_active()) == 6


def test_default_is_no_suspension():
    assert AppConfig.model_validate({}).strategies.entry_suspended == []


@pytest.mark.parametrize("keys", [["no_such_strategy"], ["failed_breakout_fade"], [SUSPENDED, SUSPENDED]])
def test_only_distinct_active_keys_are_accepted(keys, tmp_path):
    with pytest.raises(Exception):
        AppConfig.model_validate({"strategies": {"entry_suspended": keys}})
    path = tmp_path / "c.toml"
    path.write_text("[strategies]\nentry_suspended = [" + ", ".join(f'"{k}"' for k in keys) + "]\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(path)


def test_suspension_cannot_unretire_anything():
    cfg = AppConfig.model_validate({"strategies": {"entry_suspended": ACTIVE}})
    assert set(cfg.strategies.retired) >= {"failed_breakout_fade", "support_resistance_reaction"}


# ------------------------------------------------------------------ selector

def test_partition_removes_only_suspended_signals_and_keeps_order():
    signals = [_signal("momentum_continuation"), _signal(SUSPENDED), _signal("range_breakout")]
    assert partition_suspended(signals, [SUSPENDED]) == ([0, 2], [1])
    assert partition_suspended(signals, []) == ([0, 1, 2], [])


def test_the_best_edge_suspended_signal_never_reaches_the_frozen_selector():
    best, other = _signal(SUSPENDED, target=9.0), _signal("momentum_continuation", target=5.0)
    assert select_proposal([best, other], COST).selected is best                 # unfiltered V1 would take it
    kept, _ = partition_suspended([best, other], [SUSPENDED])
    assert select_proposal([[best, other][i] for i in kept], COST).selected is other


# ------------------------------------------------------------------ backtest / PAPER parity and fingerprint

def test_fingerprint_unchanged_without_suspension_and_changed_with_it():
    base = BacktestConfig()
    kw = dict(canonical_symbol="XAUUSD", resolutions=("M5",), strategies=(("x", 1),))
    assert "suspended_strategy_keys" not in describe_config(base, **kw)
    suspended = dataclasses.replace(base, suspended_strategy_keys=(SUSPENDED,))
    assert describe_config(suspended, **kw)["suspended_strategy_keys"] == [SUSPENDED]
    assert compute_config_fingerprint(base, **kw)[0] != compute_config_fingerprint(suspended, **kw)[0]


def test_paper_config_carries_the_suspension():
    from adaptive_scalper.runtime.paper import paper_config
    cfg = load_config(ROOT / "config" / "default.toml")
    assert paper_config(cfg, "XAUUSD", ()).suspended_strategy_keys == (SUSPENDED,)


def test_backtest_never_trades_a_suspended_strategy():
    from adaptive_scalper.backtest.engine import run_backtest
    from runtime_helpers import default_market, spec_for
    bars = default_market(400)["XAUUSD"]
    spec = spec_for("XAUUSD")
    base = run_backtest(bars, "XAUUSD", "M5", spec, config=BacktestConfig(), now_utc=T0)
    traded = {t.strategy_key for t in base.trades}
    assert traded, "the synthetic market must produce at least one trade"
    for key in traded:
        out = run_backtest(bars, "XAUUSD", "M5", spec, now_utc=T0, config=BacktestConfig(suspended_strategy_keys=(key,)))
        assert key not in {t.strategy_key for t in out.trades}
    none = run_backtest(bars, "XAUUSD", "M5", spec, now_utc=T0,
                        config=BacktestConfig(suspended_strategy_keys=tuple(ACTIVE)))
    assert not none.trades


# ------------------------------------------------------------------ DEMO runtime

def _demo(tmp_path, suspended):
    tmp_path.mkdir(parents=True, exist_ok=True)
    clock = FakeClock(T0 + 60 * STEP + 10)
    engine, conn, gateway = build_engine(tmp_path, mode="DEMO", clock=clock)
    engine.config.strategies.entry_suspended = list(suspended)
    bootstrap_kill_switch(conn, OperatorAuthority("test-operator"), reason="test setup")
    engine.startup()
    step(engine, clock, seconds=STEP * 4, tick=4)
    return conn, gateway


def test_demo_with_every_strategy_suspended_signals_but_never_sends(tmp_path):
    conn, gateway = _demo(tmp_path, ACTIVE)
    assert conn.execute("SELECT COUNT(*) FROM journal_events WHERE event_type = 'SIGNAL_CREATED'").fetchone()[0] > 0
    rejected = conn.execute("SELECT * FROM journal_events WHERE event_type = 'SIGNAL_REJECTED'").fetchall()
    assert rejected and all(REJECTED_ENTRY_SUSPENDED in str(dict(r)) for r in rejected)
    assert gateway.calls["order_send"] == 0 and gateway.calls["order_check"] == 0


def test_demo_never_opens_a_position_for_the_suspended_strategy(tmp_path):
    conn, _ = _demo(tmp_path / "a", [])
    traded = {r["strategy_key"] for r in conn.execute("SELECT strategy_key FROM positions")}
    assert traded, "the natural DEMO pipeline must open a position"
    key = sorted(traded)[0]
    conn2, _ = _demo(tmp_path / "b", [key])
    assert key not in {r["strategy_key"] for r in conn2.execute("SELECT strategy_key FROM positions")}
