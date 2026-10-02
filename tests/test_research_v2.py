"""Research-only Strategy V2 harness: the hooks reproduce V1 exactly when
given V1's own rules, they are refused outside a bounded BACKTEST, a
research run can never share a V1 fingerprint, V2 code is unreachable from
any live path, and calibration abstains instead of inventing a probability."""

from __future__ import annotations

import ast
import dataclasses
from pathlib import Path
from types import SimpleNamespace

import pytest

from adaptive_scalper.backtest.engine import run_backtest
from adaptive_scalper.research.v2.calibration import MIN_STRATEGY_TRADES, fit_calibration
from adaptive_scalper.research.v2.holding import directional_invalidation, no_thesis_exit, v1_entry_retrigger
from adaptive_scalper.research.v2.selectors import (
    REJECTED_COOLDOWN,
    REJECTED_FRICTION,
    REJECTED_NO_CALIBRATED_EDGE,
    REJECTED_UNCALIBRATED,
    calibrated_selector,
    friction_filter_selector,
)
from adaptive_scalper.selector.selector import select_proposal
from adaptive_scalper.simulation.types import EvidenceOrigin
from adaptive_scalper.strategies.base import StrategySignal
from sim_helpers import RES, START, SYMBOL, random_walk_bars, spec

ROOT = Path(__file__).resolve().parents[1]
BARS = random_walk_bars(1500, seed=7)


def _trade_key(trade):
    return {k: v for k, v in dataclasses.asdict(trade).items() if k not in ("config_fingerprint", "entry_evidence")}


def _v1():
    return run_backtest(BARS, SYMBOL, RES, spec(), now_utc=START)


def test_the_random_walk_fixture_actually_trades():
    assert len(_v1().trades) >= 10


def test_holding_hook_with_v1s_own_rule_reproduces_v1_exactly():
    v1 = _v1()
    hooked = run_backtest(BARS, SYMBOL, RES, spec(), now_utc=START, research_holding_thesis=v1_entry_retrigger,
                          research_variant="equivalence")
    assert [_trade_key(t) for t in hooked.trades] == [_trade_key(t) for t in v1.trades]
    assert hooked.config_fingerprint != v1.config_fingerprint


def test_selector_hook_with_the_live_selector_reproduces_v1_exactly():
    def live(candidates, costs, *, min_net_edge_price, min_raw_confidence, bar_time_utc, last_exit_time_utc):
        return select_proposal(candidates, costs, min_net_edge_price=min_net_edge_price,
                               min_raw_confidence=min_raw_confidence)

    v1 = _v1()
    hooked = run_backtest(BARS, SYMBOL, RES, spec(), now_utc=START, research_selector=live,
                          research_variant="equivalence")
    assert [_trade_key(t) for t in hooked.trades] == [_trade_key(t) for t in v1.trades]


def test_a_different_holding_thesis_changes_exits_but_never_widens_a_stop():
    v1 = _v1()
    none = run_backtest(BARS, SYMBOL, RES, spec(), now_utc=START, research_holding_thesis=no_thesis_exit,
                        research_variant="H1-NONE")
    # "thesis invalidated" has two V1 causes: the entry re-trigger (replaced
    # here) and remaining edge-to-target below the minimum (kept). So the
    # thesis exits can only become fewer, not disappear.
    thesis = lambda r: sum(1 for t in r.trades if "thesis invalidated" in (t.exit_reason or ""))  # noqa: E731
    assert thesis(none) < thesis(v1)
    # Initial risk never exceeds the unchanged 0.25 % of equity at entry.
    equity = none.config.initial_equity
    for trade in sorted(none.trades, key=lambda t: t.entry_time_utc):
        assert trade.initial_monetary_risk <= 0.0025 * equity * 1.0001
        equity += trade.realized_pnl or 0.0


