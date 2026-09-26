"""Multi-symbol concurrency in the DEMO runtime (fake broker, temp SQLite).

The runtime must let two DIFFERENT symbols hold independent positions when
every safety gate approves both, while keeping one position per symbol,
the aggregate-risk ceiling, fail-closed correlation and per-instrument
cost evidence. A stub strategy fires on chosen symbols so each scenario
controls exactly which proposals exist; everything after the signal
(selector, sizing, final permission, execution, broker fill) is the real
code path. No real broker is ever reached (tests/conftest.py).
"""

from __future__ import annotations

import dataclasses
import random

import pytest

from adaptive_scalper.core.kill_switch import bootstrap as bootstrap_kill_switch
from adaptive_scalper.core.operator_authority import OperatorAuthority
from adaptive_scalper.execution.reconciliation import get_open_positions
from adaptive_scalper.gateway.retcodes import REJECT
from adaptive_scalper.gateway.types import Bar, OrderSendResult
from adaptive_scalper.runtime.state import get_state
from adaptive_scalper.strategies.base import StrategySignal
from runtime_helpers import STEP, T0, FakeClock, LiveMarketGateway, build_engine, step

START_AT = T0 + 150 * STEP + 10


def random_walk(seed: int, n: int, *, start_price: float, scale: float) -> list[Bar]:
    rng = random.Random(seed)
    out, price = [], start_price
    for i in range(n):
        o = price
        c = o + rng.gauss(0.0, scale)
        out.append(Bar(time=T0 + i * STEP, open=o, high=max(o, c) + scale * 0.2, low=min(o, c) - scale * 0.2,
                       close=c, tick_volume=100, spread=10, real_volume=0))
        price = c
    return out


def independent_market(n: int = 400) -> dict[str, list[Bar]]:
    """XAUUSD and BTCUSD with independent returns (low measured correlation)."""
    return {
        "XAUUSD": random_walk(1, n, start_price=2000.0, scale=1.0),
        "GBPJPY": random_walk(2, n, start_price=190.0, scale=0.05),
        "BTCUSD": random_walk(3, n, start_price=60000.0, scale=40.0),
    }


def correlated_market(n: int = 400) -> dict[str, list[Bar]]:
    """BTCUSD moves as a scaled copy of XAUUSD: correlation ~ 1."""
    gold = random_walk(1, n, start_price=2000.0, scale=1.0)
    btc = [dataclasses.replace(b, open=b.open * 30, high=b.high * 30, low=b.low * 30, close=b.close * 30) for b in gold]
    return {"XAUUSD": gold, "GBPJPY": random_walk(2, n, start_price=190.0, scale=0.05), "BTCUSD": btc}


class StubStrategy:
    """Fires on the symbols in `fire`; the direction per symbol is fixed so a
    re-evaluation during position review keeps the setup valid."""

    version = 1

    def __init__(self, key: str = "multi_position_stub") -> None:
        self.key = key
        self.fire: dict[str, str] = {}

    def evaluate(self, features, regime):
        direction = self.fire.get(features.canonical_symbol)
        if direction is None:
            return None
        stop = features.close * 0.002
        return StrategySignal(
            strategy_key=self.key, strategy_version=self.version, canonical_symbol=features.canonical_symbol,
            direction=direction, raw_confidence=0.8, stop_distance=stop, target_distance=stop * 3,
            expected_duration_seconds=86_400, entry_method="MARKET", regime=regime.regime,
            rationale="test stub", feature_schema_version=features.feature_schema_version,
            data_timestamp=features.data_timestamp,
        )


class StubRegistry:
    def __init__(self, *strategies) -> None:
        self._s = {s.key: s for s in strategies}

    def get(self, key):
        return self._s.get(key)

    def all_active(self):
        return tuple(self._s.values())


def _start(tmp_path, *, bars=None, costs=True, **runtime):
    clock = FakeClock(START_AT)
    gateway = LiveMarketGateway(clock, bars or independent_market())
    engine, conn, gateway = build_engine(tmp_path, mode="DEMO", clock=clock, gateway=gateway, costs=costs, **runtime)
    bootstrap_kill_switch(conn, OperatorAuthority("test-operator"), reason="test setup")
    engine.startup()
    stub = StubStrategy()
    engine.demo.registry = StubRegistry(stub)
    step(engine, clock, seconds=STEP, tick=4)  # consume the first bar with nothing firing
    return engine, conn, gateway, clock, stub


