"""Shared fixtures for simulation (backtest/PAPER) tests.

Bars are MID prices at 2000.00 with `spread=10` points and `point=0.01`,
so the half spread is 0.05. A scripted stub strategy, monkeypatched into
`backtest.engine.build_active_registry`, fires at exact bar times so each
test can hand-compute every price.
"""

from __future__ import annotations

import random

import adaptive_scalper.backtest.engine as engine_module
from adaptive_scalper.backtest.types import BacktestConfig
from adaptive_scalper.gateway.types import Bar, SymbolSpec, SymbolTradeMode
from adaptive_scalper.position_management.adaptive_exit import AdaptiveExitParams
from adaptive_scalper.simulation.fill_model import COST_EXPLICIT_TEST_FIXTURE, FillAssumptions
from adaptive_scalper.strategies.base import StrategySignal

SYMBOL = "XAUUSD"
RES = "M5"
STEP = 300
START = 1_700_000_000
HALF_SPREAD = 0.05
STOP_DISTANCE = 2.0
TARGET_DISTANCE = 5.0
FLAT = (2000.0, 2000.01, 1999.99, 2000.0)

# Every adaptive-exit rule that could fire on its own is disabled, so a
# test isolates exactly the mechanism it is about.
QUIET_EXIT = AdaptiveExitParams(
    max_holding_enabled=False, early_take_profit_r=1e9, breakeven_enabled=False,
    profit_protection_trigger_r=1e9, regime_reversal_exit=False,
)


def spec() -> SymbolSpec:
    return SymbolSpec(
        name="XAUUSDm", description="Gold vs US Dollar", currency_base="XAU", currency_profit="USD",
        currency_margin="XAU", digits=2, point=0.01, trade_contract_size=100.0,
        volume_min=0.01, volume_max=100.0, volume_step=0.01, trade_tick_size=0.01,
        trade_tick_value=1.0, spread=10, visible=True, trade_mode=SymbolTradeMode.FULL,
    )


def bars(n: int = 40, overrides: dict | None = None, *, start: int = START, times: dict | None = None,
         spreads: dict | None = None) -> list[Bar]:
    """Flat bars; `overrides[i] = (open, high, low, close)`, `times[i]`
    replaces bar i's timestamp (later bars keep their own offsets from
    it), `spreads[i]` its spread in points."""
    overrides, times, spreads = overrides or {}, times or {}, spreads or {}
    out, t = [], start
    for i in range(n):
        if i in times:
            t = times[i]
        o, h, l, c = overrides.get(i, FLAT)
        out.append(Bar(time=t, open=o, high=h, low=l, close=c, tick_volume=100, spread=spreads.get(i, 10), real_volume=0))
        t += STEP
    return out


def config(**overrides) -> BacktestConfig:
    defaults = dict(
        fill_assumptions=FillAssumptions(
            slippage_price=0.0, commission_monetary_per_lot=0.0, provenance=COST_EXPLICIT_TEST_FIXTURE,
        ),
        adaptive_exit_params=QUIET_EXIT,
        # Scripted-strategy mechanics tests replay the frozen V1 edge formula
        # explicitly (issue #6: the production default is NONE = FLAT).
        edge_model="LEGACY_V1_RAW_SCORE",
    )
    defaults.update(overrides)
    return BacktestConfig(**defaults)


class ScriptedStrategy:
    """Fires `fire[t]` when SCANNING at bar time t; while RE-EVALUATING an
    open position (the engine's `_reevaluate_setup`, identified by its
    `"re-evaluation"` regime reason) reports the setup as still valid
    until `invalidate_from` (inclusive)."""

    key = "scripted"
    version = 1

    def __init__(self, fire: dict[int, str], invalidate_from: int | None = None, raw_confidence: float = 0.9) -> None:
        self.fire = fire
        self.invalidate_from = invalidate_from
        self.raw_confidence = raw_confidence
        self._held_direction = next(iter(fire.values()))

    def signal(self, t: int, direction: str) -> StrategySignal:
        return StrategySignal(
            strategy_key=self.key, strategy_version=self.version, canonical_symbol=SYMBOL, direction=direction,
            raw_confidence=self.raw_confidence, stop_distance=STOP_DISTANCE, target_distance=TARGET_DISTANCE,
            expected_duration_seconds=600, entry_method="market", regime="RANGE", rationale="scripted",
            feature_schema_version=1, data_timestamp=t,
        )

    def evaluate(self, features, regime):
        t = features.data_timestamp
        if regime.reason == "re-evaluation":
            if self.invalidate_from is not None and t >= self.invalidate_from:
                return None
            return self.signal(t, self._held_direction)
        direction = self.fire.get(t)
        return self.signal(t, direction) if direction else None


class StubRegistry:
    def __init__(self, *strategies) -> None:
        self._strategies = tuple(strategies)

    def all_active(self):
        return self._strategies


def install(monkeypatch, fire: dict[int, str], invalidate_from: int | None = None) -> ScriptedStrategy:
    strategy = ScriptedStrategy(fire, invalidate_from)
    monkeypatch.setattr(engine_module, "build_active_registry", lambda: StubRegistry(strategy))
    return strategy


def random_walk_bars(n: int, *, seed: int, start: int = START) -> list[Bar]:
    """Deterministic drifting random walk with alternating trend phases,
    so the real strategies/regime classifier actually trade."""
    rng = random.Random(seed)
    price = 2000.0
    out = []
    for i in range(n):
        drift = 0.35 if (i // 60) % 2 == 0 else -0.35
        o = price
        c = o + drift + rng.gauss(0.0, 0.6)
        h = max(o, c) + abs(rng.gauss(0.0, 0.3))
        l = min(o, c) - abs(rng.gauss(0.0, 0.3))
        out.append(Bar(time=start + i * STEP, open=o, high=h, low=l, close=c, tick_volume=100,
                       spread=rng.choice((8, 10, 12)), real_volume=0))
        price = c
    return out
