"""Regression tests for live transaction-cost semantics.

The configured slippage value is a PER-FILL observation. Entry permission
must price both the opening and eventual closing fill, while an already-open
position review must price only the remaining exit friction.
"""

from adaptive_scalper.config.loader import AppConfig
from adaptive_scalper.gateway.types import SymbolSpec, SymbolTradeMode, Tick
from adaptive_scalper.runtime.demo import live_cost_estimate, live_remaining_exit_cost_estimate


def _spec() -> SymbolSpec:
    return SymbolSpec(
        name="XAUUSD", description="test", currency_base="XAU", currency_profit="USD",
        currency_margin="USD", digits=2, point=0.01, trade_contract_size=100.0,
        volume_min=0.01, volume_max=100.0, volume_step=0.01,
        trade_tick_size=1.0, trade_tick_value=1.0, spread=100, visible=True,
        trade_mode=SymbolTradeMode.FULL,
    )


def _tick() -> Tick:
    return Tick(time=1_700_000_000, bid=100.0, ask=101.0, last=100.5, volume=1.0)


def _config(slippage=2.0) -> AppConfig:
    return AppConfig(costs={"XAUUSD": {
        "commission_per_lot_round_trip": 10.0,
        "slippage_price": slippage,
        "swap_per_lot_per_day": 0.0,
        "provenance": "BROKER_DEMO_CONFIRMED",
    }})


def test_pre_entry_cost_charges_slippage_on_both_fills():
    cost = live_cost_estimate(_config(), "XAUUSD", _spec(), _tick())
    assert cost is not None
    assert cost.spread_cost == 1.0
    assert cost.commission_cost == 10.0
    assert cost.slippage_cost == 4.0
    assert cost.total_cost == 16.5  # (1 spread + 10 commission + 2*2 slippage) * 1.10


def test_open_position_review_charges_only_remaining_exit_friction():
    cost = live_remaining_exit_cost_estimate(_config(), "XAUUSD", _spec(), _tick())
    assert cost is not None
    assert cost.spread_cost == 0.0
    assert cost.commission_cost == 5.0
    assert cost.slippage_cost == 2.0
    assert cost.total_cost == 7.7  # (half commission + one exit slippage) * 1.10


def test_unknown_slippage_fails_closed_for_entry_and_exit_estimates():
    cfg = _config(slippage=None)
    assert live_cost_estimate(cfg, "XAUUSD", _spec(), _tick()) is None
    assert live_remaining_exit_cost_estimate(cfg, "XAUUSD", _spec(), _tick()) is None