def _open_symbols(conn) -> list[str]:
    return sorted(p.canonical_symbol for p in get_open_positions(conn))


def _broker_symbols(gateway) -> list[str]:
    return sorted(p.symbol for p in gateway.positions_get())


def _latest(conn, symbol):
    return conn.execute(
        "SELECT * FROM entry_decisions WHERE canonical_symbol = ? AND stage != 'WAIT' ORDER BY id DESC LIMIT 1",
        (symbol,),
    ).fetchone()


# ---------------------------------------------------------------------------
# two independent positions
# ---------------------------------------------------------------------------

def test_two_eligible_symbols_hold_simultaneous_positions(tmp_path):
    engine, conn, gateway, clock, stub = _start(tmp_path)
    stub.fire = {"XAUUSD": "BUY", "BTCUSD": "SELL"}
    step(engine, clock, seconds=STEP, tick=4)
    assert _open_symbols(conn) == ["BTCUSD", "XAUUSD"]
    assert _broker_symbols(gateway) == ["BTCUSD", "XAUUSD"]
    assert gateway.calls["order_send"] == 2
    total_risk = sum(p.initial_monetary_risk for p in get_open_positions(conn))
    equity = gateway.account_info().equity
    assert total_risk <= equity * 0.0050 + 1e-6  # 2 x 0.25 %, inside the 0.75 % aggregate ceiling


def test_an_existing_btcusd_position_does_not_block_a_later_xauusd_entry(tmp_path):
    engine, conn, gateway, clock, stub = _start(tmp_path)
    stub.fire = {"BTCUSD": "SELL"}
    step(engine, clock, seconds=STEP, tick=4)
    assert _open_symbols(conn) == ["BTCUSD"]
    stub.fire = {"BTCUSD": "SELL", "XAUUSD": "BUY"}
    step(engine, clock, seconds=STEP, tick=4)
    assert _open_symbols(conn) == ["BTCUSD", "XAUUSD"]
    assert _latest(conn, "BTCUSD")["reason"] == "position or order already open on this symbol"


def test_a_position_on_the_same_symbol_blocks_a_second_entry(tmp_path):
    engine, conn, gateway, clock, stub = _start(tmp_path)
    stub.fire = {"XAUUSD": "BUY"}
    step(engine, clock, seconds=STEP, tick=4)
    step(engine, clock, seconds=STEP, tick=4)
    assert _open_symbols(conn) == ["XAUUSD"]
    assert gateway.calls["order_send"] == 1
    assert _latest(conn, "XAUUSD")["reason"] == "position or order already open on this symbol"


def test_a_third_position_is_rejected_by_max_open_positions(tmp_path):
    engine, conn, gateway, clock, stub = _start(tmp_path)
    stub.fire = {"XAUUSD": "BUY", "BTCUSD": "SELL", "GBPJPY": "BUY"}
    step(engine, clock, seconds=STEP, tick=4)
    assert len(_open_symbols(conn)) == 2
    blocked = [s for s in ("XAUUSD", "GBPJPY", "BTCUSD") if s not in _open_symbols(conn)]
    assert len(blocked) == 1
    assert "max_open_positions reached (2/2)" in _latest(conn, blocked[0])["reason"]


def test_risk_and_margin_are_recomputed_from_fresh_truth_before_the_second_submission(tmp_path):
    engine, conn, gateway, clock, stub = _start(tmp_path)
    seen = []
    original = engine.demo.fresh_evidence

    def spy(canonical, signal, proposed_risk):
        before = gateway.calls["account_info"]
        evidence = original(canonical, signal, proposed_risk)
        risk = evidence.permission_input.risk_gate_input
        seen.append((canonical, risk.current_positions_count, round(risk.current_total_open_risk, 6),
                     gateway.calls["account_info"] > before, evidence.permission_input.open_or_pending_symbols))
        return evidence

    engine.demo.fresh_evidence = spy
    stub.fire = {"XAUUSD": "BUY", "BTCUSD": "SELL"}
    step(engine, clock, seconds=STEP, tick=4)
    first, second = seen[0][0], seen[-1][0]
    assert first != second
    first_risk = round(next(p.initial_monetary_risk for p in get_open_positions(conn) if p.canonical_symbol == first), 6)
    # evidence is rebuilt twice per submission, each time from a fresh broker account read
    assert [row[1:] for row in seen if row[0] == first] == [(0, 0.0, True, [])] * 2
    assert [row[1:] for row in seen if row[0] == second] == [(1, first_risk, True, [first])] * 2


