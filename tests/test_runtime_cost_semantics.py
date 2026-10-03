"""Regression tests for transaction-cost horizon semantics (P0, PR #7).

The configured slippage value is a PER-FILL observation. Entry permission
must price both the opening and the eventual closing fill (FULL_ROUND_TRIP);
an already-open position review must price only the remaining exit friction
(REMAINING_EXIT) and never the sunk entry costs. Runtime (DEMO) and the
backtest/PAPER engine must use the same convention, and swap is horizon-aware
(issue #8): unknown swap blocks only a decision whose maximum hold can cross
the broker's server-midnight rollover.
"""

from datetime import datetime, timezone

import pytest

from adaptive_scalper.backtest import engine as bt
from adaptive_scalper.backtest.types import BacktestConfig
from adaptive_scalper.config.loader import AppConfig
from adaptive_scalper.costs.edge import evaluate_cost_gate
from adaptive_scalper.costs.model import (
    HORIZON_FULL_ROUND_TRIP,
    HORIZON_REMAINING_EXIT,
    CostEstimate,
    estimate_cost,
)
from adaptive_scalper.costs.swap_horizon import rollovers_crossed, swap_price_for_horizon
from adaptive_scalper.gateway.types import Bar, SymbolSpec, SymbolTradeMode, Tick
from adaptive_scalper.runtime.demo import (
    CLOSE_LATENCY_ALLOWANCE_SECONDS,
    live_cost_estimate,
    live_remaining_exit_cost_estimate,
    max_hold_horizon_seconds,
)
from adaptive_scalper.position_management.adaptive_exit import AdaptiveExitParams
from adaptive_scalper.selector.selector import select_proposal
from adaptive_scalper.simulation.fill_model import FillAssumptions
from adaptive_scalper.strategies.base import StrategySignal

# 2026-07-15 12:00 UTC: US DST in effect, server (UTC+3) midnight = 21:00 UTC.
NOON_UTC = int(datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc).timestamp())
SERVER_MIDNIGHT_UTC = int(datetime(2026, 7, 15, 21, 0, tzinfo=timezone.utc).timestamp())
HOLD = 660


def _spec() -> SymbolSpec:
    return SymbolSpec(
        name="XAUUSD", description="test", currency_base="XAU", currency_profit="USD",
        currency_margin="USD", digits=2, point=0.01, trade_contract_size=100.0,
        volume_min=0.01, volume_max=100.0, volume_step=0.01,
        trade_tick_size=1.0, trade_tick_value=1.0, spread=100, visible=True,
        trade_mode=SymbolTradeMode.FULL,
    )


def _tick() -> Tick:
    return Tick(time=NOON_UTC, bid=100.0, ask=101.0, last=100.5, volume=1.0)


def _config(slippage=2.0, swap=None, commission=10.0, rule="UTC+2/US_DST") -> AppConfig:
    cost = {"commission_per_lot_round_trip": commission, "slippage_price": slippage,
            "provenance": "BROKER_DEMO_CONFIRMED"}
    if swap is not None:
        cost["swap_per_lot_per_day"] = swap
    return AppConfig(costs={"XAUUSD": cost}, mt5={"server_time_rule": rule})


def _entry(cfg=None, now=NOON_UTC, hold=HOLD):
    return live_cost_estimate(cfg or _config(), "XAUUSD", _spec(), _tick(), now_utc=now, max_hold_seconds=hold)


def _remaining(cfg=None, now=NOON_UTC, hold=HOLD):
    return live_remaining_exit_cost_estimate(cfg or _config(), "XAUUSD", _spec(), _tick(), now_utc=now,
                                             remaining_hold_seconds=hold)


def _signal(**kw) -> StrategySignal:
    base = dict(strategy_key="momentum_continuation", strategy_version=1, canonical_symbol="XAUUSD",
                direction="BUY", raw_confidence=0.9, stop_distance=10.0, target_distance=30.0,
                expected_duration_seconds=600, entry_method="market", regime="TRENDING_UP",
                rationale="test", feature_schema_version=1, data_timestamp=NOON_UTC)
    base.update(kw)
    return StrategySignal(**base)


# --- DEMO pre-entry: whole trade -------------------------------------------------

def test_pre_entry_cost_charges_slippage_on_both_fills():
    cost = _entry()
    assert cost is not None and cost.horizon == HORIZON_FULL_ROUND_TRIP
    assert cost.spread_cost == 1.0
    assert cost.commission_cost == 10.0
    assert cost.slippage_cost == 4.0
    assert cost.total_cost == pytest.approx(16.5)  # (1 spread + 10 commission + 2*2 slippage) * 1.10


def test_negative_control_one_fill_slippage_underestimates_the_round_trip():
    """The pre-PR-7 estimate charged the per-fill slippage once. Removing the
    exit-fill slippage must make the estimate strictly cheaper than the real
    round trip -- i.e. the old convention was an underestimate."""
    correct = _entry()
    old_convention = estimate_cost(spread_price=1.0, commission_price_equivalent=10.0, expected_slippage_price=2.0,
                                   swap_price_equivalent=0.0)
    assert old_convention.total_cost < correct.total_cost
    assert correct.total_cost - old_convention.total_cost == pytest.approx(2.0 * 1.10)


# --- DEMO open-position review: remaining friction only --------------------------

def test_open_position_review_charges_only_remaining_exit_friction():
    cost = _remaining()
    assert cost is not None and cost.horizon == HORIZON_REMAINING_EXIT
    assert cost.spread_cost == 0.0
    assert cost.commission_cost == 5.0
    assert cost.slippage_cost == 2.0
    assert cost.total_cost == pytest.approx(7.7)  # (half commission + one exit slippage) * 1.10