@pytest.mark.parametrize("kwargs, match", [
    (dict(origin=EvidenceOrigin.PAPER_LIVE_DATA), "BACKTEST-only"),
    (dict(force_close_at_range_end=False), "incremental"),
    (dict(research_variant=None), "research_variant"),
    (dict(research_variant=""), "research_variant"),
])
def test_research_hooks_are_refused_outside_a_bounded_labelled_backtest(kwargs, match):
    call = dict(research_holding_thesis=no_thesis_exit, research_variant="x", now_utc=START)
    call.update(kwargs)
    with pytest.raises(ValueError, match=match):
        run_backtest(BARS, SYMBOL, RES, spec(), **call)


def test_a_research_variant_label_without_a_hook_is_refused():
    with pytest.raises(ValueError, match="without a research hook"):
        run_backtest(BARS, SYMBOL, RES, spec(), now_utc=START, research_variant="x")


def _imports(path: Path) -> set[str]:
    names = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_no_live_module_imports_research_v2():
    package = ROOT / "adaptive_scalper"
    offenders = [
        str(p.relative_to(ROOT)) for p in package.rglob("*.py")
        if "research" not in p.relative_to(package).parts[:1]
        and any(name.startswith("adaptive_scalper.research.v2") for name in _imports(p))
    ]
    assert offenders == []


@pytest.mark.parametrize("relative", ["adaptive_scalper/paper/engine.py", "adaptive_scalper/runtime",
                                      "adaptive_scalper/execution", "adaptive_scalper/selector",
                                      "adaptive_scalper/strategies"])
def test_live_paths_never_pass_research_hooks(relative):
    target = ROOT / relative
    files = [target] if target.is_file() else list(target.rglob("*.py"))
    for f in files:
        assert "research_selector" not in f.read_text(encoding="utf-8"), f
        assert "research_holding_thesis" not in f.read_text(encoding="utf-8"), f


def test_research_v2_never_imports_execution_or_the_gateway():
    for p in (ROOT / "adaptive_scalper" / "research" / "v2").glob("*.py"):
        for name in _imports(p):
            assert not name.startswith(("adaptive_scalper.gateway", "adaptive_scalper.execution",
                                        "adaptive_scalper.runtime", "MetaTrader5")), (p.name, name)


# --- holding theses ------------------------------------------------------------

def _signal(direction="BUY", key="microstructure_acceleration", confidence=0.9, target=5.0):
    return StrategySignal(
        strategy_key=key, strategy_version=1, canonical_symbol=SYMBOL, direction=direction, raw_confidence=confidence,
        stop_distance=2.0, target_distance=target, expected_duration_seconds=600, entry_method="market",
        regime="RANGE", rationale="t", feature_schema_version=1, data_timestamp=START,
    )


def test_directional_invalidation_only_closes_on_opposing_evidence():
    kw = dict(direction="BUY", entry_regime="RANGE")
    assert directional_invalidation(confirmed_regime="RANGE", fresh_signal=None, **kw)
    assert directional_invalidation(confirmed_regime="RANGE", fresh_signal=_signal("BUY"), **kw)
    assert not directional_invalidation(confirmed_regime="RANGE", fresh_signal=_signal("SELL"), **kw)
    assert not directional_invalidation(confirmed_regime="TRENDING_DOWN", fresh_signal=None, **kw)
    assert not v1_entry_retrigger(confirmed_regime="RANGE", fresh_signal=None, **kw)


# --- calibration -----------------------------------------------------------------

def _view(conf, gross_r, cost_r=0.2, exit_time=1000, key="k"):
    return SimpleNamespace(strategy_key=key, raw_confidence=conf, initial_risk=10.0, exit_time_utc=exit_time,
                           gross=gross_r * 10.0, total_cost=cost_r * 10.0, net=(gross_r - cost_r) * 10.0)


def test_too_few_trades_is_unknown_never_a_default_probability():
    views = [_view(0.5, 0.1) for _ in range(MIN_STRATEGY_TRADES - 1)]
    assert fit_calibration(views, "k", before_utc=10_000) is None


