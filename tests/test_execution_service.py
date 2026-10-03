"""Tests for the single execution orchestration service (execution-safety
review round 1 findings #7,#8,#9 and round 2 findings #1,#2)."""

from __future__ import annotations

import pytest

from adaptive_scalper.core.final_permission import FinalPermissionInput
from adaptive_scalper.core.kill_switch import KillSwitchState, KillSwitchStatus
from adaptive_scalper.core.kill_switch import bootstrap as bootstrap_kill_switch
from adaptive_scalper.core.kill_switch import engage as engage_kill_switch
from adaptive_scalper.core.operator_authority import OperatorAuthority
from adaptive_scalper.costs.model import estimate_cost
from adaptive_scalper.execution.reconciliation import BLOCKING_MISMATCH, CLEAN
from adaptive_scalper.execution.service import (
    BLOCKED_BROKER_CONSTRAINT,
    BLOCKED_BROKER_STATE,
    BLOCKED_MARGIN,
    BLOCKED_PERMISSION,
    BLOCKED_PRESEND_RECHECK,
    CANCELLED,
    FILLED,
    PARTIAL,
    REJECTED,
    RESTING,
    UNKNOWN,
    FreshEvidence,
    submit_new_entry,
)
from adaptive_scalper.execution.state_machine import OrderState
from adaptive_scalper.gateway.demo_gate import DemoVerificationResult
from adaptive_scalper.gateway.fake_gateway import FakeGateway
from adaptive_scalper.gateway.symbol_validation import (
    EXECUTION_VALID,
    VALID,
    DirectionCheck,
    ExecutionQuoteCheck,
    SymbolValidationResult,
)
from adaptive_scalper.gateway.types import (
    AccountSnapshot,
    HistoricalDeal,
    OrderCheckResult,
    OrderSendResult,
    SymbolSpec,
    SymbolTradeMode,
    TerminalSnapshot,
    Tick,
    TradeMode,
)
from adaptive_scalper.journal.queries import get_chain_events
from adaptive_scalper.news.blocking import ALLOW as NEWS_ALLOW
from adaptive_scalper.news.blocking import BLOCK_NEWS, NewsBlockResult
from adaptive_scalper.persistence import connect, migrate
from adaptive_scalper.portfolio.exposure import PortfolioRiskLimits
from adaptive_scalper.risk.governor import RiskGateInput, RiskLimits
from adaptive_scalper.strategies.base import StrategySignal
from edge_fixtures import TEST_CERTIFICATE_KEY, TEST_PROTOCOL, fixture_validated_evidence


@pytest.fixture()
def db(tmp_path):
    conn = connect(tmp_path / "test.sqlite3")
    migrate(conn)
    # execution/service.py's finding-#12 critical-state check reads the
    # REAL persisted kill switch directly -- bootstrap it DISENGAGED so
    # tests exercise the happy/blocked paths this file actually targets,
    # not an incidental UNINITIALIZED block every test would otherwise hit.
    bootstrap_kill_switch(conn, OperatorAuthority("test-operator"), reason="test setup")
    yield conn
    conn.close()


def _demo_account(**overrides) -> AccountSnapshot:
    defaults = dict(
        login=123, trade_mode=TradeMode.DEMO, balance=10000.0, equity=10000.0, margin_free=10000.0,
        currency="USD", server="ICMarketsSC-Demo", company="IC Markets", trade_allowed=True, trade_expert=True,
    )
    defaults.update(overrides)
    return AccountSnapshot(**defaults)


def _demo_terminal(**overrides) -> TerminalSnapshot:
    defaults = dict(connected=True, trade_allowed=True, build=1000, name="MT5", company="MetaQuotes", path="")
    defaults.update(overrides)
    return TerminalSnapshot(**defaults)


def _symbol_spec(**overrides) -> SymbolSpec:
    defaults = dict(
        name="XAUUSDm", description="Gold", currency_base="XAU", currency_profit="USD", currency_margin="USD",
        digits=2, point=0.01, trade_contract_size=100.0, volume_min=0.01, volume_max=50.0, volume_step=0.01,
        trade_tick_size=0.01, trade_tick_value=1.0, spread=20, visible=True,
        trade_mode=SymbolTradeMode.FULL, filling_mode=3,
    )
    defaults.update(overrides)
    return SymbolSpec(**defaults)


def _demo_gateway(cls=FakeGateway, **overrides) -> FakeGateway:
    """THE gateway constructor every test in this file should use: a full
    DEMO account/terminal/symbol/tick state, so execution/service.py's
    finding-#12 critical-state check (its own fresh account_info/
    terminal_info/symbol_info/symbol_info_tick calls) sees a genuinely
    tradable setup rather than incidentally blocking on missing fixture
    data every test would otherwise need to reason about."""
    defaults = dict(
        account=_demo_account(), terminal=_demo_terminal(), symbols=[_symbol_spec()],
        ticks={"XAUUSDm": Tick(time=5000, bid=1999.0, ask=2001.0, last=2000.0, volume=1.0)},
    )
    defaults.update(overrides)
    return cls(**defaults)