# ---------------------------------------------------------------------------
# correlation
# ---------------------------------------------------------------------------

def test_genuinely_high_correlation_blocks_the_second_position(tmp_path):
    engine, conn, gateway, clock, stub = _start(tmp_path, bars=correlated_market())
    stub.fire = {"XAUUSD": "BUY", "BTCUSD": "BUY"}
    step(engine, clock, seconds=STEP, tick=4)
    opened = _open_symbols(conn)
    assert len(opened) == 1
    blocked = _latest(conn, ({"XAUUSD", "BTCUSD"} - set(opened)).pop())
    assert blocked["decision"] == "BLOCKED_PERMISSION"
    assert "correlates" in blocked["reason"] and "exceeding threshold 0.7" in blocked["reason"]


def test_missing_correlation_evidence_blocks_conservatively(tmp_path):
    engine, conn, gateway, clock, stub = _start(tmp_path, correlation_min_samples=10_000)
    stub.fire = {"XAUUSD": "BUY", "BTCUSD": "SELL"}
    step(engine, clock, seconds=STEP, tick=4)
    opened = _open_symbols(conn)
    assert len(opened) == 1  # the first entry has nothing to correlate against
    reason = _latest(conn, ({"XAUUSD", "BTCUSD"} - set(opened)).pop())["reason"]
    assert "unknown (N/A" in reason and "blocking conservatively" in reason


def test_a_valid_low_correlation_allows_the_second_position(tmp_path):
    engine, conn, gateway, clock, stub = _start(tmp_path)
    stub.fire = {"XAUUSD": "BUY", "BTCUSD": "SELL"}
    step(engine, clock, seconds=STEP, tick=4)
    result = engine.demo.correlation[("XAUUSD", "BTCUSD")]
    assert result.correlation is not None and abs(result.correlation) < 0.7
    assert result.sample_size >= 30
    assert _open_symbols(conn) == ["BTCUSD", "XAUUSD"]


# ---------------------------------------------------------------------------
# per-instrument isolation
# ---------------------------------------------------------------------------

def test_unknown_execution_costs_block_only_the_affected_instrument(tmp_path):
    engine, conn, gateway, clock, stub = _start(tmp_path)
    engine.config.costs["BTCUSD"].slippage_price = None  # BTCUSD slippage evidence now UNKNOWN
    stub.fire = {"XAUUSD": "BUY", "BTCUSD": "SELL"}
    step(engine, clock, seconds=STEP, tick=4)
    assert _open_symbols(conn) == ["XAUUSD"]
    assert _latest(conn, "BTCUSD")["decision"] == "BLOCK_COST"


def test_a_rejected_order_on_one_symbol_leaves_the_other_symbol_intact(tmp_path):
    engine, conn, gateway, clock, stub = _start(tmp_path)

    def reject_gold(gw, default, request):
        if request.symbol == "XAUUSD":
            return OrderSendResult(retcode=REJECT, comment="rejected", broker_order_id=None, broker_deal_id=None,
                                   broker_position_id=None, volume_filled=0.0, price_filled=None, raw={})
        return default(request)

    gateway.on("order_send", reject_gold)
    stub.fire = {"XAUUSD": "BUY", "BTCUSD": "SELL"}
    step(engine, clock, seconds=STEP, tick=4)
    assert _open_symbols(conn) == ["BTCUSD"] and _broker_symbols(gateway) == ["BTCUSD"]
    orders = conn.execute("SELECT canonical_symbol, state FROM orders ORDER BY canonical_symbol").fetchall()
    assert [(o["canonical_symbol"], o["state"]) for o in orders] == [("BTCUSD", "FILLED"), ("XAUUSD", "REJECTED")]
    assert get_state(conn, "reconciliation")["status"] == "CLEAN"
    # the next bar retries gold once, as a new proposal, never a duplicate of the rejected one
    step(engine, clock, seconds=STEP, tick=4)
    gold_orders = conn.execute("SELECT client_request_id FROM orders WHERE canonical_symbol = 'XAUUSD'").fetchall()
    assert len({o[0] for o in gold_orders}) == len(gold_orders) == 2
    assert _open_symbols(conn) == ["BTCUSD"]


