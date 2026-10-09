"""Counterfactual lifecycle evaluator (shadow/lifecycle_counterfactual.py; audit section 9).

The decisive property: it is NOT a second implementation of the exit rules.
For every trade run_backtest makes, the evaluator -- given the same bars and
the same candidate -- must reproduce the same exit bar, reason, price and R.
"""

from __future__ import annotations

import ast
import sqlite3
from pathlib import Path

import pytest

from adaptive_scalper.backtest.engine import run_backtest
from adaptive_scalper.core.kill_switch import bootstrap as bootstrap_kill_switch
from adaptive_scalper.core.operator_authority import OperatorAuthority
from adaptive_scalper.position_management.adaptive_exit import AdaptiveExitParams
from adaptive_scalper.shadow.lifecycle_counterfactual import RESOLVED, simulate_candidate_lifecycle
from adaptive_scalper.simulation.fill_model import FillAssumptions
from adaptive_scalper.strategies import build_active_registry
from edge_fixtures import v1_replay_config
from runtime_helpers import STEP, T0, FakeClock, build_engine, step
from sim_helpers import RES, START, SYMBOL, random_walk_bars, spec

ROOT = Path(__file__).resolve().parents[1] / "adaptive_scalper"
BAR = 300


def _replay(bars, config):
    log = []
    result = run_backtest(bars, SYMBOL, RES, spec(), config=config, now_utc=START, candidate_log=log,
                          force_close_at_range_end=False)
    return result, log


def _counterfactuals(bars, config, result, log):
    strategies = {s.key: s for s in build_active_registry().all_active()}
    selected = {(c.bar_time_utc, c.strategy_key): c for c in log if c.selected}
    pairs = []
    for trade in result.trades:
        cand = selected[(trade.signal_time_utc, trade.strategy_key)]
        outcome = simulate_candidate_lifecycle(
            canonical_symbol=SYMBOL, resolution=RES, bar_seconds=BAR, decision_time_utc=cand.bar_time_utc + BAR,
            strategy=strategies[cand.strategy_key], direction=cand.direction, stop_distance=cand.stop_distance,
            target_distance=cand.target_distance, raw_score=cand.raw_confidence, entry_regime=cand.regime,
            bars=bars, symbol_spec=spec(), config=config, costs_known=True, now_utc=bars[-1].time + BAR,
        )
        pairs.append((trade, outcome))
    return pairs


def _config(**kw):
    return v1_replay_config(fill_assumptions=FillAssumptions(slippage_price=0.03, commission_monetary_per_lot=7.0),
                            **kw)


@pytest.mark.parametrize("seed", [7, 11, 23])
def test_counterfactual_lifecycle_reproduces_every_backtest_trade(seed):
    bars = random_walk_bars(700, seed=seed)
    config = _config()
    result, log = _replay(bars, config)
    pairs = _counterfactuals(bars, config, result, log)
    assert len(pairs) >= 3, "the property is vacuous without trades"
    for trade, outcome in pairs:
        assert outcome.status == RESOLVED
        assert outcome.entry_time_utc == trade.entry_time_utc
        assert outcome.exit_time_utc == trade.exit_time_utc, (trade.exit_reason, outcome.exit_reason)
        assert outcome.exit_reason == trade.exit_reason
        assert outcome.exit_price == pytest.approx(trade.exit_price)
        assert outcome.net_r == pytest.approx(trade.realized_r, abs=1e-9)
        assert outcome.gross_r == pytest.approx(trade.gross_pnl / trade.initial_monetary_risk, abs=1e-9)


def test_equivalence_covers_several_exit_kinds():
    reasons = set()
    for seed in (7, 11, 23, 31, 47):
        bars = random_walk_bars(700, seed=seed)
        config = _config()
        result, log = _replay(bars, config)
        reasons |= {o.exit_reason for _, o in _counterfactuals(bars, config, result, log)}
    assert len(reasons) >= 3, reasons


def test_negative_control_a_different_exit_policy_breaks_equivalence():
    """If the evaluator used other exit rules than the backtest, the
    equivalence test above must notice: here they deliberately differ."""
    bars = random_walk_bars(700, seed=7)
    result, log = _replay(bars, _config())
    other = _config(adaptive_exit_params=AdaptiveExitParams(max_holding_seconds=60_000, early_take_profit_r=9.0,
                                                            setup_deterioration_exit=False))
    pairs = _counterfactuals(bars, other, result, log)
    assert any(o.exit_time_utc != t.exit_time_utc or o.exit_reason != t.exit_reason for t, o in pairs)


def test_mfe_mae_are_measured_in_r_and_bounded_by_the_exit():
    bars = random_walk_bars(700, seed=11)
    config = _config()
    result, log = _replay(bars, config)
    for trade, o in _counterfactuals(bars, config, result, log):
        assert o.mfe_r >= 0 and o.mae_r >= 0
        assert o.time_to_mfe_seconds <= o.holding_seconds and o.time_to_mae_seconds <= o.holding_seconds
        assert o.stop_distance_price > 0 and o.cost_r > 0