def _signal(**overrides) -> StrategySignal:
    defaults = dict(
        strategy_key="momentum_continuation", strategy_version=1, canonical_symbol="XAUUSD",
        direction="BUY", raw_confidence=0.6, stop_distance=1.0, target_distance=2.0,
        expected_duration_seconds=300, entry_method="MARKET", regime="TRENDING_UP",
        rationale="test", feature_schema_version=1, data_timestamp=1000,
    )
    defaults.update(overrides)
    return StrategySignal(**defaults)


def _permission_input(**overrides) -> FinalPermissionInput:
    defaults = dict(
        mode="DEMO", signal=_signal(),
        kill_switch_state=KillSwitchState(status=KillSwitchStatus.DISENGAGED, reason="test",
                                           changed_at="2026-01-01T00:00:00Z", changed_by="operator"),
        demo_verification=DemoVerificationResult(allowed=True, block_reason=None, detail="DEMO confirmed"),
        reconciliation_status=CLEAN, has_dangerous_unknown_order=False, duplicate_active_order=False,
        asset_identity=SymbolValidationResult("XAUUSD", "XAUUSD", True, VALID, "ok"),
        direction_check=DirectionCheck(True, "direction_allowed", "ok"),
        execution_quote=ExecutionQuoteCheck(True, EXECUTION_VALID, "ok"),
        news_result=NewsBlockResult(NEWS_ALLOW, "no applicable event", None, None, None),
        # Execution mechanics after final permission are under test; the edge
        # gate's FLAT default is covered in tests/test_final_permission.py.
        edge_evidence=fixture_validated_evidence(),
        certificate_key=TEST_CERTIFICATE_KEY, now_utc=10_000, validation_protocol=TEST_PROTOCOL,
        cost_estimate=estimate_cost(spread_price=0.1, commission_price_equivalent=0.0,
                                     expected_slippage_price=0.0, swap_price_equivalent=0.0,
                                     uncertainty_margin_pct=0.0),
        open_or_pending_symbols=[], correlation_matrix={},
        risk_gate_input=RiskGateInput(
            proposed_symbol="XAUUSD", proposed_monetary_risk=20.0, equity=10000,
            current_total_open_risk=0.0, current_total_pending_risk=0.0,
            current_positions_count=0, current_positions_for_symbol=0,
            daily_realized_pnl=0.0, peak_equity=10000,
        ),
        risk_limits=RiskLimits(
            risk_per_trade_pct=0.25, max_total_open_risk_pct=0.75, max_daily_loss_pct=2.0,
            max_drawdown_pct=5.0, max_open_positions=2, max_positions_per_symbol=1,
        ),
        open_positions=[], pending_positions=[],
        portfolio_risk_limits=PortfolioRiskLimits(
            max_total_open_risk_pct=5.0, max_symbol_risk_pct=5.0,
            max_currency_direction_risk_pct=5.0, max_correlated_cluster_risk_pct=5.0,
        ),
    )
    defaults.update(overrides)
    return FinalPermissionInput(**defaults)


def _good_evidence(**permission_overrides) -> FreshEvidence:
    return FreshEvidence(permission_input=_permission_input(**permission_overrides), symbol_spec=_symbol_spec())


def _sequence(*evidences):
    """A fetch_fresh_evidence() stand-in that returns a DIFFERENT value
    on each successive call — the mechanism every finding-#1 regression
    test below uses to prove the service re-fetches independently rather
    than reusing a cached snapshot."""
    it = iter(evidences)

    def _fetch():
        return next(it)

    return _fetch


def _submit(db, gw, fetch_fresh_evidence, **overrides):
    defaults = dict(
        conn=db, gateway=gw, chain_key="chain-1", client_request_id="req-1", canonical_symbol="XAUUSD",
        broker_symbol="XAUUSDm", direction="BUY", volume=0.05, stop_loss=1990.0, take_profit=2020.0,
        fetch_fresh_evidence=fetch_fresh_evidence, now_utc=5000, history_window_seconds=10000,
        clock=lambda: 5000.0,  # matches _demo_gateway()'s default tick time=5000 -> always fresh unless overridden
    )
    defaults.update(overrides)
    return submit_new_entry(**defaults)


# --------------------------------------------------------------------------
# Happy path
# --------------------------------------------------------------------------