# ---------------------------------------------------------------------------
# attribution across interleaved executions
# ---------------------------------------------------------------------------

def test_interleaved_positions_keep_their_own_strategy_chain_and_broker_ids(tmp_path):
    engine, conn, gateway, clock, stub = _start(tmp_path)
    other = StubStrategy("multi_position_stub_b")
    engine.demo.registry = StubRegistry(stub, other)
    stub.fire = {"XAUUSD": "BUY"}
    step(engine, clock, seconds=STEP, tick=4)
    other.fire = {"BTCUSD": "SELL"}
    step(engine, clock, seconds=STEP, tick=4)
    rows = conn.execute(
        "SELECT p.canonical_symbol, p.broker_position_id, o.broker_order_id, o.chain_key, c.strategy_key, "
        "c.chain_key AS ctx_chain FROM positions p JOIN orders o ON o.id = p.entry_order_id "
        "JOIN position_entry_context c ON c.broker_position_id = p.broker_position_id ORDER BY p.id"
    ).fetchall()
    assert [(r["canonical_symbol"], r["strategy_key"]) for r in rows] == [
        ("XAUUSD", "multi_position_stub"), ("BTCUSD", "multi_position_stub_b")]
    for r in rows:
        assert r["chain_key"] == r["ctx_chain"] and r["canonical_symbol"] in r["chain_key"]
        assert r["chain_key"].endswith(r["strategy_key"])
    assert rows[0]["broker_position_id"] != rows[1]["broker_position_id"]


# ---------------------------------------------------------------------------
# defect: a symbol whose market is closed at startup must be re-admitted
# once its market reopens (it was excluded for the life of the process)
# ---------------------------------------------------------------------------

REOPEN_AT = START_AT + 3 * STEP


def _gold_closed_until_reopen(clock: FakeClock) -> LiveMarketGateway:
    """XAUUSD's market is closed from 30 bars before startup until REOPEN_AT:
    no bars in the gap and a quote frozen at the last pre-close bar."""
    market = independent_market()
    closed_from = START_AT - 30 * STEP
    market["XAUUSD"] = [b for b in market["XAUUSD"] if not closed_from <= b.time < REOPEN_AT]
    gateway = LiveMarketGateway(clock, market)
    live_tick = gateway._live_tick

    def tick(name):
        t = live_tick(name)
        if name == "XAUUSD" and t is not None and clock.now < REOPEN_AT:
            return dataclasses.replace(t, time=closed_from)
        return t

    gateway._live_tick = tick
    return gateway


def _start_with_gold_closed(tmp_path, mode="DEMO"):
    clock = FakeClock(START_AT)
    gateway = _gold_closed_until_reopen(clock)
    engine, conn, gateway = build_engine(tmp_path, mode=mode, clock=clock, gateway=gateway)
    bootstrap_kill_switch(conn, OperatorAuthority("test-operator"), reason="test setup")
    summary = engine.startup()
    stub = StubStrategy()  # nothing fires until a test says so
    if engine.demo is not None:
        engine.demo.registry = StubRegistry(stub)
    return engine, conn, gateway, clock, summary, stub