def test_open_position_review_never_recharges_sunk_entry_costs():
    entry, remaining = _entry(), _remaining()
    # Exactly the entry-side components are missing: the spread (already in
    # the executable mark), one fill's slippage and the entry half of the
    # commission.
    assert remaining.slippage_cost == entry.slippage_cost / 2
    assert remaining.commission_cost == entry.commission_cost / 2
    assert remaining.spread_cost == 0.0 < entry.spread_cost
    assert remaining.total_cost < entry.total_cost


def test_unknown_slippage_fails_closed_for_entry_and_exit_estimates():
    cfg = _config(slippage=None)
    assert _entry(cfg) is None
    assert _remaining(cfg) is None


# --- consumers refuse the wrong horizon -------------------------------------------

def test_entry_gate_refuses_a_remaining_exit_estimate():
    with pytest.raises(ValueError, match="FULL_ROUND_TRIP"):
        evaluate_cost_gate(_signal(), _remaining())


def test_selector_refuses_a_remaining_exit_estimate():
    with pytest.raises(ValueError, match="FULL_ROUND_TRIP"):
        select_proposal([_signal()], {"XAUUSD": _remaining()})


def test_cost_estimate_rejects_an_unknown_horizon():
    with pytest.raises(ValueError):
        CostEstimate(0, 0, 0, 0, 0, 0, horizon="WHOLE_DAY")


# --- swap horizon (issue #8) -------------------------------------------------------

def test_rollover_is_server_midnight_not_utc_midnight():
    # UTC+3 in July: server midnight = 21:00 UTC.
    assert rollovers_crossed("UTC+2/US_DST", SERVER_MIDNIGHT_UTC - 60, SERVER_MIDNIGHT_UTC + 60) == 1
    assert rollovers_crossed("UTC", SERVER_MIDNIGHT_UTC - 60, SERVER_MIDNIGHT_UTC + 60) == 0
    assert rollovers_crossed("UTC+2/US_DST", NOON_UTC, NOON_UTC + 3600) == 0


def test_unknown_swap_is_zero_only_when_the_horizon_cannot_cross_rollover():
    cfg = _config(swap=None)
    assert _entry(cfg, now=NOON_UTC).swap_cost == 0.0
    assert _entry(cfg, now=SERVER_MIDNIGHT_UTC - 300) is None          # crosses -> BLOCK_COST
    assert _remaining(cfg, now=SERVER_MIDNIGHT_UTC - 300) is None


def test_unbounded_hold_requires_swap_evidence():
    assert _entry(_config(swap=None), hold=None) is None
    assert _entry(_config(swap=3.0), hold=None).swap_cost == 3.0


def test_known_swap_is_charged_per_rollover_crossed():
    assert _entry(_config(swap=3.0), now=SERVER_MIDNIGHT_UTC - 300).swap_cost == 3.0
    assert _entry(_config(swap=3.0), now=NOON_UTC).swap_cost == 0.0
    two_days = swap_price_for_horizon(server_time_rule="UTC+2/US_DST", start_utc=NOON_UTC,
                                      max_hold_seconds=2 * 86_400, swap_per_lot_per_day=3.0,
                                      tick_size=1.0, tick_value=1.0)
    assert two_days == 6.0


def test_max_hold_horizon_follows_the_enforced_adaptive_exit_limit():
    assert max_hold_horizon_seconds(AdaptiveExitParams()) == 600 + CLOSE_LATENCY_ALLOWANCE_SECONDS
    assert max_hold_horizon_seconds(AdaptiveExitParams(max_holding_enabled=False)) is None


# --- DEMO and backtest/PAPER use the same convention --------------------------------

def _bar(spread_points=100) -> Bar:
    return Bar(time=NOON_UTC, open=100.5, high=101, low=100, close=100.5, tick_volume=1, spread=spread_points,
               real_volume=0)


def _bt_config(slippage=2.0, swap=0.0) -> BacktestConfig:
    return BacktestConfig(fill_assumptions=FillAssumptions(
        slippage_price=slippage, commission_monetary_per_lot=10.0, swap_monetary_per_lot_per_day=swap,
    ), server_time_rule="UTC+2/US_DST")


def test_backtest_entry_cost_equals_the_demo_entry_cost_for_the_same_evidence():
    # Same spread (100 points * 0.01 = 1.0), same per-fill slippage, same commission.
    backtest = bt._entry_cost(_bar(), _spec(), _bt_config(), fill_time_utc=NOON_UTC, bar_seconds=300)
    demo = _entry()
    assert backtest.horizon == demo.horizon == HORIZON_FULL_ROUND_TRIP
    assert backtest.total_cost == pytest.approx(demo.total_cost)
    assert backtest.slippage_cost == 4.0


def test_backtest_review_cost_equals_the_demo_review_cost_and_excludes_sunk_costs():
    backtest = bt._remaining_exit_cost(_spec(), _bt_config(), review_time_utc=NOON_UTC, remaining_hold_seconds=HOLD)
    demo = _remaining()
    assert backtest.horizon == demo.horizon == HORIZON_REMAINING_EXIT
    assert backtest.total_cost == pytest.approx(demo.total_cost)
    assert backtest.spread_cost == 0.0


def test_backtest_entry_blocks_when_unknown_swap_can_cross_rollover():
    assert bt._entry_cost(_bar(), _spec(), _bt_config(swap=None), fill_time_utc=SERVER_MIDNIGHT_UTC - 300,
                          bar_seconds=300) is None
    assert bt._entry_cost(_bar(), _spec(), _bt_config(swap=None), fill_time_utc=NOON_UTC,
                          bar_seconds=300) is not None


def test_backtest_max_hold_horizon_includes_the_next_bar_open_fill():
    assert bt._max_hold_horizon(BacktestConfig(), 300) == 600 + 300
