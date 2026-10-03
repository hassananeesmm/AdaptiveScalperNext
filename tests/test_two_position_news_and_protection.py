"""Two-position operation: news isolation and broker-side protection
(Phase 7 of the strategy-research release). Fake broker, temp SQLite; the
real selector, sizing, final permission and execution path run after a stub
strategy's signal (see test_multi_position.py). No real broker is reached.
"""

from __future__ import annotations

from adaptive_scalper.core.kill_switch import bootstrap as bootstrap_kill_switch
from adaptive_scalper.dashboard.panels import BLOCKED_BY_NEWS, _decision_label
from adaptive_scalper.core.operator_authority import OperatorAuthority
from adaptive_scalper.execution.reconciliation import get_open_positions
from adaptive_scalper.news.types import EconomicEvent, Impact
from runtime_helpers import STEP, FakeClock, LiveMarketGateway, StaticNewsProvider, build_engine, step
from edge_fixtures import FixtureValidatedProvider
from test_multi_position import START_AT, StubRegistry, StubStrategy, _latest, _open_symbols, independent_market


def _event(currency: str, at: int) -> EconomicEvent:
    return EconomicEvent(
        event_id=f"test-{currency}-{at}", provider="static", provider_event_id=None, scheduled_at_utc=at,
        country=currency, currency=currency, title=f"{currency} test high-impact release", normalized_event_type=None,
        impact=Impact.HIGH, actual=None, forecast=None, previous=None, retrieved_at_utc=at - 3600,
        source_reliability="PRIMARY", revision=0, source_identifier="test",
    )


def _start_with_news(tmp_path, currency: str):
    clock = FakeClock(START_AT)
    # scheduled 5 minutes after the first entry scan: inside the pre-event block window
    news = [StaticNewsProvider(events=[_event(currency, START_AT + 2 * STEP + 300)])]
    engine, conn, gateway = build_engine(tmp_path, mode="DEMO", clock=clock,
                                         gateway=LiveMarketGateway(clock, independent_market()), news=news,
                                         edge_evidence=FixtureValidatedProvider())
    bootstrap_kill_switch(conn, OperatorAuthority("test-operator"), reason="test setup")
    engine.startup()
    stub = StubStrategy()
    engine.demo.registry = StubRegistry(stub)
    step(engine, clock, seconds=STEP, tick=4)
    return engine, conn, gateway, clock, stub


def test_a_news_block_on_one_symbol_leaves_the_other_two_tradable(tmp_path):
    engine, conn, gateway, clock, stub = _start_with_news(tmp_path, "GBP")
    stub.fire = {"XAUUSD": "BUY", "BTCUSD": "SELL", "GBPJPY": "BUY"}
    step(engine, clock, seconds=STEP, tick=4)
    assert _open_symbols(conn) == ["BTCUSD", "XAUUSD"]
    assert _decision_label(conn, dict(_latest(conn, "GBPJPY"))) == BLOCKED_BY_NEWS


def test_a_usd_release_blocks_both_enabled_symbols_together(tmp_path):
    """XAUUSD and BTCUSD are both USD-quoted: one high-impact USD event
    restricts both at once (documented shared exposure, not a defect)."""
    engine, conn, gateway, clock, stub = _start_with_news(tmp_path, "USD")
    stub.fire = {"XAUUSD": "BUY", "BTCUSD": "SELL"}
    step(engine, clock, seconds=STEP, tick=4)
    assert _open_symbols(conn) == []
    assert gateway.calls["order_send"] == 0
    for symbol in ("XAUUSD", "BTCUSD"):
        assert _decision_label(conn, dict(_latest(conn, symbol))) == BLOCKED_BY_NEWS


def test_both_simultaneous_positions_carry_their_own_broker_stop_and_target(tmp_path):
    engine, conn, gateway, clock, stub = _start_with_news(tmp_path, "CHF")  # affects no traded symbol
    stub.fire = {"XAUUSD": "BUY", "BTCUSD": "SELL"}
    step(engine, clock, seconds=STEP, tick=4)
    broker = {p.symbol: p for p in gateway.positions_get()}
    assert sorted(broker) == ["BTCUSD", "XAUUSD"]
    buy, sell = broker["XAUUSD"], broker["BTCUSD"]
    assert 0 < buy.stop_loss < buy.price_open < buy.take_profit
    assert sell.take_profit < sell.price_open < sell.stop_loss
    # protection survives further management cycles, per position, and only ever tightens
    step(engine, clock, seconds=3 * STEP, tick=4)
    again = {p.symbol: p for p in gateway.positions_get()}
    for symbol, before in broker.items():
        if symbol not in again:  # closed by its own stop/target in the random walk
            continue
        now = again[symbol]
        assert now.stop_loss > 0 and now.take_profit > 0
        if now.direction == "BUY":
            assert now.stop_loss >= before.stop_loss
        else:
            assert now.stop_loss <= before.stop_loss
    local = {p.canonical_symbol: p for p in get_open_positions(conn)}
    assert set(local) == set(again)
    assert {p.strategy_key for p in local.values()} <= {"multi_position_stub"}