def test_happy_path_fills_and_journals_full_lifecycle(db):
    gw = _demo_gateway()
    outcome = _submit(db, gw, _sequence(_good_evidence(), _good_evidence()))
    assert outcome.status == FILLED
    assert outcome.order.state == OrderState.FILLED
    assert outcome.order.broker_position_id is not None

    events = [e.event_type for e in get_chain_events(db, "chain-1")]
    assert events == [
        "ENTRY_ALLOWED", "ENTRY_ALLOWED", "ORDER_SUBMITTED", "ORDER_ACCEPTED", "ORDER_FILLED", "POSITION_OPENED",
    ]

    # A real FILLED entry immediately becomes accounted local exposure —
    # not just a journaled event (finding #9's sibling fix for the DONE
    # path: nothing in this codebase created a `positions` row before).
    row = db.execute(
        "SELECT * FROM positions WHERE broker_position_id = ?", (outcome.order.broker_position_id,)
    ).fetchone()
    assert row is not None
    assert row["status"] == "OPEN"
    assert row["canonical_symbol"] == "XAUUSD"
    assert row["direction"] == "BUY"
    assert row["initial_monetary_risk"] == pytest.approx(20.0)
    assert row["strategy_key"] == "momentum_continuation"

    # External review findings #5/#6: durable typed risk accounting on
    # the order row itself, reconstructable from SQLite alone.
    order_row = db.execute("SELECT * FROM orders WHERE id = ?", (outcome.order.id,)).fetchone()
    assert order_row["requested_monetary_risk"] == pytest.approx(20.0)
    assert order_row["filled_volume"] == pytest.approx(0.05)
    assert order_row["filled_initial_monetary_risk"] == pytest.approx(20.0)
    assert order_row["remaining_volume"] == pytest.approx(0.0)
    assert order_row["remaining_pending_monetary_risk"] == pytest.approx(0.0)


def test_evidence_fetched_exactly_twice_on_the_happy_path(db):
    calls = []

    def fetch():
        calls.append(1)
        return _good_evidence()

    _submit(db, _demo_gateway(), fetch)
    assert len(calls) == 2


# --------------------------------------------------------------------------
# Initial permission block
# --------------------------------------------------------------------------

def test_blocked_initial_permission_never_calls_order_send(db):
    bad = FreshEvidence(permission_input=_permission_input(mode="REAL"), symbol_spec=_symbol_spec())
    gw = _demo_gateway()
    outcome = _submit(db, gw, _sequence(bad))
    assert outcome.status == BLOCKED_PERMISSION
    assert outcome.order.state == OrderState.PROPOSED
    assert gw.order_send_calls == []


# --------------------------------------------------------------------------
# Finding #1 (round 2): pre-send recheck must independently re-fetch and
# re-evaluate — a stale/cached ALLOW from the first check is not enough.
# --------------------------------------------------------------------------

def test_account_switches_to_real_before_send_blocks_presend_recheck(db):
    initial = _good_evidence()
    switched_to_real = FreshEvidence(permission_input=_permission_input(mode="REAL"), symbol_spec=_symbol_spec())
    gw = _demo_gateway()
    outcome = _submit(db, gw, _sequence(initial, switched_to_real))
    assert outcome.status == BLOCKED_PRESEND_RECHECK
    assert gw.order_send_calls == []


def test_kill_switch_engages_after_order_check_blocks_send(db):
    initial = _good_evidence()
    engaged_ks = KillSwitchState(status=KillSwitchStatus.ENGAGED, reason="operator", changed_at="2026-01-01T00:00:00Z", changed_by="operator")
    engaged = _good_evidence(kill_switch_state=engaged_ks)
    gw = _demo_gateway()
    outcome = _submit(db, gw, _sequence(initial, engaged))
    assert outcome.status == BLOCKED_PRESEND_RECHECK
    assert gw.order_send_calls == []


def test_quote_becomes_stale_before_send_blocks(db):
    initial = _good_evidence()
    stale = _good_evidence(execution_quote=ExecutionQuoteCheck(False, "execution_stale_quote", "too old"))
    gw = _demo_gateway()
    outcome = _submit(db, gw, _sequence(initial, stale))
    assert outcome.status == BLOCKED_PRESEND_RECHECK
    assert gw.order_send_calls == []


def test_news_window_begins_before_send_blocks(db):
    initial = _good_evidence()
    blocked_news = _good_evidence(news_result=NewsBlockResult(BLOCK_NEWS, "FOMC window", None, 60, 2700))
    gw = _demo_gateway()
    outcome = _submit(db, gw, _sequence(initial, blocked_news))
    assert outcome.status == BLOCKED_PRESEND_RECHECK
    assert gw.order_send_calls == []