def test_unknown_cost_leaves_net_r_unknown():
    bars = random_walk_bars(700, seed=7)
    config = _config()
    result, log = _replay(bars, config)
    strategies = {s.key: s for s in build_active_registry().all_active()}
    cand = next(c for c in log if c.selected)
    o = simulate_candidate_lifecycle(
        canonical_symbol=SYMBOL, resolution=RES, bar_seconds=BAR, decision_time_utc=cand.bar_time_utc + BAR,
        strategy=strategies[cand.strategy_key], direction=cand.direction, stop_distance=cand.stop_distance,
        target_distance=cand.target_distance, raw_score=cand.raw_confidence, entry_regime=cand.regime, bars=bars,
        symbol_spec=spec(), config=config, costs_known=False, now_utc=bars[-1].time + BAR,
    )
    assert o.status == RESOLVED and o.net_r is None and o.gross_r is not None


def test_no_future_bar_is_used_before_it_closes():
    bars = random_walk_bars(700, seed=7)
    config = _config()
    result, log = _replay(bars, config)
    trade, _ = _counterfactuals(bars, config, result, log)[0]
    cand = next(c for c in log if c.selected and c.bar_time_utc == trade.signal_time_utc)
    strategies = {s.key: s for s in build_active_registry().all_active()}
    # "now" is one second before the exit bar closes: the exit cannot be known yet.
    early = simulate_candidate_lifecycle(
        canonical_symbol=SYMBOL, resolution=RES, bar_seconds=BAR, decision_time_utc=cand.bar_time_utc + BAR,
        strategy=strategies[cand.strategy_key], direction=cand.direction, stop_distance=cand.stop_distance,
        target_distance=cand.target_distance, raw_score=cand.raw_confidence, entry_regime=cand.regime, bars=bars,
        symbol_spec=spec(), config=config, costs_known=True, now_utc=trade.exit_time_utc + BAR - 1,
    )
    assert early.status != RESOLVED or early.exit_time_utc < trade.exit_time_utc


# --- isolation --------------------------------------------------------------------------

def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    out |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    return {m for m in out if m.startswith("adaptive_scalper")}


def _module_path(module: str) -> Path | None:
    rel = module.replace("adaptive_scalper", "", 1).lstrip(".").replace(".", "/")
    for candidate in (ROOT / f"{rel}.py", ROOT / rel / "__init__.py"):
        if candidate.exists():
            return candidate
    return None


def test_nothing_the_shadow_package_imports_transitively_reaches_a_broker_mutation_boundary():
    forbidden_prefixes = ("adaptive_scalper.execution", "adaptive_scalper.gateway.mt5_gateway",
                          "adaptive_scalper.gateway.factory", "adaptive_scalper.gateway.synchronized")
    forbidden_names = {"order_send", "order_check", "submit_new_entry", "close_position", "modify_stop",
                       "position_close"}
    seen, frontier = set(), [f"adaptive_scalper.shadow.{p.stem}" for p in (ROOT / "shadow").glob("*.py")]
    while frontier:
        module = frontier.pop()
        if module in seen:
            continue
        seen.add(module)
        path = _module_path(module)
        if path is None:
            continue
        assert not module.startswith(forbidden_prefixes), f"shadow reaches {module}"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        calls = {getattr(n.func, "attr", getattr(n.func, "id", None)) for n in ast.walk(tree) if isinstance(n, ast.Call)}
        assert not calls & forbidden_names, (module, calls & forbidden_names)
        frontier.extend(_imports(path) - seen)
    assert "adaptive_scalper.backtest.engine" in seen   # the shared lifecycle code is what it reuses


# --- runtime integration ------------------------------------------------------------------

def test_flat_demo_resolves_counterfactual_lifecycles_without_sending(tmp_path):
    clock = FakeClock(T0 + 60 * STEP + 10)
    engine, conn, gateway = build_engine(tmp_path, mode="DEMO", clock=clock)
    bootstrap_kill_switch(conn, OperatorAuthority("test-operator"), reason="test setup")
    engine.startup()
    step(engine, clock, seconds=STEP * 14, tick=4)
    rows = conn.execute("SELECT status, exit_reason, gross_r, lifecycle_version FROM shadow_lifecycle_outcomes"
                        ).fetchall()
    assert rows and any(r[0] == "RESOLVED" and r[1] for r in rows)
    assert all(r[3] for r in rows)
    assert gateway.calls["order_send"] == 0 and gateway.calls["order_check"] == 0
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        conn.execute("UPDATE shadow_lifecycle_outcomes SET gross_r = 9")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        conn.execute("DELETE FROM shadow_lifecycle_outcomes")