def test_calibration_uses_only_trades_closed_before_the_evaluated_fold():
    early = [_view(0.5, 0.1, exit_time=100) for _ in range(MIN_STRATEGY_TRADES)]
    late = [_view(0.5, 9.9, exit_time=5000) for _ in range(500)]
    cal = fit_calibration(early + late, "k", before_utc=1000)
    assert cal.n == MIN_STRATEGY_TRADES
    assert cal.overall.mean_gross_r == pytest.approx(0.1)


def test_calibrated_selector_abstains_without_demonstrated_edge():
    good = fit_calibration([_view(0.5, 1.0 + i * 1e-3, cost_r=0.1, key="k") for i in range(200)], "k",
                           before_utc=10_000)
    bad = fit_calibration([_view(0.5, 0.05 + i * 1e-3, cost_r=0.2, key="b") for i in range(200)], "b",
                          before_utc=10_000)
    select = calibrated_selector({"k": good, "b": bad, "u": None}, margin=1.5)
    cost = SimpleNamespace(total_cost=0.1)
    kw = dict(min_net_edge_price=0.0, min_raw_confidence=0.0, bar_time_utc=0, last_exit_time_utc=None)
    result = select([_signal(key="u"), _signal(key="b"), _signal(key="k")], {SYMBOL: cost}, **kw)
    assert result.selected.strategy_key == "k"
    reasons = {e.signal.strategy_key: e.rejection_reason for e in result.candidates}
    assert reasons == {"u": REJECTED_UNCALIBRATED, "b": REJECTED_NO_CALIBRATED_EDGE, "k": None}
    flat = select([_signal(key="u"), _signal(key="b")], {SYMBOL: cost}, **kw)
    assert flat.selected is None


def test_friction_filter_and_cooldown():
    select = friction_filter_selector(3.0, cooldown_bars=6, bar_seconds=300)
    cost = SimpleNamespace(total_cost=1.0)
    kw = dict(min_net_edge_price=-1e9, min_raw_confidence=0.0)
    thin = select([_signal(target=2.9)], {SYMBOL: cost}, bar_time_utc=10_000, last_exit_time_utc=None, **kw)
    assert thin.selected is None and thin.candidates[0].rejection_reason == REJECTED_FRICTION
    cooling = select([_signal(target=5.0)], {SYMBOL: cost}, bar_time_utc=10_000, last_exit_time_utc=10_000 - 300, **kw)
    assert cooling.selected is None and cooling.candidates[0].rejection_reason == REJECTED_COOLDOWN


# --- cost diagnostics --------------------------------------------------------------

def test_cost_diagnostics_report_signed_slippage_and_never_add_spread_to_pnl():
    from adaptive_scalper.research.v2.costs import cost_diagnostics

    base = dict(canonical_symbol="XAUUSD", direction="BUY", session="LONDON", atr=1.0, requested_volume=0.1,
                filled_volume=0.1, estimated_spread_price=0.08, spread_price=0.08, estimated_slippage_price=0.41,
                estimated_commission_price=0.07, entry_commission=-0.35, exit_commission=-0.35, swap=0.0,
                exit_recorded_at_utc=1)
    rows = [dict(base, slippage_price=s, atr=a) for s, a in ((0.1, 1.0), (-0.1, 2.0), (0.0, 3.0))]
    report = cost_diagnostics(rows)["symbols"]["XAUUSD"]["all"]
    assert report["slippage_realized_signed"]["mean"] == pytest.approx(0.0)
    assert report["slippage_realized_adverse_share"] == pytest.approx(1 / 3)
    assert report["slippage_prediction_error"]["mean"] == pytest.approx(-0.41)
    assert report["commission_money_per_lot_round_trip"]["mean"] == pytest.approx(7.0)
    assert "pnl" not in json_keys(report)


def json_keys(obj) -> set[str]:
    keys = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            keys.add(k)
            keys |= json_keys(v)
    return keys