def test_reconciliation_becomes_blocking_before_send_blocks(db):
    initial = _good_evidence()
    unclean = _good_evidence(reconciliation_status=BLOCKING_MISMATCH)
    gw = _demo_gateway()
    outcome = _submit(db, gw, _sequence(initial, unclean))
    assert outcome.status == BLOCKED_PRESEND_RECHECK
    assert gw.order_send_calls == []


def test_unknown_appears_before_send_blocks(db):
    initial = _good_evidence()
    with_unknown = _good_evidence(has_dangerous_unknown_order=True)
    gw = _demo_gateway()
    outcome = _submit(db, gw, _sequence(initial, with_unknown))
    assert outcome.status == BLOCKED_PRESEND_RECHECK
    assert gw.order_send_calls == []


def test_risk_state_changes_before_send_blocks(db):
    initial = _good_evidence()
    over_limit_risk = RiskGateInput(
        proposed_symbol="XAUUSD", proposed_monetary_risk=1000.0, equity=10000,
        current_total_open_risk=0.0, current_total_pending_risk=0.0,
        current_positions_count=0, current_positions_for_symbol=0,
        daily_realized_pnl=0.0, peak_equity=10000,
    )
    risky = _good_evidence(risk_gate_input=over_limit_risk)
    gw = _demo_gateway()
    outcome = _submit(db, gw, _sequence(initial, risky))
    assert outcome.status == BLOCKED_PRESEND_RECHECK
    assert gw.order_send_calls == []


def test_duplicate_appears_before_send_blocks(db):
    initial = _good_evidence()
    dup = _good_evidence(duplicate_active_order=True)
    gw = _demo_gateway()
    outcome = _submit(db, gw, _sequence(initial, dup))
    assert outcome.status == BLOCKED_PRESEND_RECHECK
    assert gw.order_send_calls == []


# --------------------------------------------------------------------------
# External review finding #12: this module must directly re-fetch the
# minimum broker-mutating safety state itself -- never trust it solely
# from a caller-supplied FreshEvidence, which could return the SAME
# cached object on both calls.
# --------------------------------------------------------------------------

def test_round1_blocks_when_gateway_account_is_not_demo_even_with_good_evidence(db):
    # The CALLER's evidence claims everything is fine (mode=DEMO, a
    # DemoVerificationResult saying allowed=True) -- but the GATEWAY's
    # real account is REAL. This module's own verify_demo_before_order()
    # call must catch this; it must never trust evidence.permission_input
    # .demo_verification as the sole authority.
    gw = _demo_gateway(account=_demo_account(trade_mode=TradeMode.REAL))
    outcome = _submit(db, gw, _sequence(_good_evidence()))
    assert outcome.status == BLOCKED_BROKER_STATE
    assert gw.order_send_calls == []


def test_caller_returning_the_same_cached_evidence_twice_does_not_bypass_round2(db):
    # The core finding #12 scenario: fetch_fresh_evidence() returns the
    # EXACT SAME object both times (simulating a buggy/malicious caller
    # that never actually re-fetches) -- but the underlying GATEWAY
    # genuinely changed (account left DEMO) between round 1 and round 2.
    # This module's OWN direct re-fetch must still catch it.
    gw = _demo_gateway()
    original_account_info = gw.account_info
    calls = {"n": 0}

    def flaky_account_info():
        calls["n"] += 1
        if calls["n"] >= 2:
            return _demo_account(trade_mode=TradeMode.REAL)
        return original_account_info()

    gw.account_info = flaky_account_info

    cached_evidence = _good_evidence()

    def fetch_same_object_every_time():
        return cached_evidence  # a caller bug: never actually re-fetches

    outcome = _submit(db, gw, fetch_same_object_every_time)
    assert outcome.status == BLOCKED_BROKER_STATE
    assert gw.order_send_calls == []


def test_real_kill_switch_engaged_blocks_send_even_when_caller_evidence_says_disengaged(db):
    # The caller's evidence is frozen at DISENGAGED (a stale snapshot),
    # but the REAL persisted kill switch is engaged between round 1 and
    # round 2. This module reads core.kill_switch.get_state() itself.
    gw = _demo_gateway()
    frozen_evidence = _good_evidence()  # kill_switch_state=DISENGAGED baked in

    calls = {"n": 0}

    def fetch():
        calls["n"] += 1
        if calls["n"] == 1:
            engage_kill_switch(db, "test: simulate operator emergency stop", "test-operator")
        return frozen_evidence

    outcome = _submit(db, gw, fetch)
    assert outcome.status == BLOCKED_BROKER_STATE
    assert gw.order_send_calls == []


def test_gateway_symbol_disabled_between_rounds_blocks_send(db):
    gw = _demo_gateway()
    original_symbol_info = gw.symbol_info
    calls = {"n": 0}

    def flaky_symbol_info(name):
        calls["n"] += 1
        if calls["n"] >= 2:
            return _symbol_spec(trade_mode=SymbolTradeMode.DISABLED)
        return original_symbol_info(name)

    gw.symbol_info = flaky_symbol_info
    outcome = _submit(db, gw, _sequence(_good_evidence(), _good_evidence()))
    assert outcome.status == BLOCKED_BROKER_STATE
    assert gw.order_send_calls == []