def test_a_symbol_closed_at_startup_is_readmitted_when_its_market_reopens(tmp_path):
    engine, conn, gateway, clock, summary, stub = _start_with_gold_closed(tmp_path)
    assert "XAUUSD" not in summary["symbols"]
    assert summary["excluded_symbols"]["XAUUSD"].startswith("stale_quote")
    step(engine, clock, seconds=2 * STEP, tick=4)
    assert "XAUUSD" not in engine.symbols  # still closed: never admitted on a stale quote

    step(engine, clock, seconds=2 * STEP, tick=4)  # market reopened at REOPEN_AT
    assert engine.symbols["XAUUSD"] == "XAUUSD"
    assert engine.demo.symbols is engine.symbols
    admitted = conn.execute("SELECT detail FROM runtime_events WHERE event = 'SYMBOL_ADMITTED' "
                            "AND canonical_symbol = 'XAUUSD'").fetchone()
    assert admitted is not None
    excluded = conn.execute("SELECT cleared_at_utc FROM runtime_events WHERE dedup_key = 'symbol_excluded:XAUUSD'"
                            ).fetchone()
    assert excluded["cleared_at_utc"] is not None
    state = get_state(conn, "symbol_admission")
    assert "XAUUSD" in state["active"] and "XAUUSD" not in state["awaiting_market"]

    stub.fire = {"XAUUSD": "BUY", "BTCUSD": "SELL"}
    step(engine, clock, seconds=STEP, tick=4)
    assert _open_symbols(conn) == ["BTCUSD", "XAUUSD"]


def test_paper_also_readmits_a_symbol_whose_market_reopens(tmp_path):
    engine, conn, gateway, clock, summary, stub = _start_with_gold_closed(tmp_path, mode="PAPER")
    assert "XAUUSD" not in summary["symbols"]
    step(engine, clock, seconds=4 * STEP, tick=4)
    assert "XAUUSD" in engine.paper.symbols
    assert "XAUUSD" in get_state(conn, "why_no_trade")["symbols"]


def test_identity_failures_are_never_readmitted(tmp_path):
    clock = FakeClock(START_AT)
    gateway = LiveMarketGateway(clock, independent_market())
    from runtime_helpers import spec_for
    gateway.set_symbol(spec_for("XAUUSD", currency_profit="EUR"))
    engine, conn, gateway = build_engine(tmp_path, mode="DEMO", clock=clock, gateway=gateway)
    summary = engine.startup()
    assert "XAUUSD" not in summary["symbols"]
    step(engine, clock, seconds=4 * STEP, tick=4)
    assert "XAUUSD" not in engine.symbols
    assert "XAUUSD" in get_state(conn, "symbol_admission")["excluded"]


# ---------------------------------------------------------------------------
# defect: a resting/partial order on ANOTHER symbol is a potential position
# and must count toward max_open_positions (exactly once)
# ---------------------------------------------------------------------------

def _resting_order(conn, symbol, *, risk, now):
    from adaptive_scalper.execution.state_machine import OrderState
    from adaptive_scalper.execution.store import create_order, record_order_risk_accounting, transition_order_state
    order = create_order(conn, f"{symbol}:resting", symbol, symbol, "BUY", 0.01, now_utc=now)
    transition_order_state(conn, order.id, OrderState.SUBMITTED, now_utc=now)
    transition_order_state(conn, order.id, OrderState.RESTING, broker_order_id=f"99{order.id}", now_utc=now)
    record_order_risk_accounting(conn, order.id, requested_monetary_risk=risk, remaining_pending_monetary_risk=risk,
                                 now_utc=now)


def _btc_signal(engine):
    stub = StubStrategy()
    stub.fire = {"BTCUSD": "SELL"}
    return stub.evaluate(engine.demo.latest["BTCUSD"].features, type("Regime", (), {"regime": "RANGE"})())


def test_a_resting_order_on_another_symbol_counts_toward_max_open_positions(tmp_path):
    engine, conn, gateway, clock, stub = _start(tmp_path)
    stub.fire = {"XAUUSD": "BUY"}
    step(engine, clock, seconds=STEP, tick=4)
    assert _open_symbols(conn) == ["XAUUSD"]
    _resting_order(conn, "GBPJPY", risk=5.0, now=clock.now)

    evidence = engine.demo.fresh_evidence("BTCUSD", _btc_signal(engine), 5.0)
    risk = evidence.permission_input.risk_gate_input
    assert risk.current_positions_count == 2  # one open + one resting on another symbol
    assert round(risk.current_total_pending_risk, 6) == 5.0

    from adaptive_scalper.core.final_permission import evaluate_final_permission
    from adaptive_scalper.risk.governor import evaluate_risk_gate
    decision, reason = evaluate_risk_gate(risk, engine.demo.risk_limits)
    assert decision == "BLOCK_RISK" and "max_open_positions reached (2/2)" in reason
    assert evaluate_final_permission(evidence.permission_input).decision != "ALLOW"


