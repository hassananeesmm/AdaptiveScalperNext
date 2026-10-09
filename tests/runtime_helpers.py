"""Deterministic live-market simulation for runtime tests.

`LiveMarketGateway` holds pre-generated M5 bars for the three canonical
symbols and a shared fake clock. Bars (and the tick, priced from the
latest bar and stamped at the clock) only become visible as the clock
advances, exactly like a live terminal -- and every call still goes
through `ChaosGateway`'s fault plan.
"""

from __future__ import annotations

from adaptive_scalper.config.loader import AppConfig
from adaptive_scalper.gateway.types import Bar, SymbolSpec, SymbolTradeMode, Tick
from adaptive_scalper.news.types import ProviderError
from adaptive_scalper.persistence import connect, migrate
from adaptive_scalper.runtime.engine import RuntimeComponents, RuntimeEngine
from chaos_harness import ChaosGateway, demo_account, demo_terminal

STEP = 300
T0 = 1_750_000_200  # aligned to a 5-minute boundary


class FakeClock:
    def __init__(self, now: int) -> None:
        self.now = now

    def __call__(self) -> float:
        return float(self.now)

    def advance(self, seconds: int) -> None:
        self.now += seconds


def spec_for(canonical: str, **overrides) -> SymbolSpec:
    base = {
        "XAUUSD": dict(description="Gold vs US Dollar", currency_base="XAU", currency_profit="USD", digits=2,
                       point=0.01, trade_tick_size=0.01, trade_tick_value=1.0, trade_contract_size=100.0),
        "GBPJPY": dict(description="Great Britain Pound vs Japanese Yen", currency_base="GBP", currency_profit="JPY",
                       digits=3, point=0.001, trade_tick_size=0.001, trade_tick_value=0.65, trade_contract_size=100000.0),
        "BTCUSD": dict(description="Bitcoin vs US Dollar", currency_base="USD", currency_profit="USD", digits=2,
                       point=0.01, trade_tick_size=0.01, trade_tick_value=0.01, trade_contract_size=1.0),
    }[canonical]
    fields = dict(name=canonical, currency_margin="USD", volume_min=0.01, volume_max=50.0, volume_step=0.01,
                  spread=10, visible=True, trade_mode=SymbolTradeMode.FULL, filling_mode=3, **base)
    fields.update(overrides)
    return SymbolSpec(**fields)


def trending_bars(n: int, *, start_price: float, step_price: float, start: int = T0, spread: int = 10) -> list[Bar]:
    out, price = [], start_price
    for i in range(n):
        o, c = price, price + step_price
        out.append(Bar(time=start + i * STEP, open=o, high=max(o, c) + abs(step_price) * 0.1,
                       low=min(o, c) - abs(step_price) * 0.1, close=c, tick_volume=100, spread=spread, real_volume=0))
        price = c
    return out


def default_market(n: int = 400) -> dict[str, list[Bar]]:
    return {
        "XAUUSD": trending_bars(n, start_price=2000.0, step_price=0.5),
        "GBPJPY": trending_bars(n, start_price=190.0, step_price=-0.01),
        "BTCUSD": trending_bars(n, start_price=60000.0, step_price=15.0),
    }


class LiveMarketGateway(ChaosGateway):
    def __init__(self, clock: FakeClock, bars: dict[str, list[Bar]], **kwargs) -> None:
        kwargs.setdefault("account", demo_account())
        kwargs.setdefault("terminal", demo_terminal())
        kwargs.setdefault("symbols", [spec_for(c) for c in bars])
        super().__init__(bars_by_resolution={s: {"M5": b} for s, b in bars.items()}, clock=lambda: int(clock.now),
                         **kwargs)
        self.market_clock = clock
        self._market = bars

    def _live_tick(self, name):
        bars = [b for b in self._market.get(name, []) if b.time + STEP <= self.market_clock.now]
        if not bars:
            return None
        spec = self.symbol_info(name)
        half = bars[-1].spread * spec.point / 2
        return Tick(time=int(self.market_clock.now), bid=bars[-1].close - half, ask=bars[-1].close + half,
                    last=bars[-1].close, volume=1.0)

    def symbol_info_tick(self, name):
        return self._dispatch("symbol_info_tick", self._live_tick, name)

    def _fill_price_for(self, request):
        tick = self._live_tick(request.symbol)
        if tick is None:
            return 0.0
        return tick.ask if request.direction == "BUY" else tick.bid


class StaticNewsProvider:
    def __init__(self, name: str = "static", events=None, fail: bool = False) -> None:
        self.name = name
        self.events = events or []
        self.fail = fail

    def fetch(self, now_utc=None):
        if self.fail:
            raise ProviderError(f"{self.name}: simulated outage")
        return list(self.events)


def app_config(tmp_path, *, mode: str, costs: bool = True, **runtime) -> AppConfig:
    cost_section = {
        # max_spread_price: generous, so pipeline tests are not spread-capped
        # (the cap's own tests: tests/test_spread_cap.py).
        s: {"commission_per_lot_round_trip": 0.0, "slippage_price": 0.0, "provenance": "BROKER_SPEC_ESTIMATE",
            "max_spread_price": 1000.0}
        for s in ("XAUUSD", "GBPJPY", "BTCUSD")
    } if costs else {}
    runtime_section = {"position_cycle_seconds": 1.0, "entry_cycle_seconds": 4.0, "news_refresh_seconds": 1200,
                       "bar_history_count": 120, **runtime}
    return AppConfig.model_validate({
        "mode": mode, "database": {"path": str(tmp_path / "runtime.sqlite3")}, "runtime": runtime_section,
        "costs": cost_section,
    })


def build_engine(tmp_path, *, mode: str, clock: FakeClock, gateway=None, bars=None, news=None, costs=True,
                 components: RuntimeComponents | None = None, edge_evidence=None, **runtime):
    """`edge_evidence`: None keeps the production default (no validated
    evidence -> every proposal FLAT). Pipeline tests that need a proposal
    to reach execution pass `edge_fixtures.FixtureValidatedProvider()`."""
    config = app_config(tmp_path, mode=mode, costs=costs, **runtime)
    conn = connect(config.database.path)
    migrate(conn)
    gateway = gateway or LiveMarketGateway(clock, bars or default_market())
    components = components or RuntimeComponents()
    if edge_evidence is not None:
        components.edge_evidence = edge_evidence
        # A test provider carries the test-only PUBLIC key its certificates verify against.
        components.certificate_public_key = getattr(edge_evidence, "certificate_public_key", None)
    if components.news_providers is None:
        components.news_providers = news if news is not None else [StaticNewsProvider()]
    engine = RuntimeEngine(config, conn, gateway, clock=clock, monotonic=clock, components=components)
    return engine, conn, gateway


def step(engine, clock: FakeClock, *, seconds: int, tick: int = 1) -> None:
    """Advance the fake clock `seconds` in `tick`-second increments, running
    every due scheduled task at each increment."""
    for _ in range(0, seconds, tick):
        clock.advance(tick)
        engine.scheduler.run_due()