def test_gateway_quote_goes_stale_between_rounds_blocks_send(db):
    gw = _demo_gateway()
    original_tick = gw.symbol_info_tick
    calls = {"n": 0}

    def flaky_tick(name):
        calls["n"] += 1
        if calls["n"] >= 2:
            return Tick(time=1, bid=1999.0, ask=2001.0, last=2000.0, volume=1.0)  # ancient relative to now_utc=5000
        return original_tick(name)

    gw.symbol_info_tick = flaky_tick
    outcome = _submit(db, gw, _sequence(_good_evidence(), _good_evidence()))
    assert outcome.status == BLOCKED_BROKER_STATE
    assert gw.order_send_calls == []


# --------------------------------------------------------------------------
# External review finding #1 (2026-09-21, second round): the freshness
# CLOCK must be independently re-read at each round -- a frozen `now`
# carried across the whole submission lifecycle would let a quote go
# genuinely stale in real wall-clock time without round 2 ever noticing.
# --------------------------------------------------------------------------

def test_real_wall_clock_advancing_past_freshness_blocks_round_2_even_with_unchanged_tick(db):
    # The tick returned is IDENTICAL both rounds (never mutated) -- only
    # the CLOCK advances between round 1 and round 2, simulating real
    # wall-clock time genuinely passing during permission/order_check/DB
    # work. A frozen `now` value would never detect this.
    gw = _demo_gateway()
    clock_calls = {"n": 0}

    def advancing_clock():
        clock_calls["n"] += 1
        # round 1 sees a fresh clock (tick.time=5000, age=0); round 2's
        # clock has advanced well past the 5s execution-quote freshness
        # threshold, even though the SAME tick object is returned both times.
        return 5000.0 if clock_calls["n"] == 1 else 5010.0

    outcome = _submit(db, gw, _sequence(_good_evidence(), _good_evidence()), clock=advancing_clock)
    assert outcome.status == BLOCKED_BROKER_STATE
    assert gw.order_send_calls == []
    assert clock_calls["n"] >= 2  # the clock was genuinely re-read, not read once and reused


def test_clock_is_independent_of_the_event_journal_timestamp(db):
    # now_utc (journal timestamp) stays fixed for the whole submission;
    # clock() is a SEPARATE concern and can differ freely without
    # affecting journal event timestamps.
    gw = _demo_gateway()
    outcome = _submit(db, gw, _sequence(_good_evidence(), _good_evidence()), now_utc=99999, clock=lambda: 5000.0, history_window_seconds=200000)
    assert outcome.status == FILLED
    events = get_chain_events(db, "chain-1")
    assert all(e.event_timestamp_utc == 99999 for e in events)


# --------------------------------------------------------------------------
# External review finding #2 (2026-09-21): the execution boundary's own
# critical-state check must independently verify FRESH directional
# trade-mode permission and canonical asset identity -- not just
# trade_mode != DISABLED, and never solely trusted from the caller's
# (potentially stale) FinalPermissionInput.
# --------------------------------------------------------------------------

def test_full_to_closeonly_between_rounds_blocks_new_entry(db):
    gw = _demo_gateway()
    original_symbol_info = gw.symbol_info
    calls = {"n": 0}

    def flaky_symbol_info(name):
        calls["n"] += 1
        if calls["n"] >= 2:
            return _symbol_spec(trade_mode=SymbolTradeMode.CLOSEONLY)
        return original_symbol_info(name)

    gw.symbol_info = flaky_symbol_info
    outcome = _submit(db, gw, _sequence(_good_evidence(), _good_evidence()))
    assert outcome.status == BLOCKED_BROKER_STATE
    assert gw.order_send_calls == []


def test_full_to_longonly_before_a_sell_blocks_new_entry(db):
    gw = _demo_gateway()
    original_symbol_info = gw.symbol_info
    calls = {"n": 0}

    def flaky_symbol_info(name):
        calls["n"] += 1
        if calls["n"] >= 2:
            return _symbol_spec(trade_mode=SymbolTradeMode.LONGONLY)
        return original_symbol_info(name)

    gw.symbol_info = flaky_symbol_info
    outcome = _submit(db, gw, _sequence(_good_evidence(), _good_evidence()), direction="SELL")
    assert outcome.status == BLOCKED_BROKER_STATE
    assert gw.order_send_calls == []