def test_a_partial_order_and_its_own_position_count_once(tmp_path):
    engine, conn, gateway, clock, stub = _start(tmp_path)
    stub.fire = {"XAUUSD": "BUY"}
    step(engine, clock, seconds=STEP, tick=4)
    gold_order = conn.execute("SELECT id FROM orders WHERE canonical_symbol = 'XAUUSD'").fetchone()["id"]
    # the gold entry's remainder is still working: same symbol, same position
    conn.execute("UPDATE orders SET state = 'PARTIAL', remaining_pending_monetary_risk = 3.0 WHERE id = ?",
                 (gold_order,))
    risk = engine.demo.fresh_evidence("BTCUSD", _btc_signal(engine), 5.0).permission_input.risk_gate_input
    assert risk.current_positions_count == 1
    assert round(risk.current_total_pending_risk, 6) == 3.0


# ---------------------------------------------------------------------------
# risk governor scenarios at the configured DEMO limits (0.25 % / 0.75 % / 2 / 1)
# ---------------------------------------------------------------------------

EQUITY = 10_000.0
PER_TRADE = EQUITY * 0.0025  # 25.00


def _default_limits():
    from adaptive_scalper.config.loader import AppConfig
    from adaptive_scalper.risk.governor import risk_limits_from_config
    limits = risk_limits_from_config(AppConfig.model_validate({}).risk)
    assert (limits.risk_per_trade_pct, limits.max_total_open_risk_pct, limits.max_open_positions,
            limits.max_positions_per_symbol) == (0.25, 0.75, 2, 1)
    return limits


@pytest.mark.parametrize("case, overrides, expected", [
    ("second position, other symbol: 0.50 % total", {}, "ALLOW"),
    ("third position", dict(current_positions_count=2, current_total_open_risk=2 * PER_TRADE), "max_open_positions"),
    ("second position on XAUUSD", dict(proposed_symbol="XAUUSD", current_positions_for_symbol=1), "max_positions_per_symbol"),
    ("aggregate above 0.75 %", dict(current_total_open_risk=PER_TRADE, current_total_pending_risk=PER_TRADE + 0.01),
     "max_total_open_risk_pct"),
    ("pending exposure counted", dict(current_total_open_risk=0.0, current_positions_count=0,
                                      current_total_pending_risk=2 * PER_TRADE + 0.01), "max_total_open_risk_pct"),
    ("daily loss limit", dict(daily_realized_pnl=-0.02 * EQUITY), "daily loss"),
    ("drawdown limit", dict(peak_equity=EQUITY / 0.95), "drawdown"),
])
def test_risk_governor_multi_position_scenarios(case, overrides, expected):
    from adaptive_scalper.risk.governor import RiskGateInput, evaluate_risk_gate
    base = dict(proposed_symbol="BTCUSD", proposed_monetary_risk=PER_TRADE, equity=EQUITY,
                current_total_open_risk=PER_TRADE, current_total_pending_risk=0.0, current_positions_count=1,
                current_positions_for_symbol=0, daily_realized_pnl=0.0, peak_equity=EQUITY)
    decision, reason = evaluate_risk_gate(RiskGateInput(**{**base, **overrides}), _default_limits())
    if expected == "ALLOW":
        assert decision == "ALLOW", (case, reason)
    else:
        assert decision == "BLOCK_RISK" and expected in reason, (case, reason)


def test_insufficient_margin_blocks_only_the_second_submission(tmp_path):
    engine, conn, gateway, clock, stub = _start(tmp_path)

    def gold_needs_more_margin(gw, default, request):
        result = default(request)
        if request.symbol == "XAUUSD":
            return dataclasses.replace(result, margin_required=gw.account_info().margin_free + 1.0)
        return result

    gateway.on("order_check", gold_needs_more_margin)
    stub.fire = {"XAUUSD": "BUY", "BTCUSD": "SELL"}
    step(engine, clock, seconds=STEP, tick=4)
    assert _open_symbols(conn) == ["BTCUSD"]
    gold = _latest(conn, "XAUUSD")
    assert gold["decision"].startswith("BLOCK") and "margin" in gold["reason"]
    assert gateway.calls["order_send"] == 1