def test_longonly_still_allows_a_buy(db):
    gw = _demo_gateway(symbols=[_symbol_spec(trade_mode=SymbolTradeMode.LONGONLY)])
    outcome = _submit(db, gw, _sequence(_good_evidence(), _good_evidence()), direction="BUY")
    assert outcome.status == FILLED


def test_fresh_asset_identity_mismatch_between_rounds_blocks_new_entry(db):
    gw = _demo_gateway()
    original_symbol_info = gw.symbol_info
    calls = {"n": 0}

    def flaky_symbol_info(name):
        calls["n"] += 1
        if calls["n"] >= 2:
            # description no longer contains "gold" and currency_base no
            # longer XAU -- identity can no longer be proven for XAUUSD.
            return _symbol_spec(description="Silver", currency_base="XAG")
        return original_symbol_info(name)

    gw.symbol_info = flaky_symbol_info
    outcome = _submit(db, gw, _sequence(_good_evidence(), _good_evidence()))
    assert outcome.status == BLOCKED_BROKER_STATE
    assert gw.order_send_calls == []


# --------------------------------------------------------------------------
# External review finding #13: a SECOND order_check + fresh margin
# recheck, immediately before send, against the freshest broker state.
# --------------------------------------------------------------------------

def test_presend_margin_recheck_blocks_when_fresh_margin_free_insufficient(db):
    class DrainingMarginGateway(FakeGateway):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            self._check_calls = 0

        def order_check(self, request):
            self._check_calls += 1
            # first check (round 1): plenty of margin available; second
            # check (round 2, immediately before send): margin_required
            # now exceeds the fresh margin_free -- a real margin_free
            # drop (e.g. another position opened) must still be caught.
            margin_required = 50.0 if self._check_calls == 1 else 99999.0
            return OrderCheckResult(retcode=10009, comment="ok", margin_required=margin_required)

    gw = _demo_gateway(cls=DrainingMarginGateway)
    evidence = FreshEvidence(permission_input=_permission_input(), symbol_spec=_symbol_spec(), available_margin_free=10000.0)
    outcome = _submit(db, gw, _sequence(evidence, evidence))
    assert outcome.status == BLOCKED_MARGIN
    assert gw.order_send_calls == []


def test_presend_order_check_recheck_failure_blocks_send(db):
    class SecondCheckFailsGateway(FakeGateway):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            self._check_calls = 0

        def order_check(self, request):
            self._check_calls += 1
            if self._check_calls == 1:
                return OrderCheckResult(retcode=10009, comment="ok", margin_required=10.0)
            return OrderCheckResult(retcode=10016, comment="invalid stops", margin_required=None)

    gw = _demo_gateway(cls=SecondCheckFailsGateway)
    outcome = _submit(db, gw, _sequence(_good_evidence(), _good_evidence()))
    assert outcome.status == BLOCKED_BROKER_CONSTRAINT
    assert gw.order_send_calls == []


def test_filling_type_change_between_rounds_blocks_send(db):
    gw = _demo_gateway()
    original_symbol_info = gw.symbol_info
    calls = {"n": 0}

    def flaky_symbol_info(name):
        calls["n"] += 1
        if calls["n"] >= 2:
            return _symbol_spec(filling_mode=0)  # no longer any supported filling type
        return original_symbol_info(name)

    gw.symbol_info = flaky_symbol_info
    outcome = _submit(db, gw, _sequence(_good_evidence(), _good_evidence()))
    assert outcome.status == BLOCKED_BROKER_CONSTRAINT
    assert gw.order_send_calls == []


# --------------------------------------------------------------------------
# order_check / margin blocks
# --------------------------------------------------------------------------

def test_order_check_failure_blocks_before_send(db):
    class RejectingCheckGateway(FakeGateway):
        def order_check(self, request):
            return OrderCheckResult(retcode=10014, comment="invalid volume", margin_required=None)

    gw = _demo_gateway(cls=RejectingCheckGateway)
    outcome = _submit(db, gw, _sequence(_good_evidence()))
    assert outcome.status == BLOCKED_BROKER_CONSTRAINT
    assert gw.order_send_calls == []
    assert outcome.order.state == OrderState.PROPOSED


def test_insufficient_margin_blocks_before_send(db):
    class HighMarginGateway(FakeGateway):
        def order_check(self, request):
            return OrderCheckResult(retcode=10009, comment="ok", margin_required=99999.0)

    gw = _demo_gateway(cls=HighMarginGateway)
    evidence = FreshEvidence(permission_input=_permission_input(), symbol_spec=_symbol_spec(), available_margin_free=100.0)
    outcome = _submit(db, gw, _sequence(evidence))
    assert outcome.status == BLOCKED_MARGIN
    assert gw.order_send_calls == []


def test_no_supported_filling_mode_blocks_before_check_or_send(db):
    # filling_type is now derived from THIS MODULE's own fresh
    # gateway.symbol_info() fetch (finding #12), not evidence.symbol_spec.
    gw = _demo_gateway(symbols=[_symbol_spec(filling_mode=0)])
    evidence = _good_evidence()
    outcome = _submit(db, gw, _sequence(evidence))
    assert outcome.status == BLOCKED_BROKER_CONSTRAINT
    assert gw.order_send_calls == []


# --------------------------------------------------------------------------
# Finding #2 (round 2): authoritative retcode interpretation
# --------------------------------------------------------------------------

def test_done_partial_becomes_partial_never_rejected(db):
    # Finding #9: a resolvable PARTIAL fill must immediately become
    # accounted LOCAL exposure -- a positions row scaled to the actual
    # filled volume, not just a journaled event.
    gw = _demo_gateway(
        order_send_responses=[
            OrderSendResult(retcode=10010, comment="partial fill", broker_order_id="111", broker_deal_id="222",
                             broker_position_id=None, volume_filled=0.02, price_filled=2000.0, raw={}),
        ],
        historical_deals=[
            HistoricalDeal(ticket=222, order=111, time=5000, type=0, entry=0, magic=0, position_id=333,
                            volume=0.02, price=2000.0, commission=-0.1, swap=0.0, profit=0.0, fee=0.0,
                            symbol="XAUUSDm", comment="", external_id=""),
        ],
    )
    outcome = _submit(db, gw, _sequence(_good_evidence(), _good_evidence()))
    assert outcome.status == PARTIAL
    assert outcome.order.state == OrderState.PARTIAL
    events = [e for e in get_chain_events(db, "chain-1") if e.event_type == "ORDER_PARTIAL"]
    assert len(events) == 1
    assert events[0].payload["volume_filled"] == 0.02

    # The requested volume was 0.05 with proposed_monetary_risk=20.0
    # (see _permission_input's risk_gate_input default) -- filled 0.02 of
    # it, so the LOCAL position's risk must be scaled proportionally, not
    # carry the full-volume risk figure.
    row = db.execute("SELECT * FROM positions WHERE broker_position_id = '333'").fetchone()
    assert row is not None
    assert row["status"] == "OPEN"
    assert row["volume"] == pytest.approx(0.02)
    assert row["entry_price"] == pytest.approx(2000.0)
    assert row["initial_monetary_risk"] == pytest.approx(20.0 * 0.02 / 0.05)

    opened_events = [e for e in get_chain_events(db, "chain-1") if e.event_type == "POSITION_OPENED"]
    assert len(opened_events) == 1
    assert opened_events[0].payload["partial"] is True

    # External review findings #5/#6: filled/remaining risk accounting on
    # the order row narrows correctly after the partial fill.
    order_row = db.execute("SELECT * FROM orders WHERE id = ?", (outcome.order.id,)).fetchone()
    assert order_row["requested_monetary_risk"] == pytest.approx(20.0)
    assert order_row["filled_volume"] == pytest.approx(0.02)
    assert order_row["filled_initial_monetary_risk"] == pytest.approx(20.0 * 0.02 / 0.05)
    assert order_row["remaining_volume"] == pytest.approx(0.03)
    assert order_row["remaining_pending_monetary_risk"] == pytest.approx(20.0 - (20.0 * 0.02 / 0.05))
    assert opened_events[0].payload["pending_volume"] == pytest.approx(0.03)


def test_unresolvable_partial_fill_becomes_unknown_and_blocks_new_entries(db):
    # No matching historical deal for broker_deal_id="222" -> the real
    # broker position genuinely cannot be established -- this is
    # PENDING_RECONCILIATION/UNKNOWN territory (directive section 30),
    # never a guessed local position.
    gw = _demo_gateway(order_send_responses=[
        OrderSendResult(retcode=10010, comment="partial fill", broker_order_id="111", broker_deal_id="222",
                         broker_position_id=None, volume_filled=0.02, price_filled=2000.0, raw={}),
    ])
    outcome = _submit(db, gw, _sequence(_good_evidence(), _good_evidence()))
    assert outcome.status == UNKNOWN
    assert outcome.order.state == OrderState.UNKNOWN
    assert db.execute("SELECT COUNT(*) AS n FROM positions").fetchone()["n"] == 0

    from adaptive_scalper.execution.reconciliation import has_dangerous_unresolved_unknown
    assert has_dangerous_unresolved_unknown(db) is True