# ---------------------------------------------------------------------------
# MULTI-POSITION READINESS panel (observer only: reads SQLite, never MT5)
# ---------------------------------------------------------------------------

def _panel(conn, clock):
    from adaptive_scalper.dashboard.panels import compute_panel
    return compute_panel(conn, "multi_position", now=int(clock.now))


def test_readiness_panel_reports_capacity_correlation_and_per_symbol_reasons(tmp_path):
    engine, conn, gateway, clock, stub = _start(tmp_path)
    stub.fire = {"BTCUSD": "SELL"}
    step(engine, clock, seconds=STEP, tick=4)
    engine._publish_telemetry()
    p = _panel(conn, clock)
    assert (p["max_open_positions"], p["open_positions"], p["pending_orders"], p["remaining_position_slots"]) == (2, 1, 0, 1)
    assert p["open_monetary_risk"] > 0 and p["risk_capacity"]["remaining_aggregate_risk"] > 0
    assert p["symbols"]["BTCUSD"]["status"] == "EXISTING POSITION"
    assert p["symbols"]["XAUUSD"]["status"] in ("NO SIGNAL", "ELIGIBLE — AWAITING NATURAL SIGNAL")
    assert p["symbols"]["XAUUSD"]["quote_status"] == "FRESH"
    assert "not a forecast" in p["guarantee"]
    pair = next(c for c in p["correlation"] if c["pair"] == ["BTCUSD", "XAUUSD"])
    assert set(pair) >= {"correlation", "sample_size", "threshold", "decision", "reason"}
    assert pair["threshold"] == 0.7 and pair["sample_size"] >= 30 and pair["decision"] == "ALLOW"
    assert pair["evaluated_now"] is True


def test_readiness_panel_names_the_correlation_gate_from_the_journal(tmp_path):
    engine, conn, gateway, clock, stub = _start(tmp_path, bars=correlated_market())
    stub.fire = {"XAUUSD": "BUY", "BTCUSD": "BUY"}
    step(engine, clock, seconds=STEP, tick=4)
    engine._publish_telemetry()
    p = _panel(conn, clock)
    blocked = ({"XAUUSD", "BTCUSD"} - set(_open_symbols(conn))).pop()
    assert p["symbols"][blocked]["status"] == "CORRELATION BLOCK"
    pair = next(c for c in p["correlation"] if c["pair"] == ["BTCUSD", "XAUUSD"])
    assert pair["decision"] == "BLOCK_CORRELATION" and abs(pair["correlation"]) >= 0.7


def test_readiness_panel_shows_market_closed_then_admission(tmp_path):
    engine, conn, gateway, clock, summary, stub = _start_with_gold_closed(tmp_path)
    step(engine, clock, seconds=8, tick=4)
    assert _panel(conn, clock)["symbols"]["XAUUSD"]["status"] == "MARKET CLOSED"
    step(engine, clock, seconds=4 * STEP, tick=4)
    engine._publish_telemetry()
    p = _panel(conn, clock)
    assert "XAUUSD" in p["enabled_symbols"] and p["symbols"]["XAUUSD"]["status"] != "MARKET CLOSED"


def test_readiness_panel_labels_a_pre_fix_runtime_exclusion_as_market_closed(tmp_path):
    from adaptive_scalper.runtime.state import put_state
    engine, conn, gateway, clock, stub = _start(tmp_path)
    conn.execute("DELETE FROM runtime_state WHERE key = 'symbol_admission'")
    put_state(conn, "startup", {"symbols": {"BTCUSD": "BTCUSD"},
                                "excluded_symbols": {"XAUUSD": "stale_quote: quote age 70440.9s exceeds max 30.0s"}},
              now_utc=int(clock.now))
    status = _panel(conn, clock)["symbols"]["XAUUSD"]
    assert status["status"] == "MARKET CLOSED" and "restart required" in status["detail"]