def test_placed_becomes_resting_never_rejected(db):
    gw = _demo_gateway(order_send_responses=[
        OrderSendResult(retcode=10008, comment="placed", broker_order_id="111", broker_deal_id=None,
                         broker_position_id=None, volume_filled=0.0, price_filled=None, raw={}),
    ])
    outcome = _submit(db, gw, _sequence(_good_evidence(), _good_evidence()))
    assert outcome.status == RESTING
    assert outcome.order.state == OrderState.RESTING

    # External review finding #6: a RESTING order's monetary risk must be
    # derivable from persisted state -- fully pending, nothing filled.
    order_row = db.execute("SELECT * FROM orders WHERE id = ?", (outcome.order.id,)).fetchone()
    assert order_row["requested_monetary_risk"] == pytest.approx(20.0)
    assert order_row["filled_volume"] == pytest.approx(0.0)
    assert order_row["remaining_volume"] == pytest.approx(0.05)
    assert order_row["remaining_pending_monetary_risk"] == pytest.approx(20.0)


def test_cancel_retcode_becomes_cancelled(db):
    gw = _demo_gateway(order_send_responses=[
        OrderSendResult(retcode=10007, comment="cancelled", broker_order_id="111", broker_deal_id=None,
                         broker_position_id=None, volume_filled=0.0, price_filled=None, raw={}),
    ])
    outcome = _submit(db, gw, _sequence(_good_evidence(), _good_evidence()))
    assert outcome.status == CANCELLED
    assert outcome.order.state == OrderState.CANCELLED


def test_timeout_retcode_becomes_unknown_never_blindly_resent(db):
    gw = _demo_gateway(order_send_responses=[
        OrderSendResult(retcode=10012, comment="timeout", broker_order_id=None, broker_deal_id=None,
                         broker_position_id=None, volume_filled=0.0, price_filled=None, raw={}),
    ])
    outcome = _submit(db, gw, _sequence(_good_evidence(), _good_evidence()))
    assert outcome.status == UNKNOWN
    assert outcome.order.state == OrderState.UNKNOWN
    assert len(gw.order_send_calls) == 1  # exactly one attempt, never auto-resent

    from adaptive_scalper.execution.reconciliation import has_dangerous_unresolved_unknown
    assert has_dangerous_unresolved_unknown(db) is True


def test_invalid_volume_retcode_becomes_rejected(db):
    gw = _demo_gateway(order_send_responses=[
        OrderSendResult(retcode=10014, comment="invalid volume", broker_order_id=None, broker_deal_id=None,
                         broker_position_id=None, volume_filled=0.0, price_filled=None, raw={}),
    ])
    outcome = _submit(db, gw, _sequence(_good_evidence(), _good_evidence()))
    assert outcome.status == REJECTED


def test_broker_rejection_transitions_to_rejected(db):
    gw = _demo_gateway(order_send_responses=[
        OrderSendResult(retcode=10006, comment="rejected", broker_order_id=None, broker_deal_id=None,
                         broker_position_id=None, volume_filled=0.0, price_filled=None, raw={}),
    ])
    outcome = _submit(db, gw, _sequence(_good_evidence(), _good_evidence()))
    assert outcome.status == REJECTED
    assert outcome.order.state == OrderState.REJECTED
    events = [e.event_type for e in get_chain_events(db, "chain-1")]
    assert "ORDER_REJECTED" in events


def test_unresolvable_position_becomes_unknown_and_records_incident(db):
    gw = _demo_gateway(order_send_responses=[
        OrderSendResult(retcode=10009, comment="done", broker_order_id="111", broker_deal_id="222",
                         broker_position_id=None, volume_filled=0.05, price_filled=2000.0, raw={}),
    ])
    outcome = _submit(db, gw, _sequence(_good_evidence(), _good_evidence()))
    assert outcome.status == UNKNOWN
    assert outcome.order.state == OrderState.UNKNOWN

    from adaptive_scalper.execution.reconciliation import has_dangerous_unresolved_unknown
    assert has_dangerous_unresolved_unknown(db) is True


# --------------------------------------------------------------------------
# General invariants
# --------------------------------------------------------------------------

def test_exact_request_never_mutated_between_check_and_send(db):
    gw = _demo_gateway()
    _submit(db, gw, _sequence(_good_evidence(), _good_evidence()))
    assert len(gw.order_send_calls) == 1
    sent = gw.order_send_calls[0]
    assert sent.symbol == "XAUUSDm"
    assert sent.direction == "BUY"
    assert sent.volume == 0.05
    assert sent.stop_loss == 1990.0
    assert sent.take_profit == 2020.0
    assert sent.filling_type == "IOC"


def test_already_progressed_order_is_not_resubmitted(db):
    gw = _demo_gateway()
    first = _submit(db, gw, _sequence(_good_evidence(), _good_evidence()))
    assert first.status == FILLED
    calls_after_first = len(gw.order_send_calls)

    second = _submit(db, gw, _sequence(_good_evidence(), _good_evidence()), client_request_id="req-1")
    assert len(gw.order_send_calls) == calls_after_first  # no second send
    assert second.order.id == first.order.id
